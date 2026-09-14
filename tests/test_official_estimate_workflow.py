from pathlib import Path

import pytest

from official_estimate_candidate import build_official_estimate_candidate
from official_estimate_publisher import (
    OfficialCurrentState,
    OfficialEstimatePublishError,
)
from official_estimate_workflow import (
    ENABLE_FORMAL_WRITES_ENV,
    OfficialEstimateWriteCapability,
    formal_writes_enabled,
    official_estimate_write_capability,
    official_publish_error_presentation,
    official_save_button_state,
)
from shared_storage_reader import SharedStorageResult
from test_official_estimate_candidate import CLEAN_SOFTWARE, _ready


def _result(tmp_path: Path) -> SharedStorageResult:
    return SharedStorageResult(True, tmp_path, "2027-01-05T00:00:00Z", system={})


def _capability(tmp_path: Path, **changes) -> OfficialEstimateWriteCapability:
    values = {
        "result": _result(tmp_path),
        "shared_mode_enabled": True,
        "observation": OfficialCurrentState(1, "estimate-current", {}),
        "environ": {ENABLE_FORMAL_WRITES_ENV: "1"},
        "platform": "win32",
    }
    values.update(changes)
    return official_estimate_write_capability(**values)


def _candidate():
    batch, results = _ready(1)
    candidate = build_official_estimate_candidate(
        batch,
        results,
        [batch["scenarios"][0]["scenario_id"]],
        annual_data_version_id="annual-2026-001",
        shared_annual_data_validated=True,
        operator_display_name="王承辦",
        note="正式保存接線測試",
        software=CLEAN_SOFTWARE,
        previous_official_version_id="estimate-current",
        observed_official_revision=1,
        observed_official_current_version_id="estimate-current",
        estimate_version_id="estimate-workflow-test",
        created_at="2027-01-05T00:00:00Z",
    )
    return candidate


def test_formal_write_flag_is_exact_and_defaults_off():
    assert not formal_writes_enabled({})
    assert not formal_writes_enabled({ENABLE_FORMAL_WRITES_ENV: "true"})
    assert formal_writes_enabled({ENABLE_FORMAL_WRITES_ENV: "1"})


def test_capability_requires_flag_healthy_shared_root_and_windows(tmp_path):
    assert _capability(tmp_path).available
    assert not _capability(tmp_path, environ={}).available
    assert not _capability(tmp_path, platform="linux").available
    assert not _capability(tmp_path, shared_mode_enabled=False).available
    assert not _capability(
        tmp_path,
        result=SharedStorageResult(False, tmp_path, "now"),
    ).available
    assert not _capability(tmp_path, observation=None).available


def test_capability_exposes_observed_pair_for_diagnostics(tmp_path):
    capability = _capability(tmp_path)
    assert capability.observed_revision == 1
    assert capability.observed_current_version_id == "estimate-current"


def test_save_button_requires_confirmation_and_current_candidate(tmp_path):
    candidate = _candidate()
    capability = _capability(tmp_path)
    common = {
        "candidate": candidate,
        "candidate_current": True,
        "capability": capability,
        "publish_in_progress_version_id": None,
        "consumed_version_ids": set(),
    }
    assert not official_save_button_state(
        **common, final_confirmation=False
    ).enabled
    assert official_save_button_state(**common, final_confirmation=True).enabled
    assert not official_save_button_state(
        **{**common, "candidate_current": False}, final_confirmation=True
    ).enabled
    assert not official_save_button_state(
        **{
            **common,
            "publish_in_progress_version_id": candidate.version_id,
        },
        final_confirmation=True,
    ).enabled
    assert not official_save_button_state(
        **{**common, "consumed_version_ids": {candidate.version_id}},
        final_confirmation=True,
    ).enabled


@pytest.mark.parametrize(
    ("code", "clear", "consume", "message"),
    [
        ("revision_conflict", True, False, "重新產生正式保存預覽"),
        ("previous_version_conflict", True, False, "重新產生正式保存預覽"),
        ("recovery_required", True, False, "需要復原／診斷"),
        ("current_version_invalid", True, False, "完整性檢查未通過"),
        ("annual_version_invalid", True, False, "完整性檢查未通過"),
        (
            "version_published_current_not_switched",
            True,
            True,
            "尚未成功設為目前正式版本",
        ),
        (
            "current_switched_audit_incomplete",
            True,
            True,
            "操作紀錄尚未完整確認",
        ),
        ("lock_timeout", False, False, "請稍後再試"),
        ("version_id_exists", True, True, "版本 ID 已存在"),
        ("filesystem_failure", False, False, "共享資料夾連線與權限"),
    ],
)
def test_publisher_error_codes_have_stable_ui_actions(code, clear, consume, message):
    presentation = official_publish_error_presentation(
        OfficialEstimatePublishError(code, "synthetic")
    )
    assert presentation.clear_candidate is clear
    assert presentation.consume_candidate is consume
    assert message in presentation.message
