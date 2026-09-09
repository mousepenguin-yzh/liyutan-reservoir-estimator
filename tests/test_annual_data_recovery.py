import contextlib
import copy
import datetime as dt
import hashlib
from pathlib import Path

import pytest

from annual_data_activation import (
    AnnualDataActivationConflictError,
    activate_annual_data_version,
)
from annual_data_diagnostics import (
    AuditStatus,
    CurrentAuditStatus,
    CurrentStatus,
    RecoverySeverity,
    VersionStatus,
    diagnose_annual_data,
)
from annual_data_maintenance import (
    ENABLE_ANNUAL_DATA_RECOVERY_ENV,
    ENABLE_ANNUAL_DATA_WRITES_ENV,
    AnnualDataMaintenanceService,
    annual_data_recovery_capability,
    annual_data_recovery_enabled,
    annual_data_write_capability,
)
from annual_data_preview_ui import _eligible_reactivation_versions
from annual_data_recovery import (
    AnnualDataRecoveryConflictError,
    recover_annual_activation_audit,
)
from shared_storage_reader import load_shared_storage
from shared_storage_schema import (
    ANNUAL_ACTIVATION_EVENT_TYPE,
    ANNUAL_ACTIVATION_RECOVERY_EVENT_TYPE,
    StorageValidationError,
    deserialize_json,
    serialize_json,
    validate_annual_activation_audit_event,
    validate_annual_activation_recovery_audit_event,
)
from test_shared_storage_reader import ANNUAL_ID, _build_root, _write_bundle
from test_shared_storage_schema import _annual_bundle


FIXED_TIME = dt.datetime(2027, 2, 3, 4, 5, 6, 789012, tzinfo=dt.timezone.utc)
SOFTWARE = {
    "repository": "mousepenguin-yzh/liyutan-reservoir-estimator",
    "git_commit": "b" * 40,
    "app_version": "git-bbbbbbbbbbbb",
    "source_tree_dirty": False,
}
RECOVERY_ENV = {
    ENABLE_ANNUAL_DATA_WRITES_ENV: "1",
    ENABLE_ANNUAL_DATA_RECOVERY_ENV: "1",
}


@contextlib.contextmanager
def fake_lock(_path: Path):
    yield


def _remove_audits(root: Path) -> None:
    for path in (root / "audit" / "events").rglob("*.json"):
        path.unlink()


def _add_version(root: Path, version_id: str) -> Path:
    bundle = _annual_bundle(version_mutator=lambda value: value.update(version_id=version_id))
    target = root / "annual-data" / "versions" / version_id
    _write_bundle(target, bundle)
    return target


def _fingerprint(path: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (
                item.relative_to(path).as_posix(),
                hashlib.sha256(item.read_bytes()).hexdigest(),
            )
            for item in path.rglob("*")
            if item.is_file()
        )
    )


def _audit_files(root: Path) -> list[Path]:
    return sorted((root / "audit" / "events").rglob("*.json"))


def _recover(root: Path, **overrides):
    arguments = {
        "root": root,
        "observed_revision": 1,
        "observed_current_version_id": ANNUAL_ID,
        "observed_previous_version_id": None,
        "recovery_operator_display_name": "事後確認人",
        "recovery_note": "依完整 current 與 immutable evidence 補建",
        "recovery_software": SOFTWARE,
        "lock_factory": fake_lock,
        "occurred_at": FIXED_TIME,
        "event_uuid": "00000000-0000-0000-0000-000000000001",
        "audit_temp_uuid": "00000000-0000-0000-0000-000000000101",
        "hostname": "synthetic-host",
        "process_id": 4321,
    }
    arguments.update(overrides)
    return recover_annual_activation_audit(**arguments)


def _capability(root: Path):
    diagnostics = diagnose_annual_data(root)
    return annual_data_recovery_capability(
        load_shared_storage(root),
        shared_mode_enabled=True,
        diagnostics=diagnostics,
        environ=RECOVERY_ENV,
        platform="win32",
    )


