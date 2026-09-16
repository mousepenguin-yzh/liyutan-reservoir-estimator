"""Streamlit-independent UI models and atomic session transitions for 2-6D.

The functions accept mapping-like session state but never import Streamlit.
``v2_batch`` remains the only mutable working-batch source of truth; only
lineage, annual identity, and latest date-adjustment metadata are retained.
"""

from __future__ import annotations

import copy
import datetime as dt
from dataclasses import asdict, dataclass
from typing import Any, MutableMapping

import pandas as pd

from official_estimate_continuation import OfficialContinuationDraft
from official_estimate_date_adjustment import (
    ContinuationDateAdjustment,
    validate_continuation_pending_batch,
)
from official_estimate_loader import (
    OfficialEstimateHistory,
    OfficialEstimateSnapshot,
    OfficialVersionMetadata,
)
from shared_storage_reader import AnnualDataSnapshot
from ten_day_period import parse_date, working_period_keys_for_range
from v2_workflow import invalidate_session_results, validate_batch


CONTINUATION_SOURCE = "official_continuation"
NEW_WORK_SOURCE = "new_estimate"
_TAIPEI = dt.timezone(dt.timedelta(hours=8))


@dataclass(frozen=True)
class OfficialHistoryItemView:
    version_id: str
    label: str
    is_current: bool
    batch_name: str
    projection_start_date: str
    projection_end_date: str
    operator_display_name: str
    annual_data_version_id: str
    scenario_count: int


@dataclass(frozen=True)
class OfficialHistoryView:
    current_version_id: str
    items: tuple[OfficialHistoryItemView, ...]


@dataclass(frozen=True)
class OfficialSnapshotPreview:
    version_id: str
    batch_name: str
    projection_start_date: str
    projection_end_date: str
    initial_capacity: float
    scenario_names: tuple[str, ...]
    created_at: str
    operator_display_name: str
    note: str
    source_annual_data_version_id: str
    current_annual_data_version_id: str
    derived_from_official_version_id: str | None
    annual_versions_differ: bool


@dataclass(frozen=True)
class ContinuationDateRequestPreview:
    display_start_date: dt.date
    projection_start_date: dt.date
    projection_end_date: dt.date
    added_periods: tuple[str, ...]
    removed_periods: tuple[str, ...]
    preserved_periods: tuple[str, ...]
    changed: bool


def _taipei_created_label(value: str) -> str:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("正式版本 created_at 必須包含時區")
    return parsed.astimezone(_TAIPEI).strftime("%Y/%m/%d %H:%M")


def build_official_history_view(
    history: OfficialEstimateHistory,
) -> OfficialHistoryView:
    """Build current-first friendly choices from the loader-proven chain."""

    if not isinstance(history, OfficialEstimateHistory):
        raise TypeError("history 必須是 OfficialEstimateHistory")
    items = []
    for index, metadata in enumerate(history.versions):
        current = metadata.version_id == history.current_version_id
        prefix = "目前正式版本" if current else f"歷史正式版本 {index}"
        label = (
            f"{prefix}｜{_taipei_created_label(metadata.created_at)}｜"
            f"{metadata.batch_name}｜{metadata.projection_start_date}～"
            f"{metadata.projection_end_date}｜{len(metadata.scenarios)} 情境｜"
            f"{metadata.operator_display_name}"
        )
        items.append(
            OfficialHistoryItemView(
                version_id=metadata.version_id,
                label=label,
                is_current=current,
                batch_name=metadata.batch_name,
                projection_start_date=metadata.projection_start_date,
                projection_end_date=metadata.projection_end_date,
                operator_display_name=metadata.operator_display_name,
                annual_data_version_id=metadata.annual_data_version_id,
                scenario_count=len(metadata.scenarios),
            )
        )
    return OfficialHistoryView(history.current_version_id, tuple(items))


def build_official_snapshot_preview(
    snapshot: OfficialEstimateSnapshot,
    *,
    current_annual_data_version_id: str,
) -> OfficialSnapshotPreview:
    if not isinstance(snapshot, OfficialEstimateSnapshot):
        raise TypeError("snapshot 必須是 OfficialEstimateSnapshot")
    metadata = snapshot.metadata
    return OfficialSnapshotPreview(
        version_id=metadata.version_id,
        batch_name=metadata.batch_name,
        projection_start_date=metadata.projection_start_date,
        projection_end_date=metadata.projection_end_date,
        initial_capacity=float(snapshot.batch["initial_capacity"]),
        scenario_names=tuple(item.name for item in metadata.scenarios),
        created_at=metadata.created_at,
        operator_display_name=metadata.operator_display_name,
        note=metadata.note,
        source_annual_data_version_id=metadata.annual_data_version_id,
        current_annual_data_version_id=current_annual_data_version_id,
        derived_from_official_version_id=metadata.derived_from_official_version_id,
        annual_versions_differ=(
            metadata.annual_data_version_id != current_annual_data_version_id
        ),
    )


