"""Plan and execute evidence-backed repairs of a broken annual current pointer."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import socket
import time
import uuid
from dataclasses import dataclass
from enum import Enum
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
    AuditStatus,
    CurrentAuditStatus,
    CurrentStatus,
    RecoverySeverity,
    _event_transition,
    diagnose_annual_data,
)
from shared_storage_schema import (
    ANNUAL_CURRENT_REPAIR_EVENT_TYPE,
    ANNUAL_CURRENT_REPAIR_KINDS,
    ANNUAL_CURRENT_SCHEMA,
    AUDIT_EVENT_SCHEMA,
    SCHEMA_VERSION,
    StorageValidationError,
    deserialize_json,
    serialize_json,
    validate_annual_current,
    validate_annual_current_repair_audit_event,
    validate_safe_id,
    validate_software_metadata,
)


class CurrentRepairAction(str, Enum):
    NONE = "none"
    FIRST_CURRENT_INITIALIZATION = "first_current_initialization"
    RECONSTRUCT_MISSING_CURRENT = "reconstruct_missing_current"
    RECONSTRUCT_INVALID_CURRENT = "reconstruct_invalid_current"
    SWITCH_FROM_MISSING_TARGET = "switch_from_missing_target"
    SWITCH_FROM_INVALID_TARGET = "switch_from_invalid_target"
    COMPLETE_REPAIR_AUDIT = "complete_repair_audit"


@dataclass(frozen=True)
class AnnualCurrentRepairPlan:
    available: bool
    action: CurrentRepairAction
    reason: str
    observed_token: str
    target_version_ids: tuple[str, ...] = ()
    recommended_target_version_id: str | None = None
    reconstructed_revision: int | None = None
    reconstructed_current_version_id: str | None = None
    reconstructed_previous_version_id: str | None = None
    pending_audit_path: Path | None = None

    @property
    def first_current_initialization_available(self) -> bool:
        return self.available and self.action is CurrentRepairAction.FIRST_CURRENT_INITIALIZATION

    @property
    def reconstruction_available(self) -> bool:
        return self.available and self.action in {
            CurrentRepairAction.RECONSTRUCT_MISSING_CURRENT,
            CurrentRepairAction.RECONSTRUCT_INVALID_CURRENT,
        }

    @property
    def broken_target_switch_available(self) -> bool:
        return self.available and self.action in {
            CurrentRepairAction.SWITCH_FROM_MISSING_TARGET,
            CurrentRepairAction.SWITCH_FROM_INVALID_TARGET,
        }

    @property
    def repair_audit_completion_available(self) -> bool:
        return self.available and self.action is CurrentRepairAction.COMPLETE_REPAIR_AUDIT


@dataclass(frozen=True)
class AnnualCurrentRepairResult:
    status: str
    repair_kind: str
    target_version_id: str
    before_revision: int
    after_revision: int
    current_path: Path
    audit_path: Path
    current: dict
    audit_event: dict
    diagnostics: AnnualDataDiagnostics


class AnnualCurrentRepairError(AnnualDataActivationError):
    """A safe current-repair refusal or filesystem failure."""


class AnnualCurrentRepairConflictError(AnnualCurrentRepairError):
    """The lock-protected evidence differs from the operator's diagnostics."""


class AnnualCurrentRepairRecoveryRequiredError(AnnualCurrentRepairError):
    """The current repair succeeded but its repair audit was not confirmed."""

    def __init__(self, *, current: dict, audit_path: Path, pending_audit_path: Path, cause: Exception):
        super().__init__(
            "current_repaired_audit_incomplete",
            "current 已完成 repair，但 current-repair audit 尚未發布；不得 rollback 或重送 repair，"
            "請重新 diagnostics 後補完 pending repair evidence。",
            evidence_path=pending_audit_path,
        )
        self.current_repaired = True
        self.current = current
        self.audit_path = audit_path
        self.pending_audit_path = pending_audit_path
        self.__cause__ = cause


def _plan_token(diagnostics: AnnualDataDiagnostics) -> str:
    payload = {
        "system_valid": diagnostics.system_valid,
        "current_status": diagnostics.current_status.value,
        "current": diagnostics.current,
        "current_sha256": diagnostics.current_evidence.raw_bytes_sha256,
        "audit_events": [
            {
                "path": str(item.path),
                "status": item.status.value,
                "event": item.event,
            }
            for item in diagnostics.audits
        ],
        "versions": [
            {
                "entry": item.entry_name,
                "status": item.status.value,
                "valid": item.validation_ok,
                "version_id": item.version_id,
            }
            for item in diagnostics.versions
        ],
        "pending_repairs": [
            {"path": str(item.path), "event": item.event}
            for item in diagnostics.pending_current_repair_events
        ],
        "inspection_errors": list(diagnostics.inspection_errors),
    }
    return hashlib.sha256(serialize_json(payload)).hexdigest()


