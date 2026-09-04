import contextlib
import datetime as dt
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import annual_data_activation as activation_module
from annual_data_activation import (
    AnnualDataActivationConflictError,
    AnnualDataActivationError,
    AnnualDataActivationRecoveryRequiredError,
    AnnualDataAlreadyCurrentError,
    InjectedAnnualDataActivationFault,
    WindowsSMBExclusiveLock,
    activate_annual_data_version,
    fault_at,
)
from shared_storage_schema import (
    ANNUAL_CURRENT_SCHEMA,
    ANNUAL_VERSION_SCHEMA,
    SCHEMA_VERSION,
    SHARED_ROOT_SCHEMA,
    StorageValidationError,
    deserialize_json,
    serialize_json,
    sha256_bytes,
    validate_annual_activation_audit_event,
    validate_annual_current,
)
from test_shared_storage_schema import _annual_bundle


FIXED_TIME = dt.datetime(2027, 1, 2, 3, 4, 5, 678901, tzinfo=dt.timezone.utc)
VERSION_A = "annual-synthetic-a"
VERSION_B = "annual-synthetic-b"
SOFTWARE = {
    "repository": "liyutan-reservoir-estimator",
    "git_commit": "a" * 40,
    "app_version": "synthetic-test",
    "source_tree_dirty": False,
}


@contextlib.contextmanager
def fake_lock(_path: Path):
    yield


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "shared-root"
    root.mkdir(parents=True)
    (root / "system.json").write_bytes(
        serialize_json(
            {
                "schema": SHARED_ROOT_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "reservoir_id": "liyutan",
                "display_name": "鯉魚潭水庫",
            }
        )
    )
    return root


def _write_version(root: Path, version_id: str) -> Path:
    bundle = _annual_bundle(version_mutator=lambda value: value.update(version_id=version_id))
    version_path = root / "annual-data" / "versions" / version_id
    for name, data in bundle.items():
        path = version_path / Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return version_path


def _write_current(root: Path, revision: int, current_id: str, previous_id: str | None = None) -> bytes:
    data = serialize_json(
        {
            "schema": ANNUAL_CURRENT_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "revision": revision,
            "current_version_id": current_id,
            "previous_version_id": previous_id,
            "updated_at": "2027-01-01T00:00:00Z",
            "operator_display_name": "先前操作人",
        }
    )
    path = root / "annual-data" / "current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _checksums(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): sha256_bytes(item.read_bytes())
        for item in path.rglob("*")
        if item.is_file()
    }


def _activate(root: Path, target: str, revision: int, current_id: str | None, **overrides):
    arguments = {
        "root": root,
        "target_version_id": target,
        "observed_revision": revision,
        "observed_current_version_id": current_id,
        "operator_display_name": "人工宣告操作人",
        "note": f"啟用合成版本 {target}",
        "software": SOFTWARE,
        "lock_factory": fake_lock,
        "occurred_at": FIXED_TIME,
        "event_uuid": uuid.uuid4(),
        "current_temp_uuid": uuid.uuid4(),
        "audit_temp_uuid": uuid.uuid4(),
        "hostname": "synthetic-host",
        "process_id": 1234,
    }
    arguments.update(overrides)
    return activate_annual_data_version(**arguments)


def _audit_files(root: Path) -> list[Path]:
    audit_root = root / "audit" / "events"
    return sorted(audit_root.rglob("*.json")) if audit_root.exists() else []


def test_first_activation_treats_missing_current_as_revision_zero(tmp_path):
    root = _root(tmp_path)
    target_path = _write_version(root, VERSION_A)
    before = _checksums(target_path)

    result = _activate(root, VERSION_A, 0, None)

    current = validate_annual_current(deserialize_json(result.current_path.read_bytes()))
    assert result.status == "activated"
    assert current["revision"] == 1
    assert current["current_version_id"] == VERSION_A
    assert current["previous_version_id"] is None
    assert _checksums(target_path) == before
    assert result.audit_path == _audit_files(root)[0]
    assert validate_annual_activation_audit_event(
        deserialize_json(result.audit_path.read_bytes())
    ) == result.audit_event

    invalid_event = {**result.audit_event, "after_revision": 99}
    with pytest.raises(StorageValidationError, match=r"before_revision \+ 1"):
        validate_annual_activation_audit_event(invalid_event)