def annual_hydrology_frame(snapshot: AnnualDataSnapshot) -> pd.DataFrame:
    records = []
    for row in snapshot.hydrology:
        record = {"工作表": f"{int(row['month'])}月{row['period']}"}
        for quantile in range(95, 0, -5):
            record[f"Q{quantile}"] = float(row[f"q{quantile:02d}_cms"])
        records.append(record)
    return pd.DataFrame(records)


def annual_demand_frame(snapshot: AnnualDataSnapshot) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "工作表": f"{int(row['month'])}月{row['period']}",
                "上灌區需求_cms": float(row["upstream_irrigation_cms"]),
                "下灌區需求_cms": float(row["downstream_irrigation_cms"]),
                "公共出水_萬噸": float(row["public_water_10k_ton_per_day"]),
            }
            for row in snapshot.outflow_demand
        ]
    )


def apply_active_annual_helpers(
    state: MutableMapping[str, Any], snapshot: AnnualDataSnapshot
) -> None:
    """Atomically replace helper frames, never reservoir parameters."""

    version_id = snapshot.version["version_id"]
    hydrology = annual_hydrology_frame(snapshot)
    demand = annual_demand_frame(snapshot)
    patch = {
        "hydrology_df": hydrology,
        "demand_df": demand,
        "hydrology_source_status": f"工作批次年度基準：{version_id}",
        "demand_source_status": f"工作批次年度基準：{version_id}",
        "hydrology_session_upload": False,
        "demand_session_upload": False,
        "loaded_shared_annual_version_id": version_id,
        "v2_active_annual_data_version_id": version_id,
        "v2_active_annual_validated": True,
    }
    for key, value in patch.items():
        state[key] = value
    state.pop("v2_active_annual_error", None)


def mark_active_annual_unavailable(
    state: MutableMapping[str, Any], version_id: str, message: str
) -> None:
    state["v2_active_annual_data_version_id"] = version_id
    state["loaded_shared_annual_version_id"] = version_id
    state["v2_active_annual_validated"] = False
    state["v2_active_annual_error"] = message


def invalidate_working_artifacts(state: MutableMapping[str, Any]) -> None:
    """Invalidate active results/formal preview but preserve comparisons."""

    invalidate_session_results(state)  # type: ignore[arg-type]
    state.pop("v2_result_fingerprint", None)
    candidate = state.pop("official_estimate_candidate", None)
    version_id = getattr(candidate, "version_id", None)
    if version_id:
        state.pop(f"official_publish_confirmed_{version_id}", None)
    for key in tuple(state.keys()):
        if str(key).startswith("official_publish_confirmed_"):
            state.pop(key, None)
    for key in (
        "official_publish_in_progress_version_id",
        "official_publish_confirmation_to_clear",
        "official_candidate_stale_notice",
        "official_publish_receipt",
        "official_publish_notice",
    ):
        state.pop(key, None)


def _validated_batch_for_session(
    batch: dict, *, initial_capacity_requires_confirmation: bool,
    allow_pending_inflows: bool = False,
) -> dict:
    if not isinstance(batch.get("batch_name"), str) or not batch["batch_name"].strip():
        raise ValueError("batch_name 不可空白")
    if initial_capacity_requires_confirmation or allow_pending_inflows:
        return validate_continuation_pending_batch(
            batch, initial_capacity_requires_confirmation=initial_capacity_requires_confirmation
        )
    return validate_batch(batch)


def _batch_session_patch(
    batch: dict, *, initial_capacity_requires_confirmation: bool,
    allow_pending_inflows: bool = False,
) -> dict[str, Any]:
    validated = _validated_batch_for_session(
        batch,
        initial_capacity_requires_confirmation=initial_capacity_requires_confirmation,
        allow_pending_inflows=allow_pending_inflows,
    )
    params = validated["reservoir_parameters"]
    overrides = [
        {
            **copy.deepcopy(item),
            "start": parse_date(item["start"]),
            "end": parse_date(item["end"]),
        }
        for item in validated["date_overrides"]
    ]
    return {
        "v2_batch": copy.deepcopy(validated),
        "display_start_date": parse_date(validated["display_start_date"]),
        "start_date": parse_date(validated["projection_start_date"]),
        "end_date": parse_date(validated["projection_end_date"]),
        "init_capacity": validated["initial_capacity"],
        "hist_capacity": copy.deepcopy(validated.get("historical_capacities", {})),
        "max_capacity": float(params["max_capacity"]),
        "shilin_eco_flow": float(params["shilin_eco_flow"]),
        "liyutan_eco_flow": float(params["liyutan_eco_flow"]),
        "shilin_diversion_limit": float(params["shilin_diversion_limit"]),
        "override_list": overrides,
        "enable_override": bool(validated["overrides_enabled"]),
        "v2_outflows_authoritative": True,
        "v2_initial_capacity_requires_confirmation": (
            initial_capacity_requires_confirmation
        ),
    }


