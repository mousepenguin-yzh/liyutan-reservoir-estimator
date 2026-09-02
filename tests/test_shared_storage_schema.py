import copy
import pytest

from shared_storage_schema import (
    ANNUAL_CURRENT_SCHEMA,
    ANNUAL_VERSION_SCHEMA,
    COMMITTED_SCHEMA,
    DAILY_RESULT_COLUMNS,
    HYDROLOGY_COLUMNS,
    OFFICIAL_CURRENT_SCHEMA,
    OFFICIAL_ESTIMATE_SCHEMA,
    OFFICIAL_INPUTS_SCHEMA,
    OUTFLOW_COLUMNS,
    PERIODS,
    Q_COLUMNS,
    RESERVOIR_PARAMETERS_SCHEMA,
    SCHEMA_VERSION,
    SHARED_ROOT_SCHEMA,
    SUMMARY_COLUMNS,
    StorageValidationError,
    deserialize_csv,
    deserialize_json,
    deterministic_fingerprint,
    official_inputs_fingerprint,
    serialize_csv,
    serialize_json,
    sha256_bytes,
    validate_annual_bundle,
    validate_annual_current,
    validate_official_bundle,
    validate_official_current,
    validate_system,
)
from v2_workflow import validate_batch


def synthetic_parameters():
    return {
        "schema": RESERVOIR_PARAMETERS_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "max_capacity_10k_ton": 11584.0,
        "shilin_ecological_flow_cms": 2.7,
        "liyutan_ecological_release_cms": 0.3,
        "shilin_diversion_limit_cms": 33.0,
    }


def synthetic_hydrology_rows():
    rows = []
    for month in range(1, 13):
        for period in PERIODS:
            row = {"period_key": f"{month:02d}-{period}", "month": month, "period": period}
            row.update({column: month + index / 10 for index, column in enumerate(Q_COLUMNS, 1)})
            rows.append(row)
    return rows


def synthetic_outflow_rows():
    return [
        {
            "period_key": f"{month:02d}-{period}",
            "month": month,
            "period": period,
            "upstream_irrigation_cms": 2.7,
            "downstream_irrigation_cms": 0.3,
            "public_water_10k_ton_per_day": 60.0,
        }
        for month in range(1, 13)
        for period in PERIODS
    ]


def _annual_bundle(
    hydrology_rows=None,
    hydrology_columns=HYDROLOGY_COLUMNS,
    outflow_rows=None,
    parameters=None,
    version_mutator=None,
):
    hydrology_rows = copy.deepcopy(hydrology_rows or synthetic_hydrology_rows())
    outflow_rows = copy.deepcopy(outflow_rows or synthetic_outflow_rows())
    parameters = copy.deepcopy(parameters or synthetic_parameters())
    hydrology_bytes = serialize_csv(hydrology_rows, hydrology_columns)
    outflow_bytes = serialize_csv(outflow_rows, OUTFLOW_COLUMNS)
    parameter_bytes = serialize_json(parameters)
    data_files = {
        "hydrology_q.csv": hydrology_bytes,
        "outflow_demand.csv": outflow_bytes,
        "reservoir_parameters.json": parameter_bytes,
    }
    version = {
        "schema": ANNUAL_VERSION_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "version_id": "annual-synthetic-2027",
        "applicable_year": 2027,
        "created_at": "2026-12-15T02:30:00Z",
        "operator_display_name": "測試操作人",
        "note": "純合成年度資料",
        "source_references": ["synthetic-fixture"],
        "files": {name: {"sha256": sha256_bytes(data)} for name, data in data_files.items()},
    }
    if version_mutator:
        version_mutator(version)
    version_bytes = serialize_json(version)
    committed = {
        "schema": COMMITTED_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "version_id": version["version_id"],
        "committed_at": "2026-12-15T02:31:00Z",
        "manifest_file": "version.json",
        "manifest_sha256": sha256_bytes(version_bytes),
    }
    return {"version.json": version_bytes, **data_files, "COMMITTED.json": serialize_json(committed)}


