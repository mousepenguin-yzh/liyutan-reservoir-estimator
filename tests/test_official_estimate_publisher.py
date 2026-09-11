import copy
import datetime as dt
import threading
import uuid
from contextlib import contextmanager

import pytest

import annual_data_activation as activation_module
from annual_data_activation import AnnualDataActivationError
from official_estimate_candidate import (
    OfficialEstimateCandidate,
    build_official_estimate_candidate,
)
from official_estimate_publisher import (
    InjectedOfficialEstimatePublishFault,
    OfficialEstimateConflictError,
    OfficialEstimateCurrentSwitchedAuditIncompleteError,
    OfficialEstimatePublishError,
    OfficialEstimateVersionPublishedCurrentNotSwitchedError,
    fault_at,
    observe_official_current,
    publish_official_estimate_candidate,
)
from shared_storage_schema import (
    OFFICIAL_ESTIMATE_PUBLISH_EVENT_TYPE,
    StorageValidationError,
    deserialize_json,
    serialize_json,
    sha256_bytes,
    validate_official_bundle,
    validate_official_current,
    validate_official_estimate_publish_audit_event,
)
from test_official_estimate_candidate import CLEAN_SOFTWARE, _ready
from test_shared_storage_reader import ANNUAL_ID, _build_root


COMMIT_TIME = dt.datetime(2027, 1, 5, 1, 2, 3, tzinfo=dt.timezone.utc)
SWITCH_TIME = dt.datetime(2027, 1, 5, 1, 2, 4, tzinfo=dt.timezone.utc)


@contextmanager
def fake_lock(_path):
    yield


def _clock(*values):
    remaining = iter(values)
    return lambda: next(remaining)


def _candidate(version_id, previous=None, *, batch_and_results=None):
    batch, results = batch_and_results or _ready(1)
    return build_official_estimate_candidate(
        batch,
        results,
        [batch["scenarios"][0]["scenario_id"]],
        annual_data_version_id=ANNUAL_ID,
        shared_annual_data_validated=True,
        operator_display_name="王承辦",
        note=f"發布 {version_id}",
        software=CLEAN_SOFTWARE,
        previous_official_version_id=previous,
        estimate_version_id=version_id,
        created_at="2027-01-05T00:00:00Z",
    )


def _publish(root, candidate, revision=0, current_id=None, **changes):
    token = uuid.uuid5(uuid.NAMESPACE_DNS, candidate.version_id)
    values = {
        "root": root,
        "candidate": candidate,
        "observed_revision": revision,
        "observed_current_version_id": current_id,
        "lock_factory": fake_lock,
        "clock": _clock(COMMIT_TIME, SWITCH_TIME),
        "staging_uuid": token,
        "event_uuid": uuid.uuid5(token, "event"),
        "current_temp_uuid": uuid.uuid5(token, "current"),
        "audit_temp_uuid": uuid.uuid5(token, "audit"),
        "hostname": "synthetic-host",
        "process_id": 123,
    }
    values.update(changes)
    return publish_official_estimate_candidate(**values)


def _disk_bundle(directory):
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def _official_audits(root):
    files = []
    for path in (root / "audit" / "events").rglob("*.json"):
        value = deserialize_json(path.read_bytes())
        if value.get("event_type") == OFFICIAL_ESTIMATE_PUBLISH_EVENT_TYPE:
            files.append(path)
    return sorted(files)


def test_first_official_version_publish_creates_revision_one_and_valid_audit(tmp_path):
    root = _build_root(tmp_path)
    candidate = _candidate("estimate-c1-first")
    assert observe_official_current(root).revision == 0

    result = _publish(root, candidate)

    assert result.status == "success"
    assert result.before_revision == 0
    assert result.after_revision == 1
    current = validate_official_current(
        deserialize_json((root / "official-estimates" / "current.json").read_bytes())
    )
    assert current["revision"] == 1
    assert current["current_version_id"] == candidate.version_id
    assert current["previous_version_id"] is None
    published = _disk_bundle(result.version_path)
    assert validate_official_bundle(published)["manifest"]["version_id"] == candidate.version_id
    for filename in ("manifest.json", "inputs.json", "scenario_summaries.csv", "daily_results.csv"):
        assert published[filename] == candidate.files[filename]
    committed = deserialize_json(published["COMMITTED.json"])
    assert committed["committed_at"] == "2027-01-05T01:02:03.000000Z"
    assert published["COMMITTED.json"] != candidate.files["COMMITTED.json"]
    audit = validate_official_estimate_publish_audit_event(
        deserialize_json(result.audit_path.read_bytes())
    )
    assert audit == result.audit_event
    assert audit["diagnostics"]["manifest_sha256"] == sha256_bytes(
        candidate.files["manifest.json"]
    )