def apply_continuation_to_session(
    state: MutableMapping[str, Any],
    draft: OfficialContinuationDraft,
    *,
    source_metadata: OfficialVersionMetadata,
    active_annual_validated: bool,
    active_annual_error: str | None = None,
) -> None:
    """Validate the complete transition before changing any session key."""

    if draft.derived_from_official_version_id != source_metadata.version_id:
        raise ValueError("continuation derived_from 與選取正式版本不一致")
    patch = _batch_session_patch(
        draft.batch, initial_capacity_requires_confirmation=False
    )
    patch.update(
        v2_work_source=CONTINUATION_SOURCE,
        v2_continuation_active=True,
        v2_source_official_version_id=source_metadata.version_id,
        v2_source_official_metadata=asdict(source_metadata),
        v2_derived_from_official_version_id=(
            draft.derived_from_official_version_id
        ),
        v2_active_annual_data_version_id=draft.annual_data_version_id,
        loaded_shared_annual_version_id=draft.annual_data_version_id,
        v2_active_annual_validated=active_annual_validated,
        v2_latest_added_periods=(),
        v2_latest_removed_periods=(),
        v2_latest_preserved_periods=tuple(draft.batch["periods"]),
        v2_latest_added_dates=(),
        v2_extension_annual_version_id=None,
    )
    for key, value in patch.items():
        state[key] = value
    if active_annual_error:
        state["v2_active_annual_error"] = active_annual_error
    else:
        state.pop("v2_active_annual_error", None)
    state["workspace_annual_stale"] = False
    state.pop("pending_shared_annual_version_id", None)
    state.pop("workspace_annual_retain_acknowledged", None)
    state["v2_widget_version"] = int(state.get("v2_widget_version", 0)) + 1
    for key in (
        "v2_requested_display_start_date",
        "v2_requested_projection_start_date",
        "v2_requested_projection_end_date",
        "v2_pending_initial_capacity_value",
    ):
        state.pop(key, None)
    invalidate_working_artifacts(state)


def apply_portable_batch_to_session(
    state: MutableMapping[str, Any],
    batch: dict,
    *,
    current_annual_version_id: str | None,
) -> None:
    """Atomically apply a validated portable batch without official lineage."""

    patch = _batch_session_patch(
        batch, initial_capacity_requires_confirmation=False
    )
    patch.update(
        v2_work_source=NEW_WORK_SOURCE,
        v2_continuation_active=False,
        v2_derived_from_official_version_id=None,
        v2_active_annual_data_version_id=current_annual_version_id,
        loaded_shared_annual_version_id=current_annual_version_id,
        v2_active_annual_validated=current_annual_version_id is not None,
        v2_latest_added_periods=(),
        v2_latest_removed_periods=(),
        v2_latest_preserved_periods=(),
        v2_latest_added_dates=(),
        v2_extension_annual_version_id=None,
    )
    for key, value in patch.items():
        state[key] = value
    for key in (
        "v2_source_official_version_id",
        "v2_source_official_metadata",
        "v2_initial_capacity_requires_confirmation",
        "v2_active_annual_error",
    ):
        state.pop(key, None)
    # The radio widget is rendered before the JSON importer. Removing its
    # widget key lets the next rerun derive the new-work selection from the
    # now non-continuation domain state without mutating an instantiated widget.
    state.pop("v2_requested_work_source", None)
    state["v2_widget_version"] = int(state.get("v2_widget_version", 0)) + 1
    invalidate_working_artifacts(state)


