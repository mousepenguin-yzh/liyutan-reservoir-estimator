import dataclasses
import datetime as dt
import uuid
from pathlib import Path

import pytest

from annual_data_excel import parse_annual_data_excel
from annual_data_version_writer import (
    AnnualDataVersionConflictError,
    AnnualDataVersionPublishError,
    InjectedAnnualDataFault,
    fault_at,
    generate_annual_version_id,
    publish_annual_data_version,
)
from shared_storage_schema import (
    ANNUAL_REQUIRED_FILES,
    SCHEMA_VERSION,
    SHARED_ROOT_SCHEMA,
    deserialize_json,
    serialize_json,
    sha256_bytes,
    validate_annual_bundle,
)
from test_annual_data_excel import _filled_workbook, _workbook_bytes


FIXED_TIME = dt.datetime(2027, 1, 2, 3, 4, 5, 678901, tzinfo=dt.timezone.utc)
FIXED_UUID = uuid.UUID("12345678-1234-5678-9234-567812345678")
FIXED_STAGE_UUID = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
FIXED_VERSION_ID = "annual-20270102T030405678901Z-12345678-1234-5678-9234-567812345678"


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


def _candidate(raw: bytes):
    parsed = parse_annual_data_excel(raw, filename="synthetic.xlsx")
    assert parsed.ok and parsed.candidate is not None
    return parsed.candidate


def _publish(root: Path, raw: bytes, **overrides):
    candidate = overrides.pop("candidate", _candidate(raw))
    arguments = {
        "root": root,
        "candidate": candidate,
        "source_excel_bytes": raw,
        "source_filename": "synthetic.xlsx",
        "operator_display_name": "人工宣告操作人",
        "note": "建立純合成年度基準版本",
        "confirmed_candidate_fingerprint": candidate.fingerprint,
        "created_at": FIXED_TIME,
        "version_uuid": FIXED_UUID,
        "staging_uuid": FIXED_STAGE_UUID,
    }
    arguments.update(overrides)
    return publish_annual_data_version(**arguments)


def _disk_bundle(path: Path) -> dict[str, bytes]:
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in path.rglob("*")
        if item.is_file()
    }


def _tree_checksums(path: Path) -> dict[str, str]:
    return {name: sha256_bytes(data) for name, data in _disk_bundle(path).items()}


def test_deterministic_complete_bundle_round_trip_and_unactivated_state(tmp_path):
    raw = _workbook_bytes()
    left_root = _root(tmp_path / "left")
    right_root = _root(tmp_path / "right")

    left = _publish(left_root, raw)
    right = _publish(right_root, raw)

    assert left.version_id == right.version_id == FIXED_VERSION_ID
    assert _disk_bundle(left.version_path) == _disk_bundle(right.version_path)
    bundle = _disk_bundle(left.version_path)
    assert set(bundle) == set(ANNUAL_REQUIRED_FILES)
    parsed = validate_annual_bundle(bundle)
    assert len(parsed["hydrology"]) == 36
    assert len(parsed["hydrology"][0]) == 22  # period key, month, period, and 19 Q columns
    assert len(parsed["outflow_demand"]) == 36
    assert len(parsed["outflow_demand"][0]) == 6  # identity fields and three outflows
    assert len(parsed["reservoir_parameters"]) == 6  # schema controls and four parameters
    assert parsed["reservoir_parameters"]["shilin_diversion_limit_cms"] == 32
    assert parsed["source_excel"]["original_filename"] == "synthetic.xlsx"
    assert bundle["source/original.xlsx"] == raw
    assert parsed["source_excel"]["sha256"] == sha256_bytes(raw)
    assert parsed["version"]["operator_display_name"] == "人工宣告操作人"
    assert not (left_root / "annual-data" / "current.json").exists()
    assert not (left_root / "audit").exists()


def test_parameter_metadata_and_candidate_metadata_round_trip(tmp_path):
    raw = _workbook_bytes()
    candidate = _candidate(raw)
    result = _publish(_root(tmp_path), raw, candidate=candidate)
    parsed = validate_annual_bundle(_disk_bundle(result.version_path))

    expected = {
        code: {
            "effective_start_date": item["effective_start_date"],
            "source_reference": item["source_reference"],
            "note": item["note"],
        }
        for code, item in candidate.parameter_metadata.items()
    }
    assert parsed["parameter_metadata"] == expected
    assert parsed["version"]["template_version"] == candidate.template_version
    assert parsed["version"]["actual_data_cutoff_period"] == candidate.actual_data_cutoff_period
    assert parsed["version"]["candidate_fingerprint"] == candidate.fingerprint
    assert parsed["version"]["source_references"][:2] == [
        candidate.hydrology_source_period,
        candidate.annual_outflow_source,
    ]


