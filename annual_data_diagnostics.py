"""Read-only annual-data recovery diagnostics and filesystem inventory.

The diagnostics deliberately operate independently from ``SharedStorageReader``
so a broken current pointer or current bundle remains inspectable.  This module
never creates directories, acquires locks, renames files, deletes evidence, or
writes repair/audit data.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared_storage_schema import (
    ANNUAL_ACTIVATION_EVENT_TYPE,
    ANNUAL_REQUIRED_FILES,
    StorageValidationError,
    deserialize_json,
    validate_annual_activation_audit_event,
    validate_annual_bundle,
    validate_annual_current,
    validate_safe_id,
    validate_system,
)

if TYPE_CHECKING:
    from shared_storage_reader import SharedStorageResult


_AUDIT_YEAR_RE = re.compile(r"^[0-9]{4}$")
_AUDIT_MONTH_RE = re.compile(r"^(0[1-9]|1[0-2])$")


class CurrentStatus(str, Enum):
    HEALTHY = "healthy"
    MISSING = "missing"
    CURRENT_INVALID = "current_invalid"
    CURRENT_TARGET_MISSING = "current_target_missing"
    CURRENT_TARGET_INVALID = "current_target_invalid"
    UNINSPECTABLE = "uninspectable"


class VersionStatus(str, Enum):
    CURRENT = "current"
    HISTORICAL = "historical"
    ORPHAN = "orphan"
    INVALID = "invalid"


class StagingStatus(str, Enum):
    INCOMPLETE = "incomplete"
    COMPLETE_BUT_UNPUBLISHED = "complete_but_unpublished"
    INVALID = "invalid"
    UNSAFE = "unsafe"


class AuditStatus(str, Enum):
    VALID_ANNUAL_ACTIVATION = "valid_annual_activation"
    INVALID = "invalid"
    UNKNOWN = "unknown_unsupported"


class CurrentAuditStatus(str, Enum):
    MATCHED = "matched"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    NOT_APPLICABLE = "not_applicable"
    UNINSPECTABLE = "uninspectable"


class RecoverySeverity(str, Enum):
    HEALTHY = "healthy"
    ATTENTION = "attention"
    RECOVERY_REQUIRED = "recovery_required"
    UNINSPECTABLE = "uninspectable"


@dataclass(frozen=True)
class AnnualVersionDiagnostic:
    entry_name: str
    path: Path
    status: VersionStatus
    modified_at: str | None
    version_id: str | None = None
    validation_ok: bool = False
    failure_reason: str | None = None


@dataclass(frozen=True)
class AnnualStagingDiagnostic:
    entry_name: str
    path: Path
    status: StagingStatus
    modified_at: str | None
    is_directory: bool
    has_committed: bool
    looks_complete: bool
    validation_ok: bool
    failure_reason: str | None = None


@dataclass(frozen=True)
class AnnualQuarantineDiagnostic:
    entry_name: str
    path: Path
    modified_at: str | None
    is_annual_data_evidence: bool
    validation_ok: bool | None
    failure_reason: str | None = None


@dataclass(frozen=True)
class AnnualAuditDiagnostic:
    path: Path
    status: AuditStatus
    modified_at: str | None
    event: dict[str, Any] | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class TempArtifactDiagnostic:
    path: Path
    artifact_type: str
    modified_at: str | None


@dataclass(frozen=True)
class LockMetadataDiagnostic:
    path: Path
    exists: bool
    modified_at: str | None
    note: str


@dataclass(frozen=True)
class AnnualDataDiagnostics:
    root: Path | None
    inspected_at: str
    system_valid: bool
    system: dict[str, Any] | None
    system_error: str | None
    current_status: CurrentStatus
    current: dict[str, Any] | None
    current_failure_reason: str | None
    current_audit_status: CurrentAuditStatus
    current_audit_match_count: int
    versions: tuple[AnnualVersionDiagnostic, ...]
    staging: tuple[AnnualStagingDiagnostic, ...]
    quarantine: tuple[AnnualQuarantineDiagnostic, ...]
    audits: tuple[AnnualAuditDiagnostic, ...]
    temp_artifacts: tuple[TempArtifactDiagnostic, ...]
    lock_metadata: LockMetadataDiagnostic | None
    overall_severity: RecoverySeverity
    summary: str
    inspection_errors: tuple[str, ...] = ()

    @property
    def current_version_id(self) -> str | None:
        return None if self.current is None else self.current.get("current_version_id")

    @property
    def revision(self) -> int | None:
        return None if self.current is None else self.current.get("revision")

    @property
    def complete_version_count(self) -> int:
        return sum(item.validation_ok for item in self.versions)

    @property
    def is_first_version_state(self) -> bool:
        return self.current_status is CurrentStatus.MISSING and not self.versions

    @property
    def recovery_required(self) -> bool:
        return self.overall_severity is RecoverySeverity.RECOVERY_REQUIRED


class _UnsafeFilesystemEntry(ValueError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _modified_at(path: Path) -> str | None:
    try:
        timestamp = path.lstat().st_mtime
    except OSError:
        return None
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat()


def _is_reparse_or_symlink(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _direct_children(directory: Path) -> tuple[Path, ...]:
    try:
        directory.lstat()
    except FileNotFoundError:
        return ()
    except OSError:
        raise
    if _is_reparse_or_symlink(directory) or not directory.is_dir():
        raise _UnsafeFilesystemEntry(f"{directory} 不是安全的實體資料夾")
    with os.scandir(directory) as entries:
        return tuple(sorted((Path(entry.path) for entry in entries), key=lambda item: item.name))


def _read_bundle_directory(directory: Path) -> dict[str, bytes]:
    """Read one bundle without following symlinks, junctions, or special files."""
    if _is_reparse_or_symlink(directory) or not directory.is_dir():
        raise _UnsafeFilesystemEntry("項目不是安全的實體資料夾")
    bundle: dict[str, bytes] = {}
    pending = [directory]
    while pending:
        parent = pending.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_symlink() or _is_reparse_or_symlink(path):
                    raise _UnsafeFilesystemEntry(f"bundle 含 unsafe symlink/reparse entry：{path.name}")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    bundle[path.relative_to(directory).as_posix()] = path.read_bytes()
                else:
                    raise _UnsafeFilesystemEntry(f"bundle 含不支援的 filesystem entry：{path.name}")
    return bundle


def _validate_bundle_directory(directory: Path, expected_id: str | None = None) -> tuple[dict, dict[str, bytes]]:
    bundle = _read_bundle_directory(directory)
    validated = validate_annual_bundle(bundle)
    actual_id = validated["version"]["version_id"]
    if expected_id is not None and actual_id != expected_id:
        raise StorageValidationError(
            f"version.json.version_id ({actual_id}) 與版本目錄名稱 ({expected_id}) 不一致"
        )
    return validated, bundle


def _inspect_audits(root: Path) -> tuple[tuple[AnnualAuditDiagnostic, ...], list[str]]:
    audit_root = root / "audit" / "events"
    diagnostics: list[AnnualAuditDiagnostic] = []
    errors: list[str] = []
    try:
        years = _direct_children(audit_root)
    except (OSError, _UnsafeFilesystemEntry) as exc:
        return (), [f"audit inventory 無法安全讀取：{exc}"]
    for year in years:
        if not _AUDIT_YEAR_RE.fullmatch(year.name):
            continue
        try:
            months = _direct_children(year)
        except (OSError, _UnsafeFilesystemEntry) as exc:
            errors.append(f"audit 年度目錄 {year.name} 無法安全讀取：{exc}")
            continue
        for month in months:
            if not _AUDIT_MONTH_RE.fullmatch(month.name):
                continue
            try:
                entries = _direct_children(month)
            except (OSError, _UnsafeFilesystemEntry) as exc:
                errors.append(f"audit 月份目錄 {year.name}/{month.name} 無法安全讀取：{exc}")
                continue
            for path in entries:
                if path.suffix.lower() != ".json":
                    continue
                modified = _modified_at(path)
                try:
                    if _is_reparse_or_symlink(path) or not path.is_file():
                        raise _UnsafeFilesystemEntry("audit item 不是安全的實體檔案")
                    parsed = deserialize_json(path.read_bytes())
                    if not isinstance(parsed, dict):
                        raise StorageValidationError("audit JSON 必須是 object")
                    if parsed.get("event_type") != ANNUAL_ACTIVATION_EVENT_TYPE:
                        diagnostics.append(
                            AnnualAuditDiagnostic(
                                path,
                                AuditStatus.UNKNOWN,
                                modified,
                                event=parsed,
                                failure_reason="未知或尚未支援的 audit event_type；不作為 activation 證據",
                            )
                        )
                        continue
                    event = validate_annual_activation_audit_event(parsed)
                    diagnostics.append(
                        AnnualAuditDiagnostic(
                            path,
                            AuditStatus.VALID_ANNUAL_ACTIVATION,
                            modified,
                            event=event,
                        )
                    )
                except OSError as exc:
                    diagnostics.append(
                        AnnualAuditDiagnostic(
                            path,
                            AuditStatus.INVALID,
                            modified,
                            failure_reason=str(exc),
                        )
                    )
                    errors.append(f"audit file 無法讀取：{path}：{exc}")
                except (StorageValidationError, _UnsafeFilesystemEntry) as exc:
                    diagnostics.append(
                        AnnualAuditDiagnostic(
                            path,
                            AuditStatus.INVALID,
                            modified,
                            failure_reason=str(exc),
                        )
                    )
    return tuple(diagnostics), errors


def _activation_references(audits: tuple[AnnualAuditDiagnostic, ...]) -> set[str]:
    referenced: set[str] = set()
    for item in audits:
        if item.status is not AuditStatus.VALID_ANNUAL_ACTIVATION or item.event is None:
            continue
        before_id = item.event["before_current_version_id"]
        if before_id is not None:
            referenced.add(before_id)
        referenced.add(item.event["after_current_version_id"])
    return referenced


def _inspect_versions(
    root: Path,
    *,
    healthy_current_id: str | None,
    historical_references: set[str],
) -> tuple[tuple[AnnualVersionDiagnostic, ...], list[str]]:
    versions_root = root / "annual-data" / "versions"
    diagnostics: list[AnnualVersionDiagnostic] = []
    errors: list[str] = []
    try:
        entries = _direct_children(versions_root)
    except (OSError, _UnsafeFilesystemEntry) as exc:
        return (), [f"annual versions inventory 無法安全讀取：{exc}"]
    for path in entries:
        modified = _modified_at(path)
        try:
            safe_name = validate_safe_id(path.name, "annual version directory")
            validated, _ = _validate_bundle_directory(path, safe_name)
        except OSError as exc:
            diagnostics.append(
                AnnualVersionDiagnostic(
                    path.name,
                    path,
                    VersionStatus.INVALID,
                    modified,
                    failure_reason=str(exc),
                )
            )
            errors.append(f"annual version 無法讀取：{path.name}：{exc}")
            continue
        except (StorageValidationError, _UnsafeFilesystemEntry) as exc:
            diagnostics.append(
                AnnualVersionDiagnostic(
                    path.name,
                    path,
                    VersionStatus.INVALID,
                    modified,
                    failure_reason=str(exc),
                )
            )
            continue
        if safe_name == healthy_current_id:
            status = VersionStatus.CURRENT
        elif safe_name in historical_references:
            status = VersionStatus.HISTORICAL
        else:
            status = VersionStatus.ORPHAN
        diagnostics.append(
            AnnualVersionDiagnostic(
                path.name,
                path,
                status,
                modified,
                version_id=validated["version"]["version_id"],
                validation_ok=True,
            )
        )
    return tuple(diagnostics), errors


def _inspect_staging(root: Path) -> tuple[tuple[AnnualStagingDiagnostic, ...], list[str]]:
    staging_root = root / "staging"
    diagnostics: list[AnnualStagingDiagnostic] = []
    errors: list[str] = []
    try:
        entries = _direct_children(staging_root)
    except (OSError, _UnsafeFilesystemEntry) as exc:
        return (), [f"staging inventory 無法安全讀取：{exc}"]
    for path in entries:
        if not path.name.startswith("annual-data-"):
            continue
        modified = _modified_at(path)
        is_directory = False
        has_committed = False
        looks_complete = False
        try:
            if _is_reparse_or_symlink(path) or not path.is_dir():
                raise _UnsafeFilesystemEntry("staging item 不是安全的實體資料夾")
            is_directory = True
            bundle = _read_bundle_directory(path)
            has_committed = "COMMITTED.json" in bundle
            looks_complete = set(ANNUAL_REQUIRED_FILES).issubset(bundle)
            if not has_committed:
                diagnostics.append(
                    AnnualStagingDiagnostic(
                        path.name,
                        path,
                        StagingStatus.INCOMPLETE,
                        modified,
                        True,
                        False,
                        looks_complete,
                        False,
                        "缺少 COMMITTED.json；staging 尚未完成",
                    )
                )
                continue
            validate_annual_bundle(bundle)
            diagnostics.append(
                AnnualStagingDiagnostic(
                    path.name,
                    path,
                    StagingStatus.COMPLETE_BUT_UNPUBLISHED,
                    modified,
                    True,
                    True,
                    looks_complete,
                    True,
                )
            )
        except _UnsafeFilesystemEntry as exc:
            diagnostics.append(
                AnnualStagingDiagnostic(
                    path.name,
                    path,
                    StagingStatus.UNSAFE,
                    modified,
                    is_directory,
                    has_committed,
                    looks_complete,
                    False,
                    str(exc),
                )
            )
        except OSError as exc:
            diagnostics.append(
                AnnualStagingDiagnostic(
                    path.name,
                    path,
                    StagingStatus.INVALID,
                    modified,
                    is_directory,
                    has_committed,
                    looks_complete,
                    False,
                    str(exc),
                )
            )
            errors.append(f"staging item 無法讀取：{path.name}：{exc}")
        except StorageValidationError as exc:
            diagnostics.append(
                AnnualStagingDiagnostic(
                    path.name,
                    path,
                    StagingStatus.INVALID,
                    modified,
                    is_directory,
                    has_committed,
                    looks_complete,
                    False,
                    str(exc),
                )
            )
    return tuple(diagnostics), errors


def _inspect_quarantine(root: Path) -> tuple[tuple[AnnualQuarantineDiagnostic, ...], list[str]]:
    quarantine_root = root / "quarantine"
    diagnostics: list[AnnualQuarantineDiagnostic] = []
    errors: list[str] = []
    try:
        entries = _direct_children(quarantine_root)
    except (OSError, _UnsafeFilesystemEntry) as exc:
        return (), [f"quarantine inventory 無法安全讀取：{exc}"]
    for path in entries:
        annual_evidence = path.name.startswith("annual-data-")
        modified = _modified_at(path)
        validation_ok: bool | None = None
        reason: str | None = None
        if annual_evidence:
            try:
                _validate_bundle_directory(path)
                validation_ok = True
            except OSError as exc:
                validation_ok = False
                reason = str(exc)
                diagnostics.append(
                    AnnualQuarantineDiagnostic(
                        path.name,
                        path,
                        modified,
                        annual_evidence,
                        validation_ok,
                        reason,
                    )
                )
                errors.append(f"quarantine item 無法讀取：{path.name}：{exc}")
                continue
            except (StorageValidationError, _UnsafeFilesystemEntry) as exc:
                validation_ok = False
                reason = str(exc)
        diagnostics.append(
            AnnualQuarantineDiagnostic(
                path.name,
                path,
                modified,
                annual_evidence,
                validation_ok,
                reason,
            )
        )
    return tuple(diagnostics), errors


def _inspect_temp_artifacts(root: Path) -> tuple[tuple[TempArtifactDiagnostic, ...], list[str]]:
    artifacts: list[TempArtifactDiagnostic] = []
    errors: list[str] = []
    annual_root = root / "annual-data"
    try:
        annual_entries = _direct_children(annual_root)
    except (OSError, _UnsafeFilesystemEntry) as exc:
        annual_entries = ()
        errors.append(f"annual-data temp inventory 無法安全讀取：{exc}")
    for path in annual_entries:
        if path.name.startswith(".current.json.") and path.name.endswith(".tmp"):
            artifacts.append(TempArtifactDiagnostic(path, "annual_current_temp", _modified_at(path)))

    audit_root = root / "audit" / "events"
    try:
        years = _direct_children(audit_root)
        for year in years:
            if not _AUDIT_YEAR_RE.fullmatch(year.name):
                continue
            for month in _direct_children(year):
                if not _AUDIT_MONTH_RE.fullmatch(month.name):
                    continue
                for path in _direct_children(month):
                    if path.name.startswith(".") and ".json." in path.name and path.name.endswith(".tmp"):
                        artifacts.append(
                            TempArtifactDiagnostic(path, "annual_activation_audit_temp", _modified_at(path))
                        )
    except (OSError, _UnsafeFilesystemEntry) as exc:
        errors.append(f"audit temp inventory 無法安全讀取：{exc}")
    return tuple(sorted(artifacts, key=lambda item: str(item.path))), errors


def _inspect_current(root: Path) -> tuple[CurrentStatus, dict[str, Any] | None, str | None]:
    current_path = root / "annual-data" / "current.json"
    try:
        current_info = current_path.lstat()
    except FileNotFoundError:
        return CurrentStatus.MISSING, None, None
    except OSError as exc:
        return CurrentStatus.UNINSPECTABLE, None, f"current 無法讀取：{exc}"
    try:
        if _is_reparse_or_symlink(current_path) or not stat.S_ISREG(current_info.st_mode):
            raise _UnsafeFilesystemEntry("current.json 不是安全的實體檔案")
        current = validate_annual_current(deserialize_json(current_path.read_bytes()))
    except OSError as exc:
        return CurrentStatus.UNINSPECTABLE, None, f"current 無法讀取：{exc}"
    except (StorageValidationError, _UnsafeFilesystemEntry) as exc:
        return CurrentStatus.CURRENT_INVALID, None, str(exc)
    target = root / "annual-data" / "versions" / current["current_version_id"]
    try:
        target.lstat()
    except FileNotFoundError:
        return CurrentStatus.CURRENT_TARGET_MISSING, current, "current 指向的 immutable version 目錄不存在"
    except OSError as exc:
        return CurrentStatus.UNINSPECTABLE, current, f"current target 無法讀取：{exc}"
    try:
        _validate_bundle_directory(target, current["current_version_id"])
    except OSError as exc:
        return CurrentStatus.UNINSPECTABLE, current, f"current target 無法讀取：{exc}"
    except (StorageValidationError, _UnsafeFilesystemEntry) as exc:
        return CurrentStatus.CURRENT_TARGET_INVALID, current, str(exc)
    return CurrentStatus.HEALTHY, current, None


def _current_audit_state(
    current_status: CurrentStatus,
    current: dict[str, Any] | None,
    audits: tuple[AnnualAuditDiagnostic, ...],
    audit_scan_failed: bool,
) -> tuple[CurrentAuditStatus, int]:
    if current_status is not CurrentStatus.HEALTHY or current is None:
        return CurrentAuditStatus.NOT_APPLICABLE, 0
    if audit_scan_failed:
        return CurrentAuditStatus.UNINSPECTABLE, 0
    matches = 0
    for item in audits:
        if item.status is not AuditStatus.VALID_ANNUAL_ACTIVATION or item.event is None:
            continue
        event = item.event
        if (
            event["before_revision"] == current["revision"] - 1
            and event["before_current_version_id"] == current["previous_version_id"]
            and event["after_revision"] == current["revision"]
            and event["after_current_version_id"] == current["current_version_id"]
        ):
            matches += 1
    if matches == 1:
        return CurrentAuditStatus.MATCHED, 1
    if matches == 0:
        return CurrentAuditStatus.MISSING, 0
    return CurrentAuditStatus.AMBIGUOUS, matches


def _severity(
    current_status: CurrentStatus,
    audit_status: CurrentAuditStatus,
    versions: tuple[AnnualVersionDiagnostic, ...],
    staging: tuple[AnnualStagingDiagnostic, ...],
    quarantine: tuple[AnnualQuarantineDiagnostic, ...],
    audits: tuple[AnnualAuditDiagnostic, ...],
    temp_artifacts: tuple[TempArtifactDiagnostic, ...],
    inspection_errors: list[str],
) -> tuple[RecoverySeverity, str]:
    if current_status is CurrentStatus.UNINSPECTABLE or inspection_errors:
        return RecoverySeverity.UNINSPECTABLE, "部分正式狀態無法安全讀取，無法可靠完成 recovery 判斷。"
    if current_status in {
        CurrentStatus.CURRENT_INVALID,
        CurrentStatus.CURRENT_TARGET_MISSING,
        CurrentStatus.CURRENT_TARGET_INVALID,
    }:
        return RecoverySeverity.RECOVERY_REQUIRED, "current 或其正式 immutable target 不一致，需要復原處理。"
    if current_status is CurrentStatus.MISSING and versions:
        return (
            RecoverySeverity.RECOVERY_REQUIRED,
            "current 缺失，但 versions 中已存在正式資料 evidence，需要 recovery 判斷，不能視為第一版。",
        )
    if audit_status in {CurrentAuditStatus.MISSING, CurrentAuditStatus.AMBIGUOUS}:
        return RecoverySeverity.RECOVERY_REQUIRED, "current 完整，但此次 current transition audit 缺失或不唯一。"
    attention = bool(
        staging
        or quarantine
        or temp_artifacts
        or any(item.status in {VersionStatus.ORPHAN, VersionStatus.INVALID} for item in versions)
        or any(item.status in {AuditStatus.INVALID, AuditStatus.UNKNOWN} for item in audits)
    )
    if attention:
        return RecoverySeverity.ATTENTION, "目前 current 可用，但存在需要人工檢視的非正式或異常 evidence。"
    if current_status is CurrentStatus.MISSING:
        return RecoverySeverity.HEALTHY, "尚無 current 且 versions inventory 為空，屬合法 first-version 狀態。"
    return RecoverySeverity.HEALTHY, "current、immutable bundle 與 activation audit 均完整一致。"


def diagnose_annual_data(
    root: str | os.PathLike[str] | None = None,
    *,
    shared_result: "SharedStorageResult | None" = None,
) -> AnnualDataDiagnostics:
    """Inspect annual persistence state without mutating any filesystem bytes."""
    inspected_at = _now()
    configured = root if root is not None else getattr(shared_result, "root", None)
    shared_root = Path(configured) if configured is not None and str(configured).strip() else None
    if shared_root is None:
        return AnnualDataDiagnostics(
            None,
            inspected_at,
            False,
            None,
            "尚未設定 shared root",
            CurrentStatus.UNINSPECTABLE,
            None,
            "shared root 不可用",
            CurrentAuditStatus.UNINSPECTABLE,
            0,
            (), (), (), (), (), None,
            RecoverySeverity.UNINSPECTABLE,
            "shared root 不可用，無法進行 diagnostics。",
        )
    try:
        root_info = shared_root.stat()
        if not stat.S_ISDIR(root_info.st_mode):
            raise NotADirectoryError("shared root 不是 directory")
        system_path = shared_root / "system.json"
        if _is_reparse_or_symlink(system_path) or not system_path.is_file():
            raise _UnsafeFilesystemEntry("system.json 不存在或不是安全的實體檔案")
        system = validate_system(deserialize_json(system_path.read_bytes()))
    except (OSError, StorageValidationError, _UnsafeFilesystemEntry) as exc:
        return AnnualDataDiagnostics(
            shared_root,
            inspected_at,
            False,
            None,
            str(exc),
            CurrentStatus.UNINSPECTABLE,
            None,
            "system-level validation failure；未繼續猜測正式資料",
            CurrentAuditStatus.UNINSPECTABLE,
            0,
            (), (), (), (), (), None,
            RecoverySeverity.UNINSPECTABLE,
            "root/system 無法可靠驗證；未檢查或猜測年度正式資料。",
        )

    current_status, current, current_reason = _inspect_current(shared_root)
    audits, audit_errors = _inspect_audits(shared_root)
    references = _activation_references(audits)
    if current is not None and current.get("previous_version_id") is not None:
        references.add(current["previous_version_id"])
    healthy_current_id = current["current_version_id"] if current_status is CurrentStatus.HEALTHY else None
    versions, version_errors = _inspect_versions(
        shared_root,
        healthy_current_id=healthy_current_id,
        historical_references=references,
    )
    if current_status is CurrentStatus.HEALTHY and healthy_current_id is not None:
        current_inventory_item = next(
            (item for item in versions if item.entry_name == healthy_current_id),
            None,
        )
        if current_inventory_item is None:
            current_status = CurrentStatus.CURRENT_TARGET_MISSING
            current_reason = "current target 在 versions inventory 時不存在"
        elif current_inventory_item.status is VersionStatus.INVALID:
            current_status = CurrentStatus.CURRENT_TARGET_INVALID
            current_reason = current_inventory_item.failure_reason
    staging, staging_errors = _inspect_staging(shared_root)
    quarantine, quarantine_errors = _inspect_quarantine(shared_root)
    temp_artifacts, temp_errors = _inspect_temp_artifacts(shared_root)
    inspection_errors = [
        *audit_errors,
        *version_errors,
        *staging_errors,
        *quarantine_errors,
        *temp_errors,
    ]
    current_audit_status, match_count = _current_audit_state(
        current_status,
        current,
        audits,
        bool(audit_errors),
    )
    severity, summary = _severity(
        current_status,
        current_audit_status,
        versions,
        staging,
        quarantine,
        audits,
        temp_artifacts,
        inspection_errors,
    )
    lock_path = shared_root / "locks" / "annual-current.lock"
    try:
        lock_exists = lock_path.exists() or lock_path.is_symlink()
    except OSError:
        lock_exists = False
    lock = LockMetadataDiagnostic(
        lock_path,
        lock_exists,
        _modified_at(lock_path) if lock_exists else None,
        "lock metadata file 存在本身不代表目前仍持有 OS-level 排他鎖。",
    )
    return AnnualDataDiagnostics(
        shared_root,
        inspected_at,
        True,
        system,
        None,
        current_status,
        current,
        current_reason,
        current_audit_status,
        match_count,
        versions,
        staging,
        quarantine,
        audits,
        temp_artifacts,
        lock,
        severity,
        summary,
        tuple(inspection_errors),
    )