def _v2_batch(scenario_ids=("scenario-a", "scenario-b")):
    periods = ["2027-1-上旬"]
    scenarios = []
    for order, scenario_id in enumerate(scenario_ids):
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "name": f"合成情境 {order + 1}",
                "order": order,
                "inflows": {
                    periods[0]: {
                        "cms": 10.0 + order,
                        "source_type": "合成",
                        "source_unit": "cms",
                        "source_value": 10.0 + order,
                        "note": "",
                    }
                },
            }
        )
    return {
        "schema": "liyutan-reservoir-estimator/batch",
        "schema_version": 1,
        "batch_id": "batch-synthetic-1",
        "batch_name": "合成正式推估",
        "display_start_date": "2027-01-01",
        "projection_start_date": "2027-01-01",
        "projection_end_date": "2027-01-03",
        "initial_capacity": 8000.0,
        "historical_capacities": {},
        "reservoir_parameters": {
            "max_capacity": 11584.0,
            "shilin_eco_flow": 2.7,
            "liyutan_eco_flow": 0.3,
        },
        "periods": periods,
        "shared_period_count": 0,
        "shared_inflows": {},
        "scenarios": scenarios,
        "outflows": {
            periods[0]: {
                "upstream_irrigation_cms": 2.7,
                "downstream_irrigation_cms": 0.3,
                "public_water_10k_ton_per_day": 60.0,
            }
        },
        "daily_outflows": [
            {
                "date": f"2027-01-0{day}",
                "upstream_irrigation_cms": 2.7,
                "downstream_irrigation_cms": 0.3,
                "public_water_10k_ton_per_day": 60.0,
                "source_type": "合成",
                "note": "",
            }
            for day in (1, 2)
        ],
        "date_overrides": [],
        "overrides_enabled": False,
        "created_at": "2026-12-15T02:40:00Z",
        "note": "純合成批次",
    }


def _official_bundle(
    input_mutator=None,
    summary_mutator=None,
    daily_mutator=None,
    manifest_mutator=None,
):
    scenario_ids = ["scenario-a", "scenario-b"]
    inputs = {
        "schema": OFFICIAL_INPUTS_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "annual_data_version_id": "annual-synthetic-2027",
        "batch_id": "batch-synthetic-1",
        "official_scenario_ids": scenario_ids,
        "reservoir_parameters": synthetic_parameters(),
        "batch": _v2_batch(scenario_ids),
    }
    if input_mutator:
        input_mutator(inputs)
    fingerprint = official_inputs_fingerprint(inputs)
    summaries = [
        {
            "version_id": "estimate-synthetic-1",
            "batch_id": "batch-synthetic-1",
            "scenario_id": scenario_id,
            "scenario_name": f"合成情境 {order + 1}",
            "scenario_order": order,
            "calculation_status": "success",
            "settings_fingerprint": fingerprint,
            "final_capacity_10k_ton": 8001.0 + order,
            "minimum_capacity_10k_ton": 8000.0,
            "spill_volume_10k_ton": 0.0,
            "agricultural_reduction_volume_10k_ton": 0.0,
            "dry_days": 0,
        }
        for order, scenario_id in enumerate(scenario_ids)
    ]
    daily = []
    for order, scenario_id in enumerate(scenario_ids):
        for day in (1, 2):
            daily.append(
                {
                    "version_id": "estimate-synthetic-1",
                    "batch_id": "batch-synthetic-1",
                    "scenario_id": scenario_id,
                    "settings_fingerprint": fingerprint,
                    "date": f"2027-01-0{day}",
                    "natural_inflow_cms": 10.0 + order,
                    "upstream_demand_cms": 2.7,
                    "downstream_demand_cms": 0.3,
                    "actual_upstream_release_cms": 2.7,
                    "actual_downstream_release_cms": 0.3,
                    "agricultural_reduction_cms": 0.0,
                    "shilin_river_release_cms": 2.7,
                    "actual_diversion_cms": 7.3 + order,
                    "diversion_volume_10k_ton": 63.07 + order,
                    "dam_release_cms": 0.3,
                    "public_water_10k_ton": 60.0,
                    "total_outflow_10k_ton": 62.59,
                    "spill_volume_10k_ton": 0.0,
                    "previous_capacity_10k_ton": 8000.0,
                    "end_capacity_10k_ton": 8000.48,
                    "net_capacity_change_10k_ton": 0.48,
                }
            )
    if summary_mutator:
        summary_mutator(summaries)
    if daily_mutator:
        daily_mutator(daily)
    raw_files = {
        "inputs.json": serialize_json(inputs),
        "scenario_summaries.csv": serialize_csv(summaries, SUMMARY_COLUMNS),
        "daily_results.csv": serialize_csv(daily, DAILY_RESULT_COLUMNS),
    }
    manifest = {
        "schema": OFFICIAL_ESTIMATE_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "version_id": "estimate-synthetic-1",
        "batch_id": "batch-synthetic-1",
        "batch_name": "合成正式推估",
        "previous_official_version_id": None,
        "annual_data_version_id": "annual-synthetic-2027",
        "settings_fingerprint": fingerprint,
        "official_scenario_ids": scenario_ids,
        "created_at": "2026-12-15T02:45:00Z",
        "operator_display_name": "測試操作人",
        "note": "純合成正式推估",
        "software": {
            "repository": "liyutan-reservoir-estimator",
            "git_commit": "a" * 40,
            "app_version": "synthetic-test",
            "source_tree_dirty": False,
        },
        "batch_schema_version": 1,
        "files": {name: {"sha256": sha256_bytes(data)} for name, data in raw_files.items()},
    }
    if manifest_mutator:
        manifest_mutator(manifest)
    manifest_bytes = serialize_json(manifest)
    committed = {
        "schema": COMMITTED_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "version_id": manifest["version_id"],
        "committed_at": "2026-12-15T02:46:00Z",
        "manifest_file": "manifest.json",
        "manifest_sha256": sha256_bytes(manifest_bytes),
    }
    return {"manifest.json": manifest_bytes, **raw_files, "COMMITTED.json": serialize_json(committed)}


