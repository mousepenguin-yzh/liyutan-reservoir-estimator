from pathlib import Path

from streamlit.testing.v1 import AppTest

from shared_storage_reader import (
    DataSourceMode,
    ENABLE_SHARED_STORAGE_ENV,
    SHARED_ROOT_ENV,
)
from test_shared_storage_reader import ANNUAL_ID, _build_root
from test_annual_data_excel import _workbook_bytes


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
