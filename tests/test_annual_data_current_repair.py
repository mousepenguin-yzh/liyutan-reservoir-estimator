import base64
import contextlib
import copy
import datetime as dt
import hashlib
import shutil
from pathlib import Path

import pytest

from annual_data_activation import activate_annual_data_version, fault_at
from annual_data_current_repair import (
    AnnualCurrentRepairConflictError,
    AnnualCurrentRepairRecoveryRequiredError,
    CurrentRepairAction,
    complete_annual_current_repair_audit,
    plan_annual_current_repair,
    repair_annual_current,
)
from annual_data_diagnostics import (
    AuditStatus,
    CurrentAuditStatus,
    CurrentStatus,
    RecoverySeverity,
    diagnose_annual_data,
)
from annual_data_maintenance import (
    ENABLE_ANNUAL_DATA_RECOVERY_ENV,
    ENABLE_ANNUAL_DATA_WRITES_ENV,
    annual_data_recovery_capability,
)
from shared_storage_reader import load_shared_storage
from shared_storage_schema import (
    ANNUAL_ACTIVATION_EVENT_TYPE,
    ANNUAL_CURRENT_REPAIR_EVENT_TYPE,
    AUDIT_EVENT_SCHEMA,
    SCHEMA_VERSION,
    StorageValidationError,
    deserialize_json,
    serialize_json,
    validate_annual_activation_audit_event,
    validate_annual_current_repair_audit_event,
)
from test_shared_storage_reader import ANNUAL_ID, _build_root, _write_bundle
from test_shared_storage_schema import _annual_bundle


VERSION_B = "annual-synthetic-b"
FIXED_TIME = dt.datetime(2027, 3, 4, 5, 6, 7, 890123, tzinfo=dt.timezone.utc)
SOFTWARE = {
    "repository": "mousepenguin-yzh/liyutan-reservoir-estimator",
    "git_commit": "c" * 40,
    "app_version": "git-cccccccccccc",
    "source_tree_dirty": False,
}


@contextlib.contextmanager
def fake_lock(_path: Path):
    yield


def _remove_audits(root: Path) -> None:
    for path in (root / "audit" / "events").rglob("*.json"):
        path.unlink()


def _malform_activation_audit(root: Path, *, preserve_valid: bool = False) -> Path:
    source = next((root / "audit" / "events").rglob("*.json"))
    event = deserialize_json(source.read_bytes())
    assert event["event_type"] == ANNUAL_ACTIVATION_EVENT_TYPE
    del event["operator_display_name"]
    target = source.parent / "malformed-activation.json" if preserve_valid else source
    target.write_bytes(serialize_json(event))
    return target


def _add_version(root: Path, version_id: str) -> Path:
    target = root / "annual-data" / "versions" / version_id
    _write_bundle(
        target,
        _annual_bundle(version_mutator=lambda value: value.update(version_id=version_id)),
    )
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


def _repair(root: Path, plan, target_id: str, **overrides):
    arguments = {
        "root": root,
        "observed_plan_token": plan.observed_token,
        "repair_kind": plan.action.value,
        "target_version_id": target_id,
        "recovery_operator_display_name": "修復操作人",
        "recovery_note": "依畫面完整 evidence 執行受控修復",
        "recovery_software": SOFTWARE,
        "lock_factory": fake_lock,
        "occurred_at": FIXED_TIME,
        "event_uuid": "00000000-0000-0000-0000-000000000201",
        "current_temp_uuid": "00000000-0000-0000-0000-000000000202",
        "audit_temp_uuid": "00000000-0000-0000-0000-000000000203",
        "hostname": "repair-host",
        "process_id": 8765,
    }
    arguments.update(overrides)
    return repair_annual_current(**arguments)


def _activate(root: Path, target: str, revision: int, current_id: str | None):
    return activate_annual_data_version(
        root=root,
        target_version_id=target,
        observed_revision=revision,
        observed_current_version_id=current_id,
        operator_display_name="切換操作人",
        note=f"切換至 {target}",
        software=SOFTWARE,
        lock_factory=fake_lock,
    )


