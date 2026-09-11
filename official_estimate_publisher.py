"""Safely publish one validated official-estimate candidate.

Phase 2-5C1 is a backend primitive only. It publishes the exact in-memory
candidate from Phase 2-5B and never imports Streamlit or enables UI writes.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
import sys
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from annual_data_activation import AnnualDataActivationError, WindowsSMBExclusiveLock
from official_estimate_candidate import OfficialEstimateCandidate
from shared_storage_schema import (
    AUDIT_EVENT_SCHEMA,
    COMMITTED_SCHEMA,
    OFFICIAL_CURRENT_SCHEMA,
    OFFICIAL_DATA_FILES,
    OFFICIAL_ESTIMATE_PUBLISH_EVENT_TYPE,
    SCHEMA_VERSION,
    StorageValidationError,
    deserialize_json,
    serialize_json,
    sha256_bytes,
    validate_annual_bundle,
    validate_official_bundle,
    validate_official_current,
    validate_official_estimate_publish_audit_event,
    validate_safe_id,
    validate_system,
)


LOCK_RELATIVE_PATH = Path("locks") / "official-current.lock"
DEFAULT_LOCK_TIMEOUT_SECONDS = 15.0
_CANDIDATE_PAYLOAD_FILES = ("manifest.json", *OFFICIAL_DATA_FILES)

FaultInjector = Callable[[str, Path], None]
LockFactory = Callable[[Path], AbstractContextManager[None]]
Clock = Callable[[], dt.datetime]


class OfficialEstimatePublishError(RuntimeError):
    """A safe refusal or filesystem failure with a stable UI-facing code."""

    def __init__(self, code: str, message: str, *, evidence_path: Path | None = None,
                 cause: Exception | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.evidence_path = evidence_path
        if cause is not None:
            self.__cause__ = cause


class OfficialEstimateConflictError(OfficialEstimatePublishError):
    """The caller's observed official current state is no longer current."""


class OfficialEstimateVersionPublishedCurrentNotSwitchedError(OfficialEstimatePublishError):
    """The immutable version exists, but official current did not switch."""

    def __init__(self, *, version_path: Path, current_path: Path, cause: Exception) -> None:
        super().__init__(
            "version_published_current_not_switched",
            "完整正式版本已建立，但尚未設為目前版本；版本已保留，請進行後續診斷。",
            evidence_path=version_path,
            cause=cause,
        )
        self.version_published = True
        self.current_switched = False
        self.version_path = version_path
        self.current_path = current_path


class OfficialEstimateCurrentSwitchedAuditIncompleteError(OfficialEstimatePublishError):
    """Official current switched, but audit publication was not proven."""

    def __init__(self, *, version_path: Path, current_path: Path,
                 current: dict[str, Any], audit_path: Path,
                 pending_audit_path: Path, cause: Exception) -> None:
        super().__init__(
            "current_switched_audit_incomplete",
            "正式 current 已切換，但 audit 尚未完整確認；不得 rollback，需後續復原。",
            evidence_path=pending_audit_path,
            cause=cause,
        )
        self.version_published = True
        self.current_switched = True
        self.version_path = version_path
        self.current_path = current_path
        self.current = current
        self.audit_path = audit_path
        self.pending_audit_path = pending_audit_path


class InjectedOfficialEstimatePublishFault(RuntimeError):
    """Test-only fault raised by :func:`fault_at`."""


@dataclass(frozen=True)
class OfficialCurrentState:
    revision: int
    current_version_id: str | None
    current: dict[str, Any] | None


@dataclass(frozen=True)
class OfficialEstimatePublishResult:
    status: str
    estimate_version_id: str
    batch_id: str
    annual_data_version_id: str
    before_revision: int
    before_current_version_id: str | None
    after_revision: int
    after_current_version_id: str
    version_path: Path
    current_path: Path
    audit_path: Path
    committed_at: str
    current: dict[str, Any]
    audit_event: dict[str, Any]


def fault_at(target_stage: str) -> FaultInjector:
    def inject(stage: str, _path: Path) -> None:
        if stage == target_stage:
            raise InjectedOfficialEstimatePublishFault(stage)
    return inject


