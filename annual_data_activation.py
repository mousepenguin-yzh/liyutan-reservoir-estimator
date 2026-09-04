"""Safely activate an existing immutable annual-data version.

This module implements the stage 2-4C2a backend primitive only.  It does not
publish annual bundles, expose Streamlit UI, or enable formal write flags.
"""

from __future__ import annotations

import datetime as dt
import os
import random
import socket
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager, Mapping

from shared_storage_schema import (
    ANNUAL_ACTIVATION_EVENT_TYPE,
    ANNUAL_CURRENT_SCHEMA,
    AUDIT_EVENT_SCHEMA,
    SCHEMA_VERSION,
    StorageValidationError,
    deserialize_json,
    serialize_json,
    validate_annual_activation_audit_event,
    validate_annual_bundle,
    validate_annual_current,
    validate_safe_id,
    validate_software_metadata,
    validate_system,
)


LOCK_RELATIVE_PATH = Path("locks") / "annual-current.lock"
DEFAULT_LOCK_TIMEOUT_SECONDS = 15.0
DEFAULT_RETRY_MIN_SECONDS = 0.2
DEFAULT_RETRY_MAX_SECONDS = 0.5

FaultInjector = Callable[[str, Path], None]
LockFactory = Callable[[Path], ContextManager[None]]