def test_source_excel_or_candidate_change_requires_new_preview(tmp_path):
    raw = _workbook_bytes()
    candidate = _candidate(raw)
    root = _root(tmp_path)
    with pytest.raises(AnnualDataVersionPublishError, match="重新預覽") as source_error:
        _publish(root, raw + b"changed", candidate=candidate)
    assert source_error.value.code == "source_sha256_changed"

    changed_candidate = dataclasses.replace(candidate, applicable_year=2028)
    with pytest.raises(AnnualDataVersionPublishError, match="候選內容不一致") as candidate_error:
        _publish(root, raw, candidate=changed_candidate)
    assert candidate_error.value.code == "candidate_changed"
    assert not (root / "staging").exists()


def test_confirmed_fingerprint_change_is_rejected(tmp_path):
    raw = _workbook_bytes()
    with pytest.raises(AnnualDataVersionPublishError, match="fingerprint") as caught:
        _publish(_root(tmp_path), raw, confirmed_candidate_fingerprint="0" * 64)
    assert caught.value.code == "confirmed_fingerprint_changed"


def test_changed_legal_source_filename_requires_new_preview_and_creates_nothing(tmp_path):
    raw = _workbook_bytes()
    candidate = _candidate(raw)
    root = _root(tmp_path)

    with pytest.raises(AnnualDataVersionPublishError, match="重新預覽") as caught:
        _publish(root, raw, candidate=candidate, source_filename="another-synthetic.xlsx")

    assert caught.value.code == "source_filename_changed"
    assert not (root / "staging").exists()
    assert not (root / "annual-data" / "versions").exists()


def test_candidate_without_source_filename_cannot_be_published(tmp_path):
    raw = _workbook_bytes()
    candidate = dataclasses.replace(_candidate(raw), source_filename=None)
    root = _root(tmp_path)

    with pytest.raises(AnnualDataVersionPublishError, match="重新預覽") as caught:
        _publish(root, raw, candidate=candidate)

    assert caught.value.code == "source_filename_missing"
    assert not (root / "staging").exists()
    assert not (root / "annual-data" / "versions").exists()


def test_warnings_require_confirmation_and_full_warning_records_are_saved(tmp_path):
    workbook = _filled_workbook()
    workbook["水庫參數"]["F6"] = None
    workbook["水庫參數"]["G7"] = None
    raw = _workbook_bytes(workbook)
    candidate = _candidate(raw)
    assert len(candidate.warnings) == 2
    root = _root(tmp_path)

    with pytest.raises(AnnualDataVersionPublishError, match="明確確認") as caught:
        _publish(root, raw, candidate=candidate)
    assert caught.value.code == "warnings_not_confirmed"
    with pytest.raises(AnnualDataVersionPublishError, match="明確確認"):
        _publish(root, raw, candidate=candidate, warnings_confirmed=1)

    result = _publish(root, raw, candidate=candidate, warnings_confirmed=True)
    warnings = deserialize_json((result.version_path / "version.json").read_bytes())["confirmed_warnings"]
    assert warnings == [
        {
            "severity": warning.severity.value,
            "code": warning.code,
            "message": warning.message,
            "sheet": warning.sheet,
            "cell": warning.cell,
        }
        for warning in candidate.warnings
    ]


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("operator_display_name", "  ", "operator_required"),
        ("note", "", "note_required"),
    ],
)
def test_operator_and_creation_note_are_required(tmp_path, field, value, code):
    raw = _workbook_bytes()
    with pytest.raises(AnnualDataVersionPublishError) as caught:
        _publish(_root(tmp_path), raw, **{field: value})
    assert caught.value.code == code


@pytest.mark.parametrize("filename", ["../escape.xlsx", r"C:\\escape.xlsx", r"folder\\escape.xlsx"])
def test_unsafe_source_filename_cannot_escape(tmp_path, filename):
    root = _root(tmp_path)
    raw = _workbook_bytes()
    with pytest.raises(AnnualDataVersionPublishError) as caught:
        _publish(root, raw, source_filename=filename)
    assert caught.value.code == "unsafe_source_filename"
    assert not (tmp_path / "escape.xlsx").exists()
    assert not (root / "staging").exists()


def test_generated_version_id_is_utc_uuid_and_unsafe_injected_id_is_rejected(tmp_path):
    assert generate_annual_version_id(FIXED_TIME, FIXED_UUID) == FIXED_VERSION_ID
    raw = _workbook_bytes()
    with pytest.raises(AnnualDataVersionPublishError) as caught:
        _publish(_root(tmp_path), raw, version_id=r"..\\escape")
    assert caught.value.code == "unsafe_version_id"


def test_existing_final_version_is_never_overwritten_or_merged(tmp_path):
    root = _root(tmp_path)
    final = root / "annual-data" / "versions" / FIXED_VERSION_ID
    final.mkdir(parents=True)
    sentinel = final / "old.txt"
    sentinel.write_bytes(b"immutable old evidence")
    raw = _workbook_bytes()

    with pytest.raises(AnnualDataVersionConflictError) as caught:
        _publish(root, raw)
    assert caught.value.code == "version_exists"
    assert sentinel.read_bytes() == b"immutable old evidence"
    assert not (root / "staging").exists()