def test_second_version_increments_revision_preserves_previous_and_old_version(tmp_path):
    root = _build_root(tmp_path)
    batch_and_results = _ready(1)
    first = _candidate("estimate-c1-v1", batch_and_results=batch_and_results)
    first_result = _publish(root, first)
    old_bytes = _disk_bundle(first_result.version_path)
    second = _candidate(
        "estimate-c1-v2", previous=first.version_id, batch_and_results=batch_and_results
    )

    second_result = _publish(root, second, 1, first.version_id)

    assert second_result.after_revision == 2
    assert second_result.batch_id == first_result.batch_id
    assert second_result.current["previous_version_id"] == first.version_id
    assert _disk_bundle(first_result.version_path) == old_bytes
    assert first_result.version_path.is_dir()
    assert second_result.version_path.is_dir()
    assert len(_official_audits(root)) == 2


def test_existing_version_id_is_rejected_without_overwrite(tmp_path):
    root = _build_root(tmp_path)
    candidate = _candidate("estimate-c1-existing")
    existing = root / "official-estimates" / "versions" / candidate.version_id
    existing.mkdir(parents=True)
    marker = existing / "do-not-touch.txt"
    marker.write_bytes(b"immutable")

    with pytest.raises(OfficialEstimatePublishError) as caught:
        _publish(root, candidate)

    assert caught.value.code == "version_id_exists"
    assert marker.read_bytes() == b"immutable"
    assert not (root / "official-estimates" / "current.json").exists()


def test_invalid_candidate_is_rejected_before_any_publish_write(tmp_path):
    root = _build_root(tmp_path)
    candidate = _candidate("estimate-c1-invalid")
    broken_files = dict(candidate.files)
    broken_files["inputs.json"] += b"corrupt"
    broken = OfficialEstimateCandidate(
        candidate.version_id,
        broken_files,
        candidate.validated_bundle,
        candidate.preview,
        candidate.context_fingerprint,
    )

    with pytest.raises(OfficialEstimatePublishError) as caught:
        _publish(root, broken)

    assert caught.value.code == "candidate_invalid"
    assert not (root / "official-estimates").exists()
    assert not (root / "staging").exists()


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_or_corrupt_referenced_annual_version_is_rejected(tmp_path, damage):
    root = _build_root(tmp_path)
    annual = root / "annual-data" / "versions" / ANNUAL_ID
    if damage == "missing":
        (annual / "COMMITTED.json").unlink()
    else:
        (annual / "version.json").write_bytes(b"not json")

    with pytest.raises(OfficialEstimatePublishError) as caught:
        _publish(root, _candidate(f"estimate-c1-annual-{damage}"))

    assert caught.value.code == "annual_version_invalid"
    assert not (root / "official-estimates").exists()


def test_candidate_previous_must_match_observed_current_before_staging(tmp_path):
    root = _build_root(tmp_path)
    candidate = _candidate("estimate-c1-wrong-previous", previous="estimate-other")

    with pytest.raises(OfficialEstimateConflictError) as caught:
        _publish(root, candidate)

    assert caught.value.code == "previous_version_conflict"
    assert "重新確認正式保存預覽" in str(caught.value)
    assert not (root / "staging").exists()


@pytest.mark.parametrize(
    ("changed_revision", "changed_id"),
    [(2, "estimate-c1-current"), (1, "estimate-c1-other")],
)
def test_locked_revision_or_current_change_conflicts_without_retry(
    tmp_path, changed_revision, changed_id
):
    root = _build_root(tmp_path)
    current_candidate = _candidate("estimate-c1-current")
    _publish(root, current_candidate)
    next_candidate = _candidate(
        f"estimate-c1-conflict-{changed_revision}", previous=current_candidate.version_id
    )
    entries = 0

    @contextmanager
    def mutate_on_lock(_path):
        nonlocal entries
        entries += 1
        current = {
            "schema": "liyutan-reservoir-estimator/official-estimate-current",
            "schema_version": 1,
            "revision": changed_revision,
            "current_version_id": changed_id,
            "previous_version_id": None,
            "updated_at": "2027-01-05T01:03:00Z",
            "operator_display_name": "其他使用者",
        }
        (root / "official-estimates" / "current.json").write_bytes(serialize_json(current))
        yield

    with pytest.raises(OfficialEstimateConflictError) as caught:
        _publish(root, next_candidate, 1, current_candidate.version_id,
                 lock_factory=mutate_on_lock)

    assert caught.value.code == "revision_conflict"
    assert entries == 1
    assert not (root / "official-estimates" / "versions" / next_candidate.version_id).exists()


