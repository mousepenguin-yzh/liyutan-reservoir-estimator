from pathlib import Path

import pytest
from openpyxl import load_workbook

from scripts.create_annual_data_template import (
    CANONICAL_PERIODS,
    Q_CODES_DESCENDING,
    RESERVOIR_ID,
    RESERVOIR_NAME,
    SHEET_NAMES,
    TEMPLATE_VERSION,
    build_workbook,
    main,
    write_migrated_template,
    write_template,
)


def _create_and_load(tmp_path: Path):
    output = tmp_path / "annual-data-template.xlsx"
    write_template(output)
    return output, load_workbook(output, data_only=False)


def test_xlsx_round_trip_and_sheet_order(tmp_path):
    output, workbook = _create_and_load(tmp_path)
    assert output.is_file()
    assert workbook.sheetnames == list(SHEET_NAMES)
    workbook.close()


def test_fixed_technical_fields_and_blank_business_metadata(tmp_path):
    _, workbook = _create_and_load(tmp_path)
    ws = workbook["版本資訊"]
    metadata = {ws.cell(row, 1).value: ws.cell(row, 3).value for row in range(5, 13)}
    assert metadata == {
        "template_version": TEMPLATE_VERSION,
        "reservoir_id": RESERVOIR_ID,
        "reservoir_name": RESERVOIR_NAME,
        "applicable_year": None,
        "actual_data_cutoff_period": None,
        "hydrology_source_period": None,
        "annual_outflow_source": None,
        "overall_note": None,
    }
    workbook.close()


def test_hydrology_has_canonical_36_periods_and_all_q_columns(tmp_path):
    _, workbook = _create_and_load(tmp_path)
    ws = workbook["水文Q值"]
    headers = [ws.cell(5, column).value for column in range(1, 23)]
    rows = [tuple(ws.cell(row, column).value for column in range(1, 4)) for row in range(6, 42)]
    assert headers == ["period_key", "month", "period", *Q_CODES_DESCENDING]
    assert len(Q_CODES_DESCENDING) == 19
    assert rows == list(CANONICAL_PERIODS)
    assert len(rows) == len(set(rows)) == 36
    assert all(
        ws.cell(row, column).value is None
        for row in range(6, 42)
        for column in range(4, 23)
    )
    workbook.close()


def test_outflow_has_canonical_periods_fields_units_and_blank_values(tmp_path):
    _, workbook = _create_and_load(tmp_path)
    ws = workbook["年度基準出流"]
    assert [ws.cell(5, column).value for column in range(1, 7)] == [
        "period_key",
        "month",
        "period",
        "upstream_irrigation_cms",
        "downstream_irrigation_cms",
        "public_water_10k_ton_per_day",
    ]
    assert [ws.cell(4, column).value for column in range(4, 7)] == [
        "上灌區需求（cms）",
        "下灌區需求（cms）",
        "公共出水（萬噸／日）",
    ]
    rows = [tuple(ws.cell(row, column).value for column in range(1, 4)) for row in range(6, 42)]
    assert rows == list(CANONICAL_PERIODS)
    assert all(
        ws.cell(row, column).value is None
        for row in range(6, 42)
        for column in range(4, 7)
    )
    workbook.close()


def test_reservoir_parameters_are_complete_unique_and_blank(tmp_path):
    _, workbook = _create_and_load(tmp_path)
    ws = workbook["水庫參數"]
    expected_codes = [
        "max_capacity_10k_ton",
        "shilin_ecological_flow_cms",
        "liyutan_ecological_release_cms",
        "shilin_diversion_limit_cms",
    ]
    codes = [ws.cell(row, 1).value for row in range(6, 10)]
    assert codes == expected_codes
    assert len(codes) == len(set(codes))
    for row in range(6, 10):
        assert [ws.cell(row, column).value for column in (3, 5, 6, 7)] == [None] * 4
    workbook.close()


