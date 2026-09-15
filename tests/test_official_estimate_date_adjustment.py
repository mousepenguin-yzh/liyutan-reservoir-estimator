import copy
from pathlib import Path

import pytest

from official_estimate_continuation import (
    OfficialContinuationDraft,
    build_official_continuation,
)
from official_estimate_date_adjustment import (
    ContinuationDateAdjustmentError,
    ContinuationDateAdjustmentErrorCode,
    adjust_continuation_dates,
    apply_added_period_q80,
    apply_added_period_q90,
    apply_added_period_quantile,
    confirm_initial_capacity,
    validate_continuation_pending_batch,
)
from official_estimate_loader import load_official_current
from shared_storage_reader import AnnualDataSnapshot
from ten_day_period import (
    annual_period_key_for_working_period,
    dates_in_projection_range,
    working_period_key_for_date,
    working_period_keys_for_range,
)
from test_official_estimate_loader import _build_root, _filesystem_snapshot
from test_shared_storage_schema import synthetic_hydrology_rows, synthetic_outflow_rows
from v2_workflow import validate_batch


ANNUAL_A = "annual-synthetic-a"
ANNUAL_B = "annual-synthetic-b"


def _annual(version_id=ANNUAL_B, *, offset=0.0) -> AnnualDataSnapshot:
    hydrology = copy.deepcopy(synthetic_hydrology_rows())
    outflows = copy.deepcopy(synthetic_outflow_rows())
    for row in hydrology:
        row["q80_cms"] = 80.0 + row["month"] + offset
        row["q90_cms"] = 90.0 + row["month"] + offset
    for row in outflows:
        row["upstream_irrigation_cms"] = 20.0 + row["month"] + offset
        row["downstream_irrigation_cms"] = 10.0 + row["month"] + offset
        row["public_water_10k_ton_per_day"] = 50.0 + row["month"] + offset
    return AnnualDataSnapshot(
        current={"current_version_id": version_id},
        version={"version_id": version_id},
        hydrology=tuple(hydrology),
        outflow_demand=tuple(outflows),
        reservoir_parameters={},
        parameter_metadata={},
    )


def _inflow(value, source="正式人工值"):
    return {
        "cms": value,
        "source_type": source,
        "source_unit": "cms",
        "source_value": value,
        "note": "保留",
    }


def _outflow(index):
    return {
        "upstream_irrigation_cms": 100.0 + index,
        "downstream_irrigation_cms": 200.0 + index,
        "public_water_10k_ton_per_day": 300.0 + index,
        "source_type": "正式工作出流",
        "note": f"period-{index}",
    }


def _batch(
    *,
    start="2026-09-01",
    end="2026-10-01",
    display="2026-08-01",
    shared_count=0,
):
    periods = list(working_period_keys_for_range(start, end))
    scenarios = []
    for scenario_index, scenario_id in enumerate(("scenario-a", "scenario-b", "scenario-c")):
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "name": f"情境 {scenario_id[-1].upper()}",
                "order": scenario_index,
                "inflows": {
                    key: _inflow(10.0 * (scenario_index + 1) + period_index)
                    for period_index, key in enumerate(periods)
                },
            }
        )
    shared = {
        key: _inflow(500.0 + index, "正式共用值")
        for index, key in enumerate(periods[:shared_count])
    }
    outflows = {key: _outflow(index) for index, key in enumerate(periods)}
    daily = []
    for date in dates_in_projection_range(start, end):
        item = {
            "date": date.isoformat(),
            **copy.deepcopy(outflows[working_period_key_for_date(date)]),
        }
        if date.isoformat() == "2026-09-15":
            item.update(
                upstream_irrigation_cms=777.0,
                source_type="人工逐日調整",
                note="必須原樣保留",
            )
        daily.append(item)
    return {
        "schema": "liyutan-reservoir-estimator/batch",
        "schema_version": 1,
        "batch_id": "batch-continuation",
        "batch_name": "正式接續",
        "display_start_date": display,
        "projection_start_date": start,
        "projection_end_date": end,
        "initial_capacity": 8123.0,
        "historical_capacities": {"2026-08-31": 8123.0},
        "reservoir_parameters": {
            "max_capacity": 11584.0,
            "shilin_eco_flow": 2.7,
            "liyutan_eco_flow": 0.3,
            "shilin_diversion_limit": 33.0,
        },
        "periods": periods,
        "shared_period_count": shared_count,
        "shared_inflows": shared,
        "scenarios": scenarios,
        "outflows": outflows,
        "daily_outflows": daily,
        "date_overrides": [
            {
                "start": "2028-01-01",
                "end": "2028-01-02",
                "up_irr": 1.0,
                "down_irr": 2.0,
                "public": 3.0,
                "reason": "目前範圍外也要保留",
            }
        ],
        "overrides_enabled": False,
        "created_at": "2026-09-01T00:00:00Z",
        "note": "working note",
        "results": {"scenario-a": {"status": "success"}},
        "results_fingerprint": "stale-fingerprint",
    }


