import copy
import datetime as dt

import pandas as pd
import pytest

from official_estimate_candidate import (
    build_official_estimate_candidate,
    candidate_is_current,
)
from shared_storage_schema import StorageValidationError, validate_official_bundle
from v2_workflow import (
    SCHEMA_NAME,
    SCHEMA_VERSION,
    run_batch,
    scenario_template,
    settings_fingerprint,
    standardize_comparison_result,
    store_session_results,
)


PERIODS = ["2026-8-上旬", "2026-8-中旬", "2026-8-下旬"]
CLEAN_SOFTWARE = {
    "repository": "mousepenguin-yzh/liyutan-reservoir-estimator",
    "git_commit": "a" * 40,
    "app_version": "git-aaaaaaaaaaaa",
    "source_tree_dirty": False,
}


def _cell(value, source="Q50"):
    numeric = float(value)
    return {
        "cms": numeric,
        "source_type": source,
        "source_unit": "cms",
        "source_value": numeric,
        "note": "",
    }


def _batch(scenario_count=2):
    scenarios = scenario_template("standard" if scenario_count == 2 else "single", PERIODS)
    scenarios = scenarios[:scenario_count]
    for index, scenario in enumerate(scenarios):
        for key in PERIODS:
            scenario["inflows"][key] = _cell(10 + index * 10)
    return {
        "schema": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "batch_id": "batch-25b",
        "batch_name": "2-5B 測試批次",
        "display_start_date": "2026-07-30",
        "projection_start_date": "2026-08-01",
        "projection_end_date": "2026-09-01",
        "initial_capacity": 8000.0,
        "historical_capacities": {
            "2026-07-29": 8000.0,
            "2026-07-30": 7980.0,
            "2026-07-31": 7990.0,
        },
        "reservoir_parameters": {
            "max_capacity": 11584.0,
            "shilin_eco_flow": 2.7,
            "liyutan_eco_flow": 0.3,
            "shilin_diversion_limit": 31.5,
        },
        "periods": PERIODS,
        "shared_period_count": 0,
        "shared_inflows": {},
        "scenarios": scenarios,
        "outflows": {
            key: {
                "upstream_irrigation_cms": 2.7,
                "downstream_irrigation_cms": 0.3,
                "public_water_10k_ton_per_day": 60.0,
                "source_type": "年度標準",
                "note": "",
            }
            for key in PERIODS
        },
        "daily_outflows": [
            {
                "date": f"2026-08-{day:02d}",
                "upstream_irrigation_cms": 2.7,
                "downstream_irrigation_cms": 0.3,
                "public_water_10k_ton_per_day": 60.0,
                "source_type": "年度標準",
                "note": "",
            }
            for day in range(1, 32)
        ],
        "date_overrides": [],
        "overrides_enabled": False,
        "created_at": "2026-08-01T00:00:00+00:00",
        "note": "",
    }


def _profile():
    dates = pd.date_range("2026-08-01", "2026-09-01", inclusive="left").date
    daily = pd.DataFrame(
        {
            "日期": dates,
            "年份": [date.year for date in dates],
            "月份": [date.month for date in dates],
            "旬別": ["上旬" if date.day <= 10 else "中旬" if date.day <= 20 else "下旬" for date in dates],
        }
    )
    outflow = pd.DataFrame(
        {
            "日期": dates,
            "上灌區當日流量(cms)": [2.7] * len(dates),
            "下灌區當日流量(cms)": [0.3] * len(dates),
            "公共供水當日水量(萬噸)": [60.0] * len(dates),
        }
    )
    return daily, outflow


def _ready(scenario_count=2):
    batch = _batch(scenario_count)
    daily, outflow = _profile()
    results = run_batch(batch, daily, outflow)
    state = {"v2_batch": batch}
    store_session_results(state, results)
    return batch, results


def _kwargs(**changes):
    values = {
        "annual_data_version_id": "annual-2026-001",
        "shared_annual_data_validated": True,
        "operator_display_name": "王承辦",
        "note": "正式預覽測試",
        "software": CLEAN_SOFTWARE,
        "previous_official_version_id": None,
        "derived_from_official_version_id": None,
        "created_at": "2026-08-02T03:04:05Z",
    }
    values.update(changes)
    return values