def test_invalid_locked_current_version_blocks_new_publish(tmp_path):
    root = _build_root(tmp_path)
    first = _candidate("estimate-c1-good-current")
    first_result = _publish(root, first)
    (first_result.version_path / "COMMITTED.json").unlink()
    second = _candidate("estimate-c1-blocked", previous=first.version_id)

    with pytest.raises(OfficialEstimatePublishError) as caught:
        _publish(root, second, 1, first.version_id)

    assert caught.value.code == "current_version_invalid"
    assert not (root / "official-estimates" / "versions" / second.version_id).exists()


def test_committed_is_last_and_full_staging_readback_precedes_rename(tmp_path):
    root = _build_root(tmp_path)
    candidate = _candidate("estimate-c1-order")
    stages = []

    def record(stage, _path):
        stages.append(stage)

    _publish(root, candidate, fault_injector=record)

    for filename in ("manifest.json", "inputs.json", "scenario_summaries.csv", "daily_results.csv"):
        assert stages.index(f"after_readback:{filename}") < stages.index("before_committed")
    assert stages.index("after_readback:COMMITTED.json") < stages.index("before_staging_validation")
    assert stages.index("after_staging_validation") < stages.index("critical_section_entered")
    assert stages.index("after_staging_validation") < stages.index("before_version_rename")
    assert stages.index("after_validate:audit_temp") < stages.index("before_current_replace")
    assert stages.index("after_validate:current_temp") < stages.index("before_current_replace")
    assert stages.index("after_current_revalidation") < stages.index("before_audit_publish")


def test_staging_readback_corruption_is_rejected_before_rename(tmp_path):
    root = _build_root(tmp_path)
    candidate = _candidate("estimate-c1-stage-corrupt")

    def corrupt(stage, path):
        if stage == "before_staging_validation":
            (path / "inputs.json").write_bytes(b"corrupt")

    with pytest.raises(OfficialEstimatePublishError) as caught:
        _publish(root, candidate, fault_injector=corrupt)

    assert caught.value.code == "staging_validation_failed"
    assert not (root / "official-estimates" / "versions" / candidate.version_id).exists()
    assert not (root / "official-estimates" / "current.json").exists()


def test_fault_before_rename_leaves_no_version_and_does_not_change_current(tmp_path):
    root = _build_root(tmp_path)
    candidate = _candidate("estimate-c1-before-rename")

    with pytest.raises(InjectedOfficialEstimatePublishFault):
        _publish(root, candidate, fault_injector=fault_at("before_version_rename"))

    assert not (root / "official-estimates" / "versions" / candidate.version_id).exists()
    assert not (root / "official-estimates" / "current.json").exists()
    assert any((root / "staging").iterdir())


def test_fault_after_rename_preserves_orphan_and_reports_current_not_switched(tmp_path):
    root = _build_root(tmp_path)
    candidate = _candidate("estimate-c1-after-rename")

    with pytest.raises(OfficialEstimateVersionPublishedCurrentNotSwitchedError) as caught:
        _publish(root, candidate, fault_injector=fault_at("after_version_rename"))

    assert caught.value.code == "version_published_current_not_switched"
    version = root / "official-estimates" / "versions" / candidate.version_id
    assert validate_official_bundle(_disk_bundle(version))
    assert not (root / "official-estimates" / "current.json").exists()


def test_fault_after_current_replace_never_rolls_back_and_preserves_audit_evidence(tmp_path):
    root = _build_root(tmp_path)
    candidate = _candidate("estimate-c1-after-current")

    with pytest.raises(OfficialEstimateCurrentSwitchedAuditIncompleteError) as caught:
        _publish(root, candidate, fault_injector=fault_at("after_current_replace"))

    assert caught.value.code == "current_switched_audit_incomplete"
    current = validate_official_current(
        deserialize_json((root / "official-estimates" / "current.json").read_bytes())
    )
    assert current["current_version_id"] == candidate.version_id
    assert caught.value.pending_audit_path.is_file()
    assert not caught.value.audit_path.exists()
    assert validate_official_bundle(_disk_bundle(caught.value.version_path))


