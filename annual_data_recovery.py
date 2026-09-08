"""Lock-protected audit recovery for a healthy annual-data current only."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Mapping

from annual_data_activation import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    LOCK_RELATIVE_PATH,
    AnnualDataActivationError,
    FaultInjector,
    LockFactory,
    WindowsSMBExclusiveLock,
    _checkpoint,
    _prepare_json_temp,
    _publish_audit_no_replace,
    _read_immutable_annual_bundle,
    _required_text,
    _utc,
    _utc_text,
    _uuid_text,
    _validate_root_and_system,
)
from annual_data_diagnostics import (
    AnnualDataDiagnostics,
    CurrentAuditStatus,
    CurrentStatus,
    RecoverySeverity,
    diagnose_annual_data,
)
from shared_storage_schema import (
    ANNUAL_ACTIVATION_RECOVERY_EVENT_TYPE,
    ANNUAL_ACTIVATION_RECOVERY_NOTICE,
    AUDIT_EVENT_SCHEMA,
    SCHEMA_VERSION,
    StorageValidationError,
    deserialize_json,
    serialize_json,
    validate_annual_activation_recovery_audit_event,
    validate_annual_current,
    validate_safe_id,
    validate_software_metadata,
)


class AnnualDataRecoveryError(AnnualDataActivationError):
    """A safe refusal or failure that never changes current.json."""


class AnnualDataRecoveryConflictError(AnnualDataRecoveryError):
    """Locked state no longer matches the diagnostics shown to the operator."""


@dataclass(frozen=True)
class AnnualDataAuditRecoveryResult:
    status: str
    current_version_id: str
    revision: int
    current_path: Path
    audit_path: Path
    audit_event: dict
    diagnostics: AnnualDataDiagnostics


def _observed_state(
    revision: object,
    current_version_id: object,
    previous_version_id: object,
) -> tuple[int, str, str | None]:
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise AnnualDataRecoveryError(
            "invalid_observed_state",
            "audit recovery 只接受既有 healthy current 的正整數 revision。",
        )
    try:
        current_id = validate_safe_id(current_version_id, "observed_current_version_id")
        previous_id = (
            None
            if previous_version_id is None
            else validate_safe_id(previous_version_id, "observed_previous_version_id")
        )
    except StorageValidationError as exc:
        raise AnnualDataRecoveryError("invalid_observed_state", str(exc)) from exc
    return revision, current_id, previous_id


def recover_annual_activation_audit(
    *,
    root: str | os.PathLike[str],
    observed_revision: int,
    observed_current_version_id: str,
    observed_previous_version_id: str | None,
    recovery_operator_display_name: str,
    recovery_note: str,
    recovery_software: Mapping[str, object],
    lock_factory: LockFactory | None = None,
    occurred_at: dt.datetime | None = None,
    event_uuid: uuid.UUID | str | None = None,
    audit_temp_uuid: uuid.UUID | str | None = None,
    hostname: str | None = None,
    process_id: int | None = None,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    monotonic=time.monotonic,
    sleep=time.sleep,
    random_uniform=None,
    fault_injector: FaultInjector | None = None,
) -> AnnualDataAuditRecoveryResult:
    """Publish one recovery audit after a locked diagnostics rerun."""
    operator = _required_text(
        recovery_operator_display_name,
        "recovery_operator_required",
        "recovery 操作人為必填。",
    )
    note = _required_text(
        recovery_note,
        "recovery_note_required",
        "recovery 備註為必填。",
    )
    observed = _observed_state(
        observed_revision,
        observed_current_version_id,
        observed_previous_version_id,
    )
    try:
        software = validate_software_metadata(recovery_software, "recovery_software")
    except StorageValidationError as exc:
        raise AnnualDataRecoveryError("invalid_recovery_input", str(exc)) from exc
    diagnostic_hostname = _required_text(
        hostname if hostname is not None else socket.gethostname(),
        "invalid_hostname",
        "hostname 不可為空白。",
    )
    diagnostic_pid = os.getpid() if process_id is None else process_id
    if (
        isinstance(diagnostic_pid, bool)
        or not isinstance(diagnostic_pid, int)
        or diagnostic_pid < 1
    ):
        raise AnnualDataRecoveryError("invalid_process_id", "process_id 必須是正整數。")
    timestamp = _utc(occurred_at)
    event_id = _uuid_text(event_uuid, "recovery event")
    audit_temp_id = _uuid_text(audit_temp_uuid, "recovery audit temp")

    shared_root = Path(root)
    _validate_root_and_system(shared_root)
    current_path = shared_root / "annual-data" / "current.json"
    lock_path = shared_root / LOCK_RELATIVE_PATH
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AnnualDataRecoveryError(
            "lock_directory_failed",
            f"無法準備 lock 目錄：{exc}",
            evidence_path=lock_path.parent,
        ) from exc

    if lock_factory is None:
        lock_arguments = {
            "timeout_seconds": lock_timeout_seconds,
            "monotonic": monotonic,
            "sleep": sleep,
        }
        if random_uniform is not None:
            lock_arguments["random_uniform"] = random_uniform
        lock_context: ContextManager[None] = WindowsSMBExclusiveLock(
            lock_path, **lock_arguments
        )
    else:
        lock_context = lock_factory(lock_path)

    with lock_context:
        _checkpoint(fault_injector, "recovery_critical_section_entered", lock_path)
        diagnostics = diagnose_annual_data(shared_root)
        current = diagnostics.current
        locked_state = (
            diagnostics.revision,
            diagnostics.current_version_id,
            None if current is None else current.get("previous_version_id"),
        )
        if locked_state != observed:
            raise AnnualDataRecoveryConflictError(
                "recovery_state_changed",
                "current revision／current version／previous version 已改變；"
                "未建立 recovery audit，請重新執行 diagnostics。",
                evidence_path=current_path,
            )
        if (
            not diagnostics.system_valid
            or diagnostics.current_status is not CurrentStatus.HEALTHY
            or diagnostics.current_audit_status is not CurrentAuditStatus.MISSING
            or diagnostics.current_audit_match_count != 0
            or diagnostics.current_original_audit_match_count != 0
            or diagnostics.current_recovery_audit_match_count != 0
            or diagnostics.overall_severity is not RecoverySeverity.RECOVERY_REQUIRED
            or current is None
        ):
            raise AnnualDataRecoveryConflictError(
                "recovery_precondition_changed",
                "鎖內 diagnostics 已不再是 healthy current + zero audit match；"
                "未重複補建，請重新執行 diagnostics。",
                evidence_path=current_path,
            )

        try:
            current_bytes = current_path.read_bytes()
            if validate_annual_current(deserialize_json(current_bytes)) != current:
                raise StorageValidationError("current.json 與鎖內 diagnostics 不一致")
            _, bundle = _read_immutable_annual_bundle(
                shared_root, diagnostics.current_version_id
            )
        except (OSError, StorageValidationError, AnnualDataActivationError) as exc:
            raise AnnualDataRecoveryConflictError(
                "recovery_evidence_changed",
                f"鎖內 current／immutable evidence 無法再次確認：{exc}",
                evidence_path=current_path,
            ) from exc

        occurred_text = _utc_text(timestamp)
        event = {
            "schema": AUDIT_EVENT_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "event_type": ANNUAL_ACTIVATION_RECOVERY_EVENT_TYPE,
            "occurred_at": occurred_text,
            "recovered_transition": {
                "before_revision": current["revision"] - 1,
                "before_current_version_id": current["previous_version_id"],
                "after_revision": current["revision"],
                "after_current_version_id": current["current_version_id"],
            },
            "recovery_operator_display_name": operator,
            "recovery_note": note,
            "recovery_software": software,
            "diagnostics": {
                "hostname": diagnostic_hostname,
                "process_id": diagnostic_pid,
            },
            "evidence": {
                "current_json_sha256": hashlib.sha256(current_bytes).hexdigest(),
                "current_version_id": current["current_version_id"],
                "current_version_manifest_sha256": hashlib.sha256(
                    bundle["version.json"]
                ).hexdigest(),
                "diagnostics_inspected_state": {
                    "inspected_at": diagnostics.inspected_at,
                    "system_valid": diagnostics.system_valid,
                    "current_status": diagnostics.current_status.value,
                    "current_audit_status": diagnostics.current_audit_status.value,
                    "original_activation_match_count": (
                        diagnostics.current_original_audit_match_count
                    ),
                    "recovery_match_count": (
                        diagnostics.current_recovery_audit_match_count
                    ),
                    "overall_severity": diagnostics.overall_severity.value,
                },
            },
            "recovery_record_notice": ANNUAL_ACTIVATION_RECOVERY_NOTICE,
            "result": "recovered_audit_evidence",
        }
        validated_event = validate_annual_activation_recovery_audit_event(event)
        if validated_event != event:
            raise AnnualDataRecoveryError(
                "recovery_event_validation_mismatch",
                "recovery audit schema validation 結果不一致。",
            )

        event_directory = (
            shared_root / "audit" / "events" / f"{timestamp:%Y}" / f"{timestamp:%m}"
        )
        try:
            event_directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AnnualDataRecoveryError(
                "recovery_audit_directory_failed",
                f"無法準備 recovery audit 目錄：{exc}",
                evidence_path=event_directory,
            ) from exc
        filename_timestamp = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
        audit_path = event_directory / f"{filename_timestamp}_{event_id}.json"
        if os.path.lexists(audit_path):
            raise AnnualDataRecoveryError(
                "recovery_audit_event_exists",
                "recovery audit event 已存在，絕對不會覆寫。",
                evidence_path=audit_path,
            )
        audit_temp = event_directory / f".{audit_path.name}.{audit_temp_id}.tmp"
        _prepare_json_temp(
            path=audit_temp,
            value=event,
            validator=validate_annual_activation_recovery_audit_event,
            kind="recovery_audit",
            fault_injector=fault_injector,
        )
        _checkpoint(fault_injector, "before_recovery_audit_publish", audit_temp)
        _publish_audit_no_replace(audit_temp, audit_path)
        _checkpoint(fault_injector, "after_recovery_audit_publish", audit_path)
        try:
            audit_bytes = audit_path.read_bytes()
            validated = validate_annual_activation_recovery_audit_event(
                deserialize_json(audit_bytes)
            )
        except (OSError, StorageValidationError) as exc:
            raise AnnualDataRecoveryError(
                "recovery_audit_post_publish_invalid",
                f"recovery audit 發布後重讀驗證失敗：{exc}",
                evidence_path=audit_path,
            ) from exc
        if audit_bytes != serialize_json(event) or validated != event:
            raise AnnualDataRecoveryError(
                "recovery_audit_post_publish_mismatch",
                "recovery audit 發布後內容與預期不一致。",
                evidence_path=audit_path,
            )
        _checkpoint(fault_injector, "after_recovery_audit_revalidation", audit_path)

        post_diagnostics = diagnose_annual_data(shared_root)
        if (
            current_path.read_bytes() != current_bytes
            or post_diagnostics.current_audit_status
            is not CurrentAuditStatus.MATCHED_RECOVERY
            or post_diagnostics.revision != current["revision"]
            or post_diagnostics.current_version_id != current["current_version_id"]
        ):
            raise AnnualDataRecoveryError(
                "recovery_postcondition_failed",
                "recovery audit 已發布，但完成後 diagnostics 無法確認 matched_recovery；"
                "current 未由本動作修改，請立即重新 diagnostics。",
                evidence_path=audit_path,
            )
        return AnnualDataAuditRecoveryResult(
            status="recovered_audit_evidence",
            current_version_id=current["current_version_id"],
            revision=current["revision"],
            current_path=current_path,
            audit_path=audit_path,
            audit_event=event,
            diagnostics=post_diagnostics,
        )