def _build(batch, results, selected, **changes):
    return build_official_estimate_candidate(
        batch,
        results,
        selected,
        estimate_version_id="estimate-candidate-test",
        **_kwargs(**changes),
    )


def test_single_scenario_candidate_is_complete_and_valid():
    batch, results = _ready()
    selected = [batch["scenarios"][0]["scenario_id"]]
    candidate = _build(batch, results, selected)

    assert set(candidate.files) == {
        "manifest.json",
        "inputs.json",
        "scenario_summaries.csv",
        "daily_results.csv",
        "COMMITTED.json",
    }
    assert validate_official_bundle(candidate.files)["manifest"]["official_scenario_ids"] == selected
    assert candidate.preview["has_custom_or_adjusted_data"] is False


def test_multiple_scenario_candidate_is_complete_and_valid():
    batch, results = _ready()
    selected = [scenario["scenario_id"] for scenario in batch["scenarios"]]
    candidate = _build(
        batch,
        results,
        selected,
        previous_official_version_id="estimate-previous",
        derived_from_official_version_id="estimate-derived",
    )

    validated = candidate.validated_bundle
    assert validated["manifest"]["previous_official_version_id"] == "estimate-previous"
    assert validated["manifest"]["derived_from_official_version_id"] == "estimate-derived"
    assert {row["scenario_id"] for row in validated["scenario_summaries"]} == set(selected)


def test_preview_marks_explicit_batch_adjustment_metadata():
    batch = _batch(1)
    batch["note"] = "本批次使用人工調整條件"
    daily, outflow = _profile()
    results = run_batch(batch, daily, outflow)
    store_session_results({"v2_batch": batch}, results)

    candidate = _build(batch, results, [batch["scenarios"][0]["scenario_id"]])

    assert candidate.preview["has_custom_or_adjusted_data"] is True


def test_no_scenario_cannot_build_candidate():
    batch, results = _ready()
    with pytest.raises(StorageValidationError, match="不可為空"):
        _build(batch, results, [])


def test_failed_selected_scenario_cannot_build_candidate():
    batch, results = _ready()
    selected = batch["scenarios"][0]["scenario_id"]
    results[selected] = {"status": "error", "error": "測試錯誤"}
    with pytest.raises(StorageValidationError, match="尚未計算成功"):
        _build(batch, results, [selected])


def test_stale_results_cannot_build_candidate():
    batch, results = _ready()
    batch["initial_capacity"] += 1
    with pytest.raises(StorageValidationError, match="已過期"):
        _build(batch, results, [batch["scenarios"][0]["scenario_id"]])


def test_scenario_outside_current_batch_cannot_build_candidate():
    batch, results = _ready()
    with pytest.raises(StorageValidationError, match="目前正在工作的 V2 batch"):
        _build(batch, results, ["other-batch-scenario"])


@pytest.mark.parametrize("shared_validated", [False, None])
def test_builtin_or_unverified_annual_data_cannot_build_candidate(shared_validated):
    batch, results = _ready()
    with pytest.raises(StorageValidationError, match="共享年度基準"):
        _build(
            batch,
            results,
            [batch["scenarios"][0]["scenario_id"]],
            shared_annual_data_validated=shared_validated,
        )


def test_dirty_source_tree_cannot_build_candidate():
    batch, results = _ready()
    dirty = {**CLEAN_SOFTWARE, "source_tree_dirty": True}
    with pytest.raises(StorageValidationError, match="source tree dirty"):
        _build(batch, results, [batch["scenarios"][0]["scenario_id"]], software=dirty)


@pytest.mark.parametrize(
    ("field", "message"),
    [("operator_display_name", "操作人"), ("note", "備註")],
)
def test_operator_and_note_are_required(field, message):
    batch, results = _ready()
    with pytest.raises(StorageValidationError, match=message):
        _build(batch, results, [batch["scenarios"][0]["scenario_id"]], **{field: "  "})