def test_primary_data_validations_and_usability_features_exist(tmp_path):
    _, workbook = _create_and_load(tmp_path)
    version = workbook["版本資訊"]
    hydrology = workbook["水文Q值"]
    outflow = workbook["年度基準出流"]
    parameters = workbook["水庫參數"]
    assert len(version.data_validations.dataValidation) == 2
    assert any(
        validation.type == "whole" and "C8" in str(validation.sqref)
        for validation in version.data_validations.dataValidation
    )
    assert any(
        validation.type == "list"
        and validation.formula1 == "annual_period_keys"
        and "C9" in str(validation.sqref)
        for validation in version.data_validations.dataValidation
    )
    assert "annual_period_keys" in workbook.defined_names
    assert any(
        validation.type == "decimal" and "D6:V41" in str(validation.sqref)
        for validation in hydrology.data_validations.dataValidation
    )
    assert any(
        validation.type == "decimal" and "D6:F41" in str(validation.sqref)
        for validation in outflow.data_validations.dataValidation
    )
    assert any(
        validation.type == "decimal" and "C6:C9" in str(validation.sqref)
        for validation in parameters.data_validations.dataValidation
    )
    assert hydrology.freeze_panes == "D6" and hydrology.auto_filter.ref == "A5:V41"
    assert outflow.freeze_panes == "D6" and outflow.auto_filter.ref == "A5:F41"
    assert parameters.freeze_panes == "C6" and parameters.auto_filter.ref == "A5:G9"
    workbook.close()


def test_2_4d_fill_instructions_explain_required_optional_and_inheritance_rules(tmp_path):
    _, workbook = _create_and_load(tmp_path)
    version = workbook["版本資訊"]
    hydrology = workbook["水文Q值"]
    outflow = workbook["年度基準出流"]
    parameters = workbook["水庫參數"]

    assert TEMPLATE_VERSION == "2-4D.1"
    assert version["E8"].value == "請填四位數西元年，例如 2026。"
    assert "本年度已有實績資料的最後一旬" in version["E9"].value
    assert "資料來源與統計期間" in version["E10"].value
    assert version["E11"].value == "水利署水情會議、分署水源調配小組會議或其他決議等"
    assert "無則留白" in version["E12"].value
    assert "沿用目前系統基準資料" in hydrology["A2"].value
    assert "若沒有可沿用資料" in hydrology["A3"].value
    assert "沿用目前系統基準資料" in outflow["A2"].value
    assert "數值未變時" in parameters["A2"].value
    assert "空白代表本版本不填或清除" in parameters["A3"].value
    assert "數值變更時必須填寫新的適用起日" in parameters["E5"].comment.text
    workbook.close()


def test_migration_uses_new_canonical_structure_and_copies_only_business_values(tmp_path):
    source = tmp_path / "old.xlsx"
    output = tmp_path / "migrated.xlsx"
    old = build_workbook()
    old["版本資訊"]["C5"] = "2-4A.1"
    old["版本資訊"]["C8"] = 2026
    old["版本資訊"]["C9"] = "06-下旬"
    old["版本資訊"]["C10"] = "舊水文來源與統計期間"
    old["版本資訊"]["C11"] = "舊出流來源"
    old["版本資訊"]["C12"] = "舊整體備註"
    old["水文Q值"]["D6"] = 1.23
    old["年度基準出流"]["F41"] = 45.6
    old["水庫參數"]["C6"] = 11584
    old["水庫參數"]["E6"] = "2026-01-01"
    old["水庫參數"]["F6"] = "既有來源"
    old["水庫參數"]["G6"] = "既有備註"
    old["水文Q值"]["D5"].comment.text = "舊版說明，不應搬移"
    old.save(source)
    old.close()

    write_migrated_template(source, output)
    migrated = load_workbook(output, data_only=False)

    assert migrated["版本資訊"]["C5"].value == TEMPLATE_VERSION
    assert [migrated["版本資訊"][f"C{row}"].value for row in range(8, 13)] == [
        2026,
        "06-下旬",
        "舊水文來源與統計期間",
        "舊出流來源",
        "舊整體備註",
    ]
    assert migrated["水文Q值"]["D6"].value == 1.23
    assert migrated["年度基準出流"]["F41"].value == 45.6
    assert [migrated["水庫參數"][cell].value for cell in ("C6", "E6", "F6", "G6")] == [
        11584,
        "2026-01-01",
        "既有來源",
        "既有備註",
    ]
    assert "沿用目前系統基準資料" in migrated["水文Q值"]["D5"].comment.text
    assert len(migrated["水文Q值"].data_validations.dataValidation) == 1
    migrated.close()


def test_missing_output_argument_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_existing_output_is_not_silently_overwritten(tmp_path):
    output = tmp_path / "annual-data-template.xlsx"
    output.write_bytes(b"existing-user-content")
    with pytest.raises(FileExistsError, match="--overwrite"):
        write_template(output)
    assert output.read_bytes() == b"existing-user-content"


def test_explicit_overwrite_replaces_existing_file_with_valid_xlsx(tmp_path):
    output = tmp_path / "annual-data-template.xlsx"
    output.write_bytes(b"obsolete")
    write_template(output, overwrite=True)
    workbook = load_workbook(output)
    assert workbook.sheetnames == list(SHEET_NAMES)
    workbook.close()