def test_second_activation_and_switch_back_increment_history_without_mutating_versions(tmp_path):
    root = _root(tmp_path)
    path_a = _write_version(root, VERSION_A)
    path_b = _write_version(root, VERSION_B)
    immutable_before = {VERSION_A: _checksums(path_a), VERSION_B: _checksums(path_b)}

    first = _activate(root, VERSION_A, 0, None)
    second = _activate(root, VERSION_B, 1, VERSION_A)
    third = _activate(root, VERSION_A, 2, VERSION_B)

    assert first.after_revision == 1
    assert second.after_revision == 2
    assert second.before_current_version_id == VERSION_A
    assert second.current["previous_version_id"] == VERSION_A
    assert third.after_revision == 3
    assert third.current["current_version_id"] == VERSION_A
    assert third.current["previous_version_id"] == VERSION_B
    assert _checksums(path_a) == immutable_before[VERSION_A]
    assert _checksums(path_b) == immutable_before[VERSION_B]
    assert len(_audit_files(root)) == 3


def test_observed_revision_conflict_keeps_current_and_target_unchanged(tmp_path):
    root = _root(tmp_path)
    _write_version(root, VERSION_A)
    target = _write_version(root, VERSION_B)
    current_before = _write_current(root, 1, VERSION_A)
    target_before = _checksums(target)

    with pytest.raises(AnnualDataActivationConflictError) as caught:
        _activate(root, VERSION_B, 0, None)

    assert caught.value.code == "revision_conflict"
    assert (root / "annual-data" / "current.json").read_bytes() == current_before
    assert _checksums(target) == target_before
    assert not _audit_files(root)


def test_observed_current_id_conflict_is_rejected_even_when_revision_matches(tmp_path):
    root = _root(tmp_path)
    _write_version(root, VERSION_A)
    _write_version(root, VERSION_B)
    current_before = _write_current(root, 7, VERSION_A)

    with pytest.raises(AnnualDataActivationConflictError) as caught:
        _activate(root, VERSION_A, 7, VERSION_B)

    assert caught.value.code == "revision_conflict"
    assert (root / "annual-data" / "current.json").read_bytes() == current_before


def test_current_appearance_and_disappearance_are_conflicts(tmp_path):
    root = _root(tmp_path)
    _write_version(root, VERSION_A)
    current_path = root / "annual-data" / "current.json"

    def appear_on_lock(_path):
        @contextlib.contextmanager
        def lock():
            _write_current(root, 1, VERSION_A)
            yield

        return lock()

    with pytest.raises(AnnualDataActivationConflictError):
        _activate(root, VERSION_A, 0, None, lock_factory=appear_on_lock)

    def disappear_on_lock(_path):
        @contextlib.contextmanager
        def lock():
            current_path.unlink()
            yield

        return lock()

    with pytest.raises(AnnualDataActivationConflictError):
        _activate(root, VERSION_A, 1, VERSION_A, lock_factory=disappear_on_lock)


def test_already_current_is_noop_without_revision_or_audit(tmp_path):
    root = _root(tmp_path)
    _write_version(root, VERSION_A)
    current_before = _write_current(root, 4, VERSION_A, VERSION_B)

    with pytest.raises(AnnualDataAlreadyCurrentError) as caught:
        _activate(root, VERSION_A, 4, VERSION_A)

    assert caught.value.code == "already_current"
    assert (root / "annual-data" / "current.json").read_bytes() == current_before
    assert not _audit_files(root)