def apply_date_adjustment_to_session(
    state: MutableMapping[str, Any], adjustment: ContinuationDateAdjustment
) -> None:
    if not state.get("v2_continuation_active"):
        raise ValueError("目前不是正式版本接續工作")
    expected_source = state.get("v2_source_official_version_id")
    if adjustment.derived_from_official_version_id != expected_source:
        raise ValueError("date adjustment derived lineage 與 session 不一致")
    patch = _batch_session_patch(
        adjustment.batch,
        allow_pending_inflows=True,
        initial_capacity_requires_confirmation=(
            adjustment.initial_capacity_requires_confirmation
        ),
    )
    patch.update(
        v2_active_annual_data_version_id=adjustment.annual_data_version_id,
        loaded_shared_annual_version_id=adjustment.annual_data_version_id,
        v2_latest_added_periods=tuple(adjustment.added_periods),
        v2_latest_removed_periods=tuple(adjustment.removed_periods),
        v2_latest_preserved_periods=tuple(adjustment.preserved_periods),
        v2_latest_added_dates=tuple(adjustment.added_dates),
        v2_extension_annual_version_id=adjustment.extension_annual_version_id,
    )
    for key, value in patch.items():
        state[key] = value
    state["v2_widget_version"] = int(state.get("v2_widget_version", 0)) + 1
    for key in (
        "v2_requested_display_start_date",
        "v2_requested_projection_start_date",
        "v2_requested_projection_end_date",
        "v2_pending_initial_capacity_value",
    ):
        state.pop(key, None)
    invalidate_working_artifacts(state)


def current_continuation_draft(
    state: MutableMapping[str, Any]
) -> OfficialContinuationDraft:
    if not state.get("v2_continuation_active"):
        raise ValueError("目前不是正式版本接續工作")
    return OfficialContinuationDraft(
        batch=copy.deepcopy(state["v2_batch"]),
        derived_from_official_version_id=state[
            "v2_derived_from_official_version_id"
        ],
        annual_data_version_id=state["v2_active_annual_data_version_id"],
    )


def current_continuation_adjustment(
    state: MutableMapping[str, Any]
) -> ContinuationDateAdjustment:
    return ContinuationDateAdjustment(
        draft=current_continuation_draft(state),
        added_periods=tuple(state.get("v2_latest_added_periods", ())),
        removed_periods=tuple(state.get("v2_latest_removed_periods", ())),
        preserved_periods=tuple(state.get("v2_latest_preserved_periods", ())),
        added_dates=tuple(state.get("v2_latest_added_dates", ())),
        initial_capacity_requires_confirmation=bool(
            state.get("v2_initial_capacity_requires_confirmation")
        ),
        requires_recalculation=True,
        extension_annual_version_id=state.get(
            "v2_extension_annual_version_id"
        ),
    )


def preview_continuation_date_request(
    draft: OfficialContinuationDraft,
    *,
    display_start_date: object,
    projection_start_date: object,
    projection_end_date: object,
) -> ContinuationDateRequestPreview:
    display = parse_date(display_start_date)
    start = parse_date(projection_start_date)
    end = parse_date(projection_end_date)
    if display > start:
        raise ValueError("展示起日不可晚於推估起日")
    new_periods = working_period_keys_for_range(start, end)
    old_periods = tuple(draft.batch["periods"])
    old_set, new_set = set(old_periods), set(new_periods)
    return ContinuationDateRequestPreview(
        display_start_date=display,
        projection_start_date=start,
        projection_end_date=end,
        added_periods=tuple(key for key in new_periods if key not in old_set),
        removed_periods=tuple(key for key in old_periods if key not in new_set),
        preserved_periods=tuple(key for key in new_periods if key in old_set),
        changed=(
            display != parse_date(draft.batch["display_start_date"])
            or start != parse_date(draft.batch["projection_start_date"])
            or end != parse_date(draft.batch["projection_end_date"])
        ),
    )


def reset_to_new_work(
    state: MutableMapping[str, Any], *, current_annual_version_id: str | None
) -> None:
    """Discard the active batch/context while retaining comparison registries."""

    state.pop("v2_batch", None)
    invalidate_working_artifacts(state)
    for key in (
        "v2_continuation_active",
        "v2_source_official_version_id",
        "v2_source_official_metadata",
        "v2_derived_from_official_version_id",
        "v2_latest_added_periods",
        "v2_latest_removed_periods",
        "v2_latest_preserved_periods",
        "v2_latest_added_dates",
        "v2_extension_annual_version_id",
        "v2_initial_capacity_requires_confirmation",
        "v2_active_annual_error",
    ):
        state.pop(key, None)
    state["v2_work_source"] = NEW_WORK_SOURCE
    state["v2_active_annual_data_version_id"] = current_annual_version_id
    if current_annual_version_id is not None:
        state["loaded_shared_annual_version_id"] = current_annual_version_id
    state["v2_active_annual_validated"] = current_annual_version_id is not None
    state["v2_outflows_authoritative"] = False
    state["v2_widget_version"] = int(state.get("v2_widget_version", 0)) + 1
