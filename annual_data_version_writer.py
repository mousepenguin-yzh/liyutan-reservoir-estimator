"""Safely publish an immutable annual-data version without activating it.

This module implements stage 2-4C1 only.  It never creates or changes
``annual-data/current.json`` and never writes audit records.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from annual_data_excel import (
    AnnualDataCandidate,
    AnnualDataIssue,
    candidate_artifacts,
    parse_annual_data_excel,
)
from shared_storage_schema import (
    ANNUAL_DATA_FILES,
    ANNUAL_REQUIRED_FILES,
    ANNUAL_VERSION_SCHEMA,
    COMMITTED_SCHEMA,
    SCHEMA_VERSION,
    StorageValidationError,
    deserialize_json,
    serialize_json,
    sha256_bytes,
    validate_annual_bundle,
    validate_annual_version,
    validate_original_filename,
    validate_safe_id,
    validate_system,
)


FaultInjector = Callable[[str, Path], None]


class AnnualDataVersionPublishError(RuntimeError):
    """A safe, expected refusal or publication failure."""

    def __init__(self, code: str, message: str, *, evidence_path: Path | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.evidence_path = evidence_path


class AnnualDataVersionConflictError(AnnualDataVersionPublishError):
    """The immutable destination already exists."""


class InjectedAnnualDataFault(RuntimeError):
    """Test-only exception raised by :func:`fault_at`."""


@dataclass(frozen=True)
class AnnualDataVersionPublishResult:
    version_id: str
    version_path: Path
    version: dict


def fault_at(target_stage: str) -> FaultInjector:
    """Return a deterministic test-only fault injector for one checkpoint."""

    def inject(stage: str, _path: Path) -> None:
        if stage == target_stage:
            raise InjectedAnnualDataFault(stage)

    return inject


def _utc_now(value: dt.datetime | None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise AnnualDataVersionPublishError("invalid_created_at", "建立時間必須包含時區。")
    return current.astimezone(dt.timezone.utc)


def _utc_text(value: dt.datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def generate_annual_version_id(created_at: dt.datetime, value: uuid.UUID | str | None = None) -> str:
    """Generate a Windows-safe ID from UTC time and a UUID."""
    created_at = _utc_now(created_at)
    try:
        unique = (
            value
            if isinstance(value, uuid.UUID)
            else uuid.UUID(str(value)) if value else uuid.uuid4()
        )
    except (ValueError, AttributeError) as exc:
        raise AnnualDataVersionPublishError("invalid_uuid", "version UUID 格式無效。") from exc
    version_id = f"annual-{created_at.strftime('%Y%m%dT%H%M%S%fZ')}-{unique}"
    validate_safe_id(version_id, "annual_version_id")
    return version_id


def _warning_record(issue: AnnualDataIssue) -> dict:
    return {
        "severity": issue.severity.value,
        "code": issue.code,
        "message": issue.message,
        "sheet": issue.sheet,
        "cell": issue.cell,
    }


def _parameter_metadata(candidate: AnnualDataCandidate) -> dict[str, dict]:
    return {
        code: {
            "effective_start_date": metadata.get("effective_start_date"),
            "source_reference": metadata.get("source_reference"),
            "note": metadata.get("note"),
        }
        for code, metadata in candidate.parameter_metadata.items()
    }


def _source_references(candidate: AnnualDataCandidate) -> list[str]:
    references = [candidate.hydrology_source_period, candidate.annual_outflow_source]
    references.extend(
        metadata.get("source_reference")
        for metadata in candidate.parameter_metadata.values()
        if metadata.get("source_reference") is not None
    )
    return list(dict.fromkeys(references))


def _checkpoint(injector: FaultInjector | None, stage: str, path: Path) -> None:
    if injector is not None:
        injector(stage, path)


def _write_verified_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    actual = path.read_bytes()
    if sha256_bytes(actual) != sha256_bytes(data):
        raise AnnualDataVersionPublishError(
            "write_checksum_mismatch",
            f"寫入後 checksum 驗證失敗：{path.name}",
            evidence_path=path,
        )


def _read_bundle(directory: Path) -> dict[str, bytes]:
    bundle: dict[str, bytes] = {}
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        bundle[relative] = path.read_bytes()
    return bundle


def _quarantine(root: Path, staging_path: Path) -> Path:
    quarantine_root = root / "quarantine"
    quarantine_root.mkdir(parents=True, exist_ok=True)
    destination = quarantine_root / staging_path.name
    if destination.exists():
        destination = quarantine_root / f"{staging_path.name}-{uuid.uuid4().hex}"
    staging_path.rename(destination)
    return destination


def _validated_candidate(
    candidate: AnnualDataCandidate,
    source_excel_bytes: bytes,
    source_filename: str,
    confirmed_candidate_fingerprint: str,
    warnings_confirmed: bool,
) -> AnnualDataCandidate:
    if not isinstance(candidate, AnnualDataCandidate):
        raise AnnualDataVersionPublishError("invalid_candidate", "必須提供已驗證的年度資料候選。")
    if not isinstance(source_excel_bytes, bytes):
        raise AnnualDataVersionPublishError("invalid_source_bytes", "原始 Excel 必須是 bytes。")
    try:
        validate_original_filename(source_filename, "原始 Excel 檔名")
    except StorageValidationError as exc:
        raise AnnualDataVersionPublishError("unsafe_source_filename", str(exc)) from exc
    if candidate.source_filename is None:
        raise AnnualDataVersionPublishError(
            "source_filename_missing",
            "候選未保存原始 Excel 檔名，必須重新預覽與確認。",
        )
    if source_filename != candidate.source_filename:
        raise AnnualDataVersionPublishError(
            "source_filename_changed",
            "原始 Excel 檔名已變更，必須重新預覽與確認。",
        )
    if sha256_bytes(source_excel_bytes) != candidate.source_sha256:
        raise AnnualDataVersionPublishError(
            "source_sha256_changed", "原始 Excel 已變更，必須重新預覽與確認。"
        )
    if confirmed_candidate_fingerprint != candidate.fingerprint:
        raise AnnualDataVersionPublishError(
            "confirmed_fingerprint_changed", "候選 fingerprint 已變更，必須重新預覽與確認。"
        )
    parsed = parse_annual_data_excel(source_excel_bytes, filename=source_filename)
    if not parsed.ok or parsed.candidate is None:
        codes = ", ".join(issue.code for issue in parsed.errors)
        raise AnnualDataVersionPublishError(
            "source_revalidation_failed", f"原始 Excel 重新驗證失敗：{codes or 'unknown'}"
        )
    reparsed = parsed.candidate
    if reparsed.fingerprint != confirmed_candidate_fingerprint:
        raise AnnualDataVersionPublishError(
            "reparsed_fingerprint_changed", "重新解析的 fingerprint 已變更，必須重新預覽與確認。"
        )
    if reparsed != candidate:
        raise AnnualDataVersionPublishError(
            "candidate_changed", "重新解析結果與候選內容不一致，必須重新預覽與確認。"
        )
    if reparsed.warnings and warnings_confirmed is not True:
        raise AnnualDataVersionPublishError(
            "warnings_not_confirmed", "候選仍含 warnings，必須明確確認後才能建立版本。"
        )
    return reparsed


def publish_annual_data_version(
    *,
    root: str | os.PathLike[str],
    candidate: AnnualDataCandidate,
    source_excel_bytes: bytes,
    source_filename: str,
    operator_display_name: str,
    note: str,
    confirmed_candidate_fingerprint: str,
    warnings_confirmed: bool = False,
    created_at: dt.datetime | None = None,
    version_uuid: uuid.UUID | str | None = None,
    version_id: str | None = None,
    staging_uuid: uuid.UUID | str | None = None,
    fault_injector: FaultInjector | None = None,
) -> AnnualDataVersionPublishResult:
    """Publish one immutable annual-data bundle and leave it unactivated.

    ``version_id`` and the time/UUID arguments are deterministic-test seams;
    production callers should omit them and must never expose ``version_id``
    as a user-editable field.
    """
    operator = operator_display_name.strip() if isinstance(operator_display_name, str) else ""
    version_note = note.strip() if isinstance(note, str) else ""
    if not operator:
        raise AnnualDataVersionPublishError("operator_required", "人工填報操作人為必填。")
    if not version_note:
        raise AnnualDataVersionPublishError("note_required", "建立版本備註為必填。")

    shared_root = Path(root)
    if not shared_root.exists():
        raise AnnualDataVersionPublishError("root_not_found", "指定共享根目錄不存在。")
    if not shared_root.is_dir():
        raise AnnualDataVersionPublishError("root_not_directory", "指定共享根目錄不是資料夾。")
    system_path = shared_root / "system.json"
    try:
        validate_system(deserialize_json(system_path.read_bytes()))
    except FileNotFoundError as exc:
        raise AnnualDataVersionPublishError("system_missing", "system.json 不存在，不會自動建立。") from exc
    except (OSError, StorageValidationError) as exc:
        raise AnnualDataVersionPublishError("system_invalid", f"system.json 無法通過驗證：{exc}") from exc

    try:
        source_filename = validate_original_filename(source_filename, "原始 Excel 檔名")
    except StorageValidationError as exc:
        raise AnnualDataVersionPublishError("unsafe_source_filename", str(exc)) from exc
    reparsed = _validated_candidate(
        candidate,
        source_excel_bytes,
        source_filename,
        confirmed_candidate_fingerprint,
        warnings_confirmed,
    )
    now = _utc_now(created_at)
    if version_id is None:
        safe_version_id = generate_annual_version_id(now, version_uuid)
    else:
        try:
            safe_version_id = validate_safe_id(version_id, "annual_version_id")
        except StorageValidationError as exc:
            raise AnnualDataVersionPublishError("unsafe_version_id", str(exc)) from exc

    versions_root = shared_root / "annual-data" / "versions"
    final_path = versions_root / safe_version_id
    if os.path.lexists(final_path):
        raise AnnualDataVersionConflictError(
            "version_exists", "年度版本目錄已存在，拒絕覆蓋或合併。", evidence_path=final_path
        )

    try:
        stage_unique = (
            staging_uuid
            if isinstance(staging_uuid, uuid.UUID)
            else uuid.UUID(str(staging_uuid)) if staging_uuid else uuid.uuid4()
        )
    except (ValueError, AttributeError) as exc:
        raise AnnualDataVersionPublishError("invalid_staging_uuid", "staging UUID 格式無效。") from exc
    staging_root = shared_root / "staging"
    staging_path = staging_root / f"annual-data-{safe_version_id}-{stage_unique.hex}"
    if os.path.lexists(staging_path):
        raise AnnualDataVersionConflictError(
            "staging_exists", "年度版本 staging 目錄已存在。", evidence_path=staging_path
        )

    artifacts = candidate_artifacts(reparsed)
    artifacts["source/original.xlsx"] = source_excel_bytes
    files = {name: {"sha256": sha256_bytes(artifacts[name])} for name in ANNUAL_DATA_FILES}
    version = {
        "schema": ANNUAL_VERSION_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "version_id": safe_version_id,
        "applicable_year": reparsed.applicable_year,
        "created_at": _utc_text(now),
        "operator_display_name": operator,
        "note": version_note,
        "template_version": reparsed.template_version,
        "reservoir_id": reparsed.reservoir_id,
        "reservoir_name": reparsed.reservoir_name,
        "actual_data_cutoff_period": reparsed.actual_data_cutoff_period,
        "hydrology_source_period": reparsed.hydrology_source_period,
        "annual_outflow_source": reparsed.annual_outflow_source,
        "overall_note": reparsed.overall_note,
        "candidate_fingerprint": reparsed.fingerprint,
        "source_excel": {
            "original_filename": reparsed.source_filename,
            "sha256": reparsed.source_sha256,
        },
        "parameter_metadata": _parameter_metadata(reparsed),
        "confirmed_warnings": [_warning_record(issue) for issue in reparsed.warnings],
        "source_references": _source_references(reparsed),
        "files": files,
    }
    validate_annual_version(version)
    version_bytes = serialize_json(version)
    committed_bytes = serialize_json(
        {
            "schema": COMMITTED_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "version_id": safe_version_id,
            "committed_at": _utc_text(now),
            "manifest_file": "version.json",
            "manifest_sha256": sha256_bytes(version_bytes),
        }
    )
    validate_annual_bundle(
        {"version.json": version_bytes, **artifacts, "COMMITTED.json": committed_bytes}
    )

    staging_root.mkdir(parents=True, exist_ok=True)
    versions_root.mkdir(parents=True, exist_ok=True)
    staging_path.mkdir()
    _checkpoint(fault_injector, "before_first_file", staging_path)
    for name in ANNUAL_DATA_FILES:
        _write_verified_file(staging_path / Path(name), artifacts[name])
        _checkpoint(fault_injector, f"after_write:{name}", staging_path)
    _write_verified_file(staging_path / "version.json", version_bytes)
    _checkpoint(fault_injector, "after_write:version.json", staging_path)
    _checkpoint(fault_injector, "before_committed", staging_path)
    _write_verified_file(staging_path / "COMMITTED.json", committed_bytes)
    _checkpoint(fault_injector, "after_committed", staging_path)

    _checkpoint(fault_injector, "before_staging_validation", staging_path)
    try:
        validate_annual_bundle(_read_bundle(staging_path))
    except (OSError, StorageValidationError) as exc:
        try:
            evidence = _quarantine(shared_root, staging_path)
        except OSError:
            evidence = staging_path
        raise AnnualDataVersionPublishError(
            "staging_validation_failed",
            f"staging 完整驗證失敗，內容未發布：{exc}",
            evidence_path=evidence,
        ) from exc
    _checkpoint(fault_injector, "after_staging_validation", staging_path)
    _checkpoint(fault_injector, "before_rename", staging_path)
    if os.path.lexists(final_path):
        raise AnnualDataVersionConflictError(
            "version_exists", "年度版本目錄已存在，拒絕覆蓋或合併。", evidence_path=staging_path
        )
    try:
        staging_path.rename(final_path)
    except FileExistsError as exc:
        raise AnnualDataVersionConflictError(
            "version_exists", "年度版本目錄已存在，拒絕覆蓋或合併。", evidence_path=staging_path
        ) from exc
    _checkpoint(fault_injector, "after_rename", final_path)
    _checkpoint(fault_injector, "before_published_validation", final_path)
    try:
        published = validate_annual_bundle(_read_bundle(final_path))
    except (OSError, StorageValidationError) as exc:
        raise AnnualDataVersionPublishError(
            "published_validation_failed",
            f"rename 後完整驗證失敗；版本保留供診斷且未啟用：{exc}",
            evidence_path=final_path,
        ) from exc
    _checkpoint(fault_injector, "after_published_validation", final_path)
    return AnnualDataVersionPublishResult(safe_version_id, final_path, published["version"])


# A concise compatibility name for callers that describe the operation as creation.
create_annual_data_version = publish_annual_data_version