def _unavailable(diagnostics: AnnualDataDiagnostics, reason: str) -> AnnualCurrentRepairPlan:
    return AnnualCurrentRepairPlan(False, CurrentRepairAction.NONE, reason, _plan_token(diagnostics))


def _unique_transition_chain(
    diagnostics: AnnualDataDiagnostics,
) -> tuple[tuple[int, str | None, str] | None, str | None]:
    """Return the unique latest business transition, rejecting gaps and conflicts."""
    by_revision: dict[int, set[tuple[int, str | None, int, str]]] = {}
    for item in diagnostics.audits:
        transition = _event_transition(item)
        if transition is None:
            continue
        identity = (
            transition["before_revision"],
            transition["before_current_version_id"],
            transition["after_revision"],
            transition["after_current_version_id"],
        )
        by_revision.setdefault(transition["after_revision"], set()).add(identity)
    if not by_revision:
        return None, None
    maximum = max(by_revision)
    previous_id = None
    for revision in range(1, maximum + 1):
        identities = by_revision.get(revision)
        if not identities:
            return None, f"audit transition chain 缺少 revision {revision}"
        if len(identities) != 1:
            return None, f"audit transition chain 在 revision {revision} 有多個可能狀態"
        before_revision, before_id, after_revision, after_id = next(iter(identities))
        if (
            before_revision != revision - 1
            or after_revision != revision
            or before_id != previous_id
        ):
            return None, f"audit transition chain 在 revision {revision} 斷鏈或矛盾"
        previous_id = after_id
    latest = next(iter(by_revision[maximum]))
    return (maximum, latest[1], latest[3]), None


