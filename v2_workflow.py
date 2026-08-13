"""Pure domain logic for the V2 multi-scenario estimation workflow.

The module deliberately has no Streamlit dependency.  Session state is only an
adapter in ``app.py``; keeping validation and transformations here makes the
portable JSON format and the UI use exactly the same rules.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import re
import uuid
from typing import Any, Iterable

import pandas as pd

SCHEMA_NAME = "liyutan-reservoir-estimator/batch"
SCHEMA_VERSION = 1
UNIT_CMS = "cms"
UNIT_10K_TON_DAY = "10k_ton_per_day"


def new_id() -> str:
    return str(uuid.uuid4())


def to_cms(value: float, unit: str) -> float:
    value = _finite_nonnegative(value, "流量")
    if unit == UNIT_CMS:
        return value
    if unit == UNIT_10K_TON_DAY:
        return value / 8.64
    raise ValueError(f"不支援的單位：{unit}")


def _finite_nonnegative(value: Any, label: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{label}不可缺漏")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必須是數字") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label}不可為 NaN 或無限值")
    if number < 0:
        raise ValueError(f"{label}不可為負值")
    return number


def parse_pasted_values(text: str, unit: str, expected_count: int | None = None) -> list[dict]:
    """Parse spreadsheet text, including commas used as thousands separators."""
    if not text or not text.strip():
        raise ValueError("貼上內容不可空白")
    # A comma between groups of exactly three digits is a thousands separator;
    # other commas remain ordinary spreadsheet delimiters.
    normalized = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text.strip())
    tokens = [token for token in re.split(r"[\s,]+", normalized) if token]
    parsed = []
    for index, token in enumerate(tokens, 1):
        try:
            original = _finite_nonnegative(token, f"第 {index} 筆")
        except ValueError as exc:
            raise ValueError(f"無法解析第 {index} 筆「{token}」：{exc}") from exc
        parsed.append({"source_value": original, "source_unit": unit, "cms": to_cms(original, unit)})
    if expected_count is not None and len(parsed) != expected_count:
        raise ValueError(f"預期 {expected_count} 筆，實際解析 {len(parsed)} 筆")
    return parsed


def make_scenario(name: str, periods: Iterable[str], source_type: str = "待填") -> dict:
    return {
        "scenario_id": new_id(), "name": name.strip() or "未命名情境", "order": 0,
        "inflows": {key: {"cms": None, "source_type": source_type,
                          "source_unit": UNIT_CMS, "source_value": None, "note": ""}
                    for key in periods},
    }


def scenario_template(kind: str, periods: Iterable[str]) -> list[dict]:
    names = ["單一情境"] if kind == "single" else (
        ["A. 氣候上限值", "B. 氣候下限值", "C. 專業評估"] if kind == "standard" else ["自訂情境"])
    scenarios = [make_scenario(name, periods) for name in names]
    return reorder_scenarios(scenarios, [s["scenario_id"] for s in scenarios])


def add_scenario(scenarios: list[dict], name: str, periods: Iterable[str]) -> list[dict]:
    result = copy.deepcopy(scenarios) + [make_scenario(name, periods)]
    return reorder_scenarios(result, [s["scenario_id"] for s in result])


def copy_scenario(scenarios: list[dict], scenario_id: str, name: str | None = None) -> list[dict]:
    result = copy.deepcopy(scenarios)
    source = next((s for s in result if s["scenario_id"] == scenario_id), None)
    if source is None:
        raise ValueError("找不到要複製的情境")
    clone = copy.deepcopy(source)
    clone["scenario_id"] = new_id()
    clone["name"] = (name or f"{source['name']}（複製）").strip()
    for cell in clone["inflows"].values():
        cell["source_type"] = f"複製自 {source['name']}"
    result.append(clone)
    return reorder_scenarios(result, [s["scenario_id"] for s in result])


def rename_scenario(scenarios: list[dict], scenario_id: str, name: str) -> list[dict]:
    if not name.strip():
        raise ValueError("情境名稱不可空白")
    result = copy.deepcopy(scenarios)
    next(s for s in result if s["scenario_id"] == scenario_id)["name"] = name.strip()
    return result


def delete_scenario(scenarios: list[dict], scenario_id: str) -> list[dict]:
    if len(scenarios) <= 1:
        raise ValueError("至少必須保留一個情境")
    result = [copy.deepcopy(s) for s in scenarios if s["scenario_id"] != scenario_id]
    if len(result) == len(scenarios):
        raise ValueError("找不到要刪除的情境")
    return reorder_scenarios(result, [s["scenario_id"] for s in result])


def reorder_scenarios(scenarios: list[dict], ordered_ids: list[str]) -> list[dict]:
    if set(ordered_ids) != {s["scenario_id"] for s in scenarios} or len(ordered_ids) != len(scenarios):
        raise ValueError("排序清單必須恰好包含所有情境")
    lookup = {s["scenario_id"]: copy.deepcopy(s) for s in scenarios}
    result = [lookup[sid] for sid in ordered_ids]
    for order, scenario in enumerate(result):
        scenario["order"] = order
    return result


def expand_shared_inflows(periods: list[str], shared_count: int, shared: dict, scenarios: list[dict]) -> list[dict]:
    if not 0 <= shared_count <= len(periods):
        raise ValueError("共用旬數超出範圍")
    result = copy.deepcopy(scenarios)
    for scenario in result:
        for key in periods[:shared_count]:
            if key not in shared:
                raise ValueError(f"共用入流缺少 {key}")
            scenario["inflows"][key] = copy.deepcopy(shared[key])
    return result


def validate_scenario(scenario: dict, periods: list[str]) -> list[str]:
    errors = []
    for key in periods:
        cell = scenario.get("inflows", {}).get(key)
        try:
            _finite_nonnegative(None if cell is None else cell.get("cms"), f"{key} 入流")
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def find_override_overlaps(overrides: list[dict]) -> list[tuple[int, int]]:
    conflicts = []
    ranges = []
    for index, item in enumerate(overrides):
        start, end = _date(item.get("start")), _date(item.get("end"))
        if start > end:
            raise ValueError(f"第 {index + 1} 筆覆蓋的起日不可晚於迄日")
        ranges.append((start, end))
    for left in range(len(ranges)):
        for right in range(left + 1, len(ranges)):
            if max(ranges[left][0], ranges[right][0]) <= min(ranges[left][1], ranges[right][1]):
                conflicts.append((left, right))
    return conflicts


def _date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime): return value.date()
    if isinstance(value, dt.date): return value
    try: return dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc: raise ValueError(f"無效日期：{value}") from exc


def settings_fingerprint(batch: dict) -> str:
    import hashlib
    payload = copy.deepcopy(batch)
    for key in ("created_at", "results", "results_fingerprint"):
        payload.pop(key, None)
    return hashlib.sha256(json.dumps(_jsonable(payload), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def results_are_current(batch: dict) -> bool:
    return bool(batch.get("results")) and batch.get("results_fingerprint") == settings_fingerprint(batch)


def invalidate_results(batch: dict) -> dict:
    result = copy.deepcopy(batch)
    result["results_fingerprint"] = None
    return result


def export_batch(batch: dict) -> str:
    validated = validate_batch(batch)
    portable = copy.deepcopy(validated)
    portable.pop("results", None); portable.pop("results_fingerprint", None)
    return json.dumps(_jsonable(portable), ensure_ascii=False, indent=2, sort_keys=True)


def import_batch(text: str) -> dict:
    try: data = json.loads(text)
    except json.JSONDecodeError as exc: raise ValueError(f"JSON 格式錯誤：{exc.msg}") from exc
    result = validate_batch(data)
    result.pop("results", None); result.pop("results_fingerprint", None)
    return result


def validate_batch(batch: dict) -> dict:
    if not isinstance(batch, dict): raise ValueError("設定檔根節點必須是物件")
    required = {"schema", "schema_version", "batch_id", "batch_name", "display_start_date",
                "projection_start_date", "projection_end_date", "initial_capacity",
                "reservoir_parameters", "periods", "shared_period_count", "shared_inflows",
                "scenarios", "outflows", "date_overrides"}
    missing = required - batch.keys()
    if missing: raise ValueError(f"設定檔缺少必要欄位：{', '.join(sorted(missing))}")
    if batch["schema"] != SCHEMA_NAME or batch["schema_version"] != SCHEMA_VERSION:
        raise ValueError("不支援的設定檔 schema 或版本")
    start, end = _date(batch["projection_start_date"]), _date(batch["projection_end_date"])
    if start >= end: raise ValueError("推估起日必須早於迄日")
    periods = batch["periods"]
    if not isinstance(periods, list) or not periods or len(set(periods)) != len(periods): raise ValueError("旬別清單無效")
    if not 0 <= int(batch["shared_period_count"]) <= len(periods): raise ValueError("共用旬數無效")
    if not isinstance(batch["scenarios"], list) or not batch["scenarios"]: raise ValueError("至少需要一個情境")
    ids = [s.get("scenario_id") for s in batch["scenarios"]]
    if None in ids or len(ids) != len(set(ids)): raise ValueError("情境 ID 必須存在且唯一")
    find_override_overlaps(batch["date_overrides"])
    _finite_nonnegative(batch["initial_capacity"], "起始庫容")
    return copy.deepcopy(batch)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime)): return value.isoformat()
    if isinstance(value, pd.DataFrame): return value.to_dict(orient="records")
    if isinstance(value, dict): return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_jsonable(v) for v in value]
    return value


def standardize_comparison_result(batch: dict, scenario: dict, result: pd.DataFrame) -> dict:
    fingerprint = settings_fingerprint(batch)
    return {"result_id": f"{batch['batch_id']}:{scenario['scenario_id']}:{fingerprint[:12]}",
            "batch_id": batch["batch_id"], "batch_name": batch["batch_name"],
            "scenario_id": scenario["scenario_id"], "scenario_name": scenario["name"],
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "settings_snapshot": json.loads(export_batch(batch)), "result": result.copy()}


def run_water_balance(daily_profile: pd.DataFrame, inflows: dict[str, float], daily_outflow: pd.DataFrame,
                      initial_capacity: float, max_capacity: float, shilin_eco: float,
                      liyutan_eco: float) -> tuple[pd.DataFrame, dict]:
    """The legacy daily formula, isolated unchanged for batch execution.

    ``daily_profile`` must contain 日期/年份/月分/旬別 and only projection days.
    This intentionally mirrors the arithmetic and rounding in ``app.py``.
    """
    capacity = initial_capacity
    records, total_reduction, total_spill = [], 0.0, 0.0
    out_lookup = {pd.Timestamp(row["日期"]).date(): row for _, row in daily_outflow.iterrows()}
    for _, row in daily_profile.iterrows():
        date = pd.Timestamp(row["日期"]).date()
        key = f"{int(row['年份'])}-{int(row['月份'])}-{row['旬別']}"
        inflow = _finite_nonnegative(inflows.get(key), f"{key} 入流")
        out = out_lookup.get(date)
        if out is None: raise ValueError(f"共用出流缺少 {date.isoformat()}")
        upstream = _finite_nonnegative(out["上灌區當日流量(cms)"], "上灌需求")
        downstream = _finite_nonnegative(out["下灌區當日流量(cms)"], "下灌需求")
        public = _finite_nonnegative(out["公共供水當日水量(萬噸)"], "公共給水")
        actual_up = min(upstream, inflow)
        remaining = max(0.0, inflow - actual_up)
        actual_down = min(downstream, remaining)
        reduction = upstream + downstream - actual_up - actual_down
        total_reduction += reduction * 8.64
        shilin_release = min(inflow, max(shilin_eco, actual_up))
        diversion = min(33.0, max(0.0, inflow - shilin_release))
        diversion_volume = round(diversion * 8.64, 2)
        dam_release = max(liyutan_eco, actual_down)
        total_outflow = round(public + round(dam_release * 8.64, 2), 2)
        yesterday = capacity
        calculated = yesterday + diversion_volume - total_outflow
        spill = 0.0
        if calculated > max_capacity:
            spill = round(calculated - max_capacity, 2); capacity = max_capacity; total_spill += spill
        elif calculated < 0: capacity = 0.0
        else: capacity = round(calculated, 2)
        records.append({"日期": date, "年份": row["年份"], "月份": row["月份"], "旬別": row["旬別"],
            "運行狀態": "🔮 未來推估", "天然流量 (cms)": inflow, "原上灌需求 (cms)": upstream,
            "原下灌需求 (cms)": downstream, "實際上灌放水 (cms)": round(actual_up, 2),
            "實際下灌放水 (cms)": round(actual_down, 2),
            "農業削減狀態": "🚨 觸發削減" if reduction > 0 else "🟢 正常",
            "農業削減量 (cms)": round(reduction, 2), "士林堰河道保留 (cms)": round(shilin_release, 2),
            "實際引水流量 (cms)": round(diversion, 2), "今日引入量 (萬噸)": diversion_volume,
            "大壩河道放流 (cms)": round(dam_release, 2), "公共給水量 (萬噸)": round(public, 2),
            "今日出水總量 (萬噸)": total_outflow, "溢流量 (萬噸)": spill,
            "昨日期末庫容 (萬噸)": round(yesterday, 2), "本日末庫容 (萬噸)": round(capacity, 2),
            "當日庫容淨變化 (萬噸)": round(capacity - yesterday, 2)})
    frame = pd.DataFrame(records)
    summary = {"final_capacity": float(frame.iloc[-1]["本日末庫容 (萬噸)"]),
               "minimum_capacity": float(frame["本日末庫容 (萬噸)"].min()),
               "spill_volume": total_spill, "agricultural_reduction_volume": total_reduction,
               "dry_days": int((frame["本日末庫容 (萬噸)"] <= 0).sum())}
    return frame, summary


def run_batch(batch: dict, daily_profile: pd.DataFrame, daily_outflow: pd.DataFrame) -> dict:
    batch = validate_batch(batch)
    conflicts = find_override_overlaps(batch["date_overrides"])
    if conflicts: raise ValueError(f"出流覆蓋期間重疊：{conflicts}")
    expanded = expand_shared_inflows(batch["periods"], batch["shared_period_count"],
                                     batch["shared_inflows"], batch["scenarios"])
    results = {}
    params = batch["reservoir_parameters"]
    for scenario in expanded:
        errors = validate_scenario(scenario, batch["periods"])
        if errors:
            results[scenario["scenario_id"]] = {"status": "error", "error": "；".join(errors)}
            continue
        try:
            inflows = {key: cell["cms"] for key, cell in scenario["inflows"].items()}
            frame, summary = run_water_balance(daily_profile, inflows, daily_outflow,
                batch["initial_capacity"], params["max_capacity"], params["shilin_eco_flow"],
                params["liyutan_eco_flow"])
            results[scenario["scenario_id"]] = {"status": "success", "data": frame, "summary": summary}
        except Exception as exc:  # scenario isolation is a batch requirement
            results[scenario["scenario_id"]] = {"status": "error", "error": str(exc)}
    return results