def _draft(**batch_kwargs):
    batch = _batch(**batch_kwargs)
    validate_batch(batch)
    return OfficialContinuationDraft(
        batch=batch,
        derived_from_official_version_id="estimate-source-y",
        annual_data_version_id=ANNUAL_A,
    )


def _scenario(batch, scenario_id):
    return next(item for item in batch["scenarios"] if item["scenario_id"] == scenario_id)


@pytest.mark.parametrize(
    ("date", "working_key", "annual_key"),
    [
        ("2026-01-10", "2026-1-上旬", "01-上旬"),
        ("2026-01-11", "2026-1-中旬", "01-中旬"),
        ("2026-01-20", "2026-1-中旬", "01-中旬"),
        ("2026-01-21", "2026-1-下旬", "01-下旬"),
    ],
)
def test_calendar_mapping_has_explicit_day_boundaries(date, working_key, annual_key):
    assert working_period_key_for_date(date) == working_key
    assert annual_period_key_for_working_period(working_key) == annual_key


def test_end_extension_preserves_three_periods_and_adds_two_from_explicit_annual():
    draft = _draft()
    source = copy.deepcopy(draft.batch)

    adjusted = adjust_continuation_dates(
        draft,
        projection_end_date="2026-10-21",
        extension_annual_snapshot=_annual(),
    )

    assert adjusted.preserved_periods == tuple(source["periods"])
    assert adjusted.added_periods == ("2026-10-上旬", "2026-10-中旬")
    assert adjusted.removed_periods == ()
    for scenario in adjusted.batch["scenarios"]:
        old = _scenario(source, scenario["scenario_id"])
        for key in source["periods"]:
            assert scenario["inflows"][key] == old["inflows"][key]
        for key in adjusted.added_periods:
            assert scenario["inflows"][key] == {
                "cms": None,
                "source_type": "待填",
                "source_unit": "cms",
                "source_value": None,
                "note": "",
            }
    for key in source["periods"]:
        assert adjusted.batch["outflows"][key] == source["outflows"][key]
    for key in adjusted.added_periods:
        assert adjusted.batch["outflows"][key]["upstream_irrigation_cms"] == 30.0
        assert ANNUAL_B in adjusted.batch["outflows"][key]["source_type"]
    assert len(adjusted.batch["daily_outflows"]) == 50
    assert adjusted.batch["initial_capacity"] == source["initial_capacity"]
    assert adjusted.annual_data_version_id == ANNUAL_B
    assert adjusted.extension_annual_version_id == ANNUAL_B
    assert adjusted.requires_recalculation is True
    assert "results" not in adjusted.batch
    assert "results_fingerprint" not in adjusted.batch


def test_annual_transition_never_rewrites_overlap_values():
    draft = _draft()
    source = copy.deepcopy(draft.batch)
    annual_b = _annual(offset=1000.0)

    adjusted = adjust_continuation_dates(
        draft,
        projection_end_date="2026-10-11",
        extension_annual_snapshot=annual_b,
    )

    assert adjusted.annual_data_version_id == ANNUAL_B
    for key in source["periods"]:
        assert adjusted.batch["outflows"][key] == source["outflows"][key]
        for scenario in adjusted.batch["scenarios"]:
            assert scenario["inflows"][key] == _scenario(source, scenario["scenario_id"])["inflows"][key]
    old_daily = {item["date"]: item for item in source["daily_outflows"]}
    for item in adjusted.batch["daily_outflows"]:
        if item["date"] in old_daily:
            assert item == old_daily[item["date"]]