@pytest.mark.parametrize(
    ("stage", "expected_files"),
    [
        ("before_first_file", set()),
        ("after_write:hydrology_q.csv", {"hydrology_q.csv"}),
        (
            "before_committed",
            {
                "hydrology_q.csv",
                "outflow_demand.csv",
                "reservoir_parameters.json",
                "source/original.xlsx",
                "version.json",
            },
        ),
    ],
)
def test_interruption_preserves_incomplete_staging_without_formal_version(tmp_path, stage, expected_files):
    root = _root(tmp_path)
    raw = _workbook_bytes()
    with pytest.raises(InjectedAnnualDataFault):
        _publish(root, raw, fault_injector=fault_at(stage))

    staging = next((root / "staging").iterdir())
    assert set(_disk_bundle(staging)) == expected_files
    assert not (root / "annual-data" / "versions" / FIXED_VERSION_ID).exists()
    assert not (root / "annual-data" / "current.json").exists()


def test_staging_validation_failure_is_quarantined(tmp_path):
    root = _root(tmp_path)
    raw = _workbook_bytes()

    def corrupt_before_validation(stage: str, path: Path) -> None:
        if stage == "before_staging_validation":
            (path / "hydrology_q.csv").write_bytes(b"corrupted after verified write")

    with pytest.raises(AnnualDataVersionPublishError) as caught:
        _publish(root, raw, fault_injector=corrupt_before_validation)
    assert caught.value.code == "staging_validation_failed"
    assert caught.value.evidence_path.parent == root / "quarantine"
    assert caught.value.evidence_path.exists()
    assert not (root / "annual-data" / "versions" / FIXED_VERSION_ID).exists()


def test_rename_only_occurs_after_staging_is_complete_and_valid(tmp_path):
    root = _root(tmp_path)
    raw = _workbook_bytes()
    observed = []

    def observe(stage: str, path: Path) -> None:
        observed.append(stage)
        if stage == "before_rename":
            validate_annual_bundle(_disk_bundle(path))

    result = _publish(root, raw, fault_injector=observe)
    assert observed.index("before_committed") < observed.index("after_committed")
    assert observed.index("after_committed") < observed.index("before_staging_validation")
    assert observed.index("after_staging_validation") < observed.index("before_rename")
    assert result.version_path.is_dir()


def test_interruption_before_rename_keeps_complete_staging(tmp_path):
    root = _root(tmp_path)
    raw = _workbook_bytes()
    with pytest.raises(InjectedAnnualDataFault):
        _publish(root, raw, fault_injector=fault_at("before_rename"))
    staging = next((root / "staging").iterdir())
    validate_annual_bundle(_disk_bundle(staging))
    assert not (root / "annual-data" / "versions" / FIXED_VERSION_ID).exists()


def test_interruption_after_rename_leaves_complete_but_unactivated_version(tmp_path):
    root = _root(tmp_path)
    raw = _workbook_bytes()
    with pytest.raises(InjectedAnnualDataFault):
        _publish(root, raw, fault_injector=fault_at("after_rename"))
    final = root / "annual-data" / "versions" / FIXED_VERSION_ID
    validate_annual_bundle(_disk_bundle(final))
    assert not (root / "annual-data" / "current.json").exists()


def test_system_current_audit_and_old_versions_never_change(tmp_path):
    root = _root(tmp_path)
    system_before = (root / "system.json").read_bytes()
    annual_root = root / "annual-data"
    annual_root.mkdir()
    current = annual_root / "current.json"
    current.write_bytes(b"existing pointer bytes are opaque to C1")
    audit = root / "audit"
    audit.mkdir()
    (audit / "events.jsonl").write_bytes(b"existing audit evidence\n")
    old = annual_root / "versions" / "annual-old"
    old.mkdir(parents=True)
    (old / "sentinel.bin").write_bytes(b"old immutable version")
    old_checksums = _tree_checksums(old)

    _publish(root, _workbook_bytes())

    assert (root / "system.json").read_bytes() == system_before
    assert current.read_bytes() == b"existing pointer bytes are opaque to C1"
    assert (audit / "events.jsonl").read_bytes() == b"existing audit evidence\n"
    assert _tree_checksums(old) == old_checksums


def test_root_must_exist_and_system_must_already_be_valid(tmp_path):
    raw = _workbook_bytes()
    missing = tmp_path / "missing-root"
    with pytest.raises(AnnualDataVersionPublishError) as missing_error:
        _publish(missing, raw)
    assert missing_error.value.code == "root_not_found"
    assert not missing.exists()

    root = _root(tmp_path / "wrong-system")
    system = deserialize_json((root / "system.json").read_bytes())
    system["reservoir_id"] = "other-reservoir"
    bad_system_bytes = serialize_json(system)
    (root / "system.json").write_bytes(bad_system_bytes)
    with pytest.raises(AnnualDataVersionPublishError) as system_error:
        _publish(root, raw)
    assert system_error.value.code == "system_invalid"
    assert (root / "system.json").read_bytes() == bad_system_bytes
    assert not (root / "staging").exists()
