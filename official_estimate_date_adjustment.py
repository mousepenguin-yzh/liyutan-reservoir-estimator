"""Pure Phase 2-6C date migration for official continuation drafts.

Filesystem lookup, annual-current selection, Streamlit state, and publishing
are deliberately outside this module.  A caller must explicitly provide the
validated annual snapshot used for genuinely new periods.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable

from official_estimate_continuation import OfficialContinuationDraft
from shared_storage_reader import AnnualDataSnapshot
from shared_storage_schema import StorageValidationError, validate_safe_id
from ten_day_period import (
    annual_period_key,
    annual_period_key_for_working_period,
    dates_in_projection_range,
    parse_date,
    working_period_key_for_date,
    working_period_keys_for_range,
)
from v2_workflow import UNIT_CMS, migrate_batch_periods, validate_batch


_OUTFLOW_FIELDS = (
    "upstream_irrigation_cms",
    "downstream_irrigation_cms",
    "public_water_10k_ton_per_day",
)
_ALLOWED_QUANTILES = {"Q80": "q80_cms", "Q90": "q90_cms"}


class ContinuationDateAdjustmentErrorCode(str, Enum):
    INVALID_DRAFT = "invalid_draft"
    INVALID_DATE_RANGE = "invalid_date_range"
    EXTENSION_ANNUAL_REQUIRED = "extension_annual_required"
    INVALID_ANNUAL_SNAPSHOT = "invalid_annual_snapshot"
    MISSING_ANNUAL_PERIOD = "missing_annual_period"
    BATCH_VALIDATION_FAILED = "batch_validation_failed"
    INVALID_QUANTILE = "invalid_quantile"
    INVALID_SCENARIO = "invalid_scenario"
    SHARED_INFLOW_CONFLICT = "shared_inflow_conflict"
    INITIAL_CAPACITY_NOT_PENDING = "initial_capacity_not_pending"
    INVALID_INITIAL_CAPACITY = "invalid_initial_capacity"


class ContinuationDateAdjustmentError(ValueError):
    def __init__(
        self, code: ContinuationDateAdjustmentErrorCode, message: str
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ContinuationDateAdjustment:
    """A migrated continuation plus UI-neutral change metadata."""

    draft: OfficialContinuationDraft
    added_periods: tuple[str, ...]
    removed_periods: tuple[str, ...]
    preserved_periods: tuple[str, ...]
    added_dates: tuple[str, ...]
    initial_capacity_requires_confirmation: bool
    requires_recalculation: bool
    extension_annual_version_id: str | None

    @property
    def batch(self) -> dict:
        return self.draft.batch

    @property
    def derived_from_official_version_id(self) -> str:
        return self.draft.derived_from_official_version_id

    @property
    def annual_data_version_id(self) -> str:
        return self.draft.annual_data_version_id


def _finite_nonnegative(value: object, label: str) -> float:
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


def _annual_version_id(snapshot: AnnualDataSnapshot) -> str:
    if not isinstance(snapshot, AnnualDataSnapshot):
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_ANNUAL_SNAPSHOT,
            "extension annual baseline 必須是 AnnualDataSnapshot。",
        )
    try:
        return validate_safe_id(
            snapshot.version.get("version_id"), "extension annual version_id"
        )
    except (AttributeError, StorageValidationError) as exc:
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_ANNUAL_SNAPSHOT,
            f"extension annual version identity 不合法：{exc}",
        ) from exc


def _annual_lookup(rows: Iterable[dict], label: str) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    try:
        for row in rows:
            key = annual_period_key(int(row["month"]), row["period"])
            if row.get("period_key") != key:
                actual_key = row.get("period_key")
                raise ValueError(f"period_key 與 month/period 不一致：{actual_key}")
            if key in lookup:
                raise ValueError(f"period_key 重複：{key}")
            lookup[key] = row
    except (KeyError, TypeError, ValueError) as exc:
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_ANNUAL_SNAPSHOT,
            f"extension annual {label} 不合法：{exc}",
        ) from exc
    return lookup


def _extension_rows(
    snapshot: AnnualDataSnapshot, added_periods: tuple[str, ...]
) -> tuple[str, dict[str, dict], dict[str, dict]]:
    version_id = _annual_version_id(snapshot)
    hydrology = _annual_lookup(snapshot.hydrology, "hydrology")
    outflows = _annual_lookup(snapshot.outflow_demand, "outflow_demand")
    for working_key in added_periods:
        annual_key = annual_period_key_for_working_period(working_key)
        missing = []
        if annual_key not in hydrology:
            missing.append("hydrology")
        if annual_key not in outflows:
            missing.append("outflow_demand")
        if missing:
            raise ContinuationDateAdjustmentError(
                ContinuationDateAdjustmentErrorCode.MISSING_ANNUAL_PERIOD,
                f"extension annual {version_id} 的 {annual_key} 缺少：{'、'.join(missing)}。",
            )
        try:
            for field in _OUTFLOW_FIELDS:
                _finite_nonnegative(outflows[annual_key].get(field), f"{annual_key} {field}")
        except ValueError as exc:
            raise ContinuationDateAdjustmentError(
                ContinuationDateAdjustmentErrorCode.INVALID_ANNUAL_SNAPSHOT,
                f"extension annual {version_id} outflow_demand 不合法：{exc}",
            ) from exc
    return version_id, hydrology, outflows


def _annual_outflow(row: dict, version_id: str) -> dict:
    return {
        field: _finite_nonnegative(row.get(field), field) for field in _OUTFLOW_FIELDS
    } | {
        "source_type": f"年度基準 {version_id} 去年同期",
        "note": "",
    }


def _validate_pending_cell(cell: object, key: str) -> None:
    if not isinstance(cell, dict):
        raise ValueError(f"共用 {key} pending inflow 必須是物件")
    if (
        cell.get("cms") is not None
        or cell.get("source_type") != "待填"
        or cell.get("source_unit") != UNIT_CMS
        or cell.get("source_value") is not None
        or not isinstance(cell.get("note"), str)
    ):
        raise ValueError(f"共用 {key} pending inflow 格式不合法")


def validate_continuation_pending_batch(
    batch: dict, *, initial_capacity_requires_confirmation: bool
) -> dict:
    """Validate a working batch while allowing explicit 2-6C pending state.

    The formal/V2 validator remains strict.  This adapter validates the exact
    pending representation on a copy, substitutes validation-only sentinels,
    and never changes the returned or source batch.
    """

    candidate = copy.deepcopy(batch)
    if initial_capacity_requires_confirmation:
        if candidate.get("initial_capacity") is not None:
            raise ValueError("起始庫容待確認時，active initial_capacity 必須為空白")
        candidate["initial_capacity"] = 0.0
    elif candidate.get("initial_capacity") is None:
        raise ValueError("起始庫容尚未確認")
    for key, cell in candidate.get("shared_inflows", {}).items():
        if isinstance(cell, dict) and cell.get("cms") is None:
            _validate_pending_cell(cell, key)
            cell["cms"] = 0.0
    validate_batch(candidate)
    return copy.deepcopy(batch)


def _validate_source_draft(draft: OfficialContinuationDraft) -> None:
    if not isinstance(draft, OfficialContinuationDraft):
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_DRAFT,
            "日期調整只能由 OfficialContinuationDraft 建立。",
        )
    try:
        validate_safe_id(
            draft.derived_from_official_version_id,
            "derived_from_official_version_id",
        )
        validate_safe_id(draft.annual_data_version_id, "annual_data_version_id")
        validate_continuation_pending_batch(
            draft.batch,
            initial_capacity_requires_confirmation=draft.batch.get("initial_capacity") is None,
        )
    except (StorageValidationError, ValueError, TypeError, KeyError) as exc:
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_DRAFT,
            f"OfficialContinuationDraft 不合法：{exc}",
        ) from exc


def _daily_from_outflow(date_iso: str, outflow: dict) -> dict:
    return {
        "date": date_iso,
        **{field: float(outflow[field]) for field in _OUTFLOW_FIELDS},
        "source_type": outflow.get("source_type", "共用出流"),
        "note": outflow.get("note", ""),
    }


def adjust_continuation_dates(
    draft: OfficialContinuationDraft,
    *,
    display_start_date: object | None = None,
    projection_start_date: object | None = None,
    projection_end_date: object | None = None,
    extension_annual_snapshot: AnnualDataSnapshot | None = None,
) -> ContinuationDateAdjustment:
    """Migrate dates by key, preserving overlap and explicitly baselining additions."""

    _validate_source_draft(draft)
    source = draft.batch
    try:
        old_display = parse_date(source["display_start_date"])
        old_start = parse_date(source["projection_start_date"])
        old_end = parse_date(source["projection_end_date"])
        new_display = old_display if display_start_date is None else parse_date(display_start_date)
        new_start = old_start if projection_start_date is None else parse_date(projection_start_date)
        new_end = old_end if projection_end_date is None else parse_date(projection_end_date)
        if new_start >= new_end:
            raise ValueError("推估起日必須早於迄日")
        if new_display > new_start:
            raise ValueError("展示起日不可晚於推估起日")
        old_periods = tuple(source["periods"])
        new_periods = working_period_keys_for_range(new_start, new_end)
    except (KeyError, TypeError, ValueError) as exc:
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_DATE_RANGE,
            str(exc),
        ) from exc

    old_set, new_set = set(old_periods), set(new_periods)
    added_periods = tuple(key for key in new_periods if key not in old_set)
    removed_periods = tuple(key for key in old_periods if key not in new_set)
    preserved_periods = tuple(key for key in new_periods if key in old_set)
    old_dates = {
        parse_date(item["date"]).isoformat() for item in source["daily_outflows"]
    }
    new_dates = dates_in_projection_range(new_start, new_end)
    added_dates = tuple(date.isoformat() for date in new_dates if date.isoformat() not in old_dates)

    annual_version_id = draft.annual_data_version_id
    extension_version_id: str | None = None
    annual_outflows: dict[str, dict] = {}
    if added_periods:
        if extension_annual_snapshot is None:
            raise ContinuationDateAdjustmentError(
                ContinuationDateAdjustmentErrorCode.EXTENSION_ANNUAL_REQUIRED,
                "新增旬別必須由 caller 明確提供 extension AnnualDataSnapshot。",
            )
        extension_version_id, _, annual_outflows = _extension_rows(
            extension_annual_snapshot, added_periods
        )
        annual_version_id = extension_version_id

    projection_changed = new_start != old_start or new_end != old_end
    display_changed = new_display != old_display
    settings_changed = projection_changed or display_changed
    working = copy.deepcopy(source)
    if projection_changed:
        working = migrate_batch_periods(working, list(new_periods))
        for key in added_periods:
            annual_key = annual_period_key_for_working_period(key)
            working["outflows"][key] = _annual_outflow(
                annual_outflows[annual_key], annual_version_id
            )

        source_daily = {
            parse_date(item["date"]).isoformat(): item
            for item in source["daily_outflows"]
        }
        working["daily_outflows"] = []
        for date in new_dates:
            date_iso = date.isoformat()
            if date_iso in source_daily:
                working["daily_outflows"].append(copy.deepcopy(source_daily[date_iso]))
                continue
            period_key = working_period_key_for_date(date)
            working["daily_outflows"].append(
                _daily_from_outflow(date_iso, working["outflows"][period_key])
            )

    working["display_start_date"] = new_display.isoformat()
    working["projection_start_date"] = new_start.isoformat()
    working["projection_end_date"] = new_end.isoformat()
    initial_pending = source.get("initial_capacity") is None
    if new_start != old_start:
        working["initial_capacity"] = None
        initial_pending = True
    if settings_changed:
        working.pop("results", None)
        working.pop("results_fingerprint", None)

    try:
        validated = validate_continuation_pending_batch(
            working,
            initial_capacity_requires_confirmation=initial_pending,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.BATCH_VALIDATION_FAILED,
            f"日期調整後 working state 不合法：{exc}",
        ) from exc

    return ContinuationDateAdjustment(
        draft=OfficialContinuationDraft(
            batch=validated,
            derived_from_official_version_id=draft.derived_from_official_version_id,
            annual_data_version_id=annual_version_id,
        ),
        added_periods=added_periods,
        removed_periods=removed_periods,
        preserved_periods=preserved_periods,
        added_dates=added_dates,
        initial_capacity_requires_confirmation=initial_pending,
        requires_recalculation=settings_changed,
        extension_annual_version_id=extension_version_id,
    )


def _scenario_ids(value: str | Iterable[str]) -> tuple[str, ...]:
    values = (value,) if isinstance(value, str) else tuple(value)
    if not values or any(not isinstance(item, str) or not item for item in values):
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_SCENARIO,
            "scenario_ids 必須包含至少一個有效 scenario_id。",
        )
    if len(values) != len(set(values)):
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_SCENARIO,
            "scenario_ids 不可重複。",
        )
    return values


def apply_added_period_quantile(
    adjustment: ContinuationDateAdjustment,
    *,
    scenario_ids: str | Iterable[str],
    quantile: str,
    annual_snapshot: AnnualDataSnapshot,
) -> ContinuationDateAdjustment:
    """Fill only currently blank cells in this adjustment's added periods."""

    if not isinstance(adjustment, ContinuationDateAdjustment):
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_DRAFT,
            "Q80/Q90 快速套用必須接受 ContinuationDateAdjustment。",
        )
    normalized_quantile = str(quantile).upper()
    column = _ALLOWED_QUANTILES.get(normalized_quantile)
    if column is None:
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_QUANTILE,
            "快速套用只允許 Q80 或 Q90。",
        )
    selected_ids = _scenario_ids(scenario_ids)
    known_ids = {item["scenario_id"] for item in adjustment.batch["scenarios"]}
    unknown = set(selected_ids) - known_ids
    if unknown:
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_SCENARIO,
            "找不到 scenario_id：" + "、".join(sorted(unknown)),
        )
    version_id, hydrology, _ = _extension_rows(
        annual_snapshot, adjustment.added_periods
    )
    if version_id != adjustment.annual_data_version_id:
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_ANNUAL_SNAPSHOT,
            "Q80/Q90 annual snapshot 必須等於 adjustment 的 active annual baseline。",
        )

    working = copy.deepcopy(adjustment.batch)
    shared_keys = set(working["periods"][: int(working["shared_period_count"])])
    added_shared = tuple(key for key in adjustment.added_periods if key in shared_keys)
    blank_added_shared = tuple(
        key for key in added_shared if working["shared_inflows"][key].get("cms") is None
    )
    if blank_added_shared and set(selected_ids) != known_ids:
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.SHARED_INFLOW_CONFLICT,
            "新增共用旬的 Q80/Q90 必須同時套用全部 scenarios。",
        )

    def cell_for(key: str) -> dict:
        annual_key = annual_period_key_for_working_period(key)
        try:
            number = _finite_nonnegative(
                hydrology[annual_key].get(column), f"{annual_key} {column}"
            )
        except (KeyError, ValueError) as exc:
            raise ContinuationDateAdjustmentError(
                ContinuationDateAdjustmentErrorCode.INVALID_ANNUAL_SNAPSHOT,
                f"extension annual {version_id} hydrology 不合法：{exc}",
            ) from exc
        return {
            "cms": number,
            "source_type": f"年度基準 {normalized_quantile}",
            "source_unit": UNIT_CMS,
            "source_value": number,
            "note": f"annual_data_version_id={version_id}",
        }

    for key in blank_added_shared:
        scenario_cells = [scenario["inflows"][key] for scenario in working["scenarios"]]
        if any(cell.get("cms") is not None for cell in scenario_cells):
            continue
        value = cell_for(key)
        working["shared_inflows"][key] = copy.deepcopy(value)
        for scenario in working["scenarios"]:
            scenario["inflows"][key] = copy.deepcopy(value)

    selected = set(selected_ids)
    for scenario in working["scenarios"]:
        if scenario["scenario_id"] not in selected:
            continue
        for key in adjustment.added_periods:
            if key in shared_keys:
                continue
            if scenario["inflows"][key].get("cms") is None:
                scenario["inflows"][key] = cell_for(key)

    working.pop("results", None)
    working.pop("results_fingerprint", None)
    try:
        validated = validate_continuation_pending_batch(
            working,
            initial_capacity_requires_confirmation=(
                adjustment.initial_capacity_requires_confirmation
            ),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.BATCH_VALIDATION_FAILED,
            f"Q80/Q90 套用後 working state 不合法：{exc}",
        ) from exc
    return replace(
        adjustment,
        draft=OfficialContinuationDraft(
            batch=validated,
            derived_from_official_version_id=adjustment.derived_from_official_version_id,
            annual_data_version_id=adjustment.annual_data_version_id,
        ),
        requires_recalculation=True,
    )