def plan_annual_current_repair(diagnostics: AnnualDataDiagnostics) -> AnnualCurrentRepairPlan:
    """Classify exactly one safe action from fully inspected filesystem evidence."""
    token = _plan_token(diagnostics)
    if not diagnostics.system_valid:
        return _unavailable(diagnostics, "system invalid；停止自動復原。")
    if diagnostics.inspection_errors:
        return _unavailable(diagnostics, "shared root 或 audit evidence 無法完整檢查；停止自動復原。")
    if diagnostics.has_untrusted_annual_audit_evidence:
        return _unavailable(diagnostics, "annual audit evidence 無法驗證；停止自動復原。")

    pending = diagnostics.pending_current_repair_events
    if pending:
        matching = [
            item
            for item in pending
            if diagnostics.current_status is CurrentStatus.HEALTHY
            and diagnostics.current is not None
            and item.event is not None
            and item.event["resulting_current"] == diagnostics.current
        ]
        if len(pending) == 1 and len(matching) == 1:
            return AnnualCurrentRepairPlan(
                True,
                CurrentRepairAction.COMPLETE_REPAIR_AUDIT,
                "current 已健康，但前次 repair audit publication 未完成；只能補完既有 evidence。",
                token,
                target_version_ids=(diagnostics.current_version_id,),
                pending_audit_path=matching[0].path,
            )
        return _unavailable(diagnostics, "pending repair evidence 不唯一或與 current 不一致；停止自動復原。")

    valid_targets = tuple(
        sorted(
            item.version_id
            for item in diagnostics.versions
            if item.validation_ok and item.version_id is not None
        )
    )
    if diagnostics.is_first_current_initialization_state:
        return AnnualCurrentRepairPlan(
            True,
            CurrentRepairAction.FIRST_CURRENT_INITIALIZATION,
            "已建立年度資料版本，但尚未設定第一個啟用版本。",
            token,
            target_version_ids=valid_targets,
        )

    latest, chain_error = _unique_transition_chain(diagnostics)
    if chain_error:
        return _unavailable(diagnostics, chain_error + "；停止自動復原。")

    if diagnostics.current_status in {CurrentStatus.MISSING, CurrentStatus.CURRENT_INVALID}:
        if latest is None:
            return _unavailable(diagnostics, "沒有可唯一證明最後正式 current 的 transition history。")
        revision, previous_id, current_id = latest
        if current_id not in valid_targets:
            return _unavailable(diagnostics, "由 audit 推導的 current target bundle 不完整或不存在。")
        if (
            diagnostics.current_status is CurrentStatus.CURRENT_INVALID
            and diagnostics.current_evidence.raw_bytes_sha256 is None
        ):
            return _unavailable(diagnostics, "invalid current 原始 bytes 無法安全保存。")
        action = (
            CurrentRepairAction.RECONSTRUCT_MISSING_CURRENT
            if diagnostics.current_status is CurrentStatus.MISSING
            else CurrentRepairAction.RECONSTRUCT_INVALID_CURRENT
        )
        return AnnualCurrentRepairPlan(
            True,
            action,
            "audit transition chain 唯一且 reconstructed target bundle 完整。",
            token,
            target_version_ids=(current_id,),
            reconstructed_revision=revision,
            reconstructed_current_version_id=current_id,
            reconstructed_previous_version_id=previous_id,
        )

    if diagnostics.current_status in {
        CurrentStatus.CURRENT_TARGET_MISSING,
        CurrentStatus.CURRENT_TARGET_INVALID,
    }:
        current = diagnostics.current
        if current is None:
            return _unavailable(diagnostics, "合法 current state 無法讀取。")
        if latest is not None:
            latest_revision, latest_previous, latest_current = latest
            if latest_revision > current["revision"] or (
                latest_revision == current["revision"]
                and (
                    latest_current != current["current_version_id"]
                    or latest_previous != current["previous_version_id"]
                )
            ):
                return _unavailable(diagnostics, "audit latest state 與合法 current 矛盾。")
        candidates = tuple(
            version_id
            for version_id in valid_targets
            if version_id != current["current_version_id"]
        )
        if not candidates:
            return _unavailable(diagnostics, "沒有任何完整可用的 recovery target。")
        action = (
            CurrentRepairAction.SWITCH_FROM_MISSING_TARGET
            if diagnostics.current_status is CurrentStatus.CURRENT_TARGET_MISSING
            else CurrentRepairAction.SWITCH_FROM_INVALID_TARGET
        )
        recommended = (
            current["previous_version_id"]
            if current["previous_version_id"] in candidates
            else None
        )
        return AnnualCurrentRepairPlan(
            True,
            action,
            "current revision 可讀；必須人工選擇一個完整 historical/orphan target。",
            token,
            target_version_ids=candidates,
            recommended_target_version_id=recommended,
        )

    return _unavailable(diagnostics, "目前 diagnostics 不需要 broken-current repair。")


def _audit_destination(root: Path, event: dict) -> Path:
    occurred = dt.datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
    timestamp = occurred.astimezone(dt.timezone.utc)
    filename_timestamp = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
    return (
        root
        / "audit"
        / "events"
        / f"{timestamp:%Y}"
        / f"{timestamp:%m}"
        / f"{filename_timestamp}_{event['event_id']}.json"
    )


def _lock_context(
    lock_path: Path,
    lock_factory: LockFactory | None,
    lock_timeout_seconds: float,
    monotonic,
    sleep,
    random_uniform,
) -> ContextManager[None]:
    if lock_factory is not None:
        return lock_factory(lock_path)
    arguments = {
        "timeout_seconds": lock_timeout_seconds,
        "monotonic": monotonic,
        "sleep": sleep,
    }
    if random_uniform is not None:
        arguments["random_uniform"] = random_uniform
    return WindowsSMBExclusiveLock(lock_path, **arguments)