@pytest.mark.parametrize("damage", ["missing", "missing_committed", "checksum", "schema"])
def test_invalid_target_versions_are_never_activated(tmp_path, damage):
    root = _root(tmp_path)
    target = root / "annual-data" / "versions" / VERSION_A
    if damage != "missing":
        target = _write_version(root, VERSION_A)
    if damage == "missing_committed":
        (target / "COMMITTED.json").unlink()
    elif damage == "checksum":
        (target / "hydrology_q.csv").write_bytes(b"corrupt")
    elif damage == "schema":
        version_path = target / "version.json"
        version = deserialize_json(version_path.read_bytes())
        version["schema"] = "wrong/schema"
        version_bytes = serialize_json(version)
        version_path.write_bytes(version_bytes)
        committed_path = target / "COMMITTED.json"
        committed = deserialize_json(committed_path.read_bytes())
        committed["manifest_sha256"] = sha256_bytes(version_bytes)
        committed_path.write_bytes(serialize_json(committed))

    with pytest.raises(AnnualDataActivationError) as caught:
        _activate(root, VERSION_A, 0, None)

    assert caught.value.code in {"target_not_found", "target_invalid"}
    assert not (root / "annual-data" / "current.json").exists()
    assert not _audit_files(root)


def test_root_system_reservoir_and_target_path_must_be_valid(tmp_path):
    missing_root = tmp_path / "missing"
    with pytest.raises(AnnualDataActivationError) as missing:
        _activate(missing_root, VERSION_A, 0, None)
    assert missing.value.code == "root_not_found"
    assert not missing_root.exists()

    root = _root(tmp_path / "system")
    (root / "system.json").unlink()
    with pytest.raises(AnnualDataActivationError) as system_missing:
        _activate(root, VERSION_A, 0, None)
    assert system_missing.value.code == "system_missing"

    (root / "system.json").write_bytes(
        serialize_json(
            {
                "schema": SHARED_ROOT_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "reservoir_id": "other-reservoir",
                "display_name": "錯誤水庫",
            }
        )
    )
    with pytest.raises(AnnualDataActivationError) as wrong_reservoir:
        _activate(root, VERSION_A, 0, None)
    assert wrong_reservoir.value.code == "system_invalid"

    valid_root = _root(tmp_path / "unsafe-target")
    with pytest.raises(AnnualDataActivationError) as unsafe_target:
        _activate(valid_root, "../staging", 0, None)
    assert unsafe_target.value.code == "invalid_activation_input"
    assert not (valid_root / "annual-data" / "current.json").exists()


def test_target_change_while_waiting_is_rejected(tmp_path):
    root = _root(tmp_path)
    target = _write_version(root, VERSION_A)

    def mutate_on_lock(_path):
        @contextlib.contextmanager
        def lock():
            (target / "hydrology_q.csv").write_bytes(b"changed while waiting")
            yield

        return lock()

    with pytest.raises(AnnualDataActivationError) as caught:
        _activate(root, VERSION_A, 0, None, lock_factory=mutate_on_lock)

    assert caught.value.code == "target_invalid"
    assert not (root / "annual-data" / "current.json").exists()
    assert not _audit_files(root)


def test_corrupt_existing_current_blocks_activation_without_guessing(tmp_path):
    root = _root(tmp_path)
    _write_version(root, VERSION_A)
    current_path = root / "annual-data" / "current.json"
    current_path.write_bytes(b"not json")

    with pytest.raises(AnnualDataActivationError) as caught:
        _activate(root, VERSION_A, 0, None)

    assert caught.value.code == "current_invalid"
    assert current_path.read_bytes() == b"not json"
    assert not _audit_files(root)


def test_lock_timeout_does_not_change_current_or_create_local_fallback(tmp_path):
    root = _root(tmp_path)
    _write_version(root, VERSION_A)
    current_before = _write_current(root, 1, VERSION_A)
    _write_version(root, VERSION_B)

    def timeout(_path):
        raise AnnualDataActivationError("lock_timeout", "synthetic busy lock")

    with pytest.raises(AnnualDataActivationError) as caught:
        _activate(root, VERSION_B, 1, VERSION_A, lock_factory=timeout)

    assert caught.value.code == "lock_timeout"
    assert (root / "annual-data" / "current.json").read_bytes() == current_before
    assert not (tmp_path / "annual-data" / "current.json").exists()
    assert not _audit_files(root)