def _csv_without_column(rows, columns, removed):
    kept = tuple(column for column in columns if column != removed)
    stripped = [{column: row[column] for column in kept} for row in rows]
    return serialize_csv(stripped, kept)


def _repack_annual_data_file(bundle, filename, content):
    changed = dict(bundle)
    changed[filename] = content
    version = deserialize_json(changed["version.json"])
    version["files"][filename]["sha256"] = sha256_bytes(content)
    changed["version.json"] = serialize_json(version)
    committed = deserialize_json(changed["COMMITTED.json"])
    committed["manifest_sha256"] = sha256_bytes(changed["version.json"])
    changed["COMMITTED.json"] = serialize_json(committed)
    return changed


def test_json_checksum_and_fingerprint_are_deterministic_and_sensitive():
    left = {"文字": "測試", "nested": {"b": 2, "a": [1, 3]}}
    right = {"nested": {"a": [1, 3], "b": 2}, "文字": "測試"}
    assert serialize_json(left) == serialize_json(right)
    assert deterministic_fingerprint(left) == deterministic_fingerprint(right)
    assert sha256_bytes(serialize_json(left)) == sha256_bytes(serialize_json(right))
    changed = copy.deepcopy(left)
    changed["nested"]["a"][1] = 4
    assert deterministic_fingerprint(changed) != deterministic_fingerprint(left)
    assert sha256_bytes(serialize_json(changed)) != sha256_bytes(serialize_json(left))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_json_rejects_nonfinite_values(value):
    with pytest.raises(StorageValidationError):
        serialize_json({"value": value})
    token = "NaN" if math_is_nan(value) else "Infinity" if value > 0 else "-Infinity"
    with pytest.raises(StorageValidationError):
        deserialize_json(f'{{"value":{token}}}')


def math_is_nan(value):
    return value != value


def test_json_rejects_wrong_types_and_invalid_utf8():
    with pytest.raises(StorageValidationError, match="型別"):
        serialize_json({"bad": (1, 2)})
    with pytest.raises(StorageValidationError, match="UTF-8"):
        deserialize_json(b"\xff")
    with pytest.raises(StorageValidationError, match="不可重複"):
        deserialize_json('{"same":1,"same":2}')


def test_system_and_current_schemas_validate():
    system = {
        "schema": SHARED_ROOT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "reservoir_id": "liyutan",
        "display_name": "鯉魚潭水庫",
    }
    annual_current = {
        "schema": ANNUAL_CURRENT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "current_version_id": "annual-synthetic-2027",
        "previous_version_id": None,
        "updated_at": "2026-12-15T02:35:00Z",
        "operator_display_name": "測試操作人",
    }
    official_current = {
        **annual_current,
        "schema": OFFICIAL_CURRENT_SCHEMA,
        "current_version_id": "estimate-synthetic-1",
    }
    assert validate_system(deserialize_json(serialize_json(system))) == system
    assert validate_annual_current(deserialize_json(serialize_json(annual_current))) == annual_current
    assert validate_official_current(deserialize_json(serialize_json(official_current))) == official_current


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update(schema="wrong/schema"),
        lambda value: value.update(schema_version=99),
        lambda value: value.update(schema_version=1.0),
    ],
)
def test_unknown_schema_or_version_is_rejected(mutator):
    system = {
        "schema": SHARED_ROOT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "reservoir_id": "liyutan",
        "display_name": "鯉魚潭水庫",
    }
    mutator(system)
    with pytest.raises(StorageValidationError, match="不支援"):
        validate_system(system)