def repair_annual_current(
    *,
    root: str | os.PathLike[str],
    observed_plan_token: str,
    repair_kind: str,
    target_version_id: str,
    recovery_operator_display_name: str,
    recovery_note: str,
    recovery_software: Mapping[str, object],
    lock_factory: LockFactory | None = None,
    occurred_at: dt.datetime | None = None,
    event_uuid: uuid.UUID | str | None = None,
    current_temp_uuid: uuid.UUID | str | None = None,
    audit_temp_uuid: uuid.UUID | str | None = None,
    hostname: str | None = None,
    process_id: int | None = None,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    monotonic=time.monotonic,
    sleep=time.sleep,
    random_uniform=None,
    fault_injector: FaultInjector | None = None,
) -> AnnualCurrentRepairResult:
    """Repair current under the existing SMB lock and publish a distinct repair audit."""
    operator = _required_text(
        recovery_operator_display_name,
        "recovery_operator_required",
        "recovery 操作人為必填。",
    )
    note = _required_text(recovery_note, "recovery_note_required", "recovery 備註為必填。")
    if repair_kind not in ANNUAL_CURRENT_REPAIR_KINDS:
        raise AnnualCurrentRepairError("invalid_repair_kind", "repair_kind 不在允許清單。")
    try:
        target_id = validate_safe_id(target_version_id, "target_version_id")
        software = validate_software_metadata(recovery_software, "recovery_software")
    except StorageValidationError as exc:
        raise AnnualCurrentRepairError("invalid_repair_input", str(exc)) from exc
    diagnostic_hostname = _required_text(
        hostname if hostname is not None else socket.gethostname(),
        "invalid_hostname",
        "hostname 不可為空白。",
    )
    diagnostic_pid = os.getpid() if process_id is None else process_id
    if isinstance(diagnostic_pid, bool) or not isinstance(diagnostic_pid, int) or diagnostic_pid < 1:
        raise AnnualCurrentRepairError("invalid_process_id", "process_id 必須是正整數。")
    timestamp = _utc(occurred_at)
    event_id = _uuid_text(event_uuid, "repair event")
    current_temp_id = _uuid_text(current_temp_uuid, "repair current temp")
    audit_temp_id = _uuid_text(audit_temp_uuid, "repair audit temp")

    shared_root = Path(root)
    _validate_root_and_system(shared_root)
    _, initial_target_bundle = _read_immutable_annual_bundle(shared_root, target_id)
    lock_path = shared_root / LOCK_RELATIVE_PATH
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AnnualCurrentRepairError(
            "lock_directory_failed", f"無法準備 lock 目錄：{exc}", evidence_path=lock_path.parent
        ) from exc
    lock_context = _lock_context(
        lock_path,
        lock_factory,
        lock_timeout_seconds,
        monotonic,
        sleep,
        random_uniform,
    )
    current_path = shared_root / "annual-data" / "current.json"
    current_repaired = False
    new_current: dict = {}
    audit_path = shared_root / "audit" / "events" / "unknown"
    audit_temp = audit_path
    try:
        with lock_context:
            _checkpoint(fault_injector, "repair_critical_section_entered", lock_path)
            diagnostics = diagnose_annual_data(shared_root)
            plan = plan_annual_current_repair(diagnostics)
            if not plan.available or plan.observed_token != observed_plan_token:
                raise AnnualCurrentRepairConflictError(
                    "repair_state_changed",
                    "畫面 evidence 與鎖內 diagnostics 不同；未 repair，請重新執行 diagnostics。",
                    evidence_path=current_path,
                )
            if plan.action.value != repair_kind or target_id not in plan.target_version_ids:
                raise AnnualCurrentRepairConflictError(
                    "repair_plan_changed",
                    "鎖內 repair kind 或可用 target 已改變；未 repair。",
                    evidence_path=current_path,
                )
            try:
                _, final_target_bundle = _read_immutable_annual_bundle(shared_root, target_id)
            except AnnualDataActivationError as exc:
                raise AnnualCurrentRepairConflictError(
                    "repair_target_invalid", "鎖內 target 已不完整；未 repair。", evidence_path=exc.evidence_path
                ) from exc
            if final_target_bundle != initial_target_bundle:
                raise AnnualCurrentRepairConflictError(
                    "repair_target_changed",
                    "target bundle 在 repair 前發生變更；未 repair。",
                    evidence_path=shared_root / "annual-data" / "versions" / target_id,
                )

            raw_current = None
            if diagnostics.current_evidence.exists:
                try:
                    raw_current = current_path.read_bytes()
                except OSError as exc:
                    raise AnnualCurrentRepairConflictError(
                        "repair_current_evidence_changed", "原始 current bytes 無法重讀；未 repair。"
                    ) from exc
                if hashlib.sha256(raw_current).hexdigest() != diagnostics.current_evidence.raw_bytes_sha256:
                    raise AnnualCurrentRepairConflictError(
                        "repair_current_evidence_changed", "原始 current bytes 已改變；未 repair。"
                    )
            elif current_path.exists():
                raise AnnualCurrentRepairConflictError(
                    "repair_current_evidence_changed", "current 已出現；未 repair。"
                )

            reconstruction = plan.reconstruction_available
            if reconstruction:
                before_revision = plan.reconstructed_revision
                after_revision = plan.reconstructed_revision
                previous_id = plan.reconstructed_previous_version_id
            else:
                before_revision = diagnostics.revision
                after_revision = before_revision + 1
                previous_id = diagnostics.current_version_id
            occurred_text = _utc_text(timestamp)
            new_current = {
                "schema": ANNUAL_CURRENT_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "revision": after_revision,
                "current_version_id": target_id,
                "previous_version_id": previous_id,
                "updated_at": occurred_text,
                "operator_display_name": operator,
            }
            validate_annual_current(new_current)
            event = {
                "schema": AUDIT_EVENT_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "event_id": event_id,
                "event_type": ANNUAL_CURRENT_REPAIR_EVENT_TYPE,
                "occurred_at": occurred_text,
                "repair_kind": repair_kind,
                "recovery_operator_display_name": operator,
                "recovery_note": note,
                "recovery_software": software,
                "diagnostics": {
                    "hostname": diagnostic_hostname,
                    "process_id": diagnostic_pid,
                },
                "pre_repair_diagnostics": {
                    "inspected_at": diagnostics.inspected_at,
                    "system_valid": diagnostics.system_valid,
                    "current_status": diagnostics.current_status.value,
                    "current_audit_status": diagnostics.current_audit_status.value,
                    "overall_severity": diagnostics.overall_severity.value,
                    "observed_revision": diagnostics.revision,
                    "observed_current_version_id": diagnostics.current_version_id,
                    "observed_previous_version_id": (
                        None
                        if diagnostics.current is None
                        else diagnostics.current.get("previous_version_id")
                    ),
                },
                "pre_repair_current_evidence": {
                    "exists": diagnostics.current_evidence.exists,
                    "raw_bytes_sha256": (
                        None if raw_current is None else hashlib.sha256(raw_current).hexdigest()
                    ),
                    "raw_bytes_base64": (
                        None
                        if raw_current is None
                        else base64.b64encode(raw_current).decode("ascii")
                    ),
                },
                "target_version_id": target_id,
                "target_manifest_sha256": hashlib.sha256(
                    final_target_bundle["version.json"]
                ).hexdigest(),
                "before_revision": before_revision,
                "after_revision": after_revision,
                "resulting_current": new_current,
                "result": "success",
            }
            validate_annual_current_repair_audit_event(event)
            audit_path = _audit_destination(shared_root, event)
            try:
                audit_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise AnnualCurrentRepairError(
                    "repair_audit_directory_failed",
                    f"無法準備 repair audit 目錄：{exc}",
                    evidence_path=audit_path.parent,
                ) from exc
            if os.path.lexists(audit_path):
                raise AnnualCurrentRepairError(
                    "repair_audit_event_exists", "repair audit event 已存在，絕對不覆寫。"
                )
            audit_temp = audit_path.parent / (
                f".{audit_path.name}.{audit_temp_id}.current-repair-audit.tmp"
            )
            _prepare_json_temp(
                path=audit_temp,
                value=event,
                validator=validate_annual_current_repair_audit_event,
                kind="current_repair_audit",
                fault_injector=fault_injector,
            )
            current_temp = current_path.parent / f".current.json.{current_temp_id}.tmp"
            _, current_bytes = _prepare_json_temp(
                path=current_temp,
                value=new_current,
                validator=validate_annual_current,
                kind="current",
                fault_injector=fault_injector,
            )
            _checkpoint(fault_injector, "before_current_replace", current_temp)
            try:
                os.replace(current_temp, current_path)
            except OSError as exc:
                raise AnnualCurrentRepairError(
                    "current_replace_failed", f"current 原子取代失敗：{exc}", evidence_path=current_temp
                ) from exc
            current_repaired = True
            _checkpoint(fault_injector, "after_current_replace", current_path)
            try:
                reread = current_path.read_bytes()
                validated_current = validate_annual_current(deserialize_json(reread))
            except (OSError, StorageValidationError) as exc:
                raise AnnualCurrentRepairError(
                    "current_post_replace_invalid", f"repair 後 current 重讀驗證失敗：{exc}"
                ) from exc
            if reread != current_bytes or validated_current != new_current:
                raise AnnualCurrentRepairError(
                    "current_post_replace_mismatch", "repair 後 current 與預期不一致。"
                )
            _checkpoint(fault_injector, "after_current_revalidation", current_path)
            _checkpoint(fault_injector, "before_repair_audit_publish", audit_temp)
            _publish_audit_no_replace(audit_temp, audit_path)
            _checkpoint(fault_injector, "after_repair_audit_publish", audit_path)
            audit_bytes = audit_path.read_bytes()
            if (
                audit_bytes != serialize_json(event)
                or validate_annual_current_repair_audit_event(deserialize_json(audit_bytes))
                != event
            ):
                raise AnnualCurrentRepairError(
                    "repair_audit_post_publish_mismatch", "repair audit 發布後內容不一致。"
                )
            post = diagnose_annual_data(shared_root)
            if (
                post.current_status is not CurrentStatus.HEALTHY
                or post.current_audit_status is not CurrentAuditStatus.MATCHED_REPAIR
                or post.current != new_current
            ):
                raise AnnualCurrentRepairError(
                    "repair_postcondition_failed", "repair audit 已發布，但 diagnostics 無法確認 matched repair。"
                )
            return AnnualCurrentRepairResult(
                "repaired",
                repair_kind,
                target_id,
                before_revision,
                after_revision,
                current_path,
                audit_path,
                new_current,
                event,
                post,
            )
    except Exception as exc:
        if current_repaired:
            if isinstance(exc, AnnualCurrentRepairRecoveryRequiredError):
                raise
            raise AnnualCurrentRepairRecoveryRequiredError(
                current=new_current,
                audit_path=audit_path,
                pending_audit_path=audit_temp,
                cause=exc,
            ) from exc
        raise


