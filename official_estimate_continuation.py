"""Pure domain transformation from an official snapshot to working state.

This module has no filesystem, Streamlit, annual-current, or publishing
dependency.  The source snapshot remains an immutable formal record; a
continuation is always a separately identified, deep-copied working batch.
"""

from __future__ import annotations

import copy
import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from official_estimate_loader import OfficialEstimateSnapshot
from shared_storage_schema import StorageValidationError, validate_safe_id
from v2_workflow import new_id, validate_batch


BatchIdFactory = Callable[[], str]
Clock = Callable[[], dt.datetime]


class OfficialContinuationErrorCode(str, Enum):
    """Stable failure categories for continuation creation."""

    INVALID_SNAPSHOT = "invalid_snapshot"
    INVALID_BATCH_ID = "invalid_batch_id"
    INVALID_BATCH_NAME = "invalid_batch_name"
    INVALID_CREATED_AT = "invalid_created_at"
    BATCH_VALIDATION_FAILED = "batch_validation_failed"


class OfficialContinuationError(ValueError):
    """Raised before a continuation draft can be returned."""

    def __init__(self, code: OfficialContinuationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OfficialContinuationDraft:
    """A new working batch and its non-publication continuation context.

    ``derived_from_official_version_id`` records the exact snapshot selected
    for this continuation.  Publication-time ``previous_official_version_id``
    is intentionally absent from this contract.
    """

    batch: dict
    derived_from_official_version_id: str
    annual_data_version_id: str


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OfficialContinuationError(
            OfficialContinuationErrorCode.INVALID_BATCH_NAME,
            f"{label} 必須是非空白字串。",
        )
    return value


def _new_batch_id(
    source_batch_id: object, batch_id_factory: BatchIdFactory
) -> str:
    try:
        value = batch_id_factory()
    except Exception as exc:
        raise OfficialContinuationError(
            OfficialContinuationErrorCode.INVALID_BATCH_ID,
            f"無法產生新的 working batch_id：{exc}",
        ) from exc
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
    ):
        raise OfficialContinuationError(
            OfficialContinuationErrorCode.INVALID_BATCH_ID,
            "新的 working batch_id 必須是前後無空白的非空白字串。",
        )
    batch_id = value
    if batch_id == source_batch_id:
        raise OfficialContinuationError(
            OfficialContinuationErrorCode.INVALID_BATCH_ID,
            "新的 working batch_id 不得沿用 source official batch_id。",
        )
    return batch_id


def _parse_created_at(value: str) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise OfficialContinuationError(
            OfficialContinuationErrorCode.INVALID_CREATED_AT,
            "working batch created_at 必須是包含時區的 ISO 8601 UTC 時間。",
        )
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise OfficialContinuationError(
            OfficialContinuationErrorCode.INVALID_CREATED_AT,
            "working batch created_at 必須是包含時區的 ISO 8601 UTC 時間。",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OfficialContinuationError(
            OfficialContinuationErrorCode.INVALID_CREATED_AT,
            "working batch created_at 必須包含時區。",
        )
    return parsed


def _new_created_at(created_at: str | None, clock: Clock) -> str:
    if created_at is not None:
        parsed = _parse_created_at(created_at)
    else:
        try:
            clock_value = clock()
        except Exception as exc:
            raise OfficialContinuationError(
                OfficialContinuationErrorCode.INVALID_CREATED_AT,
                f"無法取得 working batch 建立時間：{exc}",
            ) from exc
        if not isinstance(clock_value, dt.datetime):
            raise OfficialContinuationError(
                OfficialContinuationErrorCode.INVALID_CREATED_AT,
                "clock 必須回傳 datetime。",
            )
        if clock_value.tzinfo is None or clock_value.utcoffset() is None:
            raise OfficialContinuationError(
                OfficialContinuationErrorCode.INVALID_CREATED_AT,
                "clock 必須回傳包含時區的 datetime。",
            )
        parsed = clock_value
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def build_official_continuation(
    snapshot: OfficialEstimateSnapshot,
    *,
    batch_name: str | None = None,
    batch_id_factory: BatchIdFactory = new_id,
    created_at: str | None = None,
    clock: Clock = _utc_now,
) -> OfficialContinuationDraft:
    """Create a new, validated working batch from one official snapshot.

    Dates, scenarios, input conditions, source scenario IDs, and the source
    annual-data version are preserved exactly.  Formal summaries and daily
    results remain reference data on ``snapshot`` and never enter the returned
    working batch.
    """

    if not isinstance(snapshot, OfficialEstimateSnapshot):
        raise OfficialContinuationError(
            OfficialContinuationErrorCode.INVALID_SNAPSHOT,
            "continuation 只能由 OfficialEstimateSnapshot 建立。",
        )

    try:
        source_version_id = validate_safe_id(
            snapshot.metadata.version_id, "source official version_id"
        )
        source_annual_version_id = validate_safe_id(
            snapshot.annual_data_version_id, "source annual_data_version_id"
        )
        source_batch = snapshot.batch
    except (AttributeError, KeyError, StorageValidationError) as exc:
        raise OfficialContinuationError(
            OfficialContinuationErrorCode.INVALID_SNAPSHOT,
            f"OfficialEstimateSnapshot identity 不合法：{exc}",
        ) from exc
    if (
        not isinstance(snapshot.manifest, dict)
        or not isinstance(snapshot.inputs, dict)
        or snapshot.manifest.get("version_id") != source_version_id
        or snapshot.manifest.get("annual_data_version_id")
        != source_annual_version_id
        or snapshot.inputs.get("annual_data_version_id")
        != source_annual_version_id
        or not isinstance(source_batch, dict)
        or source_batch.get("batch_id") != snapshot.manifest.get("batch_id")
        or source_batch.get("batch_id") != snapshot.inputs.get("batch_id")
    ):
        raise OfficialContinuationError(
            OfficialContinuationErrorCode.INVALID_SNAPSHOT,
            "OfficialEstimateSnapshot identity 或 annual-data context 不一致。",
        )

    working_batch = copy.deepcopy(source_batch)
    working_batch["batch_id"] = _new_batch_id(
        source_batch.get("batch_id"), batch_id_factory
    )
    source_name = source_batch.get("batch_name")
    resolved_name = (
        _required_text(batch_name, "自訂 batch_name")
        if batch_name is not None
        else f"{_required_text(source_name, 'source batch_name')}（接續）"
    )
    working_batch["batch_name"] = resolved_name
    working_batch["created_at"] = _new_created_at(created_at, clock)
    if working_batch["created_at"] == source_batch.get("created_at"):
        raise OfficialContinuationError(
            OfficialContinuationErrorCode.INVALID_CREATED_AT,
            "新的 working batch created_at 不得沿用 source batch.created_at。",
        )
    working_batch.pop("results", None)
    working_batch.pop("results_fingerprint", None)
    working_batch.pop("previous_official_version_id", None)
    working_batch.pop("derived_from_official_version_id", None)

    try:
        validated_batch = validate_batch(working_batch)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise OfficialContinuationError(
            OfficialContinuationErrorCode.BATCH_VALIDATION_FAILED,
            f"新的 working batch 未通過 V2 validate_batch()：{exc}",
        ) from exc

    return OfficialContinuationDraft(
        batch=validated_batch,
        derived_from_official_version_id=source_version_id,
        annual_data_version_id=source_annual_version_id,
    )