def _activate(root: Path, target_id: str, revision: int, current_id: str):
    return activate_annual_data_version(
        root=root,
        target_version_id=target_id,
        observed_revision=revision,
        observed_current_version_id=current_id,
        operator_display_name="重新啟用操作人",
        note=f"重新啟用 {target_id}",
        software=SOFTWARE,
        lock_factory=fake_lock,
    )


def test_healthy_current_missing_audit_has_independent_recovery_capability(tmp_path):
    root = _build_root(tmp_path)
    _remove_audits(root)

    diagnostics = diagnose_annual_data(root)
    recovery = _capability(root)
    ordinary = annual_data_write_capability(
        load_shared_storage(root),
        shared_mode_enabled=True,
        diagnostics=diagnostics,
        environ={ENABLE_ANNUAL_DATA_WRITES_ENV: "1"},
        platform="win32",
    )

    assert diagnostics.current_status is CurrentStatus.HEALTHY
    assert diagnostics.current_audit_status is CurrentAuditStatus.MISSING
    assert recovery.available and recovery.audit_recovery_available
    assert not recovery.reactivation_available
    assert not ordinary.available


def test_recovery_audit_preserves_current_and_bundle_then_matches_recovery(tmp_path):
    root = _build_root(tmp_path)
    _remove_audits(root)
    current_path = root / "annual-data" / "current.json"
    version_path = root / "annual-data" / "versions" / ANNUAL_ID
    current_before = current_path.read_bytes()
    version_before = _fingerprint(version_path)

    recovered = _recover(root)

    assert current_path.read_bytes() == current_before
    assert _fingerprint(version_path) == version_before
    assert recovered.revision == 1
    assert recovered.current_version_id == ANNUAL_ID
    assert recovered.diagnostics.current_audit_status is CurrentAuditStatus.MATCHED_RECOVERY
    assert recovered.diagnostics.overall_severity is RecoverySeverity.HEALTHY
    event = validate_annual_activation_recovery_audit_event(
        deserialize_json(recovered.audit_path.read_bytes())
    )
    assert event["event_type"] == ANNUAL_ACTIVATION_RECOVERY_EVENT_TYPE
    assert event["result"] == "recovered_audit_evidence"


def test_recovery_event_cannot_masquerade_as_original_activation(tmp_path):
    root = _build_root(tmp_path)
    _remove_audits(root)
    event = _recover(root).audit_event

    assert event["event_type"] != ANNUAL_ACTIVATION_EVENT_TYPE
    assert "不是原始 activation event" in event["recovery_record_notice"]
    with pytest.raises(StorageValidationError):
        validate_annual_activation_audit_event(event)


def test_recovery_checksum_mismatch_is_not_accepted_as_matching_evidence(tmp_path):
    root = _build_root(tmp_path)
    _remove_audits(root)
    recovered = _recover(root)
    event = deserialize_json(recovered.audit_path.read_bytes())
    event["evidence"]["current_json_sha256"] = "0" * 64
    recovered.audit_path.write_bytes(serialize_json(event))

    diagnostics = diagnose_annual_data(root)

    assert diagnostics.current_audit_status is CurrentAuditStatus.UNINSPECTABLE
    assert diagnostics.overall_severity is RecoverySeverity.UNINSPECTABLE
    assert not _capability(root).available


def test_older_recovery_for_same_version_remains_valid_history(tmp_path):
    root = _build_root(tmp_path)
    _remove_audits(root)
    _recover(root)
    other_id = "annual-synthetic-other"
    _add_version(root, other_id)
    _activate(root, other_id, 1, ANNUAL_ID)
    _activate(root, ANNUAL_ID, 2, other_id)

    diagnostics = diagnose_annual_data(root)

    recovery_events = [
        item
        for item in diagnostics.audits
        if item.event is not None
        and item.event["event_type"] == ANNUAL_ACTIVATION_RECOVERY_EVENT_TYPE
    ]
    assert len(recovery_events) == 1
    assert recovery_events[0].status is AuditStatus.VALID_ANNUAL_ACTIVATION_RECOVERY
    assert diagnostics.current_audit_status is CurrentAuditStatus.MATCHED


