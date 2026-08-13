import copy
import datetime as dt

import pandas as pd
import pytest

from v2_workflow import (SCHEMA_NAME, SCHEMA_VERSION, UNIT_10K_TON_DAY, UNIT_CMS,
    add_scenario, copy_scenario, delete_scenario, expand_shared_inflows, export_batch,
    find_override_overlaps, import_batch, invalidate_results, make_scenario,
    current_session_results, daily_outflow_frame, invalidate_session_results,
    parse_pasted_values, prepend_history, rename_scenario, reorder_scenarios, results_are_current,
    run_batch, run_water_balance, scenario_template, settings_fingerprint, to_cms,
    standardize_comparison_result, store_session_results, validate_scenario)

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
        "daily_outflows": [{"date": f"2026-08-{day:02d}", "upstream_irrigation_cms": 2.7,
            "downstream_irrigation_cms": .3, "public_water_10k_ton_per_day": 60,
            "source_type": "前一年度", "note": ""} for day in range(1, 32)],
        "date_overrides": [], "overrides_enabled": False,
        "created_at": "2026-08-13T00:00:00+00:00", "note": ""}


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


def test_ui_state_delete_template_and_outflow_changes_invalidate_results():
    value = batch(); state = {"v2_batch": value}
    results = {value["scenarios"][0]["scenario_id"]: {"status": "success", "data": pd.DataFrame()}}
    store_session_results(state, results)
    assert current_session_results(state) is results
    # A button handler must invalidate before rerun; removed IDs can never leak/KeyError.
    invalidate_session_results(state)
    value["scenarios"] = scenario_template("standard", PERIODS)
    assert current_session_results(state) == {}
    assert "v2_selected_scenario" not in state
    store_session_results(state, {s["scenario_id"]: {"status": "success"} for s in value["scenarios"]})
    value["daily_outflows"][0]["public_water_10k_ton_per_day"] = 55
    assert current_session_results(state) == {}


def test_fresh_session_json_restores_authoritative_daily_outflow_and_override():
    value = batch()
    value["overrides_enabled"] = True
    value["date_overrides"] = [{"start": "2026-08-04", "end": "2026-08-06", "up_irr": 1,
        "down_irr": 2, "public": 45, "reason": "抗旱測試"}]
    for item in value["daily_outflows"]:
        if "2026-08-04" <= item["date"] <= "2026-08-06":
            item.update({"upstream_irrigation_cms": 1, "downstream_irrigation_cms": 2,
                         "public_water_10k_ton_per_day": 45, "source_type": "抗旱覆寫"})
    fresh_state = {"v2_batch": import_batch(export_batch(value))}
    restored = daily_outflow_frame(fresh_state["v2_batch"])
    assert fresh_state["v2_batch"]["overrides_enabled"] is True
    assert fresh_state["v2_batch"]["date_overrides"] == value["date_overrides"]
    assert restored.loc[restored["日期"] == dt.date(2026, 8, 5), "公共供水當日水量(萬噸)"].item() == 45
    assert len(restored) == 31


def test_same_display_names_different_settings_keep_unique_result_ids_and_snapshots():
    first = batch(); second = copy.deepcopy(first); second["initial_capacity"] = 7000
    scenario1, scenario2 = first["scenarios"][0], second["scenarios"][0]
    frame = pd.DataFrame({"日期": [dt.date(2026, 8, 1)], "本日末庫容 (萬噸)": [1]})
    one = standardize_comparison_result(first, scenario1, frame)
    two = standardize_comparison_result(second, scenario2, frame)
    registry = {one["result_id"]: one, two["result_id"]: two}
    assert len(registry) == 2
    assert {item["settings_snapshot"]["initial_capacity"] for item in registry.values()} == {8000, 7000}


def test_v2_history_and_representative_boundaries_match_independent_legacy_reference():
    dates = pd.date_range("2026-01-19", "2026-02-03", inclusive="left").date
    profile = pd.DataFrame({"日期": dates, "年份": [d.year for d in dates], "月份": [d.month for d in dates],
                            "旬別": ["上旬" if d.day <= 10 else "中旬" if d.day <= 20 else "下旬" for d in dates]})
    out = pd.DataFrame({"日期": dates, "上灌區當日流量(cms)": [20] * len(dates),
                        "下灌區當日流量(cms)": [10] * len(dates),
                        "公共供水當日水量(萬噸)": [120] * len(dates)})
    keys = {f"{d.year}-{d.month}-{'上旬' if d.day <= 10 else '中旬' if d.day <= 20 else '下旬'}" for d in dates}

    def legacy_reference(initial, inflow):
        cap, rows = initial, []
        for _ in dates:
            actual_u = min(20, inflow); actual_d = min(10, max(0, inflow - actual_u))
            diversion = min(33, max(0, inflow - min(inflow, max(2.7, actual_u))))
            calculated = cap + round(diversion * 8.64, 2) - round(120 + round(max(.3, actual_d) * 8.64, 2), 2)
            spill = round(max(0, calculated - 11584), 2); cap = 11584 if calculated > 11584 else max(0, round(calculated, 2))
            rows.append((cap, spill, round(30 - actual_u - actual_d, 2)))
        return rows

    for initial, inflow in [(11580, 80), (500, 1)]:  # spill and agricultural reduction / empty boundary
        actual, _ = run_water_balance(profile, {key: inflow for key in keys}, out, initial, 11584, 2.7, .3)
        expected = legacy_reference(initial, inflow)
        assert list(zip(actual["本日末庫容 (萬噸)"], actual["溢流量 (萬噸)"], actual["農業削減量 (cms)"])) == expected
    projection, _ = run_water_balance(profile, {key: 10 for key in keys}, out, 8000, 11584, 2.7, .3)
    history = {dt.date(2026, 1, 15): 7900, dt.date(2026, 1, 16): 7920,
               dt.date(2026, 1, 17): 7950, dt.date(2026, 1, 18): 8000}
    combined = prepend_history(projection, dt.date(2026, 1, 15), dt.date(2026, 1, 19), history, 8000)
    assert combined.iloc[:4]["運行狀態"].eq("📊 觀測/歷史").all()
    assert combined.iloc[4:]["運行狀態"].eq("🔮 未來推估").all()
    assert combined["日期"].iloc[0] == dt.date(2026, 1, 15)
