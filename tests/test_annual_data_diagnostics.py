import hashlib
from pathlib import Path

import pytest

from annual_data_diagnostics import (
    AuditStatus,
    CurrentAuditStatus,
    CurrentStatus,
    RecoverySeverity,
    StagingStatus,
    VersionStatus,
    diagnose_annual_data,
)
from shared_storage_reader import load_shared_storage
from shared_storage_schema import (
    ANNUAL_ACTIVATION_EVENT_TYPE,
    ANNUAL_ACTIVATION_RECOVERY_EVENT_TYPE,
    ANNUAL_CURRENT_REPAIR_EVENT_TYPE,
    deserialize_json,
    serialize_json,
)
from test_shared_storage_reader import ANNUAL_ID, _build_root, _write_bundle
from test_shared_storage_schema import _annual_bundle


def _remove_audits(root: Path) -> None:
    for path in (root / "audit" / "events").rglob("*.json"):
        path.unlink()


def _add_version(root: Path, version_id: str) -> Path:
    bundle = _annual_bundle(version_mutator=lambda value: value.update(version_id=version_id))
    destination = root / "annual-data" / "versions" / version_id
    _write_bundle(destination, bundle)
    return destination


def _tree_fingerprint(root: Path) -> tuple[tuple[str, str], ...]:
    items = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            items.append((relative + "/", "directory"))
        else:
            items.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    return tuple(items)


def test_healthy_current_exactly_one_audit_and_read_only(tmp_path):
    root = _build_root(tmp_path)
    before = _tree_fingerprint(root)

    result = diagnose_annual_data(shared_result=load_shared_storage(root))

    assert result.current_status is CurrentStatus.HEALTHY
    assert result.current_audit_status is CurrentAuditStatus.MATCHED
    assert result.current_audit_match_count == 1
    assert result.overall_severity is RecoverySeverity.HEALTHY
    assert result.versions[0].status is VersionStatus.CURRENT
    assert _tree_fingerprint(root) == before


