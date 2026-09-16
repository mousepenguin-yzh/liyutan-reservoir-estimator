import copy

import pytest

from official_estimate_continuation import OfficialContinuationDraft
from official_estimate_continuation_ui import (
    CONTINUATION_SOURCE,
    NEW_WORK_SOURCE,
    annual_demand_frame,
    annual_hydrology_frame,
    apply_active_annual_helpers,
    apply_continuation_to_session,
    apply_date_adjustment_to_session,
    apply_portable_batch_to_session,
    build_official_history_view,
    build_official_snapshot_preview,
    current_continuation_adjustment,
    current_continuation_draft,
    invalidate_working_artifacts,
    mark_active_annual_unavailable,
    preview_continuation_date_request,
    reset_to_new_work,
)
from official_estimate_date_adjustment import adjust_continuation_dates
from official_estimate_loader import (
    OfficialEstimateLoader,
    OfficialVersionMetadata,
)
from test_official_estimate_date_adjustment import ANNUAL_A, ANNUAL_B, _annual, _draft
from test_official_estimate_loader import _build_root, _versioned_bundle, _write_bundle


def _metadata(draft):
    return OfficialVersionMetadata(
        version_id=draft.derived_from_official_version_id,
        previous_official_version_id="estimate-previous",
        derived_from_official_version_id="estimate-ancestor",
        batch_id="source-batch",
        batch_name="來源正式批次",
        annual_data_version_id=draft.annual_data_version_id,
        created_at="2026-09-01T00:00:00Z",
        operator_display_name="測試操作人",
        note="正式發布備註",
        display_start_date=draft.batch["display_start_date"],
        projection_start_date=draft.batch["projection_start_date"],
        projection_end_date=draft.batch["projection_end_date"],
        scenarios=(),
    )


def _state_with_results():
    return {
        "v2_batch_results": {"old": {"status": "success"}},
        "v2_result_fingerprint": "old-fingerprint",
        "v2_selected_scenario": "old",
        "sim_results": "old",
        "official_estimate_candidate": {"candidate": "old"},
        "official_publish_in_progress_version_id": "old-version",
        "official_publish_confirmed_old-version": True,
        "v2_comparison_results": {"keep": {"result": "comparison"}},
        "scenarios": {"keep": "legacy comparison"},
        "v2_widget_version": 3,
    }


def test_history_view_is_current_first_and_never_adds_orphan(tmp_path):
    root = _build_root(
        tmp_path,
        versions=[("estimate-current", "estimate-old"), ("estimate-old", None)],
    )
    _write_bundle(
        root / "official-estimates" / "versions" / "estimate-orphan",
        _versioned_bundle("estimate-orphan", None),
    )

    view = build_official_history_view(OfficialEstimateLoader(root).load_history())

    assert [item.version_id for item in view.items] == [
        "estimate-current",
        "estimate-old",
    ]
    assert view.items[0].is_current is True
    assert "目前正式版本" in view.items[0].label
    assert "estimate-orphan" not in {item.version_id for item in view.items}


def test_snapshot_preview_exposes_source_and_current_annual_difference(tmp_path):
    root = _build_root(tmp_path, versions=[("estimate-current", None)])
    snapshot = OfficialEstimateLoader(root).load_current().snapshot

    preview = build_official_snapshot_preview(
        snapshot, current_annual_data_version_id=ANNUAL_B
    )

    assert preview.batch_name == snapshot.batch["batch_name"]
    assert preview.scenario_names == ("合成情境 1", "合成情境 2")
    assert preview.source_annual_data_version_id == "annual-synthetic-2027"
    assert preview.current_annual_data_version_id == ANNUAL_B
    assert preview.annual_versions_differ is True


def test_atomic_continuation_load_syncs_all_working_fields_and_context():
    draft = _draft()
    state = _state_with_results()
    comparisons = copy.deepcopy(state["v2_comparison_results"])

    apply_continuation_to_session(
        state,
        draft,
        source_metadata=_metadata(draft),
        active_annual_validated=True,
    )

    assert state["v2_work_source"] == CONTINUATION_SOURCE
    assert state["v2_continuation_active"] is True
    expected_batch = copy.deepcopy(draft.batch)
    expected_batch.pop("results", None)
    expected_batch.pop("results_fingerprint", None)
    assert state["v2_batch"] == expected_batch
    assert state["v2_batch"] is not draft.batch
    assert state["display_start_date"].isoformat() == draft.batch["display_start_date"]
    assert state["start_date"].isoformat() == draft.batch["projection_start_date"]
    assert state["end_date"].isoformat() == draft.batch["projection_end_date"]
    assert state["init_capacity"] == draft.batch["initial_capacity"]
    assert state["hist_capacity"] == draft.batch["historical_capacities"]
    assert state["override_list"][0]["start"].isoformat() == "2028-01-01"
    assert state["enable_override"] is False
    assert state["v2_outflows_authoritative"] is True
    assert state["v2_derived_from_official_version_id"] == "estimate-source-y"
    assert state["v2_active_annual_data_version_id"] == ANNUAL_A
    assert state["loaded_shared_annual_version_id"] == ANNUAL_A
    assert state["v2_comparison_results"] == comparisons
    assert "v2_batch_results" not in state
    assert "v2_result_fingerprint" not in state
    assert "official_estimate_candidate" not in state
    assert "official_publish_confirmed_old-version" not in state


def test_invalid_load_leaves_original_session_untouched():
    draft = _draft()
    invalid = OfficialContinuationDraft(
        batch={**draft.batch, "batch_name": ""},
        derived_from_official_version_id=draft.derived_from_official_version_id,
        annual_data_version_id=draft.annual_data_version_id,
    )
    state = _state_with_results()
    before = copy.deepcopy(state)

    with pytest.raises(ValueError):
        apply_continuation_to_session(
            state,
            invalid,
            source_metadata=_metadata(draft),
            active_annual_validated=True,
        )

    assert state == before


