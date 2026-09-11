"""Build validated, in-memory candidates for a future official estimate save.

Phase 2-5B deliberately stops at this boundary: the returned files are bytes in
the current process only.  This module performs no filesystem, shared-storage,
current-pointer, audit, lock, or publishing work.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from shared_storage_schema import (
    BATCH_SCHEMA_VERSION,
    COMMITTED_SCHEMA,
    DAILY_RESULT_COLUMNS,
    OFFICIAL_DATA_FILES,
    OFFICIAL_ESTIMATE_SCHEMA,
    OFFICIAL_INPUTS_SCHEMA,
    RESERVOIR_PARAMETERS_SCHEMA,
    SCHEMA_VERSION,
    SUMMARY_COLUMNS,
    StorageValidationError,
    deterministic_fingerprint,
    official_inputs_fingerprint,
    serialize_csv,
    serialize_json,
    sha256_bytes,
    validate_official_bundle,
    validate_official_save_eligibility,
)
from v2_workflow import export_batch, settings_fingerprint


@dataclass(frozen=True)
class OfficialEstimateCandidate:
    """A complete official-format bundle that has not been published."""

    version_id: str
    files: dict[str, bytes]
    validated_bundle: dict[str, Any]
    preview: dict[str, Any]
    context_fingerprint: str


_DAILY_COLUMN_MAP = {
    "natural_inflow_cms": "天然流量 (cms)",
    "upstream_demand_cms": "原上灌需求 (cms)",
    "downstream_demand_cms": "原下灌需求 (cms)",
    "actual_upstream_release_cms": "實際上灌放水 (cms)",
    "actual_downstream_release_cms": "實際下灌放水 (cms)",
    "agricultural_reduction_cms": "農業削減量 (cms)",
    "shilin_river_release_cms": "士林堰河道保留 (cms)",
    "actual_diversion_cms": "實際引水流量 (cms)",
    "diversion_volume_10k_ton": "今日引入量 (萬噸)",
    "dam_release_cms": "大壩河道放流 (cms)",
    "public_water_10k_ton": "公共給水量 (萬噸)",
    "total_outflow_10k_ton": "今日出水總量 (萬噸)",
    "spill_volume_10k_ton": "溢流量 (萬噸)",
    "previous_capacity_10k_ton": "昨日期末庫容 (萬噸)",
    "end_capacity_10k_ton": "本日末庫容 (萬噸)",
    "net_capacity_change_10k_ton": "當日庫容淨變化 (萬噸)",
}
_CUSTOM_SOURCE_TOKENS = ("手動", "貼上", "自訂", "覆寫", "調整", "複製", "上傳")
_QUANTILE_SOURCE_RE = re.compile(r"^Q(?:0?[5-9]|[1-8][0-9]|9[05])$", re.IGNORECASE)


def _fail(message: str) -> None:
    raise StorageValidationError(message)


def _utc_timestamp(value: str | None) -> str:
    if value is not None:
        return value
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _portable_batch(batch: Mapping[str, Any], selected_ids: Sequence[str]) -> dict[str, Any]:
    portable = json.loads(export_batch(dict(batch)))
    selected = set(selected_ids)
    scenarios = [
        scenario for scenario in portable["scenarios"] if scenario["scenario_id"] in selected
    ]
    for order, scenario in enumerate(scenarios):
        scenario["order"] = order
    portable["scenarios"] = scenarios
    # export_batch already strips runtime results and results_fingerprint.  Keep
    # this assertion close to the formal boundary so future V2 changes cannot
    # accidentally leak runtime state into inputs.json.
    if "results" in portable or "results_fingerprint" in portable:
        _fail("正式 inputs snapshot 不得包含 V2 runtime result state")
    return portable


def _projection_daily_values(
    data: Any, projection_start_date: str, projection_end_date: str
) -> list[dict[str, Any]]:
    if not isinstance(data, pd.DataFrame):
        _fail("正式逐日結果必須來自目前 V2 計算 DataFrame")
    required = {"日期", *_DAILY_COLUMN_MAP.values()}
    missing = sorted(required - set(data.columns))
    if missing:
        _fail(f"V2 計算結果缺少正式逐日欄位：{', '.join(missing)}")
    frame = data.loc[:, list(required)].copy()
    try:
        frame["日期"] = pd.to_datetime(frame["日期"], errors="raise").dt.date
        start = dt.date.fromisoformat(projection_start_date)
        end = dt.date.fromisoformat(projection_end_date)
    except (TypeError, ValueError) as exc:
        raise StorageValidationError(f"正式逐日結果日期無效：{exc}") from exc
    # The displayed V2 frame may prepend historical capacity rows.  The
    # official candidate intentionally rebuilds rows from named calculation
    # fields and strictly filters the projection half-open interval.
    frame = frame[(frame["日期"] >= start) & (frame["日期"] < end)].sort_values("日期")
    values: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        item: dict[str, Any] = {"date": row["日期"].isoformat()}
        for official_column, v2_column in _DAILY_COLUMN_MAP.items():
            try:
                item[official_column] = float(row[v2_column])
            except (TypeError, ValueError) as exc:
                raise StorageValidationError(
                    f"V2 計算結果 {row['日期']} 的 {v2_column} 不是有效數值"
                ) from exc
        values.append(item)
    return values


def _selected_result_signature(
    batch: Mapping[str, Any], results: Mapping[str, Any], selected_ids: Sequence[str]
) -> dict[str, Any]:
    signatures: dict[str, Any] = {}
    start = str(batch.get("projection_start_date", ""))
    end = str(batch.get("projection_end_date", ""))
    for scenario_id in selected_ids:
        result = results.get(scenario_id)
        if not isinstance(result, Mapping):
            signatures[scenario_id] = None
            continue
        signature: dict[str, Any] = {
            "status": result.get("status"),
            "summary": dict(result.get("summary", {}))
            if isinstance(result.get("summary"), Mapping)
            else None,
        }
        if result.get("status") == "success":
            signature["daily_values"] = _projection_daily_values(result.get("data"), start, end)
        signatures[scenario_id] = signature
    return signatures


def official_candidate_context_fingerprint(
    batch: Mapping[str, Any],
    results: Mapping[str, Any],
    selected_scenario_ids: Sequence[str],
    *,
    annual_data_version_id: str | None,
    shared_annual_data_validated: bool,
    operator_display_name: str,
    note: str,
    software: Mapping[str, Any] | None,
    previous_official_version_id: str | None = None,
    derived_from_official_version_id: str | None = None,
) -> str:
    """Fingerprint every user-visible input that keeps a preview current."""

    requested_ids = list(selected_scenario_ids)
    requested_set = set(requested_ids)
    selected_ids = [
        scenario.get("scenario_id")
        for scenario in batch.get("scenarios", [])
        if isinstance(scenario, Mapping)
        and scenario.get("scenario_id") in requested_set
    ]
    # Unknown IDs are retained in their supplied order so an invalid context
    # can never accidentally match a valid candidate.  Known IDs are
    # canonicalized to batch order because multiselect click order is not part
    # of the formal scenario-set semantics.
    selected_ids.extend(
        scenario_id for scenario_id in requested_ids if scenario_id not in selected_ids
    )
    payload = {
        "v2_settings_fingerprint": settings_fingerprint(dict(batch)),
        "v2_results_fingerprint": batch.get("results_fingerprint"),
        "selected_scenario_ids": selected_ids,
        "selected_results": _selected_result_signature(batch, results, selected_ids),
        "annual_data_version_id": annual_data_version_id,
        "shared_annual_data_validated": shared_annual_data_validated,
        "operator_display_name": operator_display_name.strip(),
        "note": note.strip(),
        "software": dict(software) if isinstance(software, Mapping) else software,
        "previous_official_version_id": previous_official_version_id,
        "derived_from_official_version_id": derived_from_official_version_id,
    }
    return deterministic_fingerprint(payload)


def candidate_is_current(
    candidate: OfficialEstimateCandidate,
    batch: Mapping[str, Any],
    results: Mapping[str, Any],
    selected_scenario_ids: Sequence[str],
    **context: Any,
) -> bool:
    """Return False instead of raising when a prior preview no longer applies."""

    try:
        current = official_candidate_context_fingerprint(
            batch, results, selected_scenario_ids, **context
        )
    except (StorageValidationError, TypeError, ValueError, KeyError):
        return False
    return current == candidate.context_fingerprint


def _has_custom_or_adjusted_data(batch: Mapping[str, Any], selected_ids: Sequence[str]) -> bool:
    if batch.get("overrides_enabled") and batch.get("date_overrides"):
        return True
    selected = set(selected_ids)
    for scenario in batch.get("scenarios", []):
        if scenario.get("scenario_id") not in selected:
            continue
        for value in scenario.get("inflows", {}).values():
            if not isinstance(value, Mapping):
                continue
            source = str(value.get("source_type", "")).strip()
            if value.get("note") or not _QUANTILE_SOURCE_RE.fullmatch(source):
                return True
    for collection_name in ("outflows", "daily_outflows"):
        collection = batch.get(collection_name, {})
        values = collection.values() if isinstance(collection, Mapping) else ()
        for value in values:
            if not isinstance(value, Mapping):
                continue
            source = str(value.get("source_type", ""))
            if value.get("note") or any(token in source for token in _CUSTOM_SOURCE_TOKENS):
                return True
    return bool(str(batch.get("note", "")).strip())


def build_official_estimate_candidate(
    batch: Mapping[str, Any],
    results: Mapping[str, Any],
    selected_scenario_ids: Sequence[str],
    *,
    annual_data_version_id: str | None,
    shared_annual_data_validated: bool,
    operator_display_name: str,
    note: str,
    software: Mapping[str, Any] | None,
    previous_official_version_id: str | None = None,
    derived_from_official_version_id: str | None = None,
    estimate_version_id: str | None = None,
    created_at: str | None = None,
) -> OfficialEstimateCandidate:
    """Create and immediately validate a complete in-memory bundle candidate."""

    operator = operator_display_name.strip()
    candidate_note = note.strip()
    if not operator:
        _fail("產生正式保存預覽前必須填寫操作人")
    if not candidate_note:
        _fail("產生正式保存預覽前必須填寫備註")

    eligibility = validate_official_save_eligibility(
        batch,
        results,
        list(selected_scenario_ids),
        annual_data_version_id=annual_data_version_id,
        shared_annual_data_validated=shared_annual_data_validated,
        software=software,
    )
    validated_batch = eligibility["batch"]
    selected_set = set(eligibility["official_scenario_ids"])
    selected_ids = [
        scenario["scenario_id"]
        for scenario in validated_batch["scenarios"]
        if scenario["scenario_id"] in selected_set
    ]
    clean_batch = _portable_batch(validated_batch, selected_ids)
    parameters = clean_batch["reservoir_parameters"]
    reservoir_parameters = {
        "schema": RESERVOIR_PARAMETERS_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "max_capacity_10k_ton": parameters["max_capacity"],
        "shilin_ecological_flow_cms": parameters["shilin_eco_flow"],
        "liyutan_ecological_release_cms": parameters["liyutan_eco_flow"],
        "shilin_diversion_limit_cms": parameters["shilin_diversion_limit"],
    }
    inputs = {
        "schema": OFFICIAL_INPUTS_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "annual_data_version_id": eligibility["annual_data_version_id"],
        "batch_id": clean_batch["batch_id"],
        "official_scenario_ids": selected_ids,
        "reservoir_parameters": reservoir_parameters,
        "batch": clean_batch,
    }
    inputs_fingerprint = official_inputs_fingerprint(inputs)
    version_id = estimate_version_id or f"estimate-candidate-{uuid.uuid4().hex}"
    timestamp = _utc_timestamp(created_at)
    scenario_lookup = {
        scenario["scenario_id"]: scenario for scenario in clean_batch["scenarios"]
    }

    summary_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    preview_scenarios: list[dict[str, Any]] = []
    for scenario_id in selected_ids:
        scenario = scenario_lookup[scenario_id]
        result = eligibility["results"][scenario_id]
        summary = result["summary"]
        summary_row = {
            "version_id": version_id,
            "batch_id": clean_batch["batch_id"],
            "scenario_id": scenario_id,
            "scenario_name": scenario["name"],
            "scenario_order": scenario["order"],
            "calculation_status": "success",
            "inputs_fingerprint": inputs_fingerprint,
            "final_capacity_10k_ton": float(summary["final_capacity"]),
            "minimum_capacity_10k_ton": float(summary["minimum_capacity"]),
            "spill_volume_10k_ton": float(summary["spill_volume"]),
            # The V2 accumulator can retain a sub-machine-epsilon negative
            # residual when the mathematically correct reduction is zero.
            "agricultural_reduction_volume_10k_ton": max(
                0.0, float(summary["agricultural_reduction_volume"])
            ),
            "dry_days": int(summary["dry_days"]),
        }
        summary_rows.append(summary_row)
        preview_scenarios.append(
            {
                "scenario_id": scenario_id,
                "scenario_name": scenario["name"],
                "final_capacity_10k_ton": summary_row["final_capacity_10k_ton"],
                "minimum_capacity_10k_ton": summary_row["minimum_capacity_10k_ton"],
                "spill_volume_10k_ton": summary_row["spill_volume_10k_ton"],
                "agricultural_reduction_volume_10k_ton": summary_row[
                    "agricultural_reduction_volume_10k_ton"
                ],
                "dry_days": summary_row["dry_days"],
            }
        )
        values = _projection_daily_values(
            result["data"],
            clean_batch["projection_start_date"],
            clean_batch["projection_end_date"],
        )
        for value in values:
            daily_rows.append(
                {
                    "version_id": version_id,
                    "batch_id": clean_batch["batch_id"],
                    "scenario_id": scenario_id,
                    "inputs_fingerprint": inputs_fingerprint,
                    **value,
                }
            )

    data_files = {
        "inputs.json": serialize_json(inputs),
        "scenario_summaries.csv": serialize_csv(summary_rows, SUMMARY_COLUMNS),
        "daily_results.csv": serialize_csv(daily_rows, DAILY_RESULT_COLUMNS),
    }
    manifest = {
        "schema": OFFICIAL_ESTIMATE_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "version_id": version_id,
        "batch_id": clean_batch["batch_id"],
        "batch_name": clean_batch["batch_name"],
        "previous_official_version_id": previous_official_version_id,
        "derived_from_official_version_id": derived_from_official_version_id,
        "annual_data_version_id": eligibility["annual_data_version_id"],
        "inputs_fingerprint": inputs_fingerprint,
        "official_scenario_ids": selected_ids,
        "created_at": timestamp,
        "operator_display_name": operator,
        "note": candidate_note,
        "software": eligibility["software"],
        "batch_schema_version": BATCH_SCHEMA_VERSION,
        "files": {
            filename: {"sha256": sha256_bytes(data_files[filename])}
            for filename in OFFICIAL_DATA_FILES
        },
    }
    manifest_bytes = serialize_json(manifest)
    committed = {
        "schema": COMMITTED_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "version_id": version_id,
        "committed_at": timestamp,
        "manifest_file": "manifest.json",
        "manifest_sha256": sha256_bytes(manifest_bytes),
    }
    files = {
        "manifest.json": manifest_bytes,
        **data_files,
        "COMMITTED.json": serialize_json(committed),
    }
    validated_bundle = validate_official_bundle(files)
    context_fingerprint = official_candidate_context_fingerprint(
        validated_batch,
        eligibility["results"],
        selected_ids,
        annual_data_version_id=eligibility["annual_data_version_id"],
        shared_annual_data_validated=shared_annual_data_validated,
        operator_display_name=operator,
        note=candidate_note,
        software=eligibility["software"],
        previous_official_version_id=previous_official_version_id,
        derived_from_official_version_id=derived_from_official_version_id,
    )
    preview = {
        "version_id": version_id,
        "projection_start_date": clean_batch["projection_start_date"],
        "projection_end_date": clean_batch["projection_end_date"],
        "annual_data_version_id": eligibility["annual_data_version_id"],
        "scenarios": preview_scenarios,
        "has_custom_or_adjusted_data": _has_custom_or_adjusted_data(
            clean_batch, selected_ids
        ),
        "operator_display_name": operator,
        "note": candidate_note,
        "previous_official_version_id": previous_official_version_id,
        "derived_from_official_version_id": derived_from_official_version_id,
        "inputs_fingerprint": inputs_fingerprint,
        "software": eligibility["software"],
        "files": {
            filename: {"sha256": sha256_bytes(data)} for filename, data in files.items()
        },
    }
    return OfficialEstimateCandidate(
        version_id=version_id,
        files=files,
        validated_bundle=validated_bundle,
        preview=preview,
        context_fingerprint=context_fingerprint,
    )