def test_locked_revision_change_aborts_without_recovery_audit(tmp_path):
    root = _build_root(tmp_path)
    _remove_audits(root)

    @contextlib.contextmanager
    def changing_lock(_path):
        current_path = root / "annual-data" / "current.json"
        current = deserialize_json(current_path.read_bytes())
        current["revision"] = 2
        current_path.write_bytes(serialize_json(current))
        yield

    with pytest.raises(AnnualDataRecoveryConflictError, match="已改變"):
        _recover(root, lock_factory=changing_lock)
    assert _audit_files(root) == []


def test_existing_recovery_event_is_not_duplicated(tmp_path):
    root = _build_root(tmp_path)
    _remove_audits(root)
    _recover(root)
    before = _fingerprint(root / "audit")

    with pytest.raises(AnnualDataRecoveryConflictError):
        _recover(
            root,
            event_uuid="00000000-0000-0000-0000-000000000002",
            audit_temp_uuid="00000000-0000-0000-0000-000000000102",
        )
    assert _fingerprint(root / "audit") == before


def test_multiple_recovery_matches_are_ambiguous_and_disabled(tmp_path):
    root = _build_root(tmp_path)
    _remove_audits(root)
    first = _recover(root).audit_event
    second = copy.deepcopy(first)
    second["event_id"] = "00000000-0000-0000-0000-000000000002"
    validate_annual_activation_recovery_audit_event(second)
    duplicate = root / "audit" / "events" / "2027" / "02" / "duplicate.json"
    duplicate.write_bytes(serialize_json(second))

    diagnostics = diagnose_annual_data(root)

    assert diagnostics.current_audit_status is CurrentAuditStatus.AMBIGUOUS
    assert diagnostics.current_recovery_audit_match_count == 2
    assert not _capability(root).available


def test_original_plus_recovery_is_redundant_attention_not_recoverable(tmp_path):
    source = _build_root(tmp_path / "source")
    _remove_audits(source)
    recovery_event = _recover(source).audit_event
    root = _build_root(tmp_path / "target")
    _add_version(root, "annual-synthetic-redundant-target")
    path = root / "audit" / "events" / "2027" / "02"
    path.mkdir(parents=True)
    (path / "recovery.json").write_bytes(serialize_json(recovery_event))

    diagnostics = diagnose_annual_data(root)
    capability = _capability(root)

    assert diagnostics.current_audit_status is CurrentAuditStatus.REDUNDANT_EVIDENCE
    assert diagnostics.overall_severity is RecoverySeverity.ATTENTION
    assert capability.reactivation_available
    assert not capability.audit_recovery_available


@pytest.mark.parametrize("damage", ["invalid", "target_missing", "target_invalid"])
def test_broken_current_never_exposes_safe_recovery(tmp_path, damage):
    root = _build_root(tmp_path)
    current_path = root / "annual-data" / "current.json"
    if damage == "invalid":
        current_path.write_bytes(b"{")
    elif damage == "target_missing":
        current = deserialize_json(current_path.read_bytes())
        current["current_version_id"] = "annual-missing"
        current_path.write_bytes(serialize_json(current))
    else:
        target = root / "annual-data" / "versions" / ANNUAL_ID
        (target / "COMMITTED.json").unlink()

    assert not _capability(root).available