class AnnualDataActivationError(RuntimeError):
    """A safe, expected activation refusal or filesystem failure."""

    def __init__(self, code: str, message: str, *, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.evidence_path = evidence_path


class AnnualDataActivationConflictError(AnnualDataActivationError):
    """The current pointer no longer matches the caller's observed state."""


class AnnualDataAlreadyCurrentError(AnnualDataActivationError):
    """The requested immutable version is already current."""


class AnnualDataActivationRecoveryRequiredError(AnnualDataActivationError):
    """Current switched, but audit completion could not be proven."""

    def __init__(
        self,
        message: str,
        *,
        current: dict,
        audit_path: Path,
        cause: Exception,
    ) -> None:
        super().__init__(
            "current_switched_audit_incomplete",
            message,
            evidence_path=audit_path,
        )
        self.current_switched = True
        self.current = current
        self.audit_path = audit_path
        self.__cause__ = cause


class InjectedAnnualDataActivationFault(RuntimeError):
    """Test-only fault raised by :func:`fault_at`."""


@dataclass(frozen=True)
class AnnualDataActivationResult:
    status: str
    target_version_id: str
    before_revision: int
    before_current_version_id: str | None
    after_revision: int
    after_current_version_id: str
    current_path: Path
    audit_path: Path
    current: dict
    audit_event: dict


def fault_at(target_stage: str) -> FaultInjector:
    """Return a deterministic test-only fault injector for one checkpoint."""

    def inject(stage: str, _path: Path) -> None:
        if stage == target_stage:
            raise InjectedAnnualDataActivationFault(stage)

    return inject


def _checkpoint(injector: FaultInjector | None, stage: str, path: Path) -> None:
    if injector is not None:
        injector(stage, path)


class WindowsSMBExclusiveLock:
    """Hold a Windows/SMB file handle opened with no sharing allowed."""

    _BUSY_ERROR_CODES = frozenset({32, 33})  # sharing violation, lock violation

    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
        retry_min_seconds: float = DEFAULT_RETRY_MIN_SECONDS,
        retry_max_seconds: float = DEFAULT_RETRY_MAX_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds 不可為負值")
        if retry_min_seconds <= 0 or retry_max_seconds < retry_min_seconds:
            raise ValueError("lock retry 範圍無效")
        self.path = Path(path)
        self.timeout_seconds = float(timeout_seconds)
        self.retry_min_seconds = float(retry_min_seconds)
        self.retry_max_seconds = float(retry_max_seconds)
        self._monotonic = monotonic
        self._sleep = sleep
        self._random_uniform = random_uniform
        self._handle: int | None = None
        self._close_handle = None

    def __enter__(self) -> None:
        if sys.platform != "win32":
            raise AnnualDataActivationError(
                "lock_unsupported_platform",
                "正式 annual current lock 僅支援 Windows；測試必須注入 fake lock。",
                evidence_path=self.path,
            )
        deadline = self._monotonic() + self.timeout_seconds
        while True:
            handle, error_code, close_handle = self._try_open()
            if handle is not None:
                self._handle = handle
                self._close_handle = close_handle
                return None
            if error_code not in self._BUSY_ERROR_CODES:
                raise AnnualDataActivationError(
                    "lock_acquire_failed",
                    f"無法取得年度 current 排他鎖（Windows error {error_code}）。",
                    evidence_path=self.path,
                )
            now = self._monotonic()
            if now >= deadline:
                raise AnnualDataActivationError(
                    "lock_timeout",
                    "等待年度 current 排他鎖逾時，未切換 current，也未改存本機。",
                    evidence_path=self.path,
                )
            delay = self._random_uniform(self.retry_min_seconds, self.retry_max_seconds)
            self._sleep(min(delay, max(0.0, deadline - now)))

    def _try_open(self):
        # Imports and Windows DLL access remain behind the platform guard so
        # GitHub Actions and other non-Windows environments can import safely.
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        generic_read = 0x80000000
        generic_write = 0x40000000
        open_always = 4
        file_attribute_normal = 0x00000080
        handle = create_file(
            str(self.path),
            generic_read | generic_write,
            0,  # no FILE_SHARE_* flags: the open handle is the exclusive lock
            None,
            open_always,
            file_attribute_normal,
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle == invalid_handle:
            return None, ctypes.get_last_error(), close_handle
        return handle, 0, close_handle

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self._handle is not None and self._close_handle is not None:
            closed = bool(self._close_handle(self._handle))
            self._handle = None
            if not closed and exc is None:
                raise AnnualDataActivationError(
                    "lock_release_failed",
                    "年度 current 排他鎖 handle 無法正常關閉。",
                    evidence_path=self.path,
                )
        return False


def _utc(value: dt.datetime | None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise AnnualDataActivationError("invalid_occurred_at", "啟用時間必須包含時區。")
    return current.astimezone(dt.timezone.utc)


def _utc_text(value: dt.datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _uuid_text(value: uuid.UUID | str | None, label: str) -> str:
    try:
        unique = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)) if value else uuid.uuid4()
    except (ValueError, AttributeError) as exc:
        raise AnnualDataActivationError("invalid_uuid", f"{label} UUID 格式無效。") from exc
    return str(unique)


def _required_text(value: object, code: str, message: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise AnnualDataActivationError(code, message)
    return text


def _validate_observed_state(revision: object, version_id: object) -> tuple[int, str | None]:
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise AnnualDataActivationError(
            "invalid_observed_state", "observed_revision 必須是大於等於 0 的整數。"
        )
    try:
        safe_id = None if version_id is None else validate_safe_id(version_id, "observed_current_version_id")
    except StorageValidationError as exc:
        raise AnnualDataActivationError("invalid_observed_state", str(exc)) from exc
    if (revision == 0) != (safe_id is None):
        raise AnnualDataActivationError(
            "invalid_observed_state",
            "observed revision=0 必須搭配 current_version_id=null；既有 current 則兩者皆必須提供。",
        )
    return revision, safe_id


def _validate_root_and_system(root: Path) -> None:
    if not root.exists():
        raise AnnualDataActivationError("root_not_found", "指定共享根目錄不存在。")
    if not root.is_dir():
        raise AnnualDataActivationError("root_not_directory", "指定共享根目錄不是資料夾。")
    system_path = root / "system.json"
    try:
        validate_system(deserialize_json(system_path.read_bytes()))
    except FileNotFoundError as exc:
        raise AnnualDataActivationError("system_missing", "system.json 不存在，不會自動建立。") from exc
    except (OSError, StorageValidationError) as exc:
        raise AnnualDataActivationError("system_invalid", f"system.json 無法通過驗證：{exc}") from exc


def _assert_contained(base: Path, candidate: Path) -> None:
    try:
        resolved_base = base.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        common = os.path.commonpath((str(resolved_base), str(resolved_candidate)))
        if os.path.normcase(common) != os.path.normcase(str(resolved_base)):
            raise ValueError
    except (OSError, ValueError) as exc:
        raise AnnualDataActivationError(
            "unsafe_target_path", "target version 路徑跳脫 annual-data/versions，已拒絕啟用。"
        ) from exc


def _read_target_bundle(root: Path, version_id: str) -> tuple[dict, dict[str, bytes]]:
    versions_root = root / "annual-data" / "versions"
    target = versions_root / version_id
    _assert_contained(versions_root, target)
    try:
        if not target.is_dir():
            raise AnnualDataActivationError(
                "target_not_found",
                f"年度 target version 不存在：{version_id}",
                evidence_path=target,
            )
        if target.is_symlink():
            raise AnnualDataActivationError(
                "unsafe_target_path", "target version 不可為 symbolic link。", evidence_path=target
            )
        bundle: dict[str, bytes] = {}
        for path in target.rglob("*"):
            if path.is_symlink():
                raise AnnualDataActivationError(
                    "unsafe_target_path", "target version 內不可包含 symbolic link。", evidence_path=path
                )
            if path.is_file():
                _assert_contained(target, path)
                bundle[path.relative_to(target).as_posix()] = path.read_bytes()
        validated = validate_annual_bundle(bundle)
    except AnnualDataActivationError:
        raise
    except (OSError, StorageValidationError) as exc:
        raise AnnualDataActivationError(
            "target_invalid",
            f"年度 target version 不完整或驗證失敗：{exc}",
            evidence_path=target,
        ) from exc
    if validated["version"]["version_id"] != version_id:
        raise AnnualDataActivationError(
            "target_version_id_mismatch",
            "target 目錄名稱與 version.json version_id 不一致。",
            evidence_path=target,
        )
    return validated, bundle


def _read_current(path: Path) -> tuple[int, str | None, dict | None]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return 0, None, None
    except OSError as exc:
        raise AnnualDataActivationError(
            "current_read_failed", f"無法讀取 annual-data/current.json：{exc}", evidence_path=path
        ) from exc
    try:
        current = validate_annual_current(deserialize_json(raw))
    except StorageValidationError as exc:
        raise AnnualDataActivationError(
            "current_invalid",
            f"既有 annual-data/current.json 無法通過驗證：{exc}",
            evidence_path=path,
        ) from exc
    return current["revision"], current["current_version_id"], current


def _prepare_json_temp(
    *,
    path: Path,
    value: dict,
    validator: Callable[[object], dict],
    kind: str,
    fault_injector: FaultInjector | None,
) -> tuple[Path, bytes]:
    data = serialize_json(value)
    _checkpoint(fault_injector, f"before_{kind}_temp_write", path)
    try:
        with path.open("xb") as stream:
            stream.write(data)
            _checkpoint(fault_injector, f"after_{kind}_temp_bytes", path)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise AnnualDataActivationError(
            f"{kind}_temp_exists", f"唯一 {kind} temp file 已存在，拒絕覆寫。", evidence_path=path
        ) from exc
    except OSError as exc:
        raise AnnualDataActivationError(
            f"{kind}_temp_write_failed", f"寫入 {kind} temp file 失敗：{exc}", evidence_path=path
        ) from exc
    _checkpoint(fault_injector, f"after_{kind}_temp_write", path)
    _checkpoint(fault_injector, f"before_{kind}_temp_validation", path)
    try:
        reread = path.read_bytes()
        parsed = validator(deserialize_json(reread))
    except (OSError, StorageValidationError) as exc:
        raise AnnualDataActivationError(
            f"{kind}_temp_validation_failed",
            f"{kind} temp file 重讀驗證失敗：{exc}",
            evidence_path=path,
        ) from exc
    if reread != data or parsed != value:
        raise AnnualDataActivationError(
            f"{kind}_temp_mismatch", f"{kind} temp file 內容與預期不一致。", evidence_path=path
        )
    _checkpoint(fault_injector, f"after_{kind}_temp_validation", path)
    return path, data


def _publish_audit_no_replace(source: Path, destination: Path) -> None:
    if os.path.lexists(destination):
        raise AnnualDataActivationError(
            "audit_event_exists", "audit event 已存在，絕對不會覆寫。", evidence_path=destination
        )
    try:
        if sys.platform == "win32":
            # os.rename does not replace an existing destination on Windows and
            # maps to an atomic same-volume rename suitable for the SMB target.
            os.rename(source, destination)
        else:
            # CI-only no-replace publication.  link() atomically creates the
            # destination or fails with EEXIST, then the temp name is removed.
            os.link(source, destination)
            source.unlink()
    except FileExistsError as exc:
        raise AnnualDataActivationError(
            "audit_event_exists", "audit event 已存在，絕對不會覆寫。", evidence_path=destination
        ) from exc
    except OSError as exc:
        raise AnnualDataActivationError(
            "audit_publish_failed", f"audit event 原子發布失敗：{exc}", evidence_path=source
        ) from exc


def activate_annual_data_version(
    *,
    root: str | os.PathLike[str],
    target_version_id: str,
    observed_revision: int,
    observed_current_version_id: str | None,
    operator_display_name: str,
    note: str,
    software: Mapping[str, object],
    lock_factory: LockFactory | None = None,
    occurred_at: dt.datetime | None = None,
    event_uuid: uuid.UUID | str | None = None,
    current_temp_uuid: uuid.UUID | str | None = None,
    audit_temp_uuid: uuid.UUID | str | None = None,
    hostname: str | None = None,
    process_id: int | None = None,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    random_uniform: Callable[[float, float], float] = random.uniform,
    fault_injector: FaultInjector | None = None,
) -> AnnualDataActivationResult:
    """Atomically switch annual current after lock-protected conflict checks."""
    operator = _required_text(
        operator_display_name, "operator_required", "人工填報操作人為必填。"
    )
    activation_note = _required_text(note, "note_required", "年度版本啟用備註為必填。")
    observed = _validate_observed_state(observed_revision, observed_current_version_id)
    try:
        safe_target = validate_safe_id(target_version_id, "target_version_id")
        software_metadata = validate_software_metadata(software)
    except StorageValidationError as exc:
        raise AnnualDataActivationError("invalid_activation_input", str(exc)) from exc
    diagnostic_hostname = _required_text(
        hostname if hostname is not None else socket.gethostname(),
        "invalid_hostname",
        "hostname 不可為空白。",
    )
    diagnostic_pid = os.getpid() if process_id is None else process_id
    if isinstance(diagnostic_pid, bool) or not isinstance(diagnostic_pid, int) or diagnostic_pid < 1:
        raise AnnualDataActivationError("invalid_process_id", "process_id 必須是正整數。")
    timestamp = _utc(occurred_at)
    event_id = _uuid_text(event_uuid, "event")
    current_temp_id = _uuid_text(current_temp_uuid, "current temp")
    audit_temp_id = _uuid_text(audit_temp_uuid, "audit temp")

    shared_root = Path(root)
    _validate_root_and_system(shared_root)
    _, initial_target_bytes = _read_target_bundle(shared_root, safe_target)

    annual_root = shared_root / "annual-data"
    current_path = annual_root / "current.json"
    lock_path = shared_root / LOCK_RELATIVE_PATH
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AnnualDataActivationError(
            "lock_directory_failed", f"無法準備 lock 目錄：{exc}", evidence_path=lock_path.parent
        ) from exc

    if lock_factory is None:
        lock_context: ContextManager[None] = WindowsSMBExclusiveLock(
            lock_path,
            timeout_seconds=lock_timeout_seconds,
            monotonic=monotonic,
            sleep=sleep,
            random_uniform=random_uniform,
        )
    else:
        lock_context = lock_factory(lock_path)

    current_switched = False
    audit_path = shared_root / "audit" / "events" / "unknown"
    new_current: dict = {}
    try:
        with lock_context:
            _checkpoint(fault_injector, "critical_section_entered", lock_path)
            before_revision, before_id, _ = _read_current(current_path)
            if (before_revision, before_id) != observed:
                raise AnnualDataActivationConflictError(
                    "revision_conflict",
                    "另一位使用者已先更新系統基準資料，請重新載入及比較。",
                    evidence_path=current_path,
                )
            _, final_target_bytes = _read_target_bundle(shared_root, safe_target)
            if final_target_bytes != initial_target_bytes:
                raise AnnualDataActivationError(
                    "target_changed",
                    "年度 target version 在啟用前發生變動，已拒絕切換。",
                    evidence_path=shared_root / "annual-data" / "versions" / safe_target,
                )
            if safe_target == before_id:
                raise AnnualDataAlreadyCurrentError(
                    "already_current",
                    "指定年度版本已是 current；未增加 revision，也未重寫 current。",
                    evidence_path=current_path,
                )

            occurred_text = _utc_text(timestamp)
            after_revision = before_revision + 1
            new_current = {
                "schema": ANNUAL_CURRENT_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "revision": after_revision,
                "current_version_id": safe_target,
                "previous_version_id": before_id,
                "updated_at": occurred_text,
                "operator_display_name": operator,
            }
            validate_annual_current(new_current)
            audit_event = {
                "schema": AUDIT_EVENT_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "event_id": event_id,
                "event_type": ANNUAL_ACTIVATION_EVENT_TYPE,
                "occurred_at": occurred_text,
                "annual_target_version_id": safe_target,
                "before_revision": before_revision,
                "before_current_version_id": before_id,
                "after_revision": after_revision,
                "after_current_version_id": safe_target,
                "operator_display_name": operator,
                "note": activation_note,
                "result": "success",
                "software": software_metadata,
                "diagnostics": {
                    "hostname": diagnostic_hostname,
                    "process_id": diagnostic_pid,
                },
            }
            validate_annual_activation_audit_event(audit_event)

            event_directory = shared_root / "audit" / "events" / f"{timestamp:%Y}" / f"{timestamp:%m}"
            try:
                event_directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise AnnualDataActivationError(
                    "audit_directory_failed",
                    f"無法在 current 切換前準備 audit 目錄：{exc}",
                    evidence_path=event_directory,
                ) from exc
            filename_timestamp = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
            audit_path = event_directory / f"{filename_timestamp}_{event_id}.json"
            if os.path.lexists(audit_path):
                raise AnnualDataActivationError(
                    "audit_event_exists",
                    "audit event 已存在；current 尚未切換。",
                    evidence_path=audit_path,
                )
            audit_temp = event_directory / f".{audit_path.name}.{audit_temp_id}.tmp"
            _prepare_json_temp(
                path=audit_temp,
                value=audit_event,
                validator=validate_annual_activation_audit_event,
                kind="audit",
                fault_injector=fault_injector,
            )

            current_temp = annual_root / f".current.json.{current_temp_id}.tmp"
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
                raise AnnualDataActivationError(
                    "current_replace_failed",
                    f"annual-data/current.json 原子取代失敗：{exc}",
                    evidence_path=current_temp,
                ) from exc
            current_switched = True
            _checkpoint(fault_injector, "after_current_replace", current_path)
            try:
                current_reread = current_path.read_bytes()
                current_validated = validate_annual_current(deserialize_json(current_reread))
            except (OSError, StorageValidationError) as exc:
                raise AnnualDataActivationError(
                    "current_post_replace_invalid",
                    f"current replace 後重讀驗證失敗：{exc}",
                    evidence_path=current_path,
                ) from exc
            if current_reread != current_bytes or current_validated != new_current:
                raise AnnualDataActivationError(
                    "current_post_replace_mismatch",
                    "current replace 後內容與預期不一致。",
                    evidence_path=current_path,
                )
            _checkpoint(fault_injector, "after_current_revalidation", current_path)

            _checkpoint(fault_injector, "before_audit_rename", audit_temp)
            _publish_audit_no_replace(audit_temp, audit_path)
            _checkpoint(fault_injector, "after_audit_rename", audit_path)
            try:
                audit_reread = audit_path.read_bytes()
                audit_validated = validate_annual_activation_audit_event(
                    deserialize_json(audit_reread)
                )
            except (OSError, StorageValidationError) as exc:
                raise AnnualDataActivationError(
                    "audit_post_publish_invalid",
                    f"audit event 發布後重讀驗證失敗：{exc}",
                    evidence_path=audit_path,
                ) from exc
            if audit_reread != serialize_json(audit_event) or audit_validated != audit_event:
                raise AnnualDataActivationError(
                    "audit_post_publish_mismatch",
                    "audit event 發布後內容與預期不一致。",
                    evidence_path=audit_path,
                )
            _checkpoint(fault_injector, "after_audit_revalidation", audit_path)
            return AnnualDataActivationResult(
                status="activated",
                target_version_id=safe_target,
                before_revision=before_revision,
                before_current_version_id=before_id,
                after_revision=after_revision,
                after_current_version_id=safe_target,
                current_path=current_path,
                audit_path=audit_path,
                current=new_current,
                audit_event=audit_event,
            )
    except Exception as exc:
        if current_switched:
            if isinstance(exc, AnnualDataActivationRecoveryRequiredError):
                raise
            raise AnnualDataActivationRecoveryRequiredError(
                "current 已切換，但 audit event 尚未完整確認；不得 rollback，需後續復原或補建 audit。",
                current=new_current,
                audit_path=audit_path,
                cause=exc,
            ) from exc
        raise


# Concise compatibility name for callers describing the operation as activation.
activate_existing_annual_version = activate_annual_data_version