def test_empty_inventory_remains_first_version_create_state(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    shutil.rmtree(root / "annual-data" / "versions")
    _remove_audits(root)

    diagnostics = diagnose_annual_data(root)
    plan = plan_annual_current_repair(diagnostics)

    assert diagnostics.is_first_version_state
    assert diagnostics.overall_severity is RecoverySeverity.HEALTHY
    assert not plan.available


def test_first_current_initialization_uses_normal_activation_and_preserves_bundle(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    _remove_audits(root)
    version_path = root / "annual-data" / "versions" / ANNUAL_ID
    before = _fingerprint(version_path)
    diagnostics = diagnose_annual_data(root)
    plan = plan_annual_current_repair(diagnostics)

    assert diagnostics.is_first_current_initialization_state
    assert diagnostics.overall_severity is RecoverySeverity.INITIALIZATION_REQUIRED
    assert plan.action is CurrentRepairAction.FIRST_CURRENT_INITIALIZATION
    activated = activate_annual_data_version(
        root=root,
        target_version_id=ANNUAL_ID,
        observed_revision=0,
        observed_current_version_id=None,
        operator_display_name="首次設定人",
        note="設定第一個正式年度版本",
        software=SOFTWARE,
        lock_factory=fake_lock,
        first_current_initialization=True,
    )

    assert activated.after_revision == 1
    assert activated.before_current_version_id is None
    assert activated.audit_event["event_type"] == ANNUAL_ACTIVATION_EVENT_TYPE
    assert validate_annual_activation_audit_event(activated.audit_event)
    assert _fingerprint(version_path) == before


def test_multiple_first_current_orphans_require_explicit_target_without_recommendation(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    _remove_audits(root)
    _add_version(root, VERSION_B)

    plan = plan_annual_current_repair(diagnose_annual_data(root))

    assert plan.first_current_initialization_available
    assert plan.target_version_ids == tuple(sorted((ANNUAL_ID, VERSION_B)))
    assert plan.recommended_target_version_id is None


def test_existing_activation_history_is_never_first_current_initialization(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()

    diagnostics = diagnose_annual_data(root)
    plan = plan_annual_current_repair(diagnostics)

    assert not diagnostics.is_first_current_initialization_state
    assert plan.action is CurrentRepairAction.RECONSTRUCT_MISSING_CURRENT


def test_malformed_activation_audit_blocks_first_current_initialization(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    malformed_path = _malform_activation_audit(root)

    diagnostics = diagnose_annual_data(root)
    plan = plan_annual_current_repair(diagnostics)
    audit = next(item for item in diagnostics.audits if item.path == malformed_path)

    assert audit.status is AuditStatus.INVALID
    assert audit.event is not None
    assert audit.event["event_type"] == ANNUAL_ACTIVATION_EVENT_TYPE
    assert audit.failure_reason
    assert diagnostics.has_untrusted_annual_audit_evidence
    assert not diagnostics.is_first_current_initialization_state
    assert not plan.available
    assert plan.action is CurrentRepairAction.NONE
    assert "annual audit evidence 無法可靠驗證" in plan.reason


def test_malformed_activation_audit_blocks_invalid_current_reconstruction(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").write_bytes(b"{")
    _malform_activation_audit(root, preserve_valid=True)

    diagnostics = diagnose_annual_data(root)
    plan = plan_annual_current_repair(diagnostics)

    assert diagnostics.current_status is CurrentStatus.CURRENT_INVALID
    assert diagnostics.has_untrusted_annual_audit_evidence
    assert any(item.status is AuditStatus.VALID_ANNUAL_ACTIVATION for item in diagnostics.audits)
    assert not plan.available
    assert plan.action is CurrentRepairAction.NONE
    assert "annual audit evidence 無法可靠驗證" in plan.reason


@pytest.mark.parametrize(
    ("environment", "platform"),
    [
        ({ENABLE_ANNUAL_DATA_WRITES_ENV: "1"}, "win32"),
        ({ENABLE_ANNUAL_DATA_RECOVERY_ENV: "1"}, "win32"),
        (
            {
                ENABLE_ANNUAL_DATA_WRITES_ENV: "1",
                ENABLE_ANNUAL_DATA_RECOVERY_ENV: "1",
            },
            "linux",
        ),
    ],
)
def test_current_repair_capability_requires_both_flags_and_windows(
    tmp_path, environment, platform
):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").write_bytes(b"{")
    diagnostics = diagnose_annual_data(root)

    capability = annual_data_recovery_capability(
        load_shared_storage(root),
        shared_mode_enabled=True,
        diagnostics=diagnostics,
        environ=environment,
        platform=platform,
    )

    assert not capability.available


def test_missing_current_reconstructs_unique_latest_without_revision_increment(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    plan = plan_annual_current_repair(diagnose_annual_data(root))

    repaired = _repair(root, plan, ANNUAL_ID)

    assert repaired.before_revision == repaired.after_revision == 1
    assert repaired.current["current_version_id"] == ANNUAL_ID
    assert repaired.current["previous_version_id"] is None
    assert repaired.audit_event["repair_kind"] == "reconstruct_missing_current"
    assert repaired.diagnostics.current_status is CurrentStatus.HEALTHY
    assert repaired.diagnostics.current_audit_status is CurrentAuditStatus.MATCHED_REPAIR


def test_reconstruction_derives_later_revision_current_and_previous_from_chain(tmp_path):
    root = _build_root(tmp_path)
    _add_version(root, VERSION_B)
    _activate(root, VERSION_B, 1, ANNUAL_ID)
    _activate(root, ANNUAL_ID, 2, VERSION_B)
    (root / "annual-data" / "current.json").unlink()
    plan = plan_annual_current_repair(diagnose_annual_data(root))

    assert plan.reconstructed_revision == 3
    assert plan.reconstructed_current_version_id == ANNUAL_ID
    assert plan.reconstructed_previous_version_id == VERSION_B
    repaired = _repair(root, plan, ANNUAL_ID)

    assert repaired.before_revision == repaired.after_revision == 3
    assert repaired.current["current_version_id"] == ANNUAL_ID
    assert repaired.current["previous_version_id"] == VERSION_B


def test_invalid_current_reconstruction_preserves_exact_original_bytes_in_audit(tmp_path):
    root = _build_root(tmp_path)
    current_path = root / "annual-data" / "current.json"
    broken = b"{not valid json\x00\xff"
    current_path.write_bytes(broken)
    plan = plan_annual_current_repair(diagnose_annual_data(root))

    repaired = _repair(root, plan, ANNUAL_ID)
    event = validate_annual_current_repair_audit_event(
        deserialize_json(repaired.audit_path.read_bytes())
    )

    assert event["event_type"] == ANNUAL_CURRENT_REPAIR_EVENT_TYPE
    assert event["repair_kind"] == "reconstruct_invalid_current"
    assert event["pre_repair_current_evidence"]["raw_bytes_sha256"] == hashlib.sha256(broken).hexdigest()
    assert base64.b64decode(event["pre_repair_current_evidence"]["raw_bytes_base64"]) == broken
    with pytest.raises(StorageValidationError):
        validate_annual_activation_audit_event(event)


def test_ambiguous_latest_audit_disables_reconstruction(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    _add_version(root, VERSION_B)
    original = deserialize_json(next((root / "audit" / "events").rglob("*.json")).read_bytes())
    conflicting = copy.deepcopy(original)
    conflicting["event_id"] = "synthetic-conflicting-activation"
    conflicting["annual_target_version_id"] = VERSION_B
    conflicting["after_current_version_id"] = VERSION_B
    validate_annual_activation_audit_event(conflicting)
    conflict_path = root / "audit" / "events" / "2026" / "12" / "conflict.json"
    conflict_path.write_bytes(serialize_json(conflicting))

    plan = plan_annual_current_repair(diagnose_annual_data(root))

    assert not plan.available
    assert "多個可能狀態" in plan.reason


def test_reconstruction_is_disabled_when_inferred_target_bundle_is_invalid(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    (root / "annual-data" / "versions" / ANNUAL_ID / "COMMITTED.json").unlink()

    plan = plan_annual_current_repair(diagnose_annual_data(root))

    assert not plan.available
    assert "target bundle" in plan.reason


def _revision_three_broken_target(root: Path, *, corrupt: bool = False):
    target_a = root / "annual-data" / "versions" / ANNUAL_ID
    target_b = _add_version(root, VERSION_B)
    _activate(root, VERSION_B, 1, ANNUAL_ID)
    _activate(root, ANNUAL_ID, 2, VERSION_B)
    if corrupt:
        (target_a / "hydrology_q.csv").write_bytes(
            (target_a / "hydrology_q.csv").read_bytes() + b"\ncorrupt"
        )
    else:
        shutil.rmtree(target_a)
    return target_a, target_b


def test_broken_target_switch_increments_revision_and_uses_broken_id_as_previous(tmp_path):
    root = _build_root(tmp_path)
    _revision_three_broken_target(root)
    diagnostics = diagnose_annual_data(root)
    plan = plan_annual_current_repair(diagnostics)

    assert diagnostics.current_status is CurrentStatus.CURRENT_TARGET_MISSING
    assert diagnostics.revision == 3
    assert plan.action is CurrentRepairAction.SWITCH_FROM_MISSING_TARGET
    assert plan.recommended_target_version_id == VERSION_B
    repaired = _repair(root, plan, VERSION_B)

    assert repaired.before_revision == 3
    assert repaired.after_revision == 4
    assert repaired.current["current_version_id"] == VERSION_B
    assert repaired.current["previous_version_id"] == ANNUAL_ID


def test_corrupt_broken_bundle_is_never_modified_by_switch(tmp_path):
    root = _build_root(tmp_path)
    target_a, _ = _revision_three_broken_target(root, corrupt=True)
    broken_before = _fingerprint(target_a)
    plan = plan_annual_current_repair(diagnose_annual_data(root))

    repaired = _repair(root, plan, VERSION_B)

    assert repaired.after_revision == 4
    assert _fingerprint(target_a) == broken_before


def test_lock_time_state_change_aborts_without_writing(tmp_path):
    root = _build_root(tmp_path)
    _revision_three_broken_target(root)
    plan = plan_annual_current_repair(diagnose_annual_data(root))
    current_path = root / "annual-data" / "current.json"

    @contextlib.contextmanager
    def changing_lock(_path):
        current = deserialize_json(current_path.read_bytes())
        current["revision"] = 4
        current_path.write_bytes(serialize_json(current))
        changed = current_path.read_bytes()
        yield
        assert current_path.read_bytes() == changed

    with pytest.raises(AnnualCurrentRepairConflictError):
        _repair(root, plan, VERSION_B, lock_factory=changing_lock)


def test_target_change_after_plan_aborts_without_current_write(tmp_path):
    root = _build_root(tmp_path)
    _revision_three_broken_target(root)
    plan = plan_annual_current_repair(diagnose_annual_data(root))
    current_path = root / "annual-data" / "current.json"
    current_before = current_path.read_bytes()

    @contextlib.contextmanager
    def changing_target_lock(_path):
        target_file = root / "annual-data" / "versions" / VERSION_B / "hydrology_q.csv"
        target_file.write_bytes(target_file.read_bytes() + b"\nchanged")
        yield

    with pytest.raises(AnnualCurrentRepairConflictError):
        _repair(root, plan, VERSION_B, lock_factory=changing_target_lock)
    assert current_path.read_bytes() == current_before


@pytest.mark.parametrize("stage", ["after_current_temp_bytes", "before_current_replace"])
def test_atomic_interruptions_before_replace_keep_broken_current_bytes(tmp_path, stage):
    root = _build_root(tmp_path)
    current_path = root / "annual-data" / "current.json"
    broken = b"{broken"
    current_path.write_bytes(broken)
    plan = plan_annual_current_repair(diagnose_annual_data(root))

    with pytest.raises(Exception):
        _repair(root, plan, ANNUAL_ID, fault_injector=fault_at(stage))
    assert current_path.read_bytes() == broken


def test_current_success_audit_failure_is_rediscovered_and_completed_without_repair_retry(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    plan = plan_annual_current_repair(diagnose_annual_data(root))

    with pytest.raises(AnnualCurrentRepairRecoveryRequiredError) as caught:
        _repair(root, plan, ANNUAL_ID, fault_injector=fault_at("before_repair_audit_publish"))
    assert caught.value.current_repaired
    current_after = (root / "annual-data" / "current.json").read_bytes()

    partial = diagnose_annual_data(root)
    completion_plan = plan_annual_current_repair(partial)
    assert partial.current_status is CurrentStatus.HEALTHY
    assert partial.current_audit_status is CurrentAuditStatus.REPAIR_EVIDENCE_INCOMPLETE
    assert partial.overall_severity is RecoverySeverity.RECOVERY_REQUIRED
    assert completion_plan.repair_audit_completion_available

    completed = complete_annual_current_repair_audit(
        root=root,
        observed_plan_token=completion_plan.observed_token,
        lock_factory=fake_lock,
    )

    assert (root / "annual-data" / "current.json").read_bytes() == current_after
    assert completed.diagnostics.current_audit_status is CurrentAuditStatus.MATCHED_REPAIR
    assert not completed.diagnostics.pending_current_repair_events