def test_valid_orphan_can_be_reactivated_without_mutating_immutable_versions(tmp_path):
    root = _build_root(tmp_path)
    orphan_id = "annual-synthetic-orphan"
    orphan_path = _add_version(root, orphan_id)
    current_version_path = root / "annual-data" / "versions" / ANNUAL_ID
    immutable_before = {
        ANNUAL_ID: _fingerprint(current_version_path),
        orphan_id: _fingerprint(orphan_path),
    }
    workspace = {"loaded_shared_annual_version_id": ANNUAL_ID}
    diagnostics = diagnose_annual_data(root)
    capability = _capability(root)

    assert capability.available and capability.reactivation_available
    assert [item.version_id for item in _eligible_reactivation_versions(diagnostics)] == [
        orphan_id
    ]
    activated = _activate(root, orphan_id, 1, ANNUAL_ID)

    assert activated.after_revision == 2
    assert activated.before_current_version_id == ANNUAL_ID
    assert _fingerprint(current_version_path) == immutable_before[ANNUAL_ID]
    assert _fingerprint(orphan_path) == immutable_before[orphan_id]
    assert workspace == {"loaded_shared_annual_version_id": ANNUAL_ID}


def test_valid_historical_can_be_selected_and_reactivated(tmp_path):
    root = _build_root(tmp_path)
    historical_id = "annual-synthetic-history"
    _add_version(root, historical_id)
    _activate(root, historical_id, 1, ANNUAL_ID)
    _activate(root, ANNUAL_ID, 2, historical_id)
    diagnostics = diagnose_annual_data(root)

    eligible = _eligible_reactivation_versions(diagnostics)
    assert [(item.version_id, item.status) for item in eligible] == [
        (historical_id, VersionStatus.HISTORICAL)
    ]
    activated = _activate(root, historical_id, 3, ANNUAL_ID)
    assert activated.after_revision == 4
    assert activated.before_current_version_id == ANNUAL_ID


def test_invalid_and_current_versions_are_not_reactivation_targets(tmp_path):
    root = _build_root(tmp_path)
    invalid = root / "annual-data" / "versions" / "annual-invalid"
    invalid.mkdir(parents=True)
    (invalid / "version.json").write_bytes(b"{")

    diagnostics = diagnose_annual_data(root)

    assert _eligible_reactivation_versions(diagnostics) == ()
    assert not _capability(root).available


def test_reactivation_revision_conflict_is_propagated_once_without_retry():
    calls = []

    def conflict(**arguments):
        calls.append(arguments)
        raise AnnualDataActivationConflictError("revision_conflict", "changed")

    service = AnnualDataMaintenanceService(activator=conflict, platform="linux")
    with pytest.raises(AnnualDataActivationConflictError):
        service.activate(observed_revision=3)
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, False),
        ({ENABLE_ANNUAL_DATA_RECOVERY_ENV: "0"}, False),
        ({ENABLE_ANNUAL_DATA_RECOVERY_ENV: "true"}, False),
        ({ENABLE_ANNUAL_DATA_RECOVERY_ENV: " 1 "}, False),
        ({ENABLE_ANNUAL_DATA_RECOVERY_ENV: "1"}, True),
    ],
)
def test_recovery_flag_requires_exact_one(environment, expected):
    assert annual_data_recovery_enabled(environment) is expected


@pytest.mark.parametrize(
    "environment",
    [
        {ENABLE_ANNUAL_DATA_RECOVERY_ENV: "1"},
        {ENABLE_ANNUAL_DATA_WRITES_ENV: "1"},
        {},
    ],
)
def test_recovery_requires_both_flags_and_shared_mode(tmp_path, environment):
    root = _build_root(tmp_path)
    _remove_audits(root)
    result = load_shared_storage(root)
    diagnostics = diagnose_annual_data(root)

    assert not annual_data_recovery_capability(
        result,
        shared_mode_enabled=True,
        diagnostics=diagnostics,
        environ=environment,
        platform="win32",
    ).available
    assert not annual_data_recovery_capability(
        result,
        shared_mode_enabled=False,
        diagnostics=diagnostics,
        environ=RECOVERY_ENV,
        platform="win32",
    ).available
