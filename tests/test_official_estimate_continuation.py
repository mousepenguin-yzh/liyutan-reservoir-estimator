import copy
import dataclasses
import datetime as dt
import uuid
from pathlib import Path

import pytest

from official_estimate_continuation import (
    OfficialContinuationDraft,
    OfficialContinuationError,
    OfficialContinuationErrorCode,
    build_official_continuation,
)
from official_estimate_loader import (
    OfficialEstimateLoader,
    load_official_current,
    load_official_history_version,
)
from shared_storage_schema import ANNUAL_CURRENT_SCHEMA, SCHEMA_VERSION, serialize_json
from test_official_estimate_loader import (
    _build_root,
    _filesystem_snapshot,
    _versioned_bundle,
)
from v2_workflow import validate_batch


FIXED_BATCH_ID = "batch-continuation-0001"
FIXED_CREATED_AT = "2027-01-20T04:05:06Z"


def _snapshot(tmp_path: Path):
    root = _build_root(tmp_path, versions=[("estimate-current", None)])
    return root, load_official_current(root).snapshot


def _fixed_draft(snapshot, **kwargs) -> OfficialContinuationDraft:
    return build_official_continuation(
        snapshot,
        batch_id_factory=lambda: FIXED_BATCH_ID,
        created_at=FIXED_CREATED_AT,
        **kwargs,
    )


def test_current_snapshot_builds_new_valid_working_batch(tmp_path):
    _, snapshot = _snapshot(tmp_path)

    draft = _fixed_draft(snapshot)

    assert validate_batch(draft.batch) == draft.batch
    assert draft.batch["batch_id"] == FIXED_BATCH_ID
    assert draft.batch["batch_id"] != snapshot.batch["batch_id"]
    assert draft.batch["batch_name"] == f"{snapshot.batch['batch_name']}（接續）"
    assert draft.batch["created_at"] == FIXED_CREATED_AT
    assert draft.batch["created_at"] != snapshot.batch["created_at"]


def test_each_continuation_uses_a_new_injected_batch_identity(tmp_path):
    _, snapshot = _snapshot(tmp_path)
    generated = iter(("batch-continuation-1", "batch-continuation-2"))

    first = build_official_continuation(
        snapshot,
        batch_id_factory=lambda: next(generated),
        created_at="2027-01-20T00:00:00Z",
    )
    second = build_official_continuation(
        snapshot,
        batch_id_factory=lambda: next(generated),
        created_at="2027-01-20T00:00:01Z",
    )

    assert first.batch["batch_id"] == "batch-continuation-1"
    assert second.batch["batch_id"] == "batch-continuation-2"
    assert first.batch["batch_id"] != second.batch["batch_id"]


def test_default_batch_identity_is_a_new_uuid(tmp_path):
    _, snapshot = _snapshot(tmp_path)

    draft = build_official_continuation(snapshot, created_at=FIXED_CREATED_AT)

    assert str(uuid.UUID(draft.batch["batch_id"])) == draft.batch["batch_id"]
    assert draft.batch["batch_id"] != snapshot.batch["batch_id"]


def test_clock_is_injectable_and_created_at_is_normalized_to_utc(tmp_path):
    _, snapshot = _snapshot(tmp_path)
    taipei = dt.timezone(dt.timedelta(hours=8))

    draft = build_official_continuation(
        snapshot,
        batch_id_factory=lambda: FIXED_BATCH_ID,
        clock=lambda: dt.datetime(2027, 1, 20, 12, 5, 6, tzinfo=taipei),
    )

    assert draft.batch["created_at"] == FIXED_CREATED_AT
    parsed = dt.datetime.fromisoformat(draft.batch["created_at"].replace("Z", "+00:00"))
    assert parsed.utcoffset() == dt.timedelta(0)


def test_derived_lineage_uses_selected_version_not_its_ancestor(tmp_path):
    root = _build_root(
        tmp_path,
        versions=[("estimate-y", "estimate-p"), ("estimate-p", None)],
    )
    y_directory = root / "official-estimates" / "versions" / "estimate-y"
    y_bundle = _versioned_bundle(
        "estimate-y",
        "estimate-p",
        derived_from_version_id="estimate-x",
    )
    for name, data in y_bundle.items():
        (y_directory / name).write_bytes(data)
    snapshot = OfficialEstimateLoader(root).load_history_version("estimate-y")

    draft = _fixed_draft(snapshot)

    assert snapshot.manifest["derived_from_official_version_id"] == "estimate-x"
    assert snapshot.manifest["previous_official_version_id"] == "estimate-p"
    assert draft.derived_from_official_version_id == "estimate-y"
    assert draft.derived_from_official_version_id != "estimate-x"


