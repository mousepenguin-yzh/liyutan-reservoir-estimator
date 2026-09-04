"""Pure schemas and validators for versioned shared-storage artifacts.

This module deliberately performs no filesystem, network, or Streamlit work.
Version bundles are mappings of relative file names to bytes, so callers can
validate staging data before any later SMB publishing implementation exists.
"""

from __future__ import annotations

import copy
import csv
import datetime as dt
import hashlib
import io
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from v2_workflow import validate_batch as validate_v2_batch


SCHEMA_VERSION = 1
SHARED_ROOT_SCHEMA = "liyutan-reservoir-estimator/shared-root"
ANNUAL_VERSION_SCHEMA = "liyutan-reservoir-estimator/annual-data-version"
RESERVOIR_PARAMETERS_SCHEMA = "liyutan-reservoir-estimator/reservoir-parameters"
ANNUAL_CURRENT_SCHEMA = "liyutan-reservoir-estimator/annual-data-current"
AUDIT_EVENT_SCHEMA = "liyutan-reservoir-estimator/audit-event"
ANNUAL_ACTIVATION_EVENT_TYPE = "annual-data-activation"
OFFICIAL_ESTIMATE_SCHEMA = "liyutan-reservoir-estimator/official-estimate-version"
OFFICIAL_INPUTS_SCHEMA = "liyutan-reservoir-estimator/official-inputs"
OFFICIAL_CURRENT_SCHEMA = "liyutan-reservoir-estimator/official-estimate-current"
COMMITTED_SCHEMA = "liyutan-reservoir-estimator/committed"
BATCH_SCHEMA = "liyutan-reservoir-estimator/batch"
BATCH_SCHEMA_VERSION = 1

PERIODS = ("上旬", "中旬", "下旬")
Q_COLUMNS = tuple(f"q{quantile:02d}_cms" for quantile in range(5, 100, 5))
HYDROLOGY_COLUMNS = ("period_key", "month", "period", *Q_COLUMNS)
OUTFLOW_COLUMNS = (
    "period_key",
    "month",
    "period",
    "upstream_irrigation_cms",
    "downstream_irrigation_cms",
    "public_water_10k_ton_per_day",
)
SUMMARY_COLUMNS = (
    "version_id",
    "batch_id",
    "scenario_id",
    "scenario_name",
    "scenario_order",
    "calculation_status",
    "settings_fingerprint",
    "final_capacity_10k_ton",
    "minimum_capacity_10k_ton",
    "spill_volume_10k_ton",
    "agricultural_reduction_volume_10k_ton",
    "dry_days",
)
DAILY_RESULT_COLUMNS = (
    "version_id",
    "batch_id",
    "scenario_id",
    "settings_fingerprint",
    "date",
    "natural_inflow_cms",
    "upstream_demand_cms",
    "downstream_demand_cms",
    "actual_upstream_release_cms",
    "actual_downstream_release_cms",
    "agricultural_reduction_cms",
    "shilin_river_release_cms",
    "actual_diversion_cms",
    "diversion_volume_10k_ton",
    "dam_release_cms",
    "public_water_10k_ton",
    "total_outflow_10k_ton",
    "spill_volume_10k_ton",
    "previous_capacity_10k_ton",
    "end_capacity_10k_ton",
    "net_capacity_change_10k_ton",
)

ANNUAL_DATA_FILES = (
    "hydrology_q.csv",
    "outflow_demand.csv",
    "reservoir_parameters.json",
    "source/original.xlsx",
)
ANNUAL_REQUIRED_FILES = ("version.json", *ANNUAL_DATA_FILES, "COMMITTED.json")
OFFICIAL_DATA_FILES = ("inputs.json", "scenario_summaries.csv", "daily_results.csv")
OFFICIAL_REQUIRED_FILES = ("manifest.json", *OFFICIAL_DATA_FILES, "COMMITTED.json")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_CSV_INTEGER_RE = re.compile(r"^(0|[1-9][0-9]*)$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PERIOD_KEY_RE = re.compile(r"^(0[1-9]|1[0-2])-(上旬|中旬|下旬)$")
_SAFE_ORIGINAL_FILENAME_RE = re.compile(r"^[^/\\:\x00]+$")
ANNUAL_PARAMETER_CODES = (
    "max_capacity_10k_ton",
    "shilin_ecological_flow_cms",
    "liyutan_ecological_release_cms",
    "shilin_diversion_limit_cms",
)
ANNUAL_PARAMETER_METADATA_FIELDS = (
    "effective_start_date",
    "source_reference",
    "note",
)
ANNUAL_WARNING_FIELDS = ("severity", "code", "message", "sheet", "cell")
_WINDOWS_RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


class StorageValidationError(ValueError):
    """Raised when a shared-storage artifact violates its schema."""


def _fail(message: str) -> None:
    raise StorageValidationError(message)


def _validate_json_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail(f"{path} 不可為 NaN 或 Infinity")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail(f"{path} 的 JSON key 必須是字串")
            _validate_json_value(item, f"{path}.{key}")
        return
    _fail(f"{path} 含有不支援的 JSON 型別：{type(value).__name__}")


def stable_json_dumps(value: Any) -> str:
    """Serialize JSON deterministically as UTF-8-compatible text."""
    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def serialize_json(value: Any) -> bytes:
    return (stable_json_dumps(value) + "\n").encode("utf-8")


def deserialize_json(data: bytes | str) -> Any:
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise StorageValidationError("JSON 必須使用 UTF-8") from exc
    elif isinstance(data, str):
        text = data
    else:
        _fail("JSON 輸入必須是 bytes 或 str")
    try:
        value = json.loads(
            text,
            parse_constant=lambda token: _fail(f"JSON 不允許 {token}"),
            object_pairs_hook=_json_object_without_duplicates,
        )
    except json.JSONDecodeError as exc:
        raise StorageValidationError(f"JSON 格式錯誤：{exc.msg}") from exc
    _validate_json_value(value)
    return value


def _json_object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f"JSON key 不可重複：{key}")
        result[key] = value
    return result