def test_same_period_extension_does_not_switch_annual_and_uses_working_outflow():
    draft = _draft(start="2026-09-11", end="2026-09-16", display="2026-09-01")
    period = draft.batch["periods"][0]

    adjusted = adjust_continuation_dates(
        draft,
        projection_end_date="2026-09-20",
        extension_annual_snapshot=_annual(offset=1000.0),
    )

    assert adjusted.added_periods == ()
    assert adjusted.annual_data_version_id == ANNUAL_A
    assert adjusted.extension_annual_version_id is None
    assert adjusted.batch["outflows"] == draft.batch["outflows"]
    for item in adjusted.batch["daily_outflows"]:
        if item["date"] >= "2026-09-16":
            assert item["upstream_irrigation_cms"] == draft.batch["outflows"][period]["upstream_irrigation_cms"]
            assert item["source_type"] == "正式工作出流"


def test_q90_fills_only_added_blank_cells_and_keeps_manual_and_overlap():
    annual = _annual()
    adjustment = adjust_continuation_dates(
        _draft(), projection_end_date="2026-10-21", extension_annual_snapshot=annual
    )
    before_overlap = copy.deepcopy(_scenario(adjustment.batch, "scenario-a")["inflows"]["2026-9-上旬"])
    adjustment.batch["scenarios"][0]["inflows"]["2026-10-中旬"] = _inflow(999.0, "人工")

    filled = apply_added_period_q90(
        adjustment, scenario_ids="scenario-a", annual_snapshot=annual
    )

    inflows = _scenario(filled.batch, "scenario-a")["inflows"]
    assert inflows["2026-10-上旬"]["cms"] == 100.0
    assert inflows["2026-10-上旬"]["source_type"] == "年度基準 Q90"
    assert ANNUAL_B in inflows["2026-10-上旬"]["note"]
    assert inflows["2026-10-中旬"]["cms"] == 999.0
    assert inflows["2026-9-上旬"] == before_overlap
    assert _scenario(filled.batch, "scenario-b")["inflows"]["2026-10-上旬"]["cms"] is None


def test_q_fill_does_not_infer_old_pending_overlap_from_source_type():
    annual = _annual()
    draft = _draft()
    old_key = "2026-9-上旬"
    _scenario(draft.batch, "scenario-a")["inflows"][old_key] = {
        "cms": None,
        "source_type": "待填",
        "source_unit": "cms",
        "source_value": None,
        "note": "來源正式版原本就 pending",
    }
    adjustment = adjust_continuation_dates(
        draft, projection_end_date="2026-10-11", extension_annual_snapshot=annual
    )

    filled = apply_added_period_q90(
        adjustment, scenario_ids="scenario-a", annual_snapshot=annual
    )

    assert _scenario(filled.batch, "scenario-a")["inflows"][old_key]["cms"] is None
    assert _scenario(filled.batch, "scenario-a")["inflows"]["2026-10-上旬"]["cms"] == 100.0


def test_q80_q90_are_scenario_specific_and_scenario_ids_never_change():
    annual = _annual()
    adjustment = adjust_continuation_dates(
        _draft(), projection_end_date="2026-10-11", extension_annual_snapshot=annual
    )
    original_ids = [item["scenario_id"] for item in adjustment.batch["scenarios"]]

    q90 = apply_added_period_q90(adjustment, scenario_ids="scenario-a", annual_snapshot=annual)
    mixed = apply_added_period_q80(q90, scenario_ids="scenario-b", annual_snapshot=annual)

    key = "2026-10-上旬"
    assert _scenario(mixed.batch, "scenario-a")["inflows"][key]["cms"] == 100.0
    assert _scenario(mixed.batch, "scenario-b")["inflows"][key]["cms"] == 90.0
    assert _scenario(mixed.batch, "scenario-c")["inflows"][key]["cms"] is None
    assert [item["scenario_id"] for item in mixed.batch["scenarios"]] == original_ids


def test_q_fill_can_target_an_added_period_subset_for_shared_ui_routing():
    annual = _annual()
    adjustment = adjust_continuation_dates(
        _draft(), projection_end_date="2026-10-21", extension_annual_snapshot=annual
    )

    filled = apply_added_period_q90(
        adjustment,
        scenario_ids="scenario-a",
        annual_snapshot=annual,
        period_keys=("2026-10-上旬",),
    )

    inflows = _scenario(filled.batch, "scenario-a")["inflows"]
    assert inflows["2026-10-上旬"]["cms"] == 100.0
    assert inflows["2026-10-中旬"]["cms"] is None


