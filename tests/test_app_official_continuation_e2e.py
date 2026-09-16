"""Phase 2-6E: real UI/domain/publisher/loader round trips on pytest roots.

The OS lock, UI platform gate and software provenance are injected. No active
results or candidates are seeded into Streamlit state; calculation and saving use the UI.
"""

import copy
import datetime as dt

import pandas as pd
import pytest

import official_estimate_publisher as publisher
import official_estimate_workflow as workflow
from official_estimate_loader import load_official_current, load_official_history
from shared_storage_reader import ENABLE_SHARED_STORAGE_ENV, SHARED_ROOT_ENV
from shared_storage_schema import deserialize_json
from test_app_official_continuation import _key, _open_continuation_picker, _run_app
from test_app_shared_storage_integration import (
    _fill_phase_25b_preview_form,
    _switch_test_root_to_version_b,
    _use_clean_software_provenance,
)
from test_official_estimate_publisher import _candidate, _disk_bundle, _publish, fake_lock
from test_shared_storage_reader import ANNUAL_ID, OFFICIAL_ID, _build_root


CURRENT_ID = "estimate-intervening-current"
CONFIRM_SAVE = "我已確認以上內容，確定建立不可變的正式推估版本。"


def _label(elements, label):
    return next(item for item in elements if item.label == label)


def _environment(tmp_path, monkeypatch):
    root = _build_root(tmp_path, official=True)
    annual_b = _switch_test_root_to_version_b(root)
    # An independent intervening publication makes source != current.
    _publish(root, _candidate(CURRENT_ID, OFFICIAL_ID), 1, OFFICIAL_ID)
    monkeypatch.setenv(ENABLE_SHARED_STORAGE_ENV, "1")
    monkeypatch.setenv(SHARED_ROOT_ENV, str(root))
    monkeypatch.setenv(workflow.ENABLE_FORMAL_WRITES_ENV, "1")
    monkeypatch.setattr(workflow, "RUNTIME_PLATFORM", "win32")
    _use_clean_software_provenance(monkeypatch)
    real_publish = publisher.publish_official_estimate_candidate
    calls = []

    def publish_with_fake_lock(**arguments):
        calls.append(arguments)
        return real_publish(**arguments, lock_factory=fake_lock)

    monkeypatch.setattr(
        publisher, "publish_official_estimate_candidate", publish_with_fake_lock
    )
    return root, annual_b, calls


def _load(app, version_id):
    _key(app.selectbox, "v2_selected_official_version_id").set_value(version_id)
    app = app.run(timeout=30)
    _key(app.checkbox, f"v2_confirm_official_replace_{version_id}").set_value(True)
    app = app.run(timeout=30)
    app = _key(app.button, f"v2_build_continuation_{version_id}").click().run(timeout=30)
    assert not app.exception
    assert "v2_batch_results" not in app.session_state
    assert "official_estimate_candidate" not in app.session_state
    return app


def _compute_and_preview(app):
    app = _key(app.button, "v2_run_all").click().run(timeout=30)
    assert not app.exception
    results = app.session_state.v2_batch_results
    assert all(item["status"] == "success" for item in results.values())
    _fill_phase_25b_preview_form(app, "scenario-a")
    # Save every source scenario, so the round trip verifies the full batch.
    _label(app.multiselect, "選擇本批次要納入正式保存預覽的情境").set_value(list(results))
    app = app.run(timeout=30)
    button = _label(app.button, "產生正式保存預覽")
    assert not button.disabled
    app = button.click().run(timeout=30)
    assert not app.exception
    return app, app.session_state.official_estimate_candidate


def _save(app):
    _label(app.checkbox, CONFIRM_SAVE).set_value(True)
    app = app.run(timeout=30)
    button = _label(app.button, "正式保存")
    assert not button.disabled
    app = button.click().run(timeout=30)
    assert not app.exception
    assert "official_estimate_candidate" not in app.session_state
    assert any("正式保存成功" in str(item.value) for item in app.success)
    return app


def _assert_saved(root, candidate, *, annual_id, source_id, previous_id, revision):
    current = load_official_current(root)
    snapshot = current.snapshot
    assert current.current["revision"] == revision
    assert current.current["current_version_id"] == candidate.version_id
    assert snapshot.manifest == candidate.validated_bundle["manifest"]
    assert snapshot.inputs == candidate.validated_bundle["inputs"]
    assert list(snapshot.scenario_summaries) == (
        candidate.validated_bundle["scenario_summaries"]
    )
    assert list(snapshot.daily_results) == candidate.validated_bundle["daily_results"]
    assert snapshot.annual_data_version_id == annual_id
    assert snapshot.metadata.derived_from_official_version_id == source_id
    assert snapshot.metadata.previous_official_version_id == previous_id
    assert snapshot.official_scenario_ids == ("scenario-a", "scenario-b")
    return snapshot