def test_audit_destination_is_unique_and_existing_event_is_never_overwritten(tmp_path):
    root = _build_root(tmp_path)
    first = _candidate("estimate-c1-audit-first")
    first_result = _publish(root, first)
    original_audit = first_result.audit_path.read_bytes()
    second = _candidate("estimate-c1-audit-second", previous=first.version_id)

    with pytest.raises(OfficialEstimateVersionPublishedCurrentNotSwitchedError) as caught:
        _publish(root, second, 1, first.version_id,
                 event_uuid=first_result.audit_event["event_id"])

    assert caught.value.code == "version_published_current_not_switched"
    assert first_result.audit_path.read_bytes() == original_audit
    current = validate_official_current(
        deserialize_json((root / "official-estimates" / "current.json").read_bytes())
    )
    assert current["current_version_id"] == first.version_id


def test_lock_timeout_has_stable_code_and_does_not_publish(tmp_path):
    root = _build_root(tmp_path)
    candidate = _candidate("estimate-c1-lock-timeout")

    @contextmanager
    def timeout(path):
        raise AnnualDataActivationError("lock_timeout", "synthetic timeout", evidence_path=path)
        yield

    with pytest.raises(OfficialEstimatePublishError) as caught:
        _publish(root, candidate, lock_factory=timeout)

    assert caught.value.code == "lock_timeout"
    assert not (root / "official-estimates" / "versions" / candidate.version_id).exists()
    assert not (root / "official-estimates" / "current.json").exists()


def test_default_lock_is_import_safe_and_platform_guarded_off_windows(tmp_path, monkeypatch):
    root = _build_root(tmp_path)
    candidate = _candidate("estimate-c1-non-windows")
    monkeypatch.setattr(activation_module.sys, "platform", "linux")

    with pytest.raises(OfficialEstimatePublishError) as caught:
        _publish(root, candidate, lock_factory=None)

    assert caught.value.code == "filesystem_failure"
    assert not (root / "official-estimates" / "versions" / candidate.version_id).exists()


def test_fake_lock_allows_one_concurrent_publish_and_one_conflict(tmp_path):
    root = _build_root(tmp_path)
    lock = threading.Lock()

    @contextmanager
    def tracked_lock(_path):
        with lock:
            yield

    candidates = [_candidate("estimate-c1-race-a"), _candidate("estimate-c1-race-b")]
    barrier = threading.Barrier(2)
    outcomes = []

    def worker(candidate):
        barrier.wait()
        try:
            outcomes.append(_publish(root, candidate, lock_factory=tracked_lock).status)
        except OfficialEstimateConflictError as exc:
            outcomes.append(exc.code)

    threads = [threading.Thread(target=worker, args=(candidate,)) for candidate in candidates]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(outcomes) == ["revision_conflict", "success"]
    current = validate_official_current(
        deserialize_json((root / "official-estimates" / "current.json").read_bytes())
    )
    assert current["revision"] == 1
    assert len(_official_audits(root)) == 1


def test_official_publish_audit_schema_is_strict():
    event = {
        "schema": "liyutan-reservoir-estimator/audit-event",
        "schema_version": 1,
        "event_id": "event-c1",
        "event_type": OFFICIAL_ESTIMATE_PUBLISH_EVENT_TYPE,
        "occurred_at": "2027-01-05T01:02:04Z",
        "estimate_version_id": "estimate-c1",
        "batch_id": "batch-c1",
        "annual_data_version_id": ANNUAL_ID,
        "before_revision": 0,
        "before_current_version_id": None,
        "after_revision": 1,
        "after_current_version_id": "estimate-c1",
        "previous_official_version_id": None,
        "operator_display_name": "王承辦",
        "note": "正式發布",
        "software": CLEAN_SOFTWARE,
        "result": "success",
        "diagnostics": {
            "hostname": "synthetic-host",
            "process_id": 1,
            "manifest_sha256": "1" * 64,
        },
    }
    assert validate_official_estimate_publish_audit_event(event) == event
    with pytest.raises(StorageValidationError, match="未知欄位"):
        validate_official_estimate_publish_audit_event({**event, "unknown": True})
    with pytest.raises(StorageValidationError, match="before_revision \\+ 1"):
        validate_official_estimate_publish_audit_event({**event, "after_revision": 2})
