from pathlib import Path

from streamlit.testing.v1 import AppTest

from shared_storage_reader import (
    DataSourceMode,
    ENABLE_SHARED_STORAGE_ENV,
    SHARED_ROOT_ENV,
)
from test_shared_storage_reader import ANNUAL_ID, _build_root


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
