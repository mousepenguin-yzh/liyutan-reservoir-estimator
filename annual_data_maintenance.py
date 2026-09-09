"""Application/service layer for the annual-data create and activate workflow."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from annual_data_diagnostics import (
    AnnualDataDiagnostics,
    CurrentAuditStatus,
    CurrentStatus,
    RecoverySeverity,
    VersionStatus,
    diagnose_annual_data,
)
from annual_data_activation import activate_annual_data_version
from annual_data_current_repair import (
    AnnualCurrentRepairPlan,
    complete_annual_current_repair_audit,
    plan_annual_current_repair,
    repair_annual_current,
)
from annual_data_recovery import recover_annual_activation_audit
from annual_data_version_writer import publish_annual_data_version
from shared_storage_reader import SharedStorageResult, StorageErrorCode
from software_provenance import SoftwareProvenanceResult, load_software_provenance


ENABLE_ANNUAL_DATA_WRITES_ENV = "LIYUTAN_ENABLE_ANNUAL_DATA_WRITES"
ENABLE_ANNUAL_DATA_RECOVERY_ENV = "LIYUTAN_ENABLE_ANNUAL_DATA_RECOVERY"
RUNTIME_PLATFORM = sys.platform


def annual_data_write_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Require the annual-data write feature flag to be the exact string ``1``."""
    env = os.environ if environ is None else environ
    return env.get(ENABLE_ANNUAL_DATA_WRITES_ENV) == "1"