def apply_added_period_q80(
    adjustment: ContinuationDateAdjustment,
    *,
    scenario_ids: str | Iterable[str],
    annual_snapshot: AnnualDataSnapshot,
) -> ContinuationDateAdjustment:
    return apply_added_period_quantile(
        adjustment,
        scenario_ids=scenario_ids,
        quantile="Q80",
        annual_snapshot=annual_snapshot,
    )


def apply_added_period_q90(
    adjustment: ContinuationDateAdjustment,
    *,
    scenario_ids: str | Iterable[str],
    annual_snapshot: AnnualDataSnapshot,
) -> ContinuationDateAdjustment:
    return apply_added_period_quantile(
        adjustment,
        scenario_ids=scenario_ids,
        quantile="Q90",
        annual_snapshot=annual_snapshot,
    )


def confirm_initial_capacity(
    adjustment: ContinuationDateAdjustment, initial_capacity: object
) -> ContinuationDateAdjustment:
    """Confirm a replacement capacity after projection start changed."""

    if not isinstance(adjustment, ContinuationDateAdjustment):
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_DRAFT,
            "起始庫容確認必須接受 ContinuationDateAdjustment。",
        )
    if not adjustment.initial_capacity_requires_confirmation:
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INITIAL_CAPACITY_NOT_PENDING,
            "目前起始庫容不在待確認狀態。",
        )
    try:
        number = _finite_nonnegative(initial_capacity, "起始庫容")
    except ValueError as exc:
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.INVALID_INITIAL_CAPACITY,
            str(exc),
        ) from exc
    working = copy.deepcopy(adjustment.batch)
    working["initial_capacity"] = number
    working.pop("results", None)
    working.pop("results_fingerprint", None)
    try:
        validated = validate_continuation_pending_batch(
            working, initial_capacity_requires_confirmation=False
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise ContinuationDateAdjustmentError(
            ContinuationDateAdjustmentErrorCode.BATCH_VALIDATION_FAILED,
            f"起始庫容確認後 working state 不合法：{exc}",
        ) from exc
    return replace(
        adjustment,
        draft=OfficialContinuationDraft(
            batch=validated,
            derived_from_official_version_id=adjustment.derived_from_official_version_id,
            annual_data_version_id=adjustment.annual_data_version_id,
        ),
        initial_capacity_requires_confirmation=False,
        requires_recalculation=True,
    )