def test_inputs_and_csvs_only_contain_selected_scenarios_and_no_runtime_results():
    batch, results = _ready()
    selected = batch["scenarios"][1]["scenario_id"]
    batch["results"] = {"runtime-only": True}
    assert batch["results_fingerprint"] == settings_fingerprint(batch)
    candidate = _build(batch, results, [selected])
    validated = candidate.validated_bundle

    assert validated["inputs"]["official_scenario_ids"] == [selected]
    assert [item["scenario_id"] for item in validated["inputs"]["batch"]["scenarios"]] == [selected]
    assert "results" not in validated["inputs"]["batch"]
    assert "results_fingerprint" not in validated["inputs"]["batch"]
    assert {row["scenario_id"] for row in validated["scenario_summaries"]} == {selected}
    assert {row["scenario_id"] for row in validated["daily_results"]} == {selected}
    assert validated["inputs"]["reservoir_parameters"]["shilin_diversion_limit_cms"] == 31.5


def test_cross_batch_comparison_registry_is_not_a_candidate_result_source():
    batch, results = _ready()
    scenario = batch["scenarios"][0]
    comparison = standardize_comparison_result(batch, scenario, results[scenario["scenario_id"]]["data"])
    registry = {comparison["result_id"]: comparison}

    with pytest.raises(StorageValidationError, match="尚未計算成功"):
        _build(batch, registry, [scenario["scenario_id"]])


def test_historical_display_rows_are_excluded_and_projection_range_is_exact():
    batch, results = _ready()
    scenario_id = batch["scenarios"][0]["scenario_id"]
    projection = results[scenario_id]["data"]
    history = projection.iloc[[0]].copy()
    history.loc[:, "日期"] = dt.date(2026, 7, 31)
    history.loc[:, "本日末庫容 (萬噸)"] = 9999
    results[scenario_id]["data"] = pd.concat([history, projection], ignore_index=True)
    candidate = _build(batch, results, [scenario_id])
    dates = [row["date"] for row in candidate.validated_bundle["daily_results"]]

    assert dates[0] == "2026-08-01"
    assert dates[-1] == "2026-08-31"
    assert len(dates) == 31
    assert "2026-07-31" not in dates


def test_missing_projection_date_is_rejected_by_full_bundle_validator():
    batch, results = _ready()
    scenario_id = batch["scenarios"][0]["scenario_id"]
    results[scenario_id]["data"] = results[scenario_id]["data"].iloc[:-1].copy()
    with pytest.raises(StorageValidationError, match="缺少正式情境"):
        _build(batch, results, [scenario_id])


def test_candidate_becomes_stale_when_batch_changes():
    batch, results = _ready()
    selected = [batch["scenarios"][0]["scenario_id"]]
    candidate = _build(batch, results, selected)
    context = _kwargs()
    context.pop("created_at")
    assert candidate_is_current(candidate, batch, results, selected, **context)

    batch["reservoir_parameters"]["shilin_diversion_limit"] += 1
    assert not candidate_is_current(candidate, batch, results, selected, **context)


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("selection", None),
        ("operator_display_name", "李承辦"),
        ("note", "修改後備註"),
        ("annual_data_version_id", "annual-2026-002"),
    ],
)
def test_candidate_becomes_stale_when_preview_context_changes(change, value):
    batch, results = _ready()
    selected = [batch["scenarios"][0]["scenario_id"]]
    candidate = _build(batch, results, selected)
    context = _kwargs()
    context.pop("created_at")
    changed_selection = selected
    if change == "selection":
        changed_selection = [batch["scenarios"][1]["scenario_id"]]
    else:
        context[change] = value

    assert not candidate_is_current(
        candidate, batch, results, changed_selection, **context
    )


def test_multiselect_click_order_does_not_invalidate_same_scenario_set():
    batch, results = _ready()
    selected = [scenario["scenario_id"] for scenario in batch["scenarios"]]
    candidate = _build(batch, results, selected)
    context = _kwargs()
    context.pop("created_at")

    assert candidate_is_current(
        candidate, batch, results, list(reversed(selected)), **context
    )