def test_continuation_contract_has_no_publication_previous_lineage(tmp_path):
    _, snapshot = _snapshot(tmp_path)

    draft = _fixed_draft(snapshot)

    field_names = {field.name for field in dataclasses.fields(draft)}
    assert "previous_official_version_id" not in field_names
    assert "previous_official_version_id" not in draft.batch
    assert not hasattr(draft, "previous_official_version_id")


def test_scenarios_and_official_inputs_are_preserved_exactly(tmp_path):
    _, snapshot = _snapshot(tmp_path)
    source_scenarios = copy.deepcopy(snapshot.batch["scenarios"])

    draft = _fixed_draft(snapshot)

    assert draft.batch["scenarios"] == source_scenarios
    assert [item["scenario_id"] for item in draft.batch["scenarios"]] == list(
        snapshot.official_scenario_ids
    )
    assert len(draft.batch["scenarios"]) == len(snapshot.official_scenario_ids)


def test_all_working_conditions_are_preserved_except_new_identity(tmp_path):
    _, snapshot = _snapshot(tmp_path)
    source = snapshot.batch

    draft = _fixed_draft(snapshot)

    preserved_fields = (
        "display_start_date",
        "projection_start_date",
        "projection_end_date",
        "initial_capacity",
        "historical_capacities",
        "reservoir_parameters",
        "periods",
        "shared_period_count",
        "shared_inflows",
        "scenarios",
        "outflows",
        "daily_outflows",
        "date_overrides",
        "overrides_enabled",
        "note",
    )
    for field in preserved_fields:
        assert draft.batch[field] == source[field]
    assert draft.batch["note"] == "純合成批次"
    assert draft.batch["note"] != snapshot.manifest["note"]


def test_formal_results_never_become_active_working_results(tmp_path):
    _, snapshot = _snapshot(tmp_path)
    assert snapshot.scenario_summaries
    assert snapshot.daily_results
    snapshot.batch["results"] = {"scenario-a": {"status": "success"}}
    snapshot.batch["results_fingerprint"] = "historical-runtime-state"
    snapshot.batch["previous_official_version_id"] = "must-not-leak"
    snapshot.batch["derived_from_official_version_id"] = "must-not-leak"

    draft = _fixed_draft(snapshot)

    assert "results" not in draft.batch
    assert "results_fingerprint" not in draft.batch
    assert "scenario_summaries" not in draft.batch
    assert "daily_results" not in draft.batch
    assert "previous_official_version_id" not in draft.batch
    assert "derived_from_official_version_id" not in draft.batch
    assert draft.batch["daily_outflows"] == snapshot.batch["daily_outflows"]
    assert "results" in snapshot.batch
    assert "results_fingerprint" in snapshot.batch


def test_source_annual_version_is_kept_even_when_annual_current_is_newer(tmp_path):
    root, snapshot = _snapshot(tmp_path)
    annual_root = root / "annual-data"
    annual_root.mkdir()
    (annual_root / "current.json").write_bytes(
        serialize_json(
            {
                "schema": ANNUAL_CURRENT_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "revision": 2,
                "current_version_id": "annual-newer-b",
                "previous_version_id": "annual-synthetic-2027",
                "updated_at": "2027-01-20T04:00:00Z",
                "operator_display_name": "測試操作人",
            }
        )
    )

    draft = _fixed_draft(snapshot)

    assert snapshot.annual_data_version_id == "annual-synthetic-2027"
    assert draft.annual_data_version_id == "annual-synthetic-2027"
    assert draft.annual_data_version_id != "annual-newer-b"