def _checkpoint(injector: FaultInjector | None, stage: str, path: Path) -> None:
    if injector is not None:
        injector(stage, path)


def _utc(value: dt.datetime, label: str) -> dt.datetime:
    if not isinstance(value, dt.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OfficialEstimatePublishError(
            "invalid_publish_time", f"{label}必須是包含時區的 datetime。"
        )
    return value.astimezone(dt.timezone.utc)


def _utc_text(value: dt.datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _uuid_text(value: uuid.UUID | str | None, label: str) -> str:
    try:
        unique = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)) if value else uuid.uuid4()
    except (ValueError, AttributeError) as exc:
        raise OfficialEstimatePublishError("invalid_uuid", f"{label} UUID 格式無效。") from exc
    return str(unique)


def _required_text(value: object, code: str, message: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise OfficialEstimatePublishError(code, message)
    return text


def _assert_contained(base: Path, candidate: Path, label: str) -> None:
    try:
        resolved_base = base.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        common = os.path.commonpath((str(resolved_base), str(resolved_candidate)))
        if os.path.normcase(common) != os.path.normcase(str(resolved_base)):
            raise ValueError
    except (OSError, ValueError) as exc:
        raise OfficialEstimatePublishError(
            "unsafe_path", f"{label}路徑跳脫允許範圍，已拒絕。"
        ) from exc


def _validate_root(root: Path) -> None:
    if not root.exists() or not root.is_dir():
        raise OfficialEstimatePublishError("filesystem_failure", "指定共享根目錄不存在或不是資料夾。")
    try:
        validate_system(deserialize_json((root / "system.json").read_bytes()))
    except (OSError, StorageValidationError) as exc:
        raise OfficialEstimatePublishError(
            "filesystem_failure", f"system.json 不存在或無法通過驗證：{exc}"
        ) from exc


def _validate_observed_state(revision: object, version_id: object) -> tuple[int, str | None]:
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise OfficialEstimatePublishError(
            "candidate_invalid", "observed_revision 必須是大於等於 0 的整數。"
        )
    try:
        safe_id = None if version_id is None else validate_safe_id(
            version_id, "observed_current_version_id"
        )
    except StorageValidationError as exc:
        raise OfficialEstimatePublishError("candidate_invalid", str(exc)) from exc
    if (revision == 0) != (safe_id is None):
        raise OfficialEstimatePublishError(
            "candidate_invalid",
            "observed revision=0 必須搭配 current_version_id=null；既有 current 則兩者皆須提供。",
        )
    return revision, safe_id


def _read_current(path: Path) -> OfficialCurrentState:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return OfficialCurrentState(0, None, None)
    except OSError as exc:
        raise OfficialEstimatePublishError(
            "filesystem_failure", f"無法讀取 official-estimates/current.json：{exc}",
            evidence_path=path
        ) from exc
    try:
        current = validate_official_current(deserialize_json(raw))
    except StorageValidationError as exc:
        raise OfficialEstimatePublishError(
            "current_version_invalid", f"official-estimates/current.json 無法通過驗證：{exc}",
            evidence_path=path
        ) from exc
    return OfficialCurrentState(current["revision"], current["current_version_id"], current)


def observe_official_current(root: str | os.PathLike[str]) -> OfficialCurrentState:
    shared_root = Path(root)
    _validate_root(shared_root)
    return _read_current(shared_root / "official-estimates" / "current.json")


def _read_directory_bundle(directory: Path, versions_root: Path) -> dict[str, bytes]:
    _assert_contained(versions_root, directory, "正式版本")
    if not directory.is_dir() or directory.is_symlink():
        raise FileNotFoundError(directory)
    bundle: dict[str, bytes] = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise OSError(f"版本內含 symbolic link：{path}")
        if path.is_file():
            _assert_contained(directory, path, "正式版本檔案")
            bundle[path.relative_to(directory).as_posix()] = path.read_bytes()
    return bundle


def _read_annual_bundle(root: Path, version_id: str) -> tuple[dict, dict[str, bytes]]:
    versions_root = root / "annual-data" / "versions"
    path = versions_root / version_id
    try:
        bundle = _read_directory_bundle(path, versions_root)
        validated = validate_annual_bundle(bundle)
    except (OSError, StorageValidationError) as exc:
        raise OfficialEstimatePublishError(
            "annual_version_invalid",
            "candidate 引用的 annual-data version 不存在、不完整或已損壞。",
            evidence_path=path,
        ) from exc
    if validated["version"]["version_id"] != version_id:
        raise OfficialEstimatePublishError(
            "annual_version_invalid", "annual-data version 目錄名稱與 version.json 不一致。",
            evidence_path=path,
        )
    return validated, bundle


def _read_official_bundle(root: Path, version_id: str) -> tuple[dict, dict[str, bytes]]:
    versions_root = root / "official-estimates" / "versions"
    path = versions_root / version_id
    try:
        bundle = _read_directory_bundle(path, versions_root)
        validated = validate_official_bundle(bundle)
    except (OSError, StorageValidationError) as exc:
        raise OfficialEstimatePublishError(
            "current_version_invalid",
            "official current 指向的正式版本不存在、不完整或已損壞。",
            evidence_path=path,
        ) from exc
    if validated["manifest"]["version_id"] != version_id:
        raise OfficialEstimatePublishError(
            "current_version_invalid", "official current version 目錄名稱與 manifest.json 不一致。",
            evidence_path=path,
        )
    return validated, bundle


def _validated_candidate(candidate: OfficialEstimateCandidate) -> tuple[dict, dict[str, bytes]]:
    if not isinstance(candidate, OfficialEstimateCandidate):
        raise OfficialEstimatePublishError(
            "candidate_invalid", "publisher 只接受 2-5B OfficialEstimateCandidate。"
        )
    try:
        files = dict(candidate.files)
        validated = validate_official_bundle(files)
    except (TypeError, ValueError, StorageValidationError) as exc:
        raise OfficialEstimatePublishError(
            "candidate_invalid", f"正式推估 candidate 驗證失敗：{exc}"
        ) from exc
    if candidate.version_id != validated["manifest"]["version_id"]:
        raise OfficialEstimatePublishError(
            "candidate_invalid", "candidate.version_id 與 manifest.json 不一致。"
        )
    return validated, files


def _write_verified_file(path: Path, data: bytes, *, fault_injector: FaultInjector | None,
                         stage_name: str) -> None:
    _checkpoint(fault_injector, f"before_write:{stage_name}", path)
    try:
        with path.open("xb") as stream:
            stream.write(data)
            _checkpoint(fault_injector, f"after_bytes:{stage_name}", path)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise OfficialEstimatePublishError(
            "filesystem_failure", f"檔案已存在，拒絕覆寫：{path.name}", evidence_path=path
        ) from exc
    except OSError as exc:
        raise OfficialEstimatePublishError(
            "filesystem_failure", f"寫入檔案失敗：{path.name}：{exc}", evidence_path=path
        ) from exc
    _checkpoint(fault_injector, f"after_write:{stage_name}", path)
    try:
        reread = path.read_bytes()
    except OSError as exc:
        raise OfficialEstimatePublishError(
            "filesystem_failure", f"寫入後無法重讀：{path.name}", evidence_path=path
        ) from exc
    if reread != data or sha256_bytes(reread) != sha256_bytes(data):
        raise OfficialEstimatePublishError(
            "staging_validation_failed", f"寫入後 bytes/checksum 不符：{path.name}",
            evidence_path=path
        )
    _checkpoint(fault_injector, f"after_readback:{stage_name}", path)


def _read_staging_bundle(directory: Path) -> dict[str, bytes]:
    bundle: dict[str, bytes] = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise OSError(f"staging 內含 symbolic link：{path}")
        if path.is_file():
            _assert_contained(directory, path, "staging 檔案")
            bundle[path.relative_to(directory).as_posix()] = path.read_bytes()
    return bundle


def _prepare_json_temp(*, path: Path, value: dict[str, Any],
                       validator: Callable[[object], dict], kind: str,
                       fault_injector: FaultInjector | None) -> bytes:
    data = serialize_json(value)
    _write_verified_file(path, data, fault_injector=fault_injector,
                         stage_name=f"{kind}_temp")
    _checkpoint(fault_injector, f"before_validate:{kind}_temp", path)
    try:
        reread = path.read_bytes()
        parsed = validator(deserialize_json(reread))
    except (OSError, StorageValidationError) as exc:
        raise OfficialEstimatePublishError(
            "filesystem_failure", f"{kind} temp 重讀驗證失敗：{exc}", evidence_path=path
        ) from exc
    if reread != data or parsed != value:
        raise OfficialEstimatePublishError(
            "filesystem_failure", f"{kind} temp 內容與預期不一致。", evidence_path=path
        )
    _checkpoint(fault_injector, f"after_validate:{kind}_temp", path)
    return data


def _publish_audit_no_replace(source: Path, destination: Path) -> None:
    if os.path.lexists(destination):
        raise OfficialEstimatePublishError(
            "filesystem_failure", "audit event 已存在，絕對不會覆寫。", evidence_path=destination
        )
    try:
        if sys.platform == "win32":
            os.rename(source, destination)
        else:
            os.link(source, destination)
            source.unlink()
    except FileExistsError as exc:
        raise OfficialEstimatePublishError(
            "filesystem_failure", "audit event 已存在，絕對不會覆寫。", evidence_path=destination
        ) from exc
    except OSError as exc:
        raise OfficialEstimatePublishError(
            "filesystem_failure", f"audit event 發布失敗：{exc}", evidence_path=source
        ) from exc


def _map_lock_error(exc: AnnualDataActivationError) -> OfficialEstimatePublishError:
    code = "lock_timeout" if exc.code == "lock_timeout" else "filesystem_failure"
    message = (
        "等待 official current 排他鎖逾時；未切換 current，也不會自動 retry。"
        if code == "lock_timeout" else f"official current 排他鎖失敗：{exc}"
    )
    return OfficialEstimatePublishError(code, message, evidence_path=exc.evidence_path)


def publish_official_estimate_candidate(
    *,
    root: str | os.PathLike[str],
    candidate: OfficialEstimateCandidate,
    observed_revision: int,
    observed_current_version_id: str | None,
    lock_factory: LockFactory | None = None,
    clock: Clock = lambda: dt.datetime.now(dt.timezone.utc),
    staging_uuid: uuid.UUID | str | None = None,
    event_uuid: uuid.UUID | str | None = None,
    current_temp_uuid: uuid.UUID | str | None = None,
    audit_temp_uuid: uuid.UUID | str | None = None,
    hostname: str | None = None,
    process_id: int | None = None,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    random_uniform: Callable[[float, float], float] | None = None,
    fault_injector: FaultInjector | None = None,
) -> OfficialEstimatePublishResult:
    """Publish one 2-5B candidate without rebuilding its inputs or results."""
    observed = _validate_observed_state(observed_revision, observed_current_version_id)
    validated_candidate, candidate_files = _validated_candidate(candidate)
    manifest = validated_candidate["manifest"]
    version_id = manifest["version_id"]
    if manifest["previous_official_version_id"] != observed[1]:
        raise OfficialEstimateConflictError(
            "previous_version_conflict",
            "正式版本已變更，必須重新確認正式保存預覽。",
        )

    shared_root = Path(root)
    _validate_root(shared_root)
    _, initial_annual_bytes = _read_annual_bundle(
        shared_root, manifest["annual_data_version_id"]
    )

    official_root = shared_root / "official-estimates"
    versions_root = official_root / "versions"
    current_path = official_root / "current.json"
    final_path = versions_root / version_id
    staging_root = shared_root / "staging"
    lock_path = shared_root / LOCK_RELATIVE_PATH
    try:
        staging_root.mkdir(parents=True, exist_ok=True)
        versions_root.mkdir(parents=True, exist_ok=True)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OfficialEstimatePublishError(
            "filesystem_failure", f"無法準備正式發布目錄：{exc}", evidence_path=shared_root
        ) from exc
    if os.path.lexists(final_path):
        raise OfficialEstimatePublishError(
            "version_id_exists", "正式推估 version ID 已存在，絕對不會覆寫。",
            evidence_path=final_path
        )

    stage_id = _uuid_text(staging_uuid, "staging")
    event_id = _uuid_text(event_uuid, "event")
    current_temp_id = _uuid_text(current_temp_uuid, "current temp")
    audit_temp_id = _uuid_text(audit_temp_uuid, "audit temp")
    staging_path = staging_root / f"official-estimate-{version_id}-{stage_id}"
    if os.path.lexists(staging_path):
        raise OfficialEstimatePublishError(
            "filesystem_failure", "唯一 staging 目錄已存在，拒絕共用或覆寫。",
            evidence_path=staging_path
        )
    try:
        staging_path.mkdir()
    except OSError as exc:
        raise OfficialEstimatePublishError(
            "filesystem_failure", f"無法建立唯一 staging：{exc}", evidence_path=staging_path
        ) from exc

    _checkpoint(fault_injector, "staging_created", staging_path)
    for filename in _CANDIDATE_PAYLOAD_FILES:
        _write_verified_file(
            staging_path / filename,
            candidate_files[filename],
            fault_injector=fault_injector,
            stage_name=filename,
        )
    _checkpoint(fault_injector, "before_committed", staging_path)
    committed_time = _utc(clock(), "正式 COMMITTED 建立時間")
    committed = {
        "schema": COMMITTED_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "version_id": version_id,
        "committed_at": _utc_text(committed_time),
        "manifest_file": "manifest.json",
        "manifest_sha256": sha256_bytes(candidate_files["manifest.json"]),
    }
    committed_bytes = serialize_json(committed)
    _write_verified_file(
        staging_path / "COMMITTED.json",
        committed_bytes,
        fault_injector=fault_injector,
        stage_name="COMMITTED.json",
    )
    _checkpoint(fault_injector, "after_committed", staging_path)
    expected_published_files = {
        filename: candidate_files[filename] for filename in _CANDIDATE_PAYLOAD_FILES
    }
    expected_published_files["COMMITTED.json"] = committed_bytes

    _checkpoint(fault_injector, "before_staging_validation", staging_path)
    try:
        staging_files = _read_staging_bundle(staging_path)
        validate_official_bundle(staging_files)
    except (OSError, StorageValidationError, OfficialEstimatePublishError) as exc:
        raise OfficialEstimatePublishError(
            "staging_validation_failed",
            f"staging 完整重讀驗證失敗，未發布正式版本：{exc}",
            evidence_path=staging_path,
        ) from exc
    if staging_files != expected_published_files:
        raise OfficialEstimatePublishError(
            "staging_validation_failed", "staging 完整重讀 bytes 與已確認 candidate 不一致。",
            evidence_path=staging_path
        )
    _checkpoint(fault_injector, "after_staging_validation", staging_path)

    if lock_factory is None:
        lock_kwargs: dict[str, Any] = {
            "timeout_seconds": lock_timeout_seconds,
            "monotonic": monotonic,
            "sleep": sleep,
        }
        if random_uniform is not None:
            lock_kwargs["random_uniform"] = random_uniform
        lock_context: AbstractContextManager[None] = WindowsSMBExclusiveLock(
            lock_path, **lock_kwargs
        )
    else:
        lock_context = lock_factory(lock_path)

    version_published = False
    current_switched = False
    new_current: dict[str, Any] = {}
    audit_path = shared_root / "audit" / "events" / "unknown"
    audit_temp = audit_path
    try:
        try:
            with lock_context:
                _checkpoint(fault_injector, "critical_section_entered", lock_path)
                locked_state = _read_current(current_path)
                if (locked_state.revision, locked_state.current_version_id) != observed:
                    raise OfficialEstimateConflictError(
                        "revision_conflict",
                        "正式 current 已變更；必須重新載入並重新確認正式保存預覽。",
                        evidence_path=current_path,
                    )
                if locked_state.current_version_id is not None:
                    _read_official_bundle(shared_root, locked_state.current_version_id)
                _, locked_annual_bytes = _read_annual_bundle(
                    shared_root, manifest["annual_data_version_id"]
                )
                if locked_annual_bytes != initial_annual_bytes:
                    raise OfficialEstimatePublishError(
                        "annual_version_invalid",
                        "candidate 引用的 annual-data version 在發布期間發生變動。",
                        evidence_path=(shared_root / "annual-data" / "versions"
                                       / manifest["annual_data_version_id"]),
                    )
                if os.path.lexists(final_path):
                    raise OfficialEstimatePublishError(
                        "version_id_exists", "正式推估 version ID 已存在，絕對不會覆寫。",
                        evidence_path=final_path,
                    )

                _checkpoint(fault_injector, "before_version_rename", staging_path)
                try:
                    staging_path.rename(final_path)
                except FileExistsError as exc:
                    raise OfficialEstimatePublishError(
                        "version_id_exists", "正式推估 version ID 已存在，絕對不會覆寫。",
                        evidence_path=final_path,
                    ) from exc
                except OSError as exc:
                    raise OfficialEstimatePublishError(
                        "filesystem_failure", f"正式版本 rename 失敗：{exc}",
                        evidence_path=staging_path
                    ) from exc
                version_published = True
                _checkpoint(fault_injector, "after_version_rename", final_path)
                _, published_files = _read_official_bundle(shared_root, version_id)
                if published_files != expected_published_files:
                    raise OfficialEstimatePublishError(
                        "filesystem_failure", "rename 後正式版本 bytes 與 staging 結果不一致。",
                        evidence_path=final_path,
                    )
                _checkpoint(fault_injector, "after_published_validation", final_path)

                switch_time = _utc(clock(), "current 切換時間")
                occurred_text = _utc_text(switch_time)
                after_revision = locked_state.revision + 1
                new_current = {
                    "schema": OFFICIAL_CURRENT_SCHEMA,
                    "schema_version": SCHEMA_VERSION,
                    "revision": after_revision,
                    "current_version_id": version_id,
                    "previous_version_id": locked_state.current_version_id,
                    "updated_at": occurred_text,
                    "operator_display_name": manifest["operator_display_name"],
                }
                validate_official_current(new_current)
                diagnostic_hostname = _required_text(
                    hostname if hostname is not None else socket.gethostname(),
                    "filesystem_failure",
                    "hostname 不可為空白。",
                )
                diagnostic_pid = os.getpid() if process_id is None else process_id
                if (isinstance(diagnostic_pid, bool)
                        or not isinstance(diagnostic_pid, int) or diagnostic_pid < 1):
                    raise OfficialEstimatePublishError(
                        "filesystem_failure", "process_id 必須是正整數。"
                    )
                audit_event = {
                    "schema": AUDIT_EVENT_SCHEMA,
                    "schema_version": SCHEMA_VERSION,
                    "event_id": event_id,
                    "event_type": OFFICIAL_ESTIMATE_PUBLISH_EVENT_TYPE,
                    "occurred_at": occurred_text,
                    "estimate_version_id": version_id,
                    "batch_id": manifest["batch_id"],
                    "annual_data_version_id": manifest["annual_data_version_id"],
                    "before_revision": locked_state.revision,
                    "before_current_version_id": locked_state.current_version_id,
                    "after_revision": after_revision,
                    "after_current_version_id": version_id,
                    "previous_official_version_id": manifest["previous_official_version_id"],
                    "operator_display_name": manifest["operator_display_name"],
                    "note": manifest["note"],
                    "software": manifest["software"],
                    "result": "success",
                    "diagnostics": {
                        "hostname": diagnostic_hostname,
                        "process_id": diagnostic_pid,
                        "manifest_sha256": sha256_bytes(candidate_files["manifest.json"]),
                    },
                }
                validate_official_estimate_publish_audit_event(audit_event)
                event_directory = (shared_root / "audit" / "events"
                                   / f"{switch_time:%Y}" / f"{switch_time:%m}")
                try:
                    event_directory.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise OfficialEstimatePublishError(
                        "filesystem_failure", f"無法準備 audit 目錄：{exc}",
                        evidence_path=event_directory
                    ) from exc
                filename_time = switch_time.strftime("%Y%m%dT%H%M%S%fZ")
                audit_path = event_directory / f"{filename_time}_{event_id}.json"
                if os.path.lexists(audit_path):
                    raise OfficialEstimatePublishError(
                        "filesystem_failure", "audit event 已存在，絕對不會覆寫。",
                        evidence_path=audit_path
                    )
                audit_temp = event_directory / f".{audit_path.name}.{audit_temp_id}.tmp"
                _prepare_json_temp(
                    path=audit_temp,
                    value=audit_event,
                    validator=validate_official_estimate_publish_audit_event,
                    kind="audit",
                    fault_injector=fault_injector,
                )

                current_temp = official_root / f".current.json.{current_temp_id}.tmp"
                current_bytes = _prepare_json_temp(
                    path=current_temp,
                    value=new_current,
                    validator=validate_official_current,
                    kind="current",
                    fault_injector=fault_injector,
                )
                _checkpoint(fault_injector, "before_current_replace", current_temp)
                try:
                    os.replace(current_temp, current_path)
                except OSError as exc:
                    raise OfficialEstimatePublishError(
                        "filesystem_failure", f"official current 原子取代失敗：{exc}",
                        evidence_path=current_temp
                    ) from exc
                current_switched = True
                _checkpoint(fault_injector, "after_current_replace", current_path)
                try:
                    current_reread = current_path.read_bytes()
                    current_validated = validate_official_current(
                        deserialize_json(current_reread)
                    )
                except (OSError, StorageValidationError) as exc:
                    raise OfficialEstimatePublishError(
                        "filesystem_failure", f"current replace 後重讀驗證失敗：{exc}",
                        evidence_path=current_path
                    ) from exc
                if current_reread != current_bytes or current_validated != new_current:
                    raise OfficialEstimatePublishError(
                        "filesystem_failure", "current replace 後內容與預期不一致。",
                        evidence_path=current_path
                    )
                _checkpoint(fault_injector, "after_current_revalidation", current_path)

                _checkpoint(fault_injector, "before_audit_publish", audit_temp)
                _publish_audit_no_replace(audit_temp, audit_path)
                _checkpoint(fault_injector, "after_audit_publish", audit_path)
                try:
                    audit_reread = audit_path.read_bytes()
                    audit_validated = validate_official_estimate_publish_audit_event(
                        deserialize_json(audit_reread)
                    )
                except (OSError, StorageValidationError) as exc:
                    raise OfficialEstimatePublishError(
                        "filesystem_failure", f"audit 發布後重讀驗證失敗：{exc}",
                        evidence_path=audit_path
                    ) from exc
                audit_bytes = serialize_json(audit_event)
                if audit_reread != audit_bytes or audit_validated != audit_event:
                    raise OfficialEstimatePublishError(
                        "filesystem_failure", "audit 發布後 bytes 與預期不一致。",
                        evidence_path=audit_path
                    )
                _checkpoint(fault_injector, "after_audit_revalidation", audit_path)
                return OfficialEstimatePublishResult(
                    status="success",
                    estimate_version_id=version_id,
                    batch_id=manifest["batch_id"],
                    annual_data_version_id=manifest["annual_data_version_id"],
                    before_revision=locked_state.revision,
                    before_current_version_id=locked_state.current_version_id,
                    after_revision=after_revision,
                    after_current_version_id=version_id,
                    version_path=final_path,
                    current_path=current_path,
                    audit_path=audit_path,
                    committed_at=committed["committed_at"],
                    current=new_current,
                    audit_event=audit_event,
                )
        except AnnualDataActivationError as exc:
            raise _map_lock_error(exc) from exc
    except Exception as exc:
        if current_switched:
            if isinstance(exc, OfficialEstimateCurrentSwitchedAuditIncompleteError):
                raise
            raise OfficialEstimateCurrentSwitchedAuditIncompleteError(
                version_path=final_path,
                current_path=current_path,
                current=new_current,
                audit_path=audit_path,
                pending_audit_path=audit_temp,
                cause=exc,
            ) from exc
        if version_published:
            if isinstance(exc, OfficialEstimateVersionPublishedCurrentNotSwitchedError):
                raise
            raise OfficialEstimateVersionPublishedCurrentNotSwitchedError(
                version_path=final_path, current_path=current_path, cause=exc
            ) from exc
        if isinstance(exc, OSError):
            raise OfficialEstimatePublishError(
                "filesystem_failure", f"正式發布檔案系統操作失敗：{exc}"
            ) from exc
        raise
