import contextlib
import shutil
from pathlib import Path

from streamlit.testing.v1 import AppTest

import annual_data_maintenance as maintenance_module
import annual_data_preview_ui as preview_ui
from annual_data_activation import (
    AnnualDataActivationConflictError,
    AnnualDataActivationRecoveryRequiredError,
    activate_annual_data_version,
)
from annual_data_excel import parse_annual_data_excel
from shared_storage_reader import (
    DataSourceMode,
    ENABLE_SHARED_STORAGE_ENV,
    SHARED_ROOT_ENV,
)
from annual_data_maintenance import ENABLE_ANNUAL_DATA_WRITES_ENV
from shared_storage_schema import ANNUAL_CURRENT_SCHEMA, SCHEMA_VERSION, serialize_json
from test_shared_storage_reader import ANNUAL_ID, _build_root
from test_shared_storage_reader import _write_bundle
from test_shared_storage_schema import (
    _annual_bundle,
    synthetic_hydrology_rows,
    synthetic_outflow_rows,
    synthetic_parameters,
)
from test_annual_data_excel import _mutated_bytes, _workbook_bytes
from software_provenance import SoftwareProvenanceResult


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
TAB_LABELS = [
    "⚙️ 第一階段：推估需求基礎資料設定",
    "🌊 第二階段：入流條件與水文維護",
    "🚰 第三階段：出流需求與抗旱調整",
    "🧮 第四階段：庫容推估演算",
    "📊 第五階段：推估成果產品",
]


def _run_app():
    return AppTest.from_file(str(APP_PATH)).run(timeout=30)


def _assert_five_tabs(app):
    assert [tab.label for tab in app.tabs] == TAB_LABELS


def _messages(elements):
    return "\n".join(str(element.value) for element in elements)


def _annual_uploader(app):
    return next(
        uploader
        for uploader in app.get("file_uploader")
        if uploader.key == "annual_data_excel_preview_upload"
    )


def test_compatibility_mode_does_not_read_shared_path_and_opens_workspace(
    tmp_path, monkeypatch
):
    unread_path = tmp_path / "must-not-be-read"
    monkeypatch.delenv(ENABLE_SHARED_STORAGE_ENV, raising=False)
    monkeypatch.setenv(SHARED_ROOT_ENV, str(unread_path))

    app = _run_app()

    assert not app.exception
    _assert_five_tabs(app)
    assert not unread_path.exists()
    assert not app.error
    assert "相容模式" in _messages(app.info)
    assert app.session_state.active_data_source_mode == DataSourceMode.COMPATIBILITY.value
    assert app.session_state.shared_storage_readable is False
    assert app.session_state.formal_write_available is False
    assert app.session_state.formal_operations_available is False


def test_enabled_valid_shared_data_shows_official_state_and_opens_workspace(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path, official=True)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))

    app = _run_app()

    assert not app.exception
    _assert_five_tabs(app)
    assert "已連線到共享正式資料" in _messages(app.success)
    assert "正式寫入：不可用" in _messages(app.caption)
    metric_values = {metric.label: metric.value for metric in app.metric}
    assert metric_values["年度資料版本"] == ANNUAL_ID
    assert metric_values["資料適用年度"] == "2027"
    assert app.session_state.active_data_source_mode == DataSourceMode.OFFICIAL.value
    assert app.session_state.shared_storage_readable is True
    assert app.session_state.formal_write_available is False
    assert app.session_state.formal_operations_available is False


def test_annual_write_capability_uses_separate_default_off_flag(tmp_path, monkeypatch):
    root = _build_root(tmp_path)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    monkeypatch.delenv(ENABLE_ANNUAL_DATA_WRITES_ENV, raising=False)

    app = _run_app()

    assert not app.exception
    assert app.session_state.annual_data_write_available is False
    assert app.session_state.formal_write_available is False
    assert app.session_state.formal_operations_available is False
    assert not (root / "staging").exists()