@pytest.mark.parametrize("stage", ["after_current_temp_bytes", "before_current_replace"])
def test_interruptions_before_current_replace_keep_old_current_valid(tmp_path, stage):
    root = _root(tmp_path)
    _write_version(root, VERSION_A)
    _write_version(root, VERSION_B)
    current_before = _write_current(root, 1, VERSION_A)

    with pytest.raises(InjectedAnnualDataActivationFault):
        _activate(root, VERSION_B, 1, VERSION_A, fault_injector=fault_at(stage))

    assert (root / "annual-data" / "current.json").read_bytes() == current_before
    assert validate_annual_current(deserialize_json(current_before))["current_version_id"] == VERSION_A
    assert not _audit_files(root)


def test_atomic_replace_failure_is_not_reported_as_success(monkeypatch, tmp_path):
    root = _root(tmp_path)
    _write_version(root, VERSION_A)
    _write_version(root, VERSION_B)
    current_before = _write_current(root, 1, VERSION_A)

    def fail_replace(_source, _destination):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(activation_module.os, "replace", fail_replace)
    with pytest.raises(AnnualDataActivationError) as caught:
        _activate(root, VERSION_B, 1, VERSION_A)

    assert caught.value.code == "current_replace_failed"
    assert (root / "annual-data" / "current.json").read_bytes() == current_before
    assert not _audit_files(root)


def test_interruption_after_replace_leaves_complete_new_current_and_requires_recovery(tmp_path):
    root = _root(tmp_path)
    _write_version(root, VERSION_A)
    _write_version(root, VERSION_B)
    _write_current(root, 1, VERSION_A)

    with pytest.raises(AnnualDataActivationRecoveryRequiredError) as caught:
        _activate(
            root,
            VERSION_B,
            1,
            VERSION_A,
            fault_injector=fault_at("after_current_replace"),
        )

    assert caught.value.code == "current_switched_audit_incomplete"
    current = validate_annual_current(
        deserialize_json((root / "annual-data" / "current.json").read_bytes())
    )
    assert current["revision"] == 2
    assert current["current_version_id"] == VERSION_B
    assert not _audit_files(root)


def test_audit_failure_after_switch_never_rolls_back_and_is_not_success(tmp_path):
    root = _root(tmp_path)
    _write_version(root, VERSION_A)
    _write_version(root, VERSION_B)
    _write_current(root, 1, VERSION_A)

    with pytest.raises(AnnualDataActivationRecoveryRequiredError) as caught:
        _activate(
            root,
            VERSION_B,
            1,
            VERSION_A,
            fault_injector=fault_at("before_audit_rename"),
        )

    assert caught.value.current_switched is True
    assert caught.value.current["current_version_id"] == VERSION_B
    current = validate_annual_current(
        deserialize_json((root / "annual-data" / "current.json").read_bytes())
    )
    assert current["revision"] == 2
    assert current["current_version_id"] == VERSION_B
    assert current["previous_version_id"] == VERSION_A
    assert not _audit_files(root)


def test_audit_filenames_are_unique_valid_and_never_overwritten(tmp_path):
    root = _root(tmp_path)
    _write_version(root, VERSION_A)
    _write_version(root, VERSION_B)
    fixed_event = uuid.UUID("11111111-2222-4333-8444-555555555555")

    first = _activate(root, VERSION_A, 0, None, event_uuid=fixed_event)
    original_event_bytes = first.audit_path.read_bytes()
    with pytest.raises(AnnualDataActivationError) as caught:
        _activate(root, VERSION_B, 1, VERSION_A, event_uuid=fixed_event)

    assert caught.value.code == "audit_event_exists"
    assert first.audit_path.read_bytes() == original_event_bytes
    current = validate_annual_current(
        deserialize_json((root / "annual-data" / "current.json").read_bytes())
    )
    assert current["revision"] == 1
    assert current["current_version_id"] == VERSION_A
    assert len(_audit_files(root)) == 1
    assert first.audit_path.name.endswith(f"_{fixed_event}.json")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("operator_display_name", " ", "operator_required"),
        ("note", "", "note_required"),
    ],
)
def test_operator_and_activation_note_are_required(tmp_path, field, value, code):
    root = _root(tmp_path)
    _write_version(root, VERSION_A)

    with pytest.raises(AnnualDataActivationError) as caught:
        _activate(root, VERSION_A, 0, None, **{field: value})

    assert caught.value.code == code
    assert not (root / "annual-data" / "current.json").exists()


