import copy
import datetime as dt
import shutil
from pathlib import Path

from streamlit.testing.v1 import AppTest

from official_estimate_continuation_ui import annual_hydrology_frame
from shared_storage_reader import (
    ENABLE_SHARED_STORAGE_ENV,
    SHARED_ROOT_ENV,
    load_annual_data_version,
)
from test_app_shared_storage_integration import (
    _fill_phase_25b_preview_form,
    _switch_test_root_to_version_b,
    _use_clean_software_provenance,
)
from test_official_estimate_candidate import _ready as _ready_official_candidate
from test_shared_storage_reader import ANNUAL_ID, OFFICIAL_ID, _build_root
from v2_workflow import safe_export_batch, standardize_comparison_result


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _run_app():
    return AppTest.from_file(str(APP_PATH)).run(timeout=30)


def _key(elements, key):
    return next(item for item in elements if item.key == key)


def _open_continuation_picker(app):
    _key(app.radio, "v2_requested_work_source").set_value("從正式版本接續")
    return app.run(timeout=30)


def _load_selected_official(app):
    confirm_key = f"v2_confirm_official_replace_{OFFICIAL_ID}"
    _key(app.checkbox, confirm_key).set_value(True)
    app = app.run(timeout=30)
    return _key(app.button, f"v2_build_continuation_{OFFICIAL_ID}").click().run(
        timeout=30
    )


def test_ui_loads_current_official_into_new_atomic_working_state(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path, official=True)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _open_continuation_picker(_run_app())

    picker = _key(app.selectbox, "v2_selected_official_version_id")
    assert len(picker.options) == 1
    assert app.session_state.v2_selected_official_version_id == OFFICIAL_ID

    comparison_batch, comparison_results = _ready_official_candidate()
    comparison_scenario = comparison_batch["scenarios"][0]
    comparison_item = standardize_comparison_result(
        comparison_batch,
        comparison_scenario,
        comparison_results[comparison_scenario["scenario_id"]]["data"],
    )
    comparison_item["display_name"] = "既有批次｜既有情境"
    comparison = {comparison_item["result_id"]: comparison_item}
    app.session_state.v2_comparison_results = copy.deepcopy(comparison)
    app.session_state.scenarios = {}
    app.session_state.v2_batch_results = {"old": {"status": "success"}}
    app.session_state.v2_selected_scenario = "old"
    app = _load_selected_official(app)

    assert not app.exception
    batch = app.session_state.v2_batch
    assert batch["batch_id"] != "batch-synthetic-1"
    assert [item["scenario_id"] for item in batch["scenarios"]] == [
        "scenario-a",
        "scenario-b",
    ]
    assert app.session_state.v2_source_official_version_id == OFFICIAL_ID
    assert app.session_state.v2_derived_from_official_version_id == OFFICIAL_ID
    assert app.session_state.v2_active_annual_data_version_id == ANNUAL_ID
    assert app.session_state.start_date == dt.date(2027, 1, 1)
    assert app.session_state.end_date == dt.date(2027, 1, 3)
    assert app.session_state.init_capacity == 8000.0
    assert app.session_state.v2_outflows_authoritative is True
    assert not _key(app.button, "v2_release_imported_outflow").disabled
    assert comparison_item["result_id"] in app.session_state.v2_comparison_results
    assert app.session_state.v2_comparison_results[
        comparison_item["result_id"]
    ]["result"].equals(comparison_item["result"])
    assert "v2_batch_results" not in app.session_state
    assert "official_estimate_candidate" not in app.session_state


def test_old_annual_stays_active_until_explicit_extension_and_q_fill(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path, official=True)
    annual_b = _switch_test_root_to_version_b(root)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _load_selected_official(_open_continuation_picker(_run_app()))

    assert app.session_state.v2_active_annual_data_version_id == ANNUAL_ID
    assert app.session_state.loaded_shared_annual_version_id == ANNUAL_ID
    assert app.session_state.hydrology_df.equals(
        annual_hydrology_frame(load_annual_data_version(root, ANNUAL_ID))
    )
    assert app.session_state.max_capacity == 11584.0
    assert any(
        ANNUAL_ID in str(item.value) and annual_b in str(item.value)
        for item in app.info
    )
    source_batch = copy.deepcopy(app.session_state.v2_batch)

    _key(app.date_input, "v2_requested_projection_end_date").set_value(
        dt.date(2027, 1, 12)
    )
    app = app.run(timeout=30)

    assert app.session_state.v2_batch == source_batch
    assert app.session_state.end_date == dt.date(2027, 1, 3)
    assert app.session_state.v2_active_annual_data_version_id == ANNUAL_ID

    app = _key(app.button, "v2_apply_extension_dates").click().run(timeout=30)

    assert not app.exception
    assert app.session_state.end_date == dt.date(2027, 1, 12)
    assert app.session_state.v2_active_annual_data_version_id == annual_b
    assert tuple(app.session_state.v2_latest_added_periods) == ("2027-1-中旬",)
    batch = app.session_state.v2_batch
    assert batch["scenarios"][0]["inflows"]["2027-1-上旬"]["cms"] == 10.0
    assert batch["scenarios"][1]["inflows"]["2027-1-上旬"]["cms"] == 11.0
    assert batch["scenarios"][0]["inflows"]["2027-1-中旬"]["cms"] is None
    assert batch["daily_outflows"][:2] == source_batch["daily_outflows"]

    app = _key(app.button, "v2_added_q90_scenario-a").click().run(timeout=30)
    assert app.session_state.v2_batch["scenarios"][0]["inflows"]["2027-1-中旬"]["cms"] is not None
    assert app.session_state.v2_batch["scenarios"][1]["inflows"]["2027-1-中旬"]["cms"] is None
    app = _key(app.button, "v2_added_q80_scenario-b").click().run(timeout=30)
    assert app.session_state.v2_batch["scenarios"][1]["inflows"]["2027-1-中旬"]["cms"] is not None
    assert app.session_state.v2_batch["daily_outflows"][:2] == source_batch["daily_outflows"]