def test_shared_preview_still_works_while_annual_write_flag_is_off(tmp_path, monkeypatch):
    root = _build_root(tmp_path)
    current_before = (root / "annual-data" / "current.json").read_bytes()
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    monkeypatch.delenv(ENABLE_ANNUAL_DATA_WRITES_ENV, raising=False)
    app = _run_app()
    app = _annual_uploader(app).upload(
        "synthetic.xlsx",
        _workbook_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run(timeout=30)

    assert "Excel 結構與完整內容驗證成功" in _messages(app.success)
    assert app.session_state.annual_data_write_available is False
    create = next(button for button in app.button if button.label == "建立版本")
    activate = next(button for button in app.button if button.label == "啟用此版本")
    assert create.disabled and activate.disabled
    assert (root / "annual-data" / "current.json").read_bytes() == current_before
    assert not (root / "staging").exists()


def test_annual_write_flag_alone_never_reads_or_writes_shared_root(tmp_path, monkeypatch):
    untouched = tmp_path / "must-not-be-read"
    monkeypatch.delenv(ENABLE_SHARED_STORAGE_ENV, raising=False)
    monkeypatch.setenv(ENABLE_ANNUAL_DATA_WRITES_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(untouched))

    app = _run_app()

    assert not app.exception
    assert app.session_state.annual_data_write_available is False
    assert not untouched.exists()


def test_annual_diagnostics_healthy_is_green(tmp_path, monkeypatch):
    root = _build_root(tmp_path)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))

    app = _run_app()

    assert not app.exception
    assert "年度資料診斷：healthy" in _messages(app.success)
    assert "matched" in _messages(app.metric)


def test_annual_diagnostics_orphan_is_attention_but_current_remains_writable(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path)
    orphan_id = "annual-synthetic-orphan-ui"
    _write_bundle(
        root / "annual-data" / "versions" / orphan_id,
        _annual_bundle(version_mutator=lambda value: value.update(version_id=orphan_id)),
    )
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(ENABLE_ANNUAL_DATA_WRITES_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))

    app = _run_app()

    assert not app.exception
    assert "年度資料診斷：attention" in _messages(app.warning)
    assert app.session_state.annual_data_write_available is True


def test_filesystem_audit_missing_shows_recovery_and_disables_create_activate(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path)
    for audit in (root / "audit" / "events").rglob("*.json"):
        audit.unlink()
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(ENABLE_ANNUAL_DATA_WRITES_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))

    app = _run_app()

    assert not app.exception
    assert "正式年度資料需要復原處理" in _messages(app.error)
    assert "找不到對應此次 current transition" in _messages(app.error)
    assert app.session_state.annual_data_write_available is False
    assert next(button for button in app.button if button.label == "建立版本").disabled
    assert next(button for button in app.button if button.label == "啟用此版本").disabled