def sha256_bytes(data: bytes) -> str:
    if not isinstance(data, bytes):
        _fail("SHA-256 輸入必須是 bytes")
    return hashlib.sha256(data).hexdigest()


def deterministic_fingerprint(value: Any) -> str:
    return sha256_bytes(stable_json_dumps(value).encode("utf-8"))


def serialize_csv(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> bytes:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        _fail("CSV rows 必須是序列")
    fieldnames = tuple(columns)
    if not fieldnames or len(set(fieldnames)) != len(fieldnames):
        _fail("CSV 欄位定義無效")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, Mapping) or set(row) != set(fieldnames):
            _fail(f"CSV 第 {index} 列欄位不完整或含未知欄位")
        writer.writerow({column: row[column] for column in fieldnames})
    return stream.getvalue().encode("utf-8")


def deserialize_csv(data: bytes, columns: Sequence[str]) -> list[dict[str, str]]:
    if not isinstance(data, bytes):
        _fail("CSV 輸入必須是 bytes")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StorageValidationError("CSV 必須使用 UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    expected = tuple(columns)
    if tuple(reader.fieldnames or ()) != expected:
        _fail(f"CSV 欄位必須固定為：{', '.join(expected)}")
    rows = []
    for index, row in enumerate(reader, 1):
        if None in row or any(value is None for value in row.values()):
            _fail(f"CSV 第 {index} 列欄位數量錯誤")
        rows.append(dict(row))
    return rows


def _mapping(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        _fail(f"{label} 必須是 JSON object")
    return value


def _required(data: Mapping[str, Any], fields: Sequence[str], label: str) -> None:
    missing = [field for field in fields if field not in data]
    if missing:
        _fail(f"{label} 缺少必要欄位：{', '.join(missing)}")


def _schema(data: Any, expected: str, label: str) -> dict:
    item = _mapping(data, label)
    _required(item, ("schema", "schema_version"), label)
    if item["schema"] != expected:
        _fail(f"{label} schema 不支援：{item['schema']}")
    if (
        isinstance(item["schema_version"], bool)
        or not isinstance(item["schema_version"], int)
        or item["schema_version"] != SCHEMA_VERSION
    ):
        _fail(f"{label} schema version 不支援：{item['schema_version']}")
    return item


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} 必須是非空白字串")
    return value


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def validate_safe_id(value: Any, label: str = "version_id") -> str:
    """Validate an identifier that may become a Windows directory name."""
    text = _string(value, label)
    reserved_stem = text.split(".", 1)[0].upper()
    if (
        text in {".", ".."}
        or not _SAFE_ID_RE.fullmatch(text)
        or text.endswith(".")
        or reserved_stem in _WINDOWS_RESERVED_DEVICE_NAMES
    ):
        _fail(
            f"{label} 必須以英文字母或數字開頭，且只能包含英文字母、數字、.、_、-，"
            "長度上限為 128 字元，不得以句點結尾或使用 Windows 保留裝置名稱"
        )
    return text


def _optional_safe_id(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return validate_safe_id(value, label)


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(f"{label} 必須是大於等於 {minimum} 的整數")
    return value


def _number(value: Any, label: str, nonnegative: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{label} 必須是數字")
    number = float(value)
    if not math.isfinite(number):
        _fail(f"{label} 必須是有限數值")
    if nonnegative and number < 0:
        _fail(f"{label} 不可為負值")
    return number


def _csv_number(value: Any, label: str, nonnegative: bool = True) -> float:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} 必須是數字")
    try:
        number = float(value)
    except ValueError as exc:
        raise StorageValidationError(f"{label} 必須是數字") from exc
    if not math.isfinite(number):
        _fail(f"{label} 必須是有限數值")
    if nonnegative and number < 0:
        _fail(f"{label} 不可為負值")
    return number


def _csv_integer(value: Any, label: str, minimum: int = 0) -> int:
    if not isinstance(value, str) or not _CSV_INTEGER_RE.fullmatch(value):
        _fail(f"{label} 必須是整數")
    number = int(value)
    if number < minimum:
        _fail(f"{label} 必須大於等於 {minimum}")
    return number


def _timestamp(value: Any, label: str) -> dt.datetime:
    text = _string(value, label)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StorageValidationError(f"{label} 必須是 ISO 8601 時間") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(f"{label} 必須包含時區")
    return parsed


def _date(value: Any, label: str) -> dt.date:
    text = _string(value, label)
    try:
        parsed = dt.date.fromisoformat(text)
    except ValueError as exc:
        raise StorageValidationError(f"{label} 必須是 YYYY-MM-DD") from exc
    if text != parsed.isoformat():
        _fail(f"{label} 必須是 YYYY-MM-DD")
    return parsed


def _sha256(value: Any, label: str) -> str:
    text = _string(value, label)
    if not _SHA256_RE.fullmatch(text):
        _fail(f"{label} 必須是小寫 64 字元 SHA-256")
    return text


def _unique_strings(value: Any, label: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        _fail(f"{label} 必須是陣列")
    items = [_string(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if not allow_empty and not items:
        _fail(f"{label} 不可為空")
    if len(items) != len(set(items)):
        _fail(f"{label} 不可重複")
    return items


def _file_manifest(value: Any, required_files: Sequence[str], label: str) -> dict:
    files = _mapping(value, f"{label}.files")
    expected = set(required_files)
    actual = set(files)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append("缺少 " + ", ".join(missing))
        if unknown:
            details.append("包含未知檔名 " + ", ".join(unknown))
        _fail(f"{label}.files 必須恰好包含正式資料檔：{'；'.join(details)}")
    for filename, metadata in files.items():
        _string(filename, f"{label}.files filename")
        item = _mapping(metadata, f"{label}.files.{filename}")
        if set(item) != {"sha256"}:
            _fail(f"{label}.files.{filename} 只能包含 sha256")
        _sha256(item["sha256"], f"{label}.files.{filename}.sha256")
    return files


def _exact_fields(data: Mapping[str, Any], expected: Sequence[str], label: str) -> None:
    expected_set = set(expected)
    actual = set(data)
    if actual != expected_set:
        missing = sorted(expected_set - actual)
        unknown = sorted(actual - expected_set)
        details = []
        if missing:
            details.append("缺少 " + ", ".join(missing))
        if unknown:
            details.append("包含未知欄位 " + ", ".join(unknown))
        _fail(f"{label} 欄位必須固定：{'；'.join(details)}")


def validate_original_filename(value: Any, label: str = "original_filename") -> str:
    """Validate an uploaded display filename without ever using it as a path."""
    text = _string(value, label)
    if (
        text in {".", ".."}
        or len(text) > 255
        or not _SAFE_ORIGINAL_FILENAME_RE.fullmatch(text)
        or text[-1] in {" ", "."}
    ):
        _fail(f"{label} 必須是安全的單一檔名，不得包含路徑、磁碟機或路徑分隔符號")
    return text


def _validate_parameter_metadata(value: Any, label: str) -> dict:
    metadata = _mapping(value, label)
    _exact_fields(metadata, ANNUAL_PARAMETER_CODES, label)
    for code in ANNUAL_PARAMETER_CODES:
        item_label = f"{label}.{code}"
        item = _mapping(metadata[code], item_label)
        _exact_fields(item, ANNUAL_PARAMETER_METADATA_FIELDS, item_label)
        _date(item["effective_start_date"], f"{item_label}.effective_start_date")
        _optional_string(item["source_reference"], f"{item_label}.source_reference")
        _optional_string(item["note"], f"{item_label}.note")
    return copy.deepcopy(metadata)


def _validate_confirmed_warnings(value: Any, label: str) -> list[dict]:
    if not isinstance(value, list):
        _fail(f"{label} 必須是陣列")
    warnings: list[dict] = []
    for index, warning in enumerate(value):
        item_label = f"{label}[{index}]"
        item = _mapping(warning, item_label)
        _exact_fields(item, ANNUAL_WARNING_FIELDS, item_label)
        if item["severity"] != "warning":
            _fail(f"{item_label}.severity 必須是 warning")
        _string(item["code"], f"{item_label}.code")
        _string(item["message"], f"{item_label}.message")
        _optional_string(item["sheet"], f"{item_label}.sheet")
        _optional_string(item["cell"], f"{item_label}.cell")
        warnings.append(copy.deepcopy(item))
    return warnings


def validate_system(data: Any) -> dict:
    item = _schema(data, SHARED_ROOT_SCHEMA, "system.json")
    _required(item, ("reservoir_id", "display_name"), "system.json")
    if item["reservoir_id"] != "liyutan":
        _fail("system.json reservoir_id 必須是 liyutan")
    _string(item["display_name"], "system.json.display_name")
    return copy.deepcopy(item)


def validate_reservoir_parameters(data: Any) -> dict:
    item = _schema(data, RESERVOIR_PARAMETERS_SCHEMA, "reservoir_parameters.json")
    fields = ANNUAL_PARAMETER_CODES
    _exact_fields(item, ("schema", "schema_version", *fields), "reservoir_parameters.json")
    for field in fields:
        _number(item[field], f"reservoir_parameters.json.{field}")
    return copy.deepcopy(item)


def _validate_period_rows(
    rows: Sequence[Mapping[str, Any]], columns: Sequence[str], numeric_columns: Sequence[str], label: str
) -> list[dict]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        _fail(f"{label} rows 必須是序列")
    expected_periods = {(month, period) for month in range(1, 13) for period in PERIODS}
    seen: set[tuple[int, str]] = set()
    validated = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, Mapping) or set(row) != set(columns):
            _fail(f"{label} 第 {index} 列欄位不完整或含未知欄位")
        month = _csv_integer(row["month"], f"{label} 第 {index} 列 month", 1)
        if month > 12:
            _fail(f"{label} 第 {index} 列 month 必須介於 1 到 12")
        period = row["period"]
        if period not in PERIODS:
            _fail(f"{label} 第 {index} 列 period 必須是上旬、中旬或下旬")
        expected_key = f"{month:02d}-{period}"
        if row["period_key"] != expected_key:
            _fail(f"{label} 第 {index} 列 period_key 應為 {expected_key}")
        key = (month, period)
        if key in seen:
            _fail(f"{label} 旬別重複：{expected_key}")
        seen.add(key)
        for column in numeric_columns:
            _csv_number(row[column], f"{label} 第 {index} 列 {column}")
        validated.append(dict(row))
    missing = expected_periods - seen
    extra = seen - expected_periods
    if len(validated) != 36 or missing or extra:
        missing_text = "、".join(f"{month:02d}-{period}" for month, period in sorted(missing))
        _fail(f"{label} 必須完整包含 36 旬；缺少：{missing_text or '無'}")
    return validated


def validate_hydrology_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    validated = _validate_period_rows(rows, HYDROLOGY_COLUMNS, Q_COLUMNS, "hydrology_q.csv")
    for index, row in enumerate(validated, 1):
        values = [_csv_number(row[column], f"hydrology_q.csv 第 {index} 列 {column}") for column in Q_COLUMNS]
        for left_column, right_column, left, right in zip(
            Q_COLUMNS, Q_COLUMNS[1:], values, values[1:]
        ):
            if left < right:
                _fail(
                    f"hydrology_q.csv 第 {index} 列 {row['period_key']} 的 Q 值順序錯誤："
                    f"{left_column} 必須大於等於 {right_column}"
                )
    return validated


def validate_outflow_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    return _validate_period_rows(
        rows, OUTFLOW_COLUMNS, OUTFLOW_COLUMNS[3:], "outflow_demand.csv"
    )


def validate_annual_version(data: Any) -> dict:
    item = _schema(data, ANNUAL_VERSION_SCHEMA, "version.json")
    _required(
        item,
        (
            "version_id",
            "applicable_year",
            "created_at",
            "operator_display_name",
            "note",
            "template_version",
            "reservoir_id",
            "reservoir_name",
            "actual_data_cutoff_period",
            "hydrology_source_period",
            "annual_outflow_source",
            "overall_note",
            "candidate_fingerprint",
            "source_excel",
            "parameter_metadata",
            "confirmed_warnings",
            "source_references",
            "files",
        ),
        "version.json",
    )
    validate_safe_id(item["version_id"], "version.json.version_id")
    _integer(item["applicable_year"], "version.json.applicable_year", 1)
    _timestamp(item["created_at"], "version.json.created_at")
    _string(item["operator_display_name"], "version.json.operator_display_name")
    _string(item["note"], "version.json.note")
    _string(item["template_version"], "version.json.template_version")
    if item["reservoir_id"] != "liyutan":
        _fail("version.json.reservoir_id 必須是 liyutan")
    _string(item["reservoir_name"], "version.json.reservoir_name")
    cutoff = _string(item["actual_data_cutoff_period"], "version.json.actual_data_cutoff_period")
    if not _PERIOD_KEY_RE.fullmatch(cutoff):
        _fail("version.json.actual_data_cutoff_period 必須是 01-上旬 至 12-下旬")
    _string(item["hydrology_source_period"], "version.json.hydrology_source_period")
    _string(item["annual_outflow_source"], "version.json.annual_outflow_source")
    _optional_string(item["overall_note"], "version.json.overall_note")
    _sha256(item["candidate_fingerprint"], "version.json.candidate_fingerprint")
    source_excel = _mapping(item["source_excel"], "version.json.source_excel")
    _exact_fields(source_excel, ("original_filename", "sha256"), "version.json.source_excel")
    validate_original_filename(
        source_excel["original_filename"], "version.json.source_excel.original_filename"
    )
    _sha256(source_excel["sha256"], "version.json.source_excel.sha256")
    _validate_parameter_metadata(item["parameter_metadata"], "version.json.parameter_metadata")
    _validate_confirmed_warnings(item["confirmed_warnings"], "version.json.confirmed_warnings")
    _unique_strings(item["source_references"], "version.json.source_references")
    files = _file_manifest(item["files"], ANNUAL_DATA_FILES, "version.json")
    if files["source/original.xlsx"]["sha256"] != source_excel["sha256"]:
        _fail("version.json 原始 Excel checksum 與 files 清單不一致")
    return copy.deepcopy(item)


def _validate_current(data: Any, expected_schema: str, label: str) -> dict:
    item = _schema(data, expected_schema, label)
    _required(
        item,
        (
            "revision",
            "current_version_id",
            "previous_version_id",
            "updated_at",
            "operator_display_name",
        ),
        label,
    )
    _integer(item["revision"], f"{label}.revision", 1)
    validate_safe_id(item["current_version_id"], f"{label}.current_version_id")
    _optional_safe_id(item["previous_version_id"], f"{label}.previous_version_id")
    _timestamp(item["updated_at"], f"{label}.updated_at")
    _string(item["operator_display_name"], f"{label}.operator_display_name")
    return copy.deepcopy(item)


def validate_annual_current(data: Any) -> dict:
    return _validate_current(data, ANNUAL_CURRENT_SCHEMA, "annual-data/current.json")


def validate_official_current(data: Any) -> dict:
    return _validate_current(data, OFFICIAL_CURRENT_SCHEMA, "official-estimates/current.json")


def validate_software_metadata(data: Any, label: str = "software") -> dict:
    """Validate caller-supplied software provenance without inspecting Git."""
    software = _mapping(data, label)
    _exact_fields(
        software,
        ("repository", "git_commit", "app_version", "source_tree_dirty"),
        label,
    )
    _string(software["repository"], f"{label}.repository")
    git_commit = _string(software["git_commit"], f"{label}.git_commit")
    if not _GIT_SHA_RE.fullmatch(git_commit):
        _fail(f"{label}.git_commit 必須是 40 字元小寫 commit SHA")
    _string(software["app_version"], f"{label}.app_version")
    if not isinstance(software["source_tree_dirty"], bool):
        _fail(f"{label}.source_tree_dirty 必須是 boolean")
    return copy.deepcopy(software)


def validate_annual_activation_audit_event(data: Any) -> dict:
    """Validate one successful annual-data current activation audit event."""
    label = "annual activation audit event"
    item = _schema(data, AUDIT_EVENT_SCHEMA, label)
    fields = (
        "schema",
        "schema_version",
        "event_id",
        "event_type",
        "occurred_at",
        "annual_target_version_id",
        "before_revision",
        "before_current_version_id",
        "after_revision",
        "after_current_version_id",
        "operator_display_name",
        "note",
        "result",
        "software",
        "diagnostics",
    )
    _exact_fields(item, fields, label)
    validate_safe_id(item["event_id"], f"{label}.event_id")
    if item["event_type"] != ANNUAL_ACTIVATION_EVENT_TYPE:
        _fail(f"{label}.event_type 必須是 {ANNUAL_ACTIVATION_EVENT_TYPE}")
    _timestamp(item["occurred_at"], f"{label}.occurred_at")
    target = validate_safe_id(
        item["annual_target_version_id"], f"{label}.annual_target_version_id"
    )
    before_revision = _integer(item["before_revision"], f"{label}.before_revision", 0)
    before_id = _optional_safe_id(
        item["before_current_version_id"], f"{label}.before_current_version_id"
    )
    after_revision = _integer(item["after_revision"], f"{label}.after_revision", 1)
    after_id = validate_safe_id(
        item["after_current_version_id"], f"{label}.after_current_version_id"
    )
    if (before_revision == 0) != (before_id is None):
        _fail(f"{label} 的 before revision 與 current version 狀態不一致")
    if after_revision != before_revision + 1:
        _fail(f"{label}.after_revision 必須等於 before_revision + 1")
    if after_id != target:
        _fail(f"{label}.after_current_version_id 必須等於 annual_target_version_id")
    if before_id == target:
        _fail(f"{label} 不得把 already-current 記錄成成功啟用")
    _string(item["operator_display_name"], f"{label}.operator_display_name")
    _string(item["note"], f"{label}.note")
    if item["result"] != "success":
        _fail(f"{label}.result 必須是 success")
    validate_software_metadata(item["software"], f"{label}.software")
    diagnostics = _mapping(item["diagnostics"], f"{label}.diagnostics")
    _exact_fields(diagnostics, ("hostname", "process_id"), f"{label}.diagnostics")
    _string(diagnostics["hostname"], f"{label}.diagnostics.hostname")
    _integer(diagnostics["process_id"], f"{label}.diagnostics.process_id", 1)
    return copy.deepcopy(item)


def validate_committed(
    data: Any, expected_manifest_file: str, expected_version_id: str | None = None
) -> dict:
    item = _schema(data, COMMITTED_SCHEMA, "COMMITTED.json")
    _required(
        item,
        ("version_id", "committed_at", "manifest_file", "manifest_sha256"),
        "COMMITTED.json",
    )
    version_id = validate_safe_id(item["version_id"], "COMMITTED.json.version_id")
    if expected_version_id is not None and version_id != expected_version_id:
        _fail("COMMITTED.json version_id 與 manifest 不一致")
    _timestamp(item["committed_at"], "COMMITTED.json.committed_at")
    if item["manifest_file"] != expected_manifest_file:
        _fail(f"COMMITTED.json manifest_file 必須是 {expected_manifest_file}")
    _sha256(item["manifest_sha256"], "COMMITTED.json.manifest_sha256")
    return copy.deepcopy(item)


def _normalize_bundle(files: Mapping[str, bytes], required: Sequence[str], label: str) -> dict[str, bytes]:
    if not isinstance(files, Mapping):
        _fail(f"{label} 必須是檔名到 bytes 的 mapping")
    normalized: dict[str, bytes] = {}
    for name, data in files.items():
        _string(name, f"{label} filename")
        if not isinstance(data, bytes):
            _fail(f"{label} 的 {name} 必須是 bytes")
        normalized[name] = data
    missing = [name for name in required if name not in normalized]
    if missing:
        _fail(f"{label} 缺少必要檔案：{', '.join(missing)}")
    return normalized


def _verify_bundle_files(
    files: Mapping[str, bytes], manifest_files: Mapping[str, Any], control_files: Sequence[str], label: str
) -> None:
    expected = set(manifest_files) | set(control_files)
    actual = set(files)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append("缺少 " + ", ".join(missing))
        if unknown:
            details.append("未列 checksum " + ", ".join(unknown))
        _fail(f"{label} 檔案集合不一致：{'；'.join(details)}")
    for filename, metadata in manifest_files.items():
        expected_sha = metadata["sha256"]
        actual_sha = sha256_bytes(files[filename])
        if actual_sha != expected_sha:
            _fail(f"{label} checksum 不符：{filename}")


def validate_annual_bundle(files: Mapping[str, bytes]) -> dict:
    bundle = _normalize_bundle(files, ANNUAL_REQUIRED_FILES, "年度資料版本")
    version = validate_annual_version(deserialize_json(bundle["version.json"]))
    manifest_files = version["files"]
    _verify_bundle_files(bundle, manifest_files, ("version.json", "COMMITTED.json"), "年度資料版本")
    hydrology = deserialize_csv(bundle["hydrology_q.csv"], HYDROLOGY_COLUMNS)
    demands = deserialize_csv(bundle["outflow_demand.csv"], OUTFLOW_COLUMNS)
    parameters = deserialize_json(bundle["reservoir_parameters.json"])
    validate_hydrology_rows(hydrology)
    validate_outflow_rows(demands)
    validate_reservoir_parameters(parameters)
    committed = validate_committed(
        deserialize_json(bundle["COMMITTED.json"]), "version.json", version["version_id"]
    )
    if committed["manifest_sha256"] != sha256_bytes(bundle["version.json"]):
        _fail("年度資料版本 manifest checksum 不符")
    return {
        "version": version,
        "hydrology": hydrology,
        "outflow_demand": demands,
        "reservoir_parameters": copy.deepcopy(parameters),
        "parameter_metadata": copy.deepcopy(version["parameter_metadata"]),
        "source_excel": copy.deepcopy(version["source_excel"]),
        "confirmed_warnings": copy.deepcopy(version["confirmed_warnings"]),
        "committed": committed,
    }


def validate_official_inputs(data: Any) -> dict:
    item = _schema(data, OFFICIAL_INPUTS_SCHEMA, "inputs.json")
    _required(
        item,
        (
            "annual_data_version_id",
            "batch_id",
            "official_scenario_ids",
            "reservoir_parameters",
            "batch",
        ),
        "inputs.json",
    )
    _validate_json_value(item)
    validate_safe_id(item["annual_data_version_id"], "inputs.json.annual_data_version_id")
    batch_id = _string(item["batch_id"], "inputs.json.batch_id")
    official_ids = _unique_strings(item["official_scenario_ids"], "inputs.json.official_scenario_ids")
    outer_parameters = validate_reservoir_parameters(item["reservoir_parameters"])
    try:
        batch = validate_v2_batch(item["batch"])
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise StorageValidationError(f"inputs.json.batch 未通過 V2 完整驗證：{exc}") from exc
    if batch["batch_id"] != batch_id:
        _fail("inputs.json batch_id 與 batch.batch_id 不一致")
    display_start = _date(batch["display_start_date"], "inputs.json.batch.display_start_date")
    projection_start = _date(batch["projection_start_date"], "inputs.json.batch.projection_start_date")
    if display_start > projection_start:
        _fail("展示起日不可晚於推估起日")
    scenario_ids = [scenario["scenario_id"] for scenario in batch["scenarios"]]
    if scenario_ids != official_ids:
        _fail("inputs.json.batch.scenarios 必須依 official_scenario_ids 完整且不得夾帶其他情境")
    inner_parameters = _mapping(batch["reservoir_parameters"], "inputs.json.batch.reservoir_parameters")
    parameter_pairs = (
        ("max_capacity_10k_ton", "max_capacity"),
        ("shilin_ecological_flow_cms", "shilin_eco_flow"),
        ("liyutan_ecological_release_cms", "liyutan_eco_flow"),
    )
    for outer_field, inner_field in parameter_pairs:
        if inner_field not in inner_parameters:
            _fail(f"inputs.json.batch.reservoir_parameters 缺少必要欄位：{inner_field}")
        inner_value = _number(
            inner_parameters[inner_field],
            f"inputs.json.batch.reservoir_parameters.{inner_field}",
        )
        if float(outer_parameters[outer_field]) != inner_value:
            _fail(
                f"inputs.json.reservoir_parameters.{outer_field} 與 "
                f"inputs.json.batch.reservoir_parameters.{inner_field} 不一致"
            )
    validated = copy.deepcopy(item)
    validated["batch"] = batch
    return validated


def official_inputs_fingerprint(inputs: Any) -> str:
    validated = validate_official_inputs(inputs)
    return deterministic_fingerprint(validated)


def validate_official_manifest(data: Any) -> dict:
    item = _schema(data, OFFICIAL_ESTIMATE_SCHEMA, "manifest.json")
    _required(
        item,
        (
            "version_id",
            "batch_id",
            "batch_name",
            "previous_official_version_id",
            "annual_data_version_id",
            "settings_fingerprint",
            "official_scenario_ids",
            "created_at",
            "operator_display_name",
            "note",
            "software",
            "batch_schema_version",
            "files",
        ),
        "manifest.json",
    )
    validate_safe_id(item["version_id"], "manifest.json.version_id")
    _string(item["batch_id"], "manifest.json.batch_id")
    _string(item["batch_name"], "manifest.json.batch_name")
    _optional_safe_id(
        item["previous_official_version_id"], "manifest.json.previous_official_version_id"
    )
    validate_safe_id(item["annual_data_version_id"], "manifest.json.annual_data_version_id")
    _sha256(item["settings_fingerprint"], "manifest.json.settings_fingerprint")
    _unique_strings(item["official_scenario_ids"], "manifest.json.official_scenario_ids")
    _timestamp(item["created_at"], "manifest.json.created_at")
    _string(item["operator_display_name"], "manifest.json.operator_display_name")
    _string(item["note"], "manifest.json.note")
    software = _mapping(item["software"], "manifest.json.software")
    _required(
        software,
        ("repository", "git_commit", "app_version", "source_tree_dirty"),
        "manifest.json.software",
    )
    _string(software["repository"], "manifest.json.software.repository")
    git_commit = _string(software["git_commit"], "manifest.json.software.git_commit")
    if not _GIT_SHA_RE.fullmatch(git_commit):
        _fail("manifest.json.software.git_commit 必須是 40 字元小寫 commit SHA")
    _string(software["app_version"], "manifest.json.software.app_version")
    if not isinstance(software["source_tree_dirty"], bool):
        _fail("manifest.json.software.source_tree_dirty 必須是 boolean")
    if _integer(item["batch_schema_version"], "manifest.json.batch_schema_version", 1) != BATCH_SCHEMA_VERSION:
        _fail("manifest.json.batch_schema_version 不支援")
    _file_manifest(item["files"], OFFICIAL_DATA_FILES, "manifest.json")
    return copy.deepcopy(item)


def validate_scenario_summaries(rows: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]) -> list[dict]:
    official_ids = manifest["official_scenario_ids"]
    seen: set[str] = set()
    orders: set[int] = set()
    validated = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, Mapping) or set(row) != set(SUMMARY_COLUMNS):
            _fail(f"scenario_summaries.csv 第 {index} 列欄位不完整或含未知欄位")
        if row["version_id"] != manifest["version_id"] or row["batch_id"] != manifest["batch_id"]:
            _fail("scenario_summaries.csv 版本 ID 或批次 ID 與 manifest 不一致")
        scenario_id = _string(row["scenario_id"], "scenario_summaries.csv scenario_id")
        if scenario_id in seen:
            _fail("scenario_summaries.csv scenario_id 不可重複")
        seen.add(scenario_id)
        _string(row["scenario_name"], "scenario_summaries.csv scenario_name")
        order = _csv_integer(row["scenario_order"], "scenario_summaries.csv scenario_order")
        if order in orders:
            _fail("scenario_summaries.csv scenario_order 不可重複")
        orders.add(order)
        if row["calculation_status"] != "success":
            _fail("所有正式情境的 calculation_status 必須是 success")
        if row["settings_fingerprint"] != manifest["settings_fingerprint"]:
            _fail("scenario_summaries.csv settings fingerprint 與 manifest 不一致")
        for column in SUMMARY_COLUMNS[7:-1]:
            _csv_number(row[column], f"scenario_summaries.csv {column}")
        _csv_integer(row["dry_days"], "scenario_summaries.csv dry_days")
        validated.append(dict(row))
    if seen != set(official_ids) or len(validated) != len(official_ids):
        _fail("scenario_summaries.csv 必須完整且只能包含 official_scenario_ids")
    if orders != set(range(len(official_ids))):
        _fail("scenario_summaries.csv scenario_order 必須連續")
    return validated


def validate_daily_results(
    rows: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any], inputs: Mapping[str, Any]
) -> list[dict]:
    start = _date(inputs["batch"]["projection_start_date"], "projection_start_date")
    end = _date(inputs["batch"]["projection_end_date"], "projection_end_date")
    expected_dates = {start + dt.timedelta(days=offset) for offset in range((end - start).days)}
    per_scenario: dict[str, set[dt.date]] = {scenario_id: set() for scenario_id in manifest["official_scenario_ids"]}
    nonnegative_columns = DAILY_RESULT_COLUMNS[5:-1]
    validated = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, Mapping) or set(row) != set(DAILY_RESULT_COLUMNS):
            _fail(f"daily_results.csv 第 {index} 列欄位不完整或含未知欄位")
        if row["version_id"] != manifest["version_id"] or row["batch_id"] != manifest["batch_id"]:
            _fail("daily_results.csv 版本 ID 或批次 ID 與 manifest 不一致")
        scenario_id = row["scenario_id"]
        if scenario_id not in per_scenario:
            _fail("daily_results.csv 混入 official_scenario_ids 以外的情境")
        if row["settings_fingerprint"] != manifest["settings_fingerprint"]:
            _fail("daily_results.csv settings fingerprint 與 manifest 不一致")
        date = _date(row["date"], "daily_results.csv date")
        if date not in expected_dates:
            _fail("daily_results.csv 日期超出推估期間")
        if date in per_scenario[scenario_id]:
            _fail("daily_results.csv 同一情境日期不可重複")
        per_scenario[scenario_id].add(date)
        for column in nonnegative_columns:
            _csv_number(row[column], f"daily_results.csv {column}")
        _csv_number(row["net_capacity_change_10k_ton"], "daily_results.csv net_capacity_change_10k_ton", False)
        validated.append(dict(row))
    for scenario_id, dates in per_scenario.items():
        if dates != expected_dates:
            _fail(f"daily_results.csv 缺少正式情境 {scenario_id} 的完整逐日結果")
    return validated