def test_only_q80_and_q90_are_supported():
    annual = _annual()
    adjustment = adjust_continuation_dates(
        _draft(), projection_end_date="2026-10-11", extension_annual_snapshot=annual
    )

    with pytest.raises(ContinuationDateAdjustmentError) as captured:
        apply_added_period_quantile(
            adjustment,
            scenario_ids="scenario-a",
            quantile="Q50",
            annual_snapshot=annual,
        )

    assert captured.value.code is ContinuationDateAdjustmentErrorCode.INVALID_QUANTILE


def test_added_shared_period_is_filled_consistently_for_all_scenarios():
    annual = _annual()
    draft = _draft(start="2026-09-11", end="2026-10-01", display="2026-09-01", shared_count=1)
    adjustment = adjust_continuation_dates(
        draft,
        projection_start_date="2026-09-01",
        extension_annual_snapshot=annual,
    )
    assert adjustment.added_periods == ("2026-9-上旬",)
    assert adjustment.batch["shared_inflows"]["2026-9-上旬"]["cms"] is None
    former_shared = draft.batch["shared_inflows"]["2026-9-中旬"]
    for scenario in adjustment.batch["scenarios"]:
        assert scenario["inflows"]["2026-9-中旬"] == former_shared

    filled = apply_added_period_q80(
        adjustment,
        scenario_ids=("scenario-a", "scenario-b", "scenario-c"),
        annual_snapshot=annual,
    )

    shared = filled.batch["shared_inflows"]["2026-9-上旬"]
    assert shared["cms"] == 89.0
    for scenario in filled.batch["scenarios"]:
        assert scenario["inflows"]["2026-9-上旬"] == shared


def test_shared_added_period_rejects_partial_scenario_selection_atomically():
    annual = _annual()
    adjustment = adjust_continuation_dates(
        _draft(start="2026-09-11", end="2026-10-01", display="2026-09-01", shared_count=1),
        projection_start_date="2026-09-01",
        extension_annual_snapshot=annual,
    )
    before = copy.deepcopy(adjustment.batch)

    with pytest.raises(ContinuationDateAdjustmentError) as captured:
        apply_added_period_q90(
            adjustment, scenario_ids="scenario-a", annual_snapshot=annual
        )

    assert captured.value.code is ContinuationDateAdjustmentErrorCode.SHARED_INFLOW_CONFLICT
    assert adjustment.batch == before


def test_daily_overlap_manual_row_is_preserved_exactly():
    draft = _draft()
    original = next(item for item in draft.batch["daily_outflows"] if item["date"] == "2026-09-15")

    adjusted = adjust_continuation_dates(
        draft, projection_end_date="2026-10-11", extension_annual_snapshot=_annual()
    )

    retained = next(item for item in adjusted.batch["daily_outflows"] if item["date"] == "2026-09-15")
    assert retained == original
    assert retained is not original


def test_end_shrink_removes_periods_and_dates_without_annual_or_capacity_change():
    draft = _draft()

    adjusted = adjust_continuation_dates(draft, projection_end_date="2026-09-16")

    assert adjusted.added_periods == ()
    assert adjusted.removed_periods == ("2026-9-下旬",)
    assert adjusted.preserved_periods == ("2026-9-上旬", "2026-9-中旬")
    assert [item["date"] for item in adjusted.batch["daily_outflows"]][-1] == "2026-09-15"
    assert adjusted.batch["initial_capacity"] == draft.batch["initial_capacity"]
    assert adjusted.annual_data_version_id == ANNUAL_A


def test_projection_start_forward_clears_capacity_and_preserves_overlap_by_key():
    draft = _draft(shared_count=1)
    source = copy.deepcopy(draft.batch)

    adjusted = adjust_continuation_dates(draft, projection_start_date="2026-09-11")

    assert adjusted.added_periods == ()
    assert adjusted.removed_periods == ("2026-9-上旬",)
    assert adjusted.batch["initial_capacity"] is None
    assert adjusted.initial_capacity_requires_confirmation is True
    for scenario in adjusted.batch["scenarios"]:
        source_scenario = _scenario(source, scenario["scenario_id"])
        for key in adjusted.preserved_periods:
            assert scenario["inflows"][key] == source_scenario["inflows"][key]
    assert adjusted.batch["batch_id"] == draft.batch["batch_id"]
    assert adjusted.draft.derived_from_official_version_id == draft.derived_from_official_version_id