def test_empty_first_version_state_is_not_recovery_required(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    for path in (root / "annual-data" / "versions").iterdir():
        for child in sorted(path.rglob("*"), reverse=True):
            child.unlink() if child.is_file() else child.rmdir()
        path.rmdir()
    _remove_audits(root)

    result = diagnose_annual_data(root)

    assert result.current_status is CurrentStatus.MISSING
    assert not result.versions
    assert result.is_first_version_state
    assert result.overall_severity is RecoverySeverity.HEALTHY


def test_missing_current_with_complete_orphan_requires_first_current_initialization(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    _remove_audits(root)

    result = diagnose_annual_data(root)

    assert result.current_status is CurrentStatus.MISSING
    assert result.versions[0].status is VersionStatus.ORPHAN
    assert not result.is_first_version_state
    assert result.is_first_current_initialization_state
    assert result.overall_severity is RecoverySeverity.INITIALIZATION_REQUIRED
    assert "尚未設定第一個啟用版本" in result.summary


def test_missing_current_with_invalid_version_entry_requires_recovery(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    _remove_audits(root)
    version_dir = root / "annual-data" / "versions" / ANNUAL_ID
    (version_dir / "COMMITTED.json").unlink()

    result = diagnose_annual_data(root)

    assert result.current_status is CurrentStatus.MISSING
    assert len(result.versions) == 1
    assert result.versions[0].status is VersionStatus.INVALID
    assert not result.is_first_version_state
    assert result.overall_severity is RecoverySeverity.RECOVERY_REQUIRED
    assert "不能視為第一版" in result.summary


def test_corrupt_current_still_produces_versions_inventory(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").write_bytes(b"{")

    result = diagnose_annual_data(shared_result=load_shared_storage(root))

    assert result.current_status is CurrentStatus.CURRENT_INVALID
    assert result.complete_version_count == 1
    assert result.overall_severity is RecoverySeverity.RECOVERY_REQUIRED


def test_current_target_missing_requires_recovery(tmp_path):
    root = _build_root(tmp_path)
    current = deserialize_json((root / "annual-data" / "current.json").read_bytes())
    current["current_version_id"] = "annual-missing"
    (root / "annual-data" / "current.json").write_bytes(serialize_json(current))

    result = diagnose_annual_data(root)

    assert result.current_status is CurrentStatus.CURRENT_TARGET_MISSING
    assert result.overall_severity is RecoverySeverity.RECOVERY_REQUIRED


def test_current_target_checksum_corrupt_is_invalid_not_crash(tmp_path):
    root = _build_root(tmp_path)
    target = root / "annual-data" / "versions" / ANNUAL_ID
    (target / "hydrology_q.csv").write_bytes((target / "hydrology_q.csv").read_bytes() + b"\n")

    result = diagnose_annual_data(root)

    assert result.current_status is CurrentStatus.CURRENT_TARGET_INVALID
    assert result.versions[0].status is VersionStatus.INVALID
    assert "checksum" in result.versions[0].failure_reason
    assert result.overall_severity is RecoverySeverity.RECOVERY_REQUIRED


def test_valid_orphan_is_attention_but_not_corrupt(tmp_path):
    root = _build_root(tmp_path)
    orphan_id = "annual-synthetic-orphan"
    _add_version(root, orphan_id)

    result = diagnose_annual_data(root)
    orphan = next(item for item in result.versions if item.entry_name == orphan_id)

    assert orphan.status is VersionStatus.ORPHAN
    assert orphan.validation_ok
    assert result.current_status is CurrentStatus.HEALTHY
    assert result.overall_severity is RecoverySeverity.ATTENTION


def test_current_previous_reference_makes_valid_version_historical(tmp_path):
    root = _build_root(tmp_path)
    previous_id = "annual-synthetic-previous"
    _add_version(root, previous_id)
    current_path = root / "annual-data" / "current.json"
    current = deserialize_json(current_path.read_bytes())
    current["previous_version_id"] = previous_id
    current_path.write_bytes(serialize_json(current))

    result = diagnose_annual_data(root)
    previous = next(item for item in result.versions if item.entry_name == previous_id)

    assert previous.status is VersionStatus.HISTORICAL


def test_invalid_version_reason_is_preserved(tmp_path):
    root = _build_root(tmp_path)
    invalid = root / "annual-data" / "versions" / "annual-invalid"
    invalid.mkdir()
    (invalid / "version.json").write_bytes(b"not-json")

    result = diagnose_annual_data(root)
    item = next(item for item in result.versions if item.entry_name == "annual-invalid")

    assert item.status is VersionStatus.INVALID
    assert item.failure_reason
    assert result.overall_severity is RecoverySeverity.ATTENTION


def test_incomplete_and_complete_staging_are_never_formal_versions(tmp_path):
    root = _build_root(tmp_path)
    incomplete = root / "staging" / "annual-data-incomplete-fixture"
    incomplete.mkdir(parents=True)
    (incomplete / "version.json").write_bytes(b"{}")
    complete = root / "staging" / "annual-data-complete-fixture"
    _write_bundle(complete, _annual_bundle())
    (root / "staging" / "unknown-purpose").mkdir()

    result = diagnose_annual_data(root)
    statuses = {item.entry_name: item.status for item in result.staging}

    assert statuses == {
        "annual-data-complete-fixture": StagingStatus.COMPLETE_BUT_UNPUBLISHED,
        "annual-data-incomplete-fixture": StagingStatus.INCOMPLETE,
    }
    assert all(item.status is not VersionStatus.CURRENT for item in result.staging)
    assert result.overall_severity is RecoverySeverity.ATTENTION


def test_quarantine_inventory_does_not_modify_evidence(tmp_path):
    root = _build_root(tmp_path)
    evidence = root / "quarantine" / "annual-data-failed-fixture"
    evidence.mkdir(parents=True)
    (evidence / "version.json").write_bytes(b"{")
    before = _tree_fingerprint(root)

    result = diagnose_annual_data(root)

    assert len(result.quarantine) == 1
    assert result.quarantine[0].is_annual_data_evidence
    assert result.quarantine[0].validation_ok is False
    assert _tree_fingerprint(root) == before


def test_missing_and_duplicate_current_transition_audits_require_recovery(tmp_path):
    missing_root = _build_root(tmp_path / "missing")
    audit_path = next((missing_root / "audit" / "events").rglob("*.json"))
    event_bytes = audit_path.read_bytes()
    audit_path.unlink()

    missing = diagnose_annual_data(missing_root)
    assert missing.current_status is CurrentStatus.HEALTHY
    assert missing.current_audit_status is CurrentAuditStatus.MISSING
    assert missing.overall_severity is RecoverySeverity.RECOVERY_REQUIRED

    duplicate_root = _build_root(tmp_path / "duplicate")
    duplicate_dir = duplicate_root / "audit" / "events" / "2026" / "12"
    (duplicate_dir / "20261215T023500000001Z_duplicate.json").write_bytes(event_bytes)
    duplicate = diagnose_annual_data(duplicate_root)
    assert duplicate.current_audit_status is CurrentAuditStatus.AMBIGUOUS
    assert duplicate.current_audit_match_count == 2
    assert duplicate.overall_severity is RecoverySeverity.RECOVERY_REQUIRED


def test_invalid_recognized_audit_is_untrusted_while_unknown_remains_inventory_only(tmp_path):
    root = _build_root(tmp_path)
    audit_dir = root / "audit" / "events" / "2026" / "12"
    (audit_dir / "invalid.json").write_bytes(
        serialize_json({"event_type": "annual-data-activation"})
    )
    (audit_dir / "unknown.json").write_bytes(
        serialize_json({"event_type": "future-event", "payload": 1})
    )

    result = diagnose_annual_data(root)

    assert {item.status for item in result.audits} == {
        AuditStatus.VALID_ANNUAL_ACTIVATION,
        AuditStatus.INVALID,
        AuditStatus.UNKNOWN,
    }
    assert result.has_untrusted_annual_audit_evidence
    assert result.current_audit_status is CurrentAuditStatus.UNINSPECTABLE
    assert result.overall_severity is RecoverySeverity.UNINSPECTABLE
    assert any("annual audit evidence 無法可靠驗證" in error for error in result.inspection_errors)


@pytest.mark.parametrize(
    "event_type",
    (
        ANNUAL_ACTIVATION_EVENT_TYPE,
        ANNUAL_ACTIVATION_RECOVERY_EVENT_TYPE,
        ANNUAL_CURRENT_REPAIR_EVENT_TYPE,
    ),
)
def test_each_recognized_invalid_annual_event_is_untrusted(tmp_path, event_type):
    root = _build_root(tmp_path)
    audit_path = root / "audit" / "events" / "2026" / "12" / "invalid-recognized.json"
    audit_path.write_bytes(serialize_json({"event_type": event_type}))

    result = diagnose_annual_data(root)
    audit = next(item for item in result.audits if item.path == audit_path)

    assert audit.status is AuditStatus.INVALID
    assert audit.event == {"event_type": event_type}
    assert audit.failure_reason
    assert result.has_untrusted_annual_audit_evidence
    assert any("annual audit evidence 無法可靠驗證" in error for error in result.inspection_errors)


def test_leftover_current_and_audit_temp_are_attention_only(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / ".current.json.fixture.tmp").write_bytes(b"temporary")
    audit_dir = root / "audit" / "events" / "2026" / "12"
    (audit_dir / ".event.json.fixture.tmp").write_bytes(b"temporary")

    result = diagnose_annual_data(root)

    assert result.current_status is CurrentStatus.HEALTHY
    assert result.current_audit_status is CurrentAuditStatus.MATCHED
    assert len(result.temp_artifacts) == 2
    assert result.overall_severity is RecoverySeverity.ATTENTION


def test_lock_metadata_file_does_not_claim_live_lock_or_change_severity(tmp_path):
    root = _build_root(tmp_path)
    lock = root / "locks" / "annual-current.lock"
    lock.parent.mkdir()
    lock.write_bytes(b"stale metadata")

    result = diagnose_annual_data(root)

    assert result.lock_metadata.exists
    assert "不代表" in result.lock_metadata.note
    assert result.overall_severity is RecoverySeverity.HEALTHY


def test_untrusted_system_stops_before_annual_inventory(tmp_path):
    root = _build_root(tmp_path)
    system = deserialize_json((root / "system.json").read_bytes())
    system["reservoir_id"] = "other"
    (root / "system.json").write_bytes(serialize_json(system))

    result = diagnose_annual_data(root)

    assert not result.system_valid
    assert result.current_status is CurrentStatus.UNINSPECTABLE
    assert not result.versions and not result.audits
    assert result.overall_severity is RecoverySeverity.UNINSPECTABLE


def test_unreadable_audit_evidence_makes_result_uninspectable(tmp_path, monkeypatch):
    root = _build_root(tmp_path)
    audit_path = next((root / "audit" / "events").rglob("*.json"))
    original_read_bytes = Path.read_bytes

    def read_bytes(path):
        if path == audit_path:
            raise PermissionError("synthetic audit permission denial")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    result = diagnose_annual_data(root)

    assert result.current_status is CurrentStatus.HEALTHY
    assert result.current_audit_status is CurrentAuditStatus.UNINSPECTABLE
    assert result.overall_severity is RecoverySeverity.UNINSPECTABLE
    assert result.inspection_errors