def test_start_change_requires_explicit_capacity_confirmation(tmp_path, monkeypatch):
    root = _build_root(tmp_path, official=True)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _load_selected_official(_open_continuation_picker(_run_app()))

    _key(app.date_input, "v2_requested_projection_start_date").set_value(
        dt.date(2027, 1, 2)
    )
    app = app.run(timeout=30)
    app = _key(app.button, "v2_apply_dates_without_extension").click().run(
        timeout=30
    )

    assert not app.exception
    assert app.session_state.v2_batch["initial_capacity"] is None
    assert app.session_state.v2_initial_capacity_requires_confirmation is True
    assert _key(app.button, "v2_run_all").disabled

    _key(app.number_input, "v2_pending_initial_capacity_value").set_value(7900.0)
    app = app.run(timeout=30)
    app = _key(app.button, "v2_confirm_initial_capacity").click().run(timeout=30)

    assert not app.exception
    assert app.session_state.v2_batch["initial_capacity"] == 7900.0
    assert app.session_state.init_capacity == 7900.0
    assert app.session_state.v2_initial_capacity_requires_confirmation is False


def test_historical_active_annual_can_build_formal_preview_with_publish_time_previous(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path, official=True)
    annual_b = _switch_test_root_to_version_b(root)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    _use_clean_software_provenance(monkeypatch)
    app = _load_selected_official(_open_continuation_picker(_run_app()))

    app = _key(app.button, "v2_run_all").click().run(timeout=30)
    assert not app.exception
    assert app.session_state.v2_active_annual_data_version_id == ANNUAL_ID
    assert annual_b != ANNUAL_ID
    _fill_phase_25b_preview_form(app, "scenario-a")
    app = app.run(timeout=30)
    preview_button = next(
        item for item in app.button if item.label == "產生正式保存預覽"
    )
    assert not preview_button.disabled
    app = preview_button.click().run(timeout=30)

    candidate = app.session_state.official_estimate_candidate
    manifest = candidate.validated_bundle["manifest"]
    assert manifest["annual_data_version_id"] == ANNUAL_ID
    assert manifest["derived_from_official_version_id"] == OFFICIAL_ID
    assert manifest["previous_official_version_id"] == OFFICIAL_ID


def test_missing_source_annual_keeps_work_viewable_without_silent_switch(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path, official=True)
    annual_b = _switch_test_root_to_version_b(root)
    shutil.rmtree(root / "annual-data" / "versions" / ANNUAL_ID)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))

    app = _load_selected_official(_open_continuation_picker(_run_app()))

    assert not app.exception
    assert app.session_state.v2_batch["batch_name"].endswith("（接續）")
    assert app.session_state.v2_active_annual_data_version_id == ANNUAL_ID
    assert app.session_state.v2_active_annual_validated is False
    assert app.session_state.loaded_shared_annual_version_id == ANNUAL_ID
    assert annual_b != app.session_state.v2_active_annual_data_version_id
    assert any("正式保存已停用" in str(item.value) for item in app.warning)
    takeover = _key(app.button, "v2_release_imported_outflow")
    assert takeover.disabled
    assert any(
        "工作批次年度基準目前無法驗證" in str(item.value)
        for item in app.warning
    )


def test_corrupt_source_annual_disables_authoritative_outflow_takeover(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path, official=True)
    _switch_test_root_to_version_b(root)
    annual_file = (
        root / "annual-data" / "versions" / ANNUAL_ID / "hydrology_q.csv"
    )
    annual_file.write_bytes(annual_file.read_bytes() + b"corrupt")
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))

    app = _load_selected_official(_open_continuation_picker(_run_app()))

    assert not app.exception
    assert app.session_state.v2_active_annual_validated is False
    assert app.session_state.v2_outflows_authoritative is True
    assert _key(app.button, "v2_release_imported_outflow").disabled


def test_portable_import_returns_work_source_radio_to_new_estimate(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path, official=True)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _load_selected_official(_open_continuation_picker(_run_app()))
    portable_batch = copy.deepcopy(app.session_state.v2_batch)
    json_text, export_error = safe_export_batch(portable_batch)
    assert export_error is None

    uploader = _key(app.get("file_uploader"), "v2_json_upload")
    app = uploader.upload(
        "portable.json", json_text.encode("utf-8"), "application/json"
    ).run(timeout=30)
    confirm = next(
        button for button in app.button if button.label == "確認覆蓋目前設定"
    )
    app = confirm.click().run(timeout=30)

    assert not app.exception
    assert app.session_state.v2_continuation_active is False
    assert app.session_state.v2_derived_from_official_version_id is None
    assert "v2_source_official_version_id" not in app.session_state
    assert _key(app.radio, "v2_requested_work_source").value == "建立全新推估"
    assert not _key(app.button, "v2_release_imported_outflow").disabled