def test_fake_lock_allows_only_one_concurrent_critical_section(tmp_path):
    root = _root(tmp_path)
    _write_version(root, VERSION_A)
    _write_version(root, VERSION_B)
    mutex = threading.Lock()
    count_guard = threading.Lock()
    state = {"active": 0, "maximum": 0}

    def tracked_lock(_path):
        @contextlib.contextmanager
        def held():
            with mutex:
                with count_guard:
                    state["active"] += 1
                    state["maximum"] = max(state["maximum"], state["active"])
                try:
                    yield
                finally:
                    with count_guard:
                        state["active"] -= 1

        return held()

    def run(target):
        try:
            return _activate(root, target, 0, None, lock_factory=tracked_lock).status
        except AnnualDataActivationConflictError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(run, (VERSION_A, VERSION_B)))

    assert sorted(outcomes) == ["activated", "revision_conflict"]
    assert state["maximum"] == 1
    current = validate_annual_current(
        deserialize_json((root / "annual-data" / "current.json").read_bytes())
    )
    assert current["revision"] == 1
    assert current["current_version_id"] in {VERSION_A, VERSION_B}
    assert len(_audit_files(root)) == 1


def test_windows_lock_retries_with_injected_jitter_without_real_sleep(monkeypatch, tmp_path):
    monkeypatch.setattr(activation_module.sys, "platform", "win32")
    current = [0.0]
    delays = []

    class FakeWindowsLock(WindowsSMBExclusiveLock):
        attempts = 0

        def _try_open(self):
            self.attempts += 1
            if self.attempts < 3:
                return None, 32, lambda _handle: True
            return 99, 0, lambda _handle: True

    def monotonic():
        return current[0]

    def sleep(delay):
        delays.append(delay)
        current[0] += delay

    lock = FakeWindowsLock(
        tmp_path / "annual-current.lock",
        monotonic=monotonic,
        sleep=sleep,
        random_uniform=lambda low, high: 0.3,
    )
    with lock:
        pass

    assert lock.attempts == 3
    assert delays == [0.3, 0.3]
    assert all(0.2 <= delay <= 0.5 for delay in delays)


def test_windows_lock_timeout_uses_injected_clock_and_never_really_waits(monkeypatch, tmp_path):
    monkeypatch.setattr(activation_module.sys, "platform", "win32")
    current = [0.0]

    class AlwaysBusyLock(WindowsSMBExclusiveLock):
        def _try_open(self):
            return None, 32, lambda _handle: True

    def monotonic():
        return current[0]

    def sleep(delay):
        current[0] += delay

    lock = AlwaysBusyLock(
        tmp_path / "annual-current.lock",
        timeout_seconds=0.5,
        monotonic=monotonic,
        sleep=sleep,
        random_uniform=lambda low, high: 0.3,
    )
    with pytest.raises(AnnualDataActivationError) as caught:
        with lock:
            pass

    assert caught.value.code == "lock_timeout"
    assert current[0] == pytest.approx(0.5)


def test_default_lock_is_platform_guarded_on_non_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(activation_module.sys, "platform", "linux")
    lock = WindowsSMBExclusiveLock(tmp_path / "annual-current.lock")
    with pytest.raises(AnnualDataActivationError) as caught:
        with lock:
            pass
    assert caught.value.code == "lock_unsupported_platform"