def test_valid_annual_bundle_round_trip(tmp_path):
    bundle = _annual_bundle()
    parsed = validate_annual_bundle(bundle)
    assert len(parsed["hydrology"]) == 36
    assert len(parsed["outflow_demand"]) == 36
    assert parsed["reservoir_parameters"]["shilin_diversion_limit_cms"] == 33.0
    assert serialize_json(parsed["version"]) == bundle["version.json"]
    assert serialize_csv(parsed["hydrology"], HYDROLOGY_COLUMNS) == bundle["hydrology_q.csv"]
    target = tmp_path / "synthetic-version.json"
    target.write_bytes(bundle["version.json"])
    assert deserialize_json(target.read_bytes()) == parsed["version"]


@pytest.mark.parametrize("mode", ["duplicate", "missing", "bad_period", "bad_key"])
def test_annual_period_integrity_failures(mode):
    rows = synthetic_hydrology_rows()
    if mode == "duplicate":
        rows[-1] = copy.deepcopy(rows[0])
    elif mode == "missing":
        rows.pop()
    elif mode == "bad_period":
        rows[0]["period"] = "月底"
    else:
        rows[0]["period_key"] = "01-中旬"
    with pytest.raises(StorageValidationError):
        validate_annual_bundle(_annual_bundle(hydrology_rows=rows))


def test_annual_month_out_of_range_is_rejected():
    rows = synthetic_outflow_rows()
    rows[0].update(month=13, period_key="13-上旬")
    with pytest.raises(StorageValidationError, match="1 到 12"):
        validate_annual_bundle(_annual_bundle(outflow_rows=rows))


def test_missing_q_column_is_rejected():
    bundle = _annual_bundle()
    bad_csv = _csv_without_column(synthetic_hydrology_rows(), HYDROLOGY_COLUMNS, "q95_cms")
    bundle = _repack_annual_data_file(bundle, "hydrology_q.csv", bad_csv)
    with pytest.raises(StorageValidationError, match="CSV 欄位"):
        validate_annual_bundle(bundle)


@pytest.mark.parametrize("value", [-1, "nan", "inf"])
def test_annual_negative_nan_and_infinity_are_rejected(value):
    rows = synthetic_hydrology_rows()
    rows[0]["q05_cms"] = value
    with pytest.raises(StorageValidationError):
        validate_annual_bundle(_annual_bundle(hydrology_rows=rows))


@pytest.mark.parametrize("value", [-1, "nan", "inf"])
def test_outflow_demand_negative_nan_and_infinity_are_rejected(value):
    rows = synthetic_outflow_rows()
    rows[0]["public_water_10k_ton_per_day"] = value
    with pytest.raises(StorageValidationError):
        validate_annual_bundle(_annual_bundle(outflow_rows=rows))


def test_negative_reservoir_parameter_is_rejected():
    parameters = synthetic_parameters()
    parameters["max_capacity_10k_ton"] = -1
    with pytest.raises(StorageValidationError, match="負值"):
        validate_annual_bundle(_annual_bundle(parameters=parameters))


def test_missing_diversion_limit_is_rejected():
    parameters = synthetic_parameters()
    parameters.pop("shilin_diversion_limit_cms")
    with pytest.raises(StorageValidationError, match="shilin_diversion_limit_cms"):
        validate_annual_bundle(_annual_bundle(parameters=parameters))


def test_annual_missing_file_and_checksum_mismatch_are_rejected():
    missing = _annual_bundle()
    missing.pop("outflow_demand.csv")
    with pytest.raises(StorageValidationError, match="缺少必要檔案"):
        validate_annual_bundle(missing)
    corrupt = _annual_bundle()
    corrupt["hydrology_q.csv"] += b"\n"
    with pytest.raises(StorageValidationError, match="checksum"):
        validate_annual_bundle(corrupt)


def test_committed_manifest_checksum_mismatch_is_rejected():
    bundle = _annual_bundle()
    committed = deserialize_json(bundle["COMMITTED.json"])
    committed["manifest_sha256"] = "0" * 64
    bundle["COMMITTED.json"] = serialize_json(committed)
    with pytest.raises(StorageValidationError, match="manifest checksum"):
        validate_annual_bundle(bundle)