def _assert_immutable_files(root, before):
    # current is the only pre-existing file this publication may replace.
    for name, data in before.items():
        if name != "official-estimates/current.json":
            assert (root / name).read_bytes() == data, name


@pytest.mark.parametrize(
    "adjust_dates", [False, True], ids=["historical-annual", "explicit-extension"]
)
def test_historical_continuation_publish_and_fresh_session_round_trip(
    tmp_path, monkeypatch, adjust_dates
):
    root, annual_b, calls = _environment(tmp_path, monkeypatch)
    before = _disk_bundle(root)
    app = _load(_open_continuation_picker(_run_app()), OFFICIAL_ID)
    source_batch = copy.deepcopy(app.session_state.v2_batch)
    assert app.session_state.v2_active_annual_data_version_id == ANNUAL_ID
    assert app.session_state.loaded_shared_annual_version_id == ANNUAL_ID

    # First prepare valid results/candidate, then prove an adjustment retires them.
    app, old_candidate = _compute_and_preview(app)
    # Keep an actual comparison snapshot while the working batch changes.
    app = _label(app.button, "➕ 一次加入所有成功情境").click().run(timeout=30)
    comparisons = copy.deepcopy(app.session_state.v2_comparison_results)
    assert len(comparisons) == 2
    _key(app.checkbox, f"official_publish_confirmed_{old_candidate.version_id}").set_value(True)
    app = app.run(timeout=30)
    if adjust_dates:
        _key(app.date_input, "v2_requested_projection_end_date").set_value(dt.date(2027, 1, 12))
        _key(app.date_input, "v2_requested_projection_start_date").set_value(dt.date(2027, 1, 2))
        app = app.run(timeout=30)
        assert app.session_state.v2_batch["projection_end_date"] == "2027-01-03"
        app = _key(app.button, "v2_apply_extension_dates").click().run(timeout=30)
        assert not app.exception
        assert "v2_batch_results" not in app.session_state
        assert "official_estimate_candidate" not in app.session_state
        assert f"official_publish_confirmed_{old_candidate.version_id}" not in app.session_state
        assert _key(app.button, "v2_run_all").disabled
        assert app.session_state.v2_batch["initial_capacity"] is None
        assert app.session_state.v2_active_annual_data_version_id == annual_b
        for sid, quantile in [("scenario-a", "q90"), ("scenario-b", "q80")]:
            app = _key(app.button, f"v2_added_{quantile}_{sid}").click().run(timeout=30)
        _key(app.number_input, "v2_pending_initial_capacity_value").set_value(7900.0)
        app = app.run(timeout=30)
        app = _key(app.button, "v2_confirm_initial_capacity").click().run(timeout=30)
        assert app.session_state.v2_batch["initial_capacity"] == 7900.0
        assert app.session_state.v2_batch["daily_outflows"][0] == source_batch["daily_outflows"][1]
        for original, scenario in zip(source_batch["scenarios"], app.session_state.v2_batch["scenarios"]):
            assert scenario["inflows"]["2027-1-上旬"] == original["inflows"]["2027-1-上旬"]
        app, candidate = _compute_and_preview(app)
        assert candidate.version_id != old_candidate.version_id
    else:
        candidate = old_candidate
    annual_id = annual_b if adjust_dates else ANNUAL_ID
    assert set(app.session_state.v2_comparison_results) == set(comparisons)
    for result_id, item in comparisons.items():
        pd.testing.assert_frame_equal(
            app.session_state.v2_comparison_results[result_id]["result"], item["result"]
        )
    computed = copy.deepcopy(app.session_state.v2_batch_results)
    assert _disk_bundle(root) == before  # All work before Save is session-only.
    app = _save(app)
    saved = _assert_saved(root, candidate, annual_id=annual_id, source_id=OFFICIAL_ID,
                          previous_id=CURRENT_ID, revision=3)
    assert calls[0]["observed_revision"] == 2
    assert calls[0]["observed_current_version_id"] == CURRENT_ID
    assert app.session_state.official_publish_receipt["annual_data_version_id"] == annual_id
    _assert_immutable_files(root, before)
    assert [item.version_id for item in load_official_history(root).versions] == [
        candidate.version_id, CURRENT_ID, OFFICIAL_ID
    ]

    # A fresh AppTest has no old working results/session state to fall back to.
    after_save = _disk_bundle(root)
    fresh = _load(_open_continuation_picker(_run_app()), candidate.version_id)
    restored = fresh.session_state.v2_batch
    assert restored["batch_id"] != saved.batch["batch_id"]
    assert fresh.session_state.v2_derived_from_official_version_id == candidate.version_id
    assert fresh.session_state.v2_active_annual_data_version_id == annual_id
    for key, value in saved.batch.items():
        if key not in {"batch_id", "batch_name", "created_at", "results", "results_fingerprint"}:
            assert restored[key] == value, key
    assert _disk_bundle(root) == after_save
    fresh, second_candidate = _compute_and_preview(fresh)
    for sid, result in computed.items():
        pd.testing.assert_frame_equal(
            fresh.session_state.v2_batch_results[sid]["data"], result["data"]
        )
        assert fresh.session_state.v2_batch_results[sid]["summary"] == result["summary"]
    fresh = _save(fresh)
    _assert_saved(root, second_candidate, annual_id=annual_id, source_id=candidate.version_id,
                  previous_id=candidate.version_id, revision=4)
    assert len(calls) == 2
    _assert_immutable_files(root, after_save)
    annual_current = deserialize_json((root / "annual-data" / "current.json").read_bytes())
    assert annual_current["current_version_id"] == annual_b