def complete_annual_current_repair_audit(
    *,
    root: str | os.PathLike[str],
    observed_plan_token: str,
    lock_factory: LockFactory | None = None,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    monotonic=time.monotonic,
    sleep=time.sleep,
    random_uniform=None,
) -> AnnualCurrentRepairResult:
    """Publish the one pending repair audit without rewriting current.json."""
    shared_root = Path(root)
    _validate_root_and_system(shared_root)
    lock_path = shared_root / LOCK_RELATIVE_PATH
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AnnualCurrentRepairError("lock_directory_failed", str(exc)) from exc
    with _lock_context(
        lock_path,
        lock_factory,
        lock_timeout_seconds,
        monotonic,
        sleep,
        random_uniform,
    ):
        diagnostics = diagnose_annual_data(shared_root)
        plan = plan_annual_current_repair(diagnostics)
        if (
            not plan.repair_audit_completion_available
            or plan.observed_token != observed_plan_token
            or plan.pending_audit_path is None
        ):
            raise AnnualCurrentRepairConflictError(
                "repair_completion_state_changed",
                "pending repair evidence 或 current 已改變；未發布 audit，請重新 diagnostics。",
            )
        pending_path = plan.pending_audit_path
        try:
            event_bytes = pending_path.read_bytes()
            event = validate_annual_current_repair_audit_event(deserialize_json(event_bytes))
            current_bytes = (shared_root / "annual-data" / "current.json").read_bytes()
            current = validate_annual_current(deserialize_json(current_bytes))
            _, target_bundle = _read_immutable_annual_bundle(
                shared_root, event["target_version_id"]
            )
        except (OSError, StorageValidationError, AnnualDataActivationError) as exc:
            raise AnnualCurrentRepairConflictError(
                "repair_completion_evidence_changed",
                f"pending repair evidence 無法再次確認：{exc}",
                evidence_path=pending_path,
            ) from exc
        if (
            current != event["resulting_current"]
            or current_bytes != serialize_json(current)
            or hashlib.sha256(target_bundle["version.json"]).hexdigest()
            != event["target_manifest_sha256"]
        ):
            raise AnnualCurrentRepairConflictError(
                "repair_completion_evidence_changed",
                "current 或 target manifest 與 pending repair evidence 不一致。",
                evidence_path=pending_path,
            )
        audit_path = _audit_destination(shared_root, event)
        if os.path.lexists(audit_path):
            raise AnnualCurrentRepairConflictError(
                "repair_audit_event_exists", "repair audit 已存在；不重複發布。", evidence_path=audit_path
            )
        _publish_audit_no_replace(pending_path, audit_path)
        post = diagnose_annual_data(shared_root)
        if (
            post.current != current
            or post.current_audit_status is not CurrentAuditStatus.MATCHED_REPAIR
        ):
            raise AnnualCurrentRepairError(
                "repair_completion_postcondition_failed",
                "repair audit 已發布，但 diagnostics 無法確認 matched repair。",
                evidence_path=audit_path,
            )
        return AnnualCurrentRepairResult(
            "repair_audit_completed",
            event["repair_kind"],
            event["target_version_id"],
            event["before_revision"],
            event["after_revision"],
            shared_root / "annual-data" / "current.json",
            audit_path,
            current,
            event,
            post,
        )