def test_valid_official_bundle_round_trip(tmp_path):
    bundle = _official_bundle()
    parsed = validate_official_bundle(bundle)
    assert len(parsed["scenario_summaries"]) == 2
    assert len(parsed["daily_results"]) == 4
    assert serialize_json(parsed["manifest"]) == bundle["manifest.json"]
    assert serialize_json(parsed["inputs"]) == bundle["inputs.json"]
    assert serialize_csv(parsed["scenario_summaries"], SUMMARY_COLUMNS) == bundle["scenario_summaries.csv"]
    assert validate_batch(parsed["inputs"]["batch"]) == parsed["inputs"]["batch"]
    target = tmp_path / "synthetic-inputs.json"
    target.write_bytes(bundle["inputs.json"])
    assert official_inputs_fingerprint(deserialize_json(target.read_bytes())) == parsed["manifest"]["settings_fingerprint"]


@pytest.mark.parametrize(
    "mutator",
    [
        lambda manifest: manifest.update(official_scenario_ids=[]),
        lambda manifest: manifest.update(official_scenario_ids=["scenario-a", "scenario-a"]),
    ],
)
def test_official_scenario_ids_cannot_be_empty_or_duplicate(mutator):
    with pytest.raises(StorageValidationError, match="official_scenario_ids"):
        validate_official_bundle(_official_bundle(manifest_mutator=mutator))


def test_official_failed_scenario_blocks_entire_bundle():
    def fail(rows):
        rows[1]["calculation_status"] = "error"

    with pytest.raises(StorageValidationError, match="success"):
        validate_official_bundle(_official_bundle(summary_mutator=fail))


def test_official_missing_summary_blocks_entire_bundle():
    with pytest.raises(StorageValidationError, match="official_scenario_ids"):
        validate_official_bundle(_official_bundle(summary_mutator=lambda rows: rows.pop()))


def test_official_missing_daily_result_blocks_entire_bundle():
    with pytest.raises(StorageValidationError, match="完整逐日結果"):
        validate_official_bundle(_official_bundle(daily_mutator=lambda rows: rows.pop()))


def test_official_fingerprint_mismatch_blocks_entire_bundle():
    def mismatch(rows):
        rows[0]["settings_fingerprint"] = "0" * 64

    with pytest.raises(StorageValidationError, match="fingerprint"):
        validate_official_bundle(_official_bundle(summary_mutator=mismatch))


def test_official_inputs_change_invalidates_manifest_fingerprint():
    def change_inputs(inputs):
        inputs["batch"]["initial_capacity"] = 7000.0

    def preserve_old_fingerprint(manifest):
        manifest["settings_fingerprint"] = "0" * 64

    with pytest.raises(StorageValidationError, match="fingerprint"):
        validate_official_bundle(
            _official_bundle(input_mutator=change_inputs, manifest_mutator=preserve_old_fingerprint)
        )


def test_official_daily_results_cannot_include_nonofficial_scenario():
    def add_nonofficial(rows):
        extra = copy.deepcopy(rows[0])
        extra["scenario_id"] = "scenario-nonofficial"
        rows.append(extra)

    with pytest.raises(StorageValidationError, match="以外"):
        validate_official_bundle(_official_bundle(daily_mutator=add_nonofficial))


@pytest.mark.parametrize(
    "mutator, message",
    [
        (lambda rows: rows[0].update(version_id="wrong-version"), "版本 ID"),
        (lambda rows: rows[0].update(batch_id="wrong-batch"), "批次 ID"),
        (lambda rows: rows[0].update(date="2027-01-03"), "日期超出"),
    ],
)
def test_official_daily_version_batch_and_date_relationships(mutator, message):
    with pytest.raises(StorageValidationError, match=message):
        validate_official_bundle(_official_bundle(daily_mutator=mutator))


def test_official_missing_file_and_checksum_mismatch_are_rejected():
    missing = _official_bundle()
    missing.pop("daily_results.csv")
    with pytest.raises(StorageValidationError, match="缺少必要檔案"):
        validate_official_bundle(missing)
    corrupt = _official_bundle()
    corrupt["inputs.json"] += b"\n"
    with pytest.raises(StorageValidationError, match="checksum"):
        validate_official_bundle(corrupt)


def test_csv_round_trip_is_stable():
    original = serialize_csv(synthetic_outflow_rows(), OUTFLOW_COLUMNS)
    restored = deserialize_csv(original, OUTFLOW_COLUMNS)
    assert serialize_csv(restored, OUTFLOW_COLUMNS) == original


def test_checksum_changes_when_one_byte_changes():
    data = b"synthetic-data"
    assert sha256_bytes(data) == sha256_bytes(data)
    assert sha256_bytes(data) != sha256_bytes(data + b"!")