def test_transformation_deep_copies_every_nested_working_input(tmp_path):
    _, snapshot = _snapshot(tmp_path)
    source_before = copy.deepcopy(snapshot.batch)

    draft = _fixed_draft(snapshot)
    assert snapshot.batch == source_before

    period = draft.batch["periods"][0]
    draft.batch["batch_name"] = "修改後工作批次"
    draft.batch["scenarios"][0]["name"] = "修改後情境"
    draft.batch["scenarios"][0]["inflows"][period]["cms"] = 999.0
    draft.batch["outflows"][period]["upstream_irrigation_cms"] = 999.0
    draft.batch["daily_outflows"][0]["public_water_10k_ton_per_day"] = 999.0
    draft.batch["reservoir_parameters"]["max_capacity"] = 999.0
    draft.batch["historical_capacities"]["2026-12-31"] = 999.0
    draft.batch["date_overrides"].append(
        {
            "start": "2027-01-01",
            "end": "2027-01-01",
            "up_irr": 1.0,
            "down_irr": 1.0,
            "public": 1.0,
            "reason": "draft-only mutation",
        }
    )

    assert snapshot.batch == source_before


def test_old_history_snapshot_can_start_a_new_continuation(tmp_path):
    root = _build_root(
        tmp_path,
        versions=[("estimate-current", "estimate-old"), ("estimate-old", None)],
    )
    old_snapshot = load_official_history_version(root, "estimate-old")

    draft = _fixed_draft(old_snapshot, batch_name="歷史版接續工作")

    assert draft.derived_from_official_version_id == "estimate-old"
    assert draft.batch["batch_name"] == "歷史版接續工作"
    assert validate_batch(draft.batch) == draft.batch


def test_continuation_transformation_writes_nothing_to_shared_root(tmp_path):
    root, snapshot = _snapshot(tmp_path)
    before = _filesystem_snapshot(root)

    _fixed_draft(snapshot)

    assert _filesystem_snapshot(root) == before


@pytest.mark.parametrize(
    ("factory", "expected_message"),
    [
        (lambda: "", "非空白"),
        (lambda: "batch-synthetic-1", "不得沿用"),
    ],
)
def test_invalid_or_reused_batch_id_fails(tmp_path, factory, expected_message):
    _, snapshot = _snapshot(tmp_path)

    with pytest.raises(OfficialContinuationError, match=expected_message) as captured:
        build_official_continuation(
            snapshot,
            batch_id_factory=factory,
            created_at=FIXED_CREATED_AT,
        )

    assert captured.value.code is OfficialContinuationErrorCode.INVALID_BATCH_ID


def test_blank_custom_batch_name_fails(tmp_path):
    _, snapshot = _snapshot(tmp_path)

    with pytest.raises(OfficialContinuationError, match="batch_name") as captured:
        _fixed_draft(snapshot, batch_name="   ")

    assert captured.value.code is OfficialContinuationErrorCode.INVALID_BATCH_NAME


@pytest.mark.parametrize("created_at", ["not-a-time", "2027-01-20T04:05:06"])
def test_invalid_or_naive_created_at_fails(tmp_path, created_at):
    _, snapshot = _snapshot(tmp_path)

    with pytest.raises(OfficialContinuationError) as captured:
        build_official_continuation(
            snapshot,
            batch_id_factory=lambda: FIXED_BATCH_ID,
            created_at=created_at,
        )

    assert captured.value.code is OfficialContinuationErrorCode.INVALID_CREATED_AT


def test_source_created_at_cannot_be_reused(tmp_path):
    _, snapshot = _snapshot(tmp_path)

    with pytest.raises(OfficialContinuationError, match="不得沿用") as captured:
        build_official_continuation(
            snapshot,
            batch_id_factory=lambda: FIXED_BATCH_ID,
            created_at=snapshot.batch["created_at"],
        )

    assert captured.value.code is OfficialContinuationErrorCode.INVALID_CREATED_AT


def test_invalid_transformed_batch_fails_without_returning_partial_draft(tmp_path):
    _, snapshot = _snapshot(tmp_path)
    snapshot.batch["periods"] = []

    with pytest.raises(OfficialContinuationError, match="validate_batch") as captured:
        _fixed_draft(snapshot)

    assert captured.value.code is OfficialContinuationErrorCode.BATCH_VALIDATION_FAILED


def test_arbitrary_raw_dict_is_not_an_accepted_source():
    with pytest.raises(OfficialContinuationError) as captured:
        build_official_continuation(  # type: ignore[arg-type]
            {"batch": {}},
            batch_id_factory=lambda: FIXED_BATCH_ID,
            created_at=FIXED_CREATED_AT,
        )

    assert captured.value.code is OfficialContinuationErrorCode.INVALID_SNAPSHOT