def test_projection_start_backward_adds_annual_period_and_invalidates_capacity():
    draft = _draft(start="2026-09-11", end="2026-10-01", display="2026-09-01")

    adjusted = adjust_continuation_dates(
        draft,
        projection_start_date="2026-09-01",
        extension_annual_snapshot=_annual(),
    )

    assert adjusted.added_periods == ("2026-9-上旬",)
    assert adjusted.batch["initial_capacity"] is None
    assert adjusted.initial_capacity_requires_confirmation is True
    assert adjusted.annual_data_version_id == ANNUAL_B
    for scenario in adjusted.batch["scenarios"]:
        assert scenario["inflows"]["2026-9-上旬"]["cms"] is None
    assert adjusted.batch["outflows"]["2026-9-上旬"]["upstream_irrigation_cms"] == 29.0


@pytest.mark.parametrize("invalid", [-1, float("nan"), float("inf"), None, True, "invalid"])
def test_confirm_initial_capacity_rejects_invalid_values(invalid):
    pending = adjust_continuation_dates(_draft(), projection_start_date="2026-09-11")

    with pytest.raises(ContinuationDateAdjustmentError) as captured:
        confirm_initial_capacity(pending, invalid)

    assert captured.value.code is ContinuationDateAdjustmentErrorCode.INVALID_INITIAL_CAPACITY
    assert pending.batch["initial_capacity"] is None


def test_confirm_initial_capacity_clears_pending_and_can_restore_full_validation():
    pending = adjust_continuation_dates(_draft(), projection_start_date="2026-09-11")

    confirmed = confirm_initial_capacity(pending, 7000.5)

    assert confirmed.batch["initial_capacity"] == 7000.5
    assert confirmed.initial_capacity_requires_confirmation is False
    assert confirmed.requires_recalculation is True
    assert validate_batch(confirmed.batch) == confirmed.batch


def test_display_start_only_preserves_capacity_periods_annual_and_marks_stale():
    draft = _draft()
    source = copy.deepcopy(draft.batch)

    adjusted = adjust_continuation_dates(draft, display_start_date="2026-08-15")

    assert adjusted.batch["initial_capacity"] == source["initial_capacity"]
    assert adjusted.batch["periods"] == source["periods"]
    assert adjusted.batch["scenarios"] == source["scenarios"]
    assert adjusted.batch["outflows"] == source["outflows"]
    assert adjusted.batch["daily_outflows"] == source["daily_outflows"]
    assert adjusted.annual_data_version_id == ANNUAL_A
    assert adjusted.requires_recalculation is True
    assert "results" not in adjusted.batch


def test_out_of_range_override_and_historical_capacities_are_never_clipped():
    draft = _draft()

    adjusted = adjust_continuation_dates(draft, projection_end_date="2026-09-16")

    assert adjusted.batch["date_overrides"] == draft.batch["date_overrides"]
    assert adjusted.batch["historical_capacities"] == draft.batch["historical_capacities"]


def test_year_boundary_maps_working_periods_to_month_period_annual_keys():
    draft = _draft(start="2026-12-21", end="2027-01-01", display="2026-12-01")

    adjusted = adjust_continuation_dates(
        draft,
        projection_end_date="2027-01-11",
        extension_annual_snapshot=_annual(),
    )

    assert adjusted.preserved_periods == ("2026-12-下旬",)
    assert adjusted.added_periods == ("2027-1-上旬",)
    assert annual_period_key_for_working_period("2026-12-下旬") == "12-下旬"
    assert annual_period_key_for_working_period("2027-1-上旬") == "01-上旬"
    assert adjusted.batch["outflows"]["2027-1-上旬"]["upstream_irrigation_cms"] == 21.0


