"""Canonical calendar-to-ten-day-period mapping used by domain modules."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any


PERIODS = ("上旬", "中旬", "下旬")
_WORKING_PERIOD_RE = re.compile(r"^(\d{4})-(\d{1,2})-(上旬|中旬|下旬)$")


def parse_date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"無效日期：{value}") from exc


def period_name_for_date(value: Any) -> str:
    """Return 上旬/中旬/下旬 from an actual calendar date."""

    day = parse_date(value).day
    return PERIODS[0] if day <= 10 else PERIODS[1] if day <= 20 else PERIODS[2]


def annual_period_key(month: int, period: str) -> str:
    """Build the annual-data month/period key without a year."""

    if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError(f"無效月份：{month}")
    if period not in PERIODS:
        raise ValueError(f"無效旬別：{period}")
    return f"{month:02d}-{period}"


def annual_period_key_for_date(value: Any) -> str:
    date = parse_date(value)
    return annual_period_key(date.month, period_name_for_date(date))


def working_period_key_for_date(value: Any) -> str:
    date = parse_date(value)
    return f"{date.year}-{date.month}-{period_name_for_date(date)}"


def parse_working_period_key(value: object) -> tuple[int, int, str]:
    """Parse an exact ``YYYY-M-旬別`` working key."""

    if not isinstance(value, str):
        raise ValueError(f"無效 working period key：{value}")
    matched = _WORKING_PERIOD_RE.fullmatch(value)
    if matched is None:
        raise ValueError(f"無效 working period key：{value}")
    year, month, period = int(matched.group(1)), int(matched.group(2)), matched.group(3)
    if not 1 <= month <= 12:
        raise ValueError(f"無效 working period key：{value}")
    return year, month, period


def annual_period_key_for_working_period(value: object) -> str:
    _, month, period = parse_working_period_key(value)
    return annual_period_key(month, period)


def dates_in_projection_range(start: Any, end: Any) -> tuple[dt.date, ...]:
    """Return every date in the half-open projection range ``[start, end)``."""

    start_date, end_date = parse_date(start), parse_date(end)
    if start_date >= end_date:
        raise ValueError("推估起日必須早於迄日")
    return tuple(
        start_date + dt.timedelta(days=offset)
        for offset in range((end_date - start_date).days)
    )


def working_period_keys_for_range(start: Any, end: Any) -> tuple[str, ...]:
    """Build chronologically ordered unique working keys for ``[start, end)``."""

    seen: set[str] = set()
    ordered: list[str] = []
    for date in dates_in_projection_range(start, end):
        key = working_period_key_for_date(date)
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return tuple(ordered)
