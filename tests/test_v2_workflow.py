import copy
import datetime as dt

import pandas as pd
import pytest

from v2_workflow import (SCHEMA_NAME, SCHEMA_VERSION, UNIT_10K_TON_DAY, UNIT_CMS,
    add_scenario, copy_scenario, delete_scenario, expand_shared_inflows, export_batch,
    find_override_overlaps, import_batch, invalidate_results, make_scenario,
    parse_pasted_values, rename_scenario, reorder_scenarios, results_are_current,
    run_batch, run_water_balance, scenario_template, settings_fingerprint, to_cms,
    validate_scenario)

PERIODS = ["2026-8-上旬", "2026-8-中旬", "2026-8-下旬"]


def cell(value, source="手動"):
    return {"cms": value, "source_type": source, "source_unit": "cms", "source_value": value, "note": ""}


def batch():
    scenarios = scenario_template("single", PERIODS)
    for key in PERIODS: scenarios[0]["inflows"][key] = cell(10)
    return {"schema": SCHEMA_NAME, "schema_version": SCHEMA_VERSION, "batch_id": "batch-1",
        "batch_name": "測試", "display_start_date": "2026-08-01", "projection_start_date": "2026-08-01",
        "projection_end_date": "2026-09-01", "initial_capacity": 8000,
        "reservoir_parameters": {"max_capacity": 11584, "shilin_eco_flow": 2.7, "liyutan_eco_flow": .3},
        "periods": PERIODS, "shared_period_count": 0, "shared_inflows": {}, "scenarios": scenarios,
        "outflows": {key: {"upstream_irrigation_cms": 2.7, "downstream_irrigation_cms": .3,
                            "public_water_10k_ton_per_day": 60} for key in PERIODS},
        "date_overrides": [], "created_at": "2026-08-13T00:00:00+00:00", "note": ""}


def profiles(days=3):
    dates = [dt.date(2026, 8, i) for i in range(1, days + 1)]
    daily = pd.DataFrame({"日期": dates, "年份": [2026] * days, "月份": [8] * days, "旬別": ["上旬"] * days})
    out = pd.DataFrame({"日期": dates, "上灌區當日流量(cms)": [2.7] * days,
                        "下灌區當日流量(cms)": [.3] * days, "公共供水當日水量(萬噸)": [60.] * days})
    return daily, out


def test_units_and_paste_thousands_preview():
    assert to_cms(86.4, UNIT_10K_TON_DAY) == pytest.approx(10)
    assert to_cms(10, UNIT_CMS) == 10
    parsed = parse_pasted_values("1,234\t86.4\n10", UNIT_10K_TON_DAY, 3)
    assert [x["source_value"] for x in parsed] == [1234, 86.4, 10]
    assert parsed[1]["cms"] == pytest.approx(10)
    with pytest.raises(ValueError, match="預期 2 筆，實際解析 3 筆"):
        parse_pasted_values("1 2 3", UNIT_CMS, 2)


@pytest.mark.parametrize("bad", ["abc", "nan", "inf", "-1"])
def test_invalid_values_are_never_defaulted(bad):
    with pytest.raises(ValueError): parse_pasted_values(bad, UNIT_CMS, 1)
    scenario = make_scenario("缺漏", PERIODS)
    assert len(validate_scenario(scenario, PERIODS)) == 3


@pytest.mark.parametrize("shared_count", [0, 2, 3])
def test_shared_period_expansion_boundaries(shared_count):
    scenarios = scenario_template("standard", PERIODS)
    shared = {key: cell(i + 1, "共用研判") for i, key in enumerate(PERIODS[:shared_count])}
    result = expand_shared_inflows(PERIODS, shared_count, shared, scenarios)
    for scenario in result:
        for key in PERIODS[:shared_count]: assert scenario["inflows"][key] == shared[key]
        for key in PERIODS[shared_count:]: assert scenario["inflows"][key]["cms"] is None


def test_scenario_crud_and_order_preserve_inflows():
    scenarios = scenario_template("single", PERIODS)
    scenarios[0]["inflows"][PERIODS[0]] = cell(12)
    original = scenarios[0]["scenario_id"]
    scenarios = add_scenario(scenarios, "第二", PERIODS)
    scenarios = copy_scenario(scenarios, original)
    clone = scenarios[-1]["scenario_id"]
    assert scenarios[-1]["inflows"][PERIODS[0]]["cms"] == 12
    scenarios = rename_scenario(scenarios, clone, "複本")
    scenarios = reorder_scenarios(scenarios, [clone, original, scenarios[1]["scenario_id"]])
    scenarios = delete_scenario(scenarios, original)
    assert [s["order"] for s in scenarios] == [0, 1]
    assert scenarios[0]["name"] == "複本"


def test_override_overlap_detection_inclusive():
    overrides = [{"start": "2026-08-01", "end": "2026-08-05"},
                 {"start": "2026-08-05", "end": "2026-08-06"},
                 {"start": "2026-08-07", "end": "2026-08-08"}]
    assert find_override_overlaps(overrides) == [(0, 1)]


def test_json_round_trip_and_results_invalidation():
    original = batch()
    assert import_batch(export_batch(original)) == original
    original["results"] = {"x": "result"}
    original["results_fingerprint"] = settings_fingerprint(original)
    assert results_are_current(original)
    changed = invalidate_results(original); changed["initial_capacity"] = 7000
    assert not results_are_current(changed)
    assert "results" not in import_batch(export_batch(original))


def test_all_scenarios_share_outflow_and_failure_is_isolated():
    value = batch(); second = copy_scenario(value["scenarios"], value["scenarios"][0]["scenario_id"], "二")[1]
    for key in PERIODS: second["inflows"][key] = cell(20)
    value["scenarios"].append(second)
    daily, out = profiles()
    result = run_batch(value, daily, out)
    assert all(item["status"] == "success" for item in result.values())
    assert all(item["data"]["公共給水量 (萬噸)"].tolist() == [60, 60, 60] for item in result.values())
    value["scenarios"][1]["inflows"][PERIODS[0]]["cms"] = None
    result = run_batch(value, daily, out)
    assert sorted(item["status"] for item in result.values()) == ["error", "success"]


def test_single_scenario_matches_legacy_formula_reference():
    daily, out = profiles(2)
    frame, summary = run_water_balance(daily, {PERIODS[0]: 10}, out, 8000, 11584, 2.7, .3)
    # Legacy app.py: diversion=(10-max(2.7,2.7))*8.64=63.07; out=60+2.592=62.59.
    assert frame["本日末庫容 (萬噸)"].tolist() == [8000.48, 8000.96]
    assert summary["final_capacity"] == 8000.96