@pytest.mark.parametrize("missing_kind", ["hydrology", "outflow"])
def test_missing_annual_period_fails_atomically(missing_kind):
    draft = _draft()
    source = copy.deepcopy(draft.batch)
    annual = _annual()
    if missing_kind == "hydrology":
        annual = AnnualDataSnapshot(
            current=annual.current,
            version=annual.version,
            hydrology=tuple(row for row in annual.hydrology if row["period_key"] != "10-上旬"),
            outflow_demand=annual.outflow_demand,
            reservoir_parameters=annual.reservoir_parameters,
            parameter_metadata=annual.parameter_metadata,
        )
    else:
        annual = AnnualDataSnapshot(
            current=annual.current,
            version=annual.version,
            hydrology=annual.hydrology,
            outflow_demand=tuple(row for row in annual.outflow_demand if row["period_key"] != "10-上旬"),
            reservoir_parameters=annual.reservoir_parameters,
            parameter_metadata=annual.parameter_metadata,
        )

    with pytest.raises(ContinuationDateAdjustmentError) as captured:
        adjust_continuation_dates(
            draft, projection_end_date="2026-10-11", extension_annual_snapshot=annual
        )

    assert captured.value.code is ContinuationDateAdjustmentErrorCode.MISSING_ANNUAL_PERIOD
    assert draft.batch == source


def test_new_period_requires_explicit_annual_snapshot():
    with pytest.raises(ContinuationDateAdjustmentError) as captured:
        adjust_continuation_dates(_draft(), projection_end_date="2026-10-11")

    assert captured.value.code is ContinuationDateAdjustmentErrorCode.EXTENSION_ANNUAL_REQUIRED


def test_invalid_annual_version_identity_fails_before_returning_a_draft():
    draft = _draft()
    source = copy.deepcopy(draft.batch)
    annual = _annual(version_id="invalid/version")

    with pytest.raises(ContinuationDateAdjustmentError) as captured:
        adjust_continuation_dates(
            draft, projection_end_date="2026-10-11", extension_annual_snapshot=annual
        )

    assert captured.value.code is ContinuationDateAdjustmentErrorCode.INVALID_ANNUAL_SNAPSHOT
    assert draft.batch == source


def test_adjustment_and_followup_mutations_never_alias_source_draft():
    draft = _draft()
    source = copy.deepcopy(draft.batch)
    adjusted = adjust_continuation_dates(
        draft, projection_end_date="2026-10-11", extension_annual_snapshot=_annual()
    )

    adjusted.batch["scenarios"][0]["inflows"]["2026-9-上旬"]["cms"] = 9999.0
    adjusted.batch["outflows"]["2026-9-上旬"]["upstream_irrigation_cms"] = 9999.0
    adjusted.batch["daily_outflows"][0]["upstream_irrigation_cms"] = 9999.0
    adjusted.batch["date_overrides"][0]["reason"] = "changed"

    assert draft.batch == source


def test_loader_to_continuation_to_date_adjustment_is_read_only(tmp_path: Path):
    root = _build_root(tmp_path, versions=[("estimate-current", None)])
    snapshot = load_official_current(root).snapshot
    draft = build_official_continuation(
        snapshot,
        batch_id_factory=lambda: "batch-date-adjustment",
        created_at="2027-01-20T04:05:06Z",
    )
    before = _filesystem_snapshot(root)

    adjusted = adjust_continuation_dates(
        draft,
        projection_end_date="2027-01-12",
        extension_annual_snapshot=_annual(),
    )

    assert adjusted.added_periods == ("2027-1-中旬",)
    assert adjusted.batch["batch_id"] == "batch-date-adjustment"
    assert adjusted.draft.derived_from_official_version_id == "estimate-current"
    assert _filesystem_snapshot(root) == before


def test_noop_does_not_rebase_or_invalidate_results():
    draft = _draft()

    adjusted = adjust_continuation_dates(draft, extension_annual_snapshot=_annual())

    assert adjusted.added_periods == ()
    assert adjusted.annual_data_version_id == ANNUAL_A
    assert adjusted.requires_recalculation is False
    assert adjusted.batch["results"] == draft.batch["results"]
    assert adjusted.batch["results_fingerprint"] == draft.batch["results_fingerprint"]


def test_pending_validator_does_not_relax_formal_batch_validation():
    pending = adjust_continuation_dates(_draft(), projection_start_date="2026-09-11")

    assert validate_continuation_pending_batch(
        pending.batch, initial_capacity_requires_confirmation=True
    ) == pending.batch
    with pytest.raises(ValueError):
        validate_batch(pending.batch)