def annual_data_recovery_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Require the high-risk recovery feature flag to be the exact string 1."""
    env = os.environ if environ is None else environ
    return env.get(ENABLE_ANNUAL_DATA_RECOVERY_ENV) == "1"


@dataclass(frozen=True)
class AnnualDataWriteCapability:
    available: bool
    activation_available: bool
    state: str
    reason: str
    root: Path | None = None
    observed_revision: int | None = None
    observed_current_version_id: str | None = None


@dataclass(frozen=True)
class AnnualDataRecoveryCapability:
    available: bool
    audit_recovery_available: bool
    reactivation_available: bool
    first_current_initialization_available: bool
    current_repair_available: bool
    repair_audit_completion_available: bool
    state: str
    reason: str
    root: Path | None = None
    observed_revision: int | None = None
    observed_current_version_id: str | None = None
    observed_previous_version_id: str | None = None
    repair_plan: AnnualCurrentRepairPlan | None = None


def annual_data_recovery_capability(
    result: SharedStorageResult | None,
    *,
    shared_mode_enabled: bool,
    diagnostics: AnnualDataDiagnostics | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> AnnualDataRecoveryCapability:
    """Expose exactly one condition-specific annual recovery/repair action."""
    unavailable = lambda state, reason, root=None: AnnualDataRecoveryCapability(
        False,
        False,
        False,
        False,
        False,
        False,
        state,
        reason,
        root=root,
    )
    if not annual_data_recovery_enabled(environ):
        return unavailable("feature_disabled", "年度資料 recovery 高風險開關目前為關閉。")
    if not annual_data_write_enabled(environ):
        return unavailable("annual_writes_disabled", "年度正式寫入功能未啟用，recovery 不可用。")
    if not shared_mode_enabled:
        return unavailable("shared_mode_disabled", "共享資料模式未啟用，recovery 不可用。")
    if result is None or result.root is None:
        return unavailable("shared_state_unavailable", "共享資料根目錄或讀取結果不可用。")
    if (RUNTIME_PLATFORM if platform is None else platform) != "win32":
        return unavailable(
            "platform_unsupported",
            "正式 recovery 僅支援 Windows/SMB production environment。",
            result.root,
        )
    if diagnostics is None or diagnostics.root != result.root:
        diagnostics = diagnose_annual_data(shared_result=result)
    repair_plan = plan_annual_current_repair(diagnostics)
    if repair_plan.available:
        first_current = repair_plan.first_current_initialization_available
        current_repair = (
            repair_plan.reconstruction_available
            or repair_plan.broken_target_switch_available
        )
        completion = repair_plan.repair_audit_completion_available
        return AnnualDataRecoveryCapability(
            True,
            False,
            False,
            first_current,
            current_repair,
            completion,
            repair_plan.action.value,
            repair_plan.reason,
            root=result.root,
            observed_revision=diagnostics.revision,
            observed_current_version_id=diagnostics.current_version_id,
            observed_previous_version_id=(
                None
                if diagnostics.current is None
                else diagnostics.current.get("previous_version_id")
            ),
            repair_plan=repair_plan,
        )
    if diagnostics.has_untrusted_annual_audit_evidence:
        return unavailable(
            "untrusted_annual_audit_evidence",
            repair_plan.reason,
            result.root,
        )
    if (
        diagnostics is None
        or not diagnostics.system_valid
        or diagnostics.current_status is not CurrentStatus.HEALTHY
        or diagnostics.current is None
    ):
        return unavailable(
            "broken_current_repair_required",
            "目前狀態需要下一階段 broken-current repair；本階段不可寫入。",
            result.root,
        )
    if diagnostics.current_audit_status in {
        CurrentAuditStatus.AMBIGUOUS,
        CurrentAuditStatus.UNINSPECTABLE,
        CurrentAuditStatus.NOT_APPLICABLE,
    }:
        return unavailable(
            "audit_not_safely_actionable",
            "current audit evidence 不唯一或無法可靠檢查；本階段不可寫入。",
            result.root,
        )

    audit_recovery = (
        diagnostics.current_audit_status is CurrentAuditStatus.MISSING
        and diagnostics.current_audit_match_count == 0
        and diagnostics.current_original_audit_match_count == 0
        and diagnostics.current_recovery_audit_match_count == 0
        and diagnostics.overall_severity is RecoverySeverity.RECOVERY_REQUIRED
    )
    has_reactivation_target = any(
        item.validation_ok
        and item.version_id != diagnostics.current_version_id
        and item.status in {VersionStatus.HISTORICAL, VersionStatus.ORPHAN}
        for item in diagnostics.versions
    )
    reactivation = (
        diagnostics.current_audit_status
        in {
            CurrentAuditStatus.MATCHED,
            CurrentAuditStatus.MATCHED_RECOVERY,
            CurrentAuditStatus.MATCHED_REPAIR,
            CurrentAuditStatus.REDUNDANT_EVIDENCE,
        }
        and diagnostics.overall_severity
        in {RecoverySeverity.HEALTHY, RecoverySeverity.ATTENTION}
        and has_reactivation_target
    )
    if audit_recovery:
        state = "audit_recovery_available"
        reason = "healthy current 的原始 activation audit 缺失，可人工補建 recovery evidence。"
    elif reactivation:
        state = "existing_version_reactivation_available"
        reason = "current 健康，可人工重新啟用既有合法 immutable version。"
    else:
        return unavailable(
            "recovery_not_safely_actionable",
            "diagnostics 未符合本階段兩種明確 safe recovery case。",
            result.root,
        )
    return AnnualDataRecoveryCapability(
        True,
        audit_recovery,
        reactivation,
        False,
        False,
        False,
        state,
        reason,
        root=result.root,
        observed_revision=diagnostics.revision,
        observed_current_version_id=diagnostics.current_version_id,
        observed_previous_version_id=diagnostics.current.get("previous_version_id"),
        repair_plan=repair_plan,
    )


def annual_data_write_capability(
    result: SharedStorageResult | None,
    *,
    shared_mode_enabled: bool,
    diagnostics: AnnualDataDiagnostics | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> AnnualDataWriteCapability:
    """Allow writes only for a healthy current or a validated first-version state."""
    if not annual_data_write_enabled(environ):
        return AnnualDataWriteCapability(
            False, False, "feature_disabled", "年度正式寫入功能開關目前為關閉。"
        )
    if not shared_mode_enabled:
        return AnnualDataWriteCapability(
            False, False, "shared_mode_disabled", "共享資料模式未啟用，年度正式寫入不可用。"
        )
    if result is None or result.root is None:
        return AnnualDataWriteCapability(
            False, False, "shared_state_unavailable", "共享資料根目錄或讀取結果不可用。"
        )

    if diagnostics is None or diagnostics.root != result.root:
        diagnostics = diagnose_annual_data(shared_result=result)

    # Filesystem diagnostics are authoritative for recovery interlocks.  The
    # ordinary reader cannot prove whether a successful current transition has
    # its matching audit, and a missing current is not a first-version state
    # when complete immutable versions already exist.
    if diagnostics is not None:
        if diagnostics.overall_severity is RecoverySeverity.RECOVERY_REQUIRED:
            return AnnualDataWriteCapability(
                False,
                False,
                "recovery_required",
                f"年度正式資料需要復原處理；正常建立／啟用已停止。{diagnostics.summary}",
                root=result.root,
                observed_revision=diagnostics.revision,
                observed_current_version_id=diagnostics.current_version_id,
            )
        if diagnostics.overall_severity is RecoverySeverity.INITIALIZATION_REQUIRED:
            return AnnualDataWriteCapability(
                False,
                False,
                "first_current_initialization_required",
                diagnostics.summary,
                root=result.root,
            )
        if diagnostics.overall_severity is RecoverySeverity.UNINSPECTABLE:
            return AnnualDataWriteCapability(
                False,
                False,
                "diagnostics_uninspectable",
                f"年度 diagnostics 無法可靠完成；正常建立／啟用已停止。{diagnostics.summary}",
                root=result.root,
            )

    if result.ok:
        current = result.annual.current
        revision = current["revision"]
        current_id = current["current_version_id"]
        state = "healthy_current"
    elif result.error is not None and result.error.code is StorageErrorCode.ANNUAL_CURRENT_MISSING:
        # The reader reaches this code only after root and system.json, including
        # reservoir_id=liyutan, have passed validation.
        if diagnostics is not None and not diagnostics.is_first_version_state:
            return AnnualDataWriteCapability(
                False,
                False,
                "shared_state_invalid",
                "current 缺失，但 versions 中已存在正式資料 evidence，需要 recovery 判斷，"
                "不能視為第一版。",
                root=result.root,
            )
        revision = 0
        current_id = None
        state = "first_version"
    else:
        detail = result.error.code.value if result.error is not None else "unknown"
        return AnnualDataWriteCapability(
            False,
            False,
            "shared_state_invalid",
            f"共享正式狀態未通過完整驗證（{detail}），正常年度寫入已停止。",
            root=result.root,
        )

    windows = (RUNTIME_PLATFORM if platform is None else platform) == "win32"
    activation_reason = (
        "年度版本可建立與啟用。"
        if windows
        else "版本可建立，但正式啟用只支援 Windows/SMB；此平台不得執行 production activation。"
    )
    return AnnualDataWriteCapability(
        True,
        windows,
        state,
        activation_reason,
        root=result.root,
        observed_revision=revision,
        observed_current_version_id=current_id,
    )


class AnnualDataMaintenanceService:
    """Injectable application boundary around the existing safe backends."""

    def __init__(
        self,
        *,
        publisher: Callable = publish_annual_data_version,
        activator: Callable = activate_annual_data_version,
        audit_recovery: Callable = recover_annual_activation_audit,
        current_repairer: Callable = repair_annual_current,
        repair_audit_completer: Callable = complete_annual_current_repair_audit,
        provenance_loader: Callable[[], SoftwareProvenanceResult] = load_software_provenance,
        platform: str | None = None,
    ) -> None:
        self._publisher = publisher
        self._activator = activator
        self._audit_recovery = audit_recovery
        self._current_repairer = current_repairer
        self._repair_audit_completer = repair_audit_completer
        self._provenance_loader = provenance_loader
        self._platform = RUNTIME_PLATFORM if platform is None else platform
        self._production_activator = activator is activate_annual_data_version
        self._production_audit_recovery = audit_recovery is recover_annual_activation_audit
        self._production_current_repairer = current_repairer is repair_annual_current
        self._production_repair_audit_completer = (
            repair_audit_completer is complete_annual_current_repair_audit
        )

    def publish(self, **arguments):
        return self._publisher(**arguments)

    def software_provenance(self) -> SoftwareProvenanceResult:
        return self._provenance_loader()

    def activate(self, **arguments):
        if self._platform != "win32" and self._production_activator:
            raise RuntimeError(
                "正式 annual current activation 僅支援 Windows/SMB；測試須注入 backend。"
            )
        return self._activator(**arguments)

    def initialize_first_current(self, **arguments):
        arguments["first_current_initialization"] = True
        return self.activate(**arguments)

    def recover_audit(self, **arguments):
        if self._platform != "win32" and self._production_audit_recovery:
            raise RuntimeError(
                "正式 annual audit recovery 僅支援 Windows/SMB；測試須注入 backend。"
            )
        return self._audit_recovery(**arguments)

    def repair_current(self, **arguments):
        if self._platform != "win32" and self._production_current_repairer:
            raise RuntimeError(
                "正式 annual current repair 僅支援 Windows/SMB；測試須注入 backend。"
            )
        return self._current_repairer(**arguments)

    def complete_repair_audit(self, **arguments):
        if self._platform != "win32" and self._production_repair_audit_completer:
            raise RuntimeError(
                "正式 annual current repair audit completion 僅支援 Windows/SMB；"
                "測試須注入 backend。"
            )
        return self._repair_audit_completer(**arguments)