def test_filesystem_recovery_is_rediscovered_after_session_key_is_cleared(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path)
    for audit in (root / "audit" / "events").rglob("*.json"):
        audit.unlink()
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(ENABLE_ANNUAL_DATA_WRITES_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()
    app.session_state.annual_activation_recovery_required = {"synthetic": True}
    del app.session_state["annual_activation_recovery_required"]

    app = app.run(timeout=30)

    assert not app.exception
    assert "正式年度資料需要復原處理" in _messages(app.error)
    assert app.session_state.annual_data_write_available is False


def test_healthy_current_and_first_version_enable_annual_specific_capability(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(ENABLE_ANNUAL_DATA_WRITES_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))

    healthy = _run_app()
    assert not healthy.exception
    assert healthy.session_state.annual_data_write_available is True
    assert healthy.session_state.formal_write_available is False
    assert healthy.session_state.formal_operations_available is False

    (root / "annual-data" / "current.json").unlink()
    shutil.rmtree(root / "annual-data" / "versions")
    shutil.rmtree(root / "audit")
    first = _run_app()
    assert not first.exception
    assert first.session_state.annual_data_write_available is True
    assert "first-version observed state = (0, None)" in _messages(first.success)


def _switch_test_root_to_version_b(root):
    version_b = "annual-synthetic-2028"
    hydrology = synthetic_hydrology_rows()
    hydrology[0]["q05_cms"] += 10
    outflow = synthetic_outflow_rows()
    outflow[0]["public_water_10k_ton_per_day"] = 88
    parameters = synthetic_parameters()
    parameters["max_capacity_10k_ton"] = 12000
    bundle = _annual_bundle(
        hydrology_rows=hydrology,
        outflow_rows=outflow,
        parameters=parameters,
        version_mutator=lambda value: value.update(
            version_id=version_b,
            applicable_year=2028,
            candidate_fingerprint="2" * 64,
        ),
    )
    _write_bundle(root / "annual-data" / "versions" / version_b, bundle)
    (root / "annual-data" / "current.json").write_bytes(
        serialize_json(
            {
                "schema": ANNUAL_CURRENT_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "revision": 2,
                "current_version_id": version_b,
                "previous_version_id": ANNUAL_ID,
                "updated_at": "2027-12-15T02:35:00Z",
                "operator_display_name": "另一台電腦",
            }
        )
    )
    return version_b


def test_current_changed_never_silently_replaces_workspace_and_reload_is_complete(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()
    hydrology_a = app.session_state.hydrology_df.copy(deep=True)
    demand_a = app.session_state.demand_df.copy(deep=True)
    parameters_a = (
        app.session_state.max_capacity,
        app.session_state.shilin_eco_flow,
        app.session_state.liyutan_eco_flow,
        app.session_state.shilin_diversion_limit,
    )
    app.session_state.hydrology_session_upload = True
    app.session_state.demand_session_upload = True

    version_b = _switch_test_root_to_version_b(root)
    app = app.run(timeout=30)

    assert not app.exception
    assert {metric.label: metric.value for metric in app.metric}["年度資料版本"] == version_b
    assert app.session_state.hydrology_df.equals(hydrology_a)
    assert app.session_state.demand_df.equals(demand_a)
    assert parameters_a == (
        app.session_state.max_capacity,
        app.session_state.shilin_eco_flow,
        app.session_state.liyutan_eco_flow,
        app.session_state.shilin_diversion_limit,
    )
    assert app.session_state.loaded_shared_annual_version_id == ANNUAL_ID
    assert app.session_state.pending_shared_annual_version_id == version_b
    assert app.session_state.workspace_annual_stale is True
    assert f"仍使用年度版本 {ANNUAL_ID}" in _messages(app.error)
    assert f"current 已更新為 {version_b}" in _messages(app.error)
    assert "會清除目前的水文／出流年度資料工作階段上傳" in _messages(app.warning)

    retain = next(button for button in app.button if button.label == "暫時保留目前工作區")
    app = retain.click().run(timeout=30)
    assert app.session_state.hydrology_df.equals(hydrology_a)
    assert app.session_state.loaded_shared_annual_version_id == ANNUAL_ID
    assert app.session_state.workspace_annual_stale is True
    assert "不一致警示會持續顯示" in _messages(app.info)

    app.session_state.v2_batch_results = {"old": "result"}
    app.session_state.sim_results = {"old": "result"}
    reload_button = next(
        button
        for button in app.button
        if button.label == f"重新載入新版系統基準資料 {version_b}"
    )
    app = reload_button.click().run(timeout=30)

    assert not app.exception
    assert app.session_state.loaded_shared_annual_version_id == version_b
    assert app.session_state.workspace_annual_stale is False
    assert "pending_shared_annual_version_id" not in app.session_state
    assert app.session_state.hydrology_session_upload is False
    assert app.session_state.demand_session_upload is False
    assert app.session_state.max_capacity == 12000
    assert app.session_state.hydrology_df.iloc[0]["Q5"] == 12.9
    assert app.session_state.demand_df.iloc[0]["公共出水_萬噸"] == 88
    assert "v2_batch_results" not in app.session_state
    assert "sim_results" not in app.session_state
    assert app.session_state.v2_results_stale is True


def _set_widget_value(elements, label, value):
    element = next(item for item in elements if item.label == label)
    element.set_value(value)
    return element


def test_create_then_activate_are_independent_actions_and_workspace_is_not_replaced(
    tmp_path, monkeypatch
):
    @contextlib.contextmanager
    def fake_lock(_path):
        yield

    def activate_with_fake_lock(**arguments):
        return activate_annual_data_version(**arguments, lock_factory=fake_lock)

    service = maintenance_module.AnnualDataMaintenanceService(
        activator=activate_with_fake_lock,
        provenance_loader=lambda: SoftwareProvenanceResult(
            True,
            software={
                "repository": "mousepenguin-yzh/liyutan-reservoir-estimator",
                "git_commit": "a" * 40,
                "app_version": "git-aaaaaaaaaaaa",
                "source_tree_dirty": False,
            },
        ),
        platform="linux",
    )
    monkeypatch.setattr(maintenance_module, "RUNTIME_PLATFORM", "win32")
    monkeypatch.setattr(preview_ui, "AnnualDataMaintenanceService", lambda: service)
    root = _build_root(tmp_path)
    current_before = (root / "annual-data" / "current.json").read_bytes()
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(ENABLE_ANNUAL_DATA_WRITES_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()
    hydrology_before = app.session_state.hydrology_df.copy(deep=True)

    app = _annual_uploader(app).upload(
        "synthetic.xlsx",
        _workbook_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run(timeout=30)
    _set_widget_value(app.text_input, "人工填報操作人", "年度維護測試人")
    _set_widget_value(app.text_area, "建立版本備註", "建立後必須保持未啟用")
    _set_widget_value(
        app.checkbox,
        "我已確認上述內容與差異，建立新的正式年度版本；建立後尚不會自動啟用。",
        True,
    )
    app = app.run(timeout=30)
    create = next(button for button in app.button if button.label == "建立版本" and not button.disabled)
    app = create.click().run(timeout=30)

    assert not app.exception
    pending = app.session_state.annual_pending_published_version
    version_id = pending["version_id"]
    assert (root / "annual-data" / "versions" / version_id).is_dir()
    assert (root / "annual-data" / "current.json").read_bytes() == current_before
    assert "已建立完成，但尚未設為目前啟用版本" in _messages(app.success)

    _set_widget_value(
        app.text_area,
        "啟用備註（與建立版本備註是不同動作）",
        "第二個獨立人工啟用動作",
    )
    confirmation = next(
        item for item in app.checkbox if item.label == f"我確認要將 immutable 年度版本 {version_id} 設為 current。"
    )
    confirmation.set_value(True)
    app = app.run(timeout=30)
    activate = next(button for button in app.button if button.label == "啟用此版本" and not button.disabled)
    app = activate.click().run(timeout=30)

    assert not app.exception
    assert app.session_state.hydrology_df.equals(hydrology_before)
    assert app.session_state.loaded_shared_annual_version_id == ANNUAL_ID
    activation = app.session_state.annual_activation_result
    assert activation["target_version_id"] == version_id
    assert activation["after_revision"] == 2
    assert activation["previous_version_id"] == ANNUAL_ID
    assert Path(activation["audit_path"]).is_file()
    assert "audit event 已建立" in _messages(app.success)

    app = app.run(timeout=30)
    assert app.session_state.loaded_shared_annual_version_id == ANNUAL_ID
    assert app.session_state.pending_shared_annual_version_id == version_id
    assert app.session_state.workspace_annual_stale is True


def test_warning_candidate_requires_separate_warning_confirmation(tmp_path, monkeypatch):
    root = _build_root(tmp_path)
    raw = _mutated_bytes(
        lambda workbook: (
            setattr(workbook["水庫參數"]["F6"], "value", None),
            setattr(workbook["水庫參數"]["G7"], "value", None),
        )
    )
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(ENABLE_ANNUAL_DATA_WRITES_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()
    app = _annual_uploader(app).upload(
        "warnings.xlsx",
        raw,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run(timeout=30)
    _set_widget_value(app.text_input, "人工填報操作人", "年度維護測試人")
    _set_widget_value(app.text_area, "建立版本備註", "確認 warning gate")
    _set_widget_value(
        app.checkbox,
        "我已確認上述內容與差異，建立新的正式年度版本；建立後尚不會自動啟用。",
        True,
    )
    app = app.run(timeout=30)

    create = next(button for button in app.button if button.label == "建立版本")
    assert create.disabled
    assert not (root / "staging").exists()
    assert any("逐項確認上述 warnings" in checkbox.label for checkbox in app.checkbox)


def test_source_filename_change_cannot_reuse_prior_confirmation(tmp_path, monkeypatch):
    root = _build_root(tmp_path)
    raw = _workbook_bytes()
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(ENABLE_ANNUAL_DATA_WRITES_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()
    app = _annual_uploader(app).upload(
        "first-name.xlsx",
        raw,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run(timeout=30)
    _set_widget_value(app.text_input, "人工填報操作人", "年度維護測試人")
    _set_widget_value(app.text_area, "建立版本備註", "尚未按建立")
    _set_widget_value(
        app.checkbox,
        "我已確認上述內容與差異，建立新的正式年度版本；建立後尚不會自動啟用。",
        True,
    )
    app = app.run(timeout=30)
    assert next(button for button in app.button if button.label == "建立版本").disabled is False

    app = _annual_uploader(app).upload(
        "renamed.xlsx",
        raw,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run(timeout=30)
    confirmation = next(
        item
        for item in app.checkbox
        if item.label == "我已確認上述內容與差異，建立新的正式年度版本；建立後尚不會自動啟用。"
    )
    assert confirmation.value is False
    assert next(button for button in app.button if button.label == "建立版本").disabled
    assert not (root / "staging").exists()


def test_recovery_required_banner_persists_and_blocks_reactivation(tmp_path, monkeypatch):
    root = _build_root(tmp_path)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(ENABLE_ANNUAL_DATA_WRITES_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()
    app.session_state.annual_activation_recovery_required = {
        "target_version_id": "annual-pending-recovery",
        "after_revision": 2,
        "current": {},
        "audit_path": "synthetic-audit-path",
    }

    app = app.run(timeout=30)
    assert "current 可能已經成功切換" in _messages(app.error)


def _pending_for_ui():
    parsed = parse_annual_data_excel(_workbook_bytes(), filename="synthetic.xlsx")
    candidate = parsed.candidate
    return {
        "version_id": "annual-pending-ui-test",
        "candidate_fingerprint": candidate.fingerprint,
        "source_filename": "synthetic.xlsx",
        "source_sha256": candidate.source_sha256,
        "applicable_year": candidate.applicable_year,
        "published_metadata": {},
        "publish_operator": "原建立操作人",
        "candidate_preview": candidate,
    }


def _install_failing_activation_service(monkeypatch, activator):
    service = maintenance_module.AnnualDataMaintenanceService(
        activator=activator,
        provenance_loader=lambda: SoftwareProvenanceResult(
            True,
            software={
                "repository": "mousepenguin-yzh/liyutan-reservoir-estimator",
                "git_commit": "b" * 40,
                "app_version": "git-bbbbbbbbbbbb",
                "source_tree_dirty": False,
            },
        ),
        platform="linux",
    )
    monkeypatch.setattr(maintenance_module, "RUNTIME_PLATFORM", "win32")
    monkeypatch.setattr(preview_ui, "AnnualDataMaintenanceService", lambda: service)


def _confirm_pending_activation(app):
    target = app.session_state.annual_pending_published_version["version_id"]
    _set_widget_value(
        app.text_area,
        "啟用備註（與建立版本備註是不同動作）",
        "合成啟用確認",
    )
    _set_widget_value(
        app.checkbox,
        f"我確認要將 immutable 年度版本 {target} 設為 current。",
        True,
    )
    app = app.run(timeout=30)
    return next(
        button for button in app.button if button.label == "啟用此版本" and not button.disabled
    )


def test_activation_conflict_uses_exact_observed_state_and_never_retries(tmp_path, monkeypatch):
    calls = []

    def conflict(**arguments):
        calls.append(arguments)
        raise AnnualDataActivationConflictError("revision_conflict", "synthetic conflict")

    _install_failing_activation_service(monkeypatch, conflict)
    root = _build_root(tmp_path)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(ENABLE_ANNUAL_DATA_WRITES_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()
    app.session_state.annual_pending_published_version = _pending_for_ui()
    app = app.run(timeout=30)
    activate = _confirm_pending_activation(app)
    app = activate.click().run(timeout=30)

    assert len(calls) == 1
    assert calls[0]["observed_revision"] == 1
    assert calls[0]["observed_current_version_id"] == ANNUAL_ID
    assert "另一位使用者已先更新年度基準資料" in _messages(app.error)
    assert "annual_activation_result" not in app.session_state


def test_activation_recovery_error_is_persistent_and_does_not_claim_success(
    tmp_path, monkeypatch
):
    calls = []

    def recovery(**arguments):
        calls.append(arguments)
        raise AnnualDataActivationRecoveryRequiredError(
            "synthetic audit incomplete",
            current={"revision": 2, "current_version_id": arguments["target_version_id"]},
            audit_path=tmp_path / "missing-audit.json",
            cause=RuntimeError("synthetic"),
        )

    _install_failing_activation_service(monkeypatch, recovery)
    root = _build_root(tmp_path)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(ENABLE_ANNUAL_DATA_WRITES_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()
    app.session_state.annual_pending_published_version = _pending_for_ui()
    app = app.run(timeout=30)
    activate = _confirm_pending_activation(app)
    app = activate.click().run(timeout=30)

    assert len(calls) == 1
    assert "current 可能已經成功切換" in _messages(app.error)
    assert "annual_activation_result" not in app.session_state
    assert app.session_state.annual_activation_recovery_required["after_revision"] == 2
    app = app.run(timeout=30)
    assert len(calls) == 1
    assert "current 可能已經成功切換" in _messages(app.error)
    app = app.run(timeout=30)
    assert "current 可能已經成功切換" in _messages(app.error)


def test_enabled_without_root_blocks_workspace_without_automatic_fallback(
    monkeypatch,
):
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.delenv(SHARED_ROOT_ENV, raising=False)

    app = _run_app()

    assert not app.exception
    assert not app.tabs
    assert "尚未設定共享資料來源" in _messages(app.error)
    assert app.session_state.active_data_source_mode == DataSourceMode.UNAVAILABLE.value
    assert app.session_state.shared_storage_readable is False
    assert app.session_state.formal_write_available is False
    assert "hydrology_df" not in app.session_state
    assert "demand_df" not in app.session_state
    assert _annual_uploader(app)
    assert any("系統基準資料維護" in expander.label for expander in app.expander)


def test_explicit_fallback_restores_workspace_and_remains_unofficial(monkeypatch):
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.delenv(SHARED_ROOT_ENV, raising=False)
    app = _run_app()

    fallback_button = next(
        button
        for button in app.button
        if button.label == "使用內建備援資料進行非正式試算"
    )
    app = fallback_button.click().run(timeout=30)

    assert not app.exception
    _assert_five_tabs(app)
    assert "非正式／備援資料模式" in _messages(app.warning)
    assert app.session_state.active_data_source_mode == DataSourceMode.BUILTIN_FALLBACK.value
    assert app.session_state.shared_storage_readable is False
    assert app.session_state.formal_write_available is False
    assert app.session_state.formal_operations_available is False

    app = app.run(timeout=30)
    _assert_five_tabs(app)
    assert "非正式／備援資料模式" in _messages(app.warning)


def test_preview_upload_does_not_change_estimation_workspace_data(monkeypatch):
    monkeypatch.delenv(ENABLE_SHARED_STORAGE_ENV, raising=False)
    monkeypatch.delenv(SHARED_ROOT_ENV, raising=False)
    app = _run_app()
    hydrology_before = app.session_state.hydrology_df.copy(deep=True)
    demand_before = app.session_state.demand_df.copy(deep=True)
    parameters_before = (
        app.session_state.max_capacity,
        app.session_state.shilin_eco_flow,
        app.session_state.liyutan_eco_flow,
        app.session_state.shilin_diversion_limit,
    )

    app = _annual_uploader(app).upload(
        "synthetic.xlsx",
        _workbook_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run(timeout=30)

    assert not app.exception
    assert "Excel 結構與完整內容驗證成功" in _messages(app.success)
    assert "尚未建立或啟用正式系統基準版本" in _messages(app.warning)
    assert "共享模式未啟用" in _messages(app.info)
    assert "無法確認正式環境是否存在舊版" in _messages(app.info)
    assert "候選內容完整預覽（未與舊版比較）" in _messages(app.subheader)
    assert "這是第一個候選系統基準版本" not in _messages(app.info)
    assert app.session_state.hydrology_df.equals(hydrology_before)
    assert app.session_state.demand_df.equals(demand_before)
    assert parameters_before == (
        app.session_state.max_capacity,
        app.session_state.shilin_eco_flow,
        app.session_state.liyutan_eco_flow,
        app.session_state.shilin_diversion_limit,
    )
    assert app.session_state.formal_write_available is False
    assert app.session_state.formal_operations_available is False


def test_system_missing_allows_candidate_preview_without_claiming_first_version(
    tmp_path, monkeypatch
):
    root = tmp_path / "uninitialized"
    root.mkdir()
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()

    app = _annual_uploader(app).upload(
        "synthetic.xlsx",
        _workbook_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run(timeout=30)

    assert not app.exception
    assert not app.tabs
    assert "這是第一個候選系統基準版本" not in _messages(app.info)
    assert "目前沒有舊版" not in _messages(app.info)
    assert "根目錄尚未初始化" in _messages(app.warning)
    assert "無法確認正式環境是否存在舊版" in _messages(app.warning)
    assert "候選內容完整預覽（未與舊版比較）" in _messages(app.subheader)
    assert "system.json 不存在" in _messages(app.error)
    assert app.session_state.active_data_source_mode == DataSourceMode.UNAVAILABLE.value
    assert app.session_state.formal_write_available is False
    assert app.session_state.formal_operations_available is False


def test_missing_shared_root_does_not_claim_that_no_old_version_exists(
    tmp_path, monkeypatch
):
    root = tmp_path / "not-connected"
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()

    app = _annual_uploader(app).upload(
        "synthetic.xlsx",
        _workbook_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run(timeout=30)

    assert not app.exception
    assert not app.tabs
    assert "root_not_found" in _messages(app.error)
    assert "無法確認正式環境是否存在舊版" in _messages(app.error)
    assert "這是第一個候選系統基準版本" not in _messages(app.info)
    assert "目前沒有舊版" not in _messages(app.info)
    assert "候選內容完整預覽（未與舊版比較）" in _messages(app.subheader)
    assert app.session_state.formal_write_available is False
    assert app.session_state.formal_operations_available is False


def test_valid_system_without_annual_current_confirms_first_version(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    shutil.rmtree(root / "annual-data" / "versions")
    shutil.rmtree(root / "audit")
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()

    app = _annual_uploader(app).upload(
        "synthetic.xlsx",
        _workbook_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run(timeout=30)

    assert not app.exception
    assert not app.tabs
    assert "這是第一個候選系統基準版本" in _messages(app.info)
    assert "第一版候選內容完整預覽" in _messages(app.subheader)
    assert "年度資料 current pointer 不存在" in _messages(app.error)
    assert app.session_state.formal_write_available is False
    assert app.session_state.formal_operations_available is False


def test_missing_current_with_complete_version_is_not_misreported_as_first_version(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()

    app = _annual_uploader(app).upload(
        "synthetic.xlsx",
        _workbook_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run(timeout=30)

    assert not app.exception
    assert "current 缺失，但 versions 中已存在正式資料 evidence" in _messages(app.error)
    assert "不能視為第一版" in _messages(app.error)
    assert "這是第一個候選系統基準版本" not in _messages(app.info)
    assert "候選內容完整預覽（未與舊版比較）" in _messages(app.subheader)


def test_missing_current_with_invalid_version_is_recovery_required_not_first_version(
    tmp_path, monkeypatch
):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").unlink()
    (
        root
        / "annual-data"
        / "versions"
        / ANNUAL_ID
        / "COMMITTED.json"
    ).unlink()
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(ENABLE_ANNUAL_DATA_WRITES_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()

    app = _annual_uploader(app).upload(
        "synthetic.xlsx",
        _workbook_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run(timeout=30)

    assert not app.exception
    assert "正式年度資料需要復原處理" in _messages(app.error)
    assert "current 缺失，但 versions 中已存在正式資料 evidence" in _messages(app.error)
    assert "這是第一個候選系統基準版本" not in _messages(app.info)
    assert app.session_state.annual_data_write_available is False
    assert next(button for button in app.button if button.label == "建立版本").disabled
    assert next(button for button in app.button if button.label == "啟用此版本").disabled


def test_damaged_active_baseline_is_not_misreported_as_first_version(tmp_path, monkeypatch):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").write_bytes(b"{")
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    app = _run_app()

    app = _annual_uploader(app).upload(
        "synthetic.xlsx",
        _workbook_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run(timeout=30)

    assert not app.exception
    assert "current_invalid" in _messages(app.error)
    assert "無法確認正式環境是否存在舊版" in _messages(app.error)
    assert "這是第一個候選系統基準版本" not in _messages(app.info)
    assert "候選內容完整預覽（未與舊版比較）" in _messages(app.subheader)
    assert app.session_state.formal_write_available is False