def validate_official_bundle(files: Mapping[str, bytes]) -> dict:
    bundle = _normalize_bundle(files, OFFICIAL_REQUIRED_FILES, "正式推估版本")
    manifest = validate_official_manifest(deserialize_json(bundle["manifest.json"]))
    manifest_files = manifest["files"]
    _verify_bundle_files(bundle, manifest_files, ("manifest.json", "COMMITTED.json"), "正式推估版本")
    inputs = validate_official_inputs(deserialize_json(bundle["inputs.json"]))
    if inputs["annual_data_version_id"] != manifest["annual_data_version_id"]:
        _fail("inputs.json 年度資料版本 ID 與 manifest 不一致")
    if inputs["batch_id"] != manifest["batch_id"]:
        _fail("inputs.json 批次 ID 與 manifest 不一致")
    if inputs["official_scenario_ids"] != manifest["official_scenario_ids"]:
        _fail("inputs.json official_scenario_ids 與 manifest 不一致")
    if official_inputs_fingerprint(inputs) != manifest["settings_fingerprint"]:
        _fail("inputs.json 設定 fingerprint 與 manifest 不一致")
    summaries = deserialize_csv(bundle["scenario_summaries.csv"], SUMMARY_COLUMNS)
    daily_results = deserialize_csv(bundle["daily_results.csv"], DAILY_RESULT_COLUMNS)
    validate_scenario_summaries(summaries, manifest)
    validate_daily_results(daily_results, manifest, inputs)
    scenario_lookup = {scenario["scenario_id"]: scenario for scenario in inputs["batch"]["scenarios"]}
    for row in summaries:
        scenario = scenario_lookup[row["scenario_id"]]
        if row["scenario_name"] != scenario["name"] or int(row["scenario_order"]) != scenario["order"]:
            _fail("scenario_summaries.csv 情境名稱或排序與 inputs.json 不一致")
    committed = validate_committed(
        deserialize_json(bundle["COMMITTED.json"]), "manifest.json", manifest["version_id"]
    )
    if committed["manifest_sha256"] != sha256_bytes(bundle["manifest.json"]):
        _fail("正式推估版本 manifest checksum 不符")
    return {
        "manifest": manifest,
        "inputs": inputs,
        "scenario_summaries": summaries,
        "daily_results": daily_results,
        "committed": committed,
    }