def test_date_request_preview_is_staged_and_does_not_mutate_active_dates():
    draft = _draft()
    source = copy.deepcopy(draft.batch)

    preview = preview_continuation_date_request(
        draft,
        display_start_date="2026-08-01",
        projection_start_date="2026-09-01",
        projection_end_date="2026-10-21",
    )

    assert preview.added_periods == ("2026-10-上旬", "2026-10-中旬")
    assert preview.changed is True
    assert draft.batch == source


def test_date_adjustment_session_transition_switches_annual_and_preserves_identity():
    draft = _draft()
    state = _state_with_results()
    apply_continuation_to_session(
        state, draft, source_metadata=_metadata(draft), active_annual_validated=True
    )
    adjustment = adjust_continuation_dates(
        current_continuation_draft(state),
        projection_end_date="2026-10-11",
        extension_annual_snapshot=_annual(),
    )

    apply_date_adjustment_to_session(state, adjustment)

    assert state["v2_active_annual_data_version_id"] == ANNUAL_B
    assert state["v2_latest_added_periods"] == ("2026-10-上旬",)
    assert state["end_date"].isoformat() == "2026-10-11"
    assert state["v2_batch"]["batch_id"] == draft.batch["batch_id"]
    assert state["v2_derived_from_official_version_id"] == "estimate-source-y"


def test_current_adjustment_rebuilds_from_latest_batch_not_stale_copy():
    draft = _draft()
    state = {}
    apply_continuation_to_session(
        state, draft, source_metadata=_metadata(draft), active_annual_validated=True
    )
    state["v2_latest_added_periods"] = ("2026-9-下旬",)
    state["v2_batch"]["scenarios"][0]["inflows"]["2026-9-下旬"]["cms"] = 987.0

    current = current_continuation_adjustment(state)

    assert current.batch["scenarios"][0]["inflows"]["2026-9-下旬"]["cms"] == 987.0
    assert current.batch is not state["v2_batch"]


def test_active_annual_helpers_change_frames_but_never_reservoir_parameters():
    state = {
        "max_capacity": 9999.0,
        "shilin_eco_flow": 9.0,
        "liyutan_eco_flow": 8.0,
        "shilin_diversion_limit": 7.0,
    }
    annual = _annual()

    apply_active_annual_helpers(state, annual)

    assert state["hydrology_df"].equals(annual_hydrology_frame(annual))
    assert state["demand_df"].equals(annual_demand_frame(annual))
    assert state["v2_active_annual_data_version_id"] == ANNUAL_B
    assert state["v2_active_annual_validated"] is True
    assert state["max_capacity"] == 9999.0
    assert state["shilin_eco_flow"] == 9.0
    assert state["liyutan_eco_flow"] == 8.0
    assert state["shilin_diversion_limit"] == 7.0


def test_missing_historical_annual_marks_helpers_and_formal_context_unavailable():
    state = {}

    mark_active_annual_unavailable(state, ANNUAL_A, "bundle missing")

    assert state["v2_active_annual_data_version_id"] == ANNUAL_A
    assert state["v2_active_annual_validated"] is False
    assert state["v2_active_annual_error"] == "bundle missing"


def test_reset_to_new_work_clears_lineage_and_preserves_comparison_registry():
    draft = _draft()
    state = _state_with_results()
    apply_continuation_to_session(
        state, draft, source_metadata=_metadata(draft), active_annual_validated=True
    )
    comparisons = copy.deepcopy(state["v2_comparison_results"])

    reset_to_new_work(state, current_annual_version_id=ANNUAL_B)

    assert state["v2_work_source"] == NEW_WORK_SOURCE
    assert "v2_batch" not in state
    assert "v2_derived_from_official_version_id" not in state
    assert "v2_source_official_version_id" not in state
    assert "v2_latest_added_periods" not in state
    assert state["v2_active_annual_data_version_id"] == ANNUAL_B
    assert state["v2_outflows_authoritative"] is False
    assert state["v2_comparison_results"] == comparisons


def test_cleanup_preserves_both_comparison_registries():
    state = _state_with_results()
    v2_comparisons = copy.deepcopy(state["v2_comparison_results"])
    legacy_comparisons = copy.deepcopy(state["scenarios"])

    invalidate_working_artifacts(state)

    assert state["v2_comparison_results"] == v2_comparisons
    assert state["scenarios"] == legacy_comparisons
    assert "v2_batch_results" not in state
    assert "official_estimate_candidate" not in state


def test_portable_import_uses_atomic_batch_sync_and_clears_official_lineage():
    draft = _draft()
    state = _state_with_results()
    state.update(
        v2_continuation_active=True,
        v2_source_official_version_id="estimate-source-y",
        v2_source_official_metadata={"version_id": "estimate-source-y"},
        v2_derived_from_official_version_id="estimate-source-y",
        v2_requested_work_source="從正式版本接續",
    )

    apply_portable_batch_to_session(
        state, draft.batch, current_annual_version_id=ANNUAL_B
    )

    assert state["v2_batch"]["batch_id"] == draft.batch["batch_id"]
    assert state["v2_outflows_authoritative"] is True
    assert state["v2_work_source"] == NEW_WORK_SOURCE
    assert state["v2_continuation_active"] is False
    assert state["v2_derived_from_official_version_id"] is None
    assert "v2_source_official_version_id" not in state
    assert "v2_requested_work_source" not in state
    assert state["v2_active_annual_data_version_id"] == ANNUAL_B
    assert "v2_batch_results" not in state
    assert state["v2_comparison_results"] == {"keep": {"result": "comparison"}}