def test_competing_publication_rejects_stale_continuation_then_explicit_preview_recovers(
    tmp_path, monkeypatch
):
    root, _, calls = _environment(tmp_path, monkeypatch)
    app = _load(_open_continuation_picker(_run_app()), OFFICIAL_ID)
    app, stale = _compute_and_preview(app)
    before = _disk_bundle(root)
    _label(app.checkbox, CONFIRM_SAVE).set_value(True)
    app = app.run(timeout=30)
    publish_with_fake_lock = publisher.publish_official_estimate_candidate
    competitor = _candidate("estimate-concurrent-winner", CURRENT_ID)

    def compete_after_ui_observation(**arguments):
        # Fault injection: a complete competing publication lands after the UI
        # observation, before this session's real publisher checks the revision.
        _publish(root, competitor, 2, CURRENT_ID)
        return publish_with_fake_lock(**arguments)

    monkeypatch.setattr(
        publisher, "publish_official_estimate_candidate", compete_after_ui_observation
    )
    app = _label(app.button, "正式保存").click().run(timeout=30)
    assert not app.exception
    assert len(calls) == 1  # No automatic retry/rebase.
    assert any("重新產生正式保存預覽" in str(item.value) for item in app.error)
    assert not any("正式保存成功" in str(item.value) for item in app.success)
    assert "official_estimate_candidate" not in app.session_state
    assert "official_publish_receipt" not in app.session_state
    assert not (root / "official-estimates" / "versions" / stale.version_id).exists()
    assert load_official_current(root).current["current_version_id"] == competitor.version_id
    assert app.session_state.v2_derived_from_official_version_id == OFFICIAL_ID
    assert app.session_state.v2_active_annual_data_version_id == ANNUAL_ID
    _assert_immutable_files(root, before)

    monkeypatch.setattr(
        publisher, "publish_official_estimate_candidate", publish_with_fake_lock
    )
    app, refreshed = _compute_and_preview(app)
    assert refreshed.version_id != stale.version_id
    app = _save(app)
    _assert_saved(root, refreshed, annual_id=ANNUAL_ID, source_id=OFFICIAL_ID,
                  previous_id=competitor.version_id, revision=4)
    assert len(calls) == 2


def test_source_annual_corruption_after_preview_blocks_save_without_rebase(
    tmp_path, monkeypatch
):
    root, annual_b, calls = _environment(tmp_path, monkeypatch)
    app = _load(_open_continuation_picker(_run_app()), OFFICIAL_ID)
    app, candidate = _compute_and_preview(app)
    current_before = (root / "official-estimates" / "current.json").read_bytes()
    annual_file = root / "annual-data" / "versions" / ANNUAL_ID / "hydrology_q.csv"
    annual_file.write_bytes(annual_file.read_bytes() + b"corrupt")
    app = app.run(timeout=30)
    assert not app.exception
    assert "official_estimate_candidate" not in app.session_state
    assert not any(item.label == "正式保存" for item in app.button)
    assert _label(app.button, "產生正式保存預覽").disabled
    assert app.session_state.v2_active_annual_data_version_id == ANNUAL_ID != annual_b
    assert app.session_state.v2_active_annual_validated is False
    assert app.session_state.v2_derived_from_official_version_id == OFFICIAL_ID
    assert (root / "official-estimates" / "current.json").read_bytes() == current_before
    assert not (root / "official-estimates" / "versions" / candidate.version_id).exists()
    assert calls == []
