"""Application-layer policy for the Phase 2-5C2 official-save UI."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from official_estimate_candidate import OfficialEstimateCandidate
from official_estimate_publisher import (
    OfficialCurrentState,
    OfficialEstimatePublishError,
)
from shared_storage_reader import SharedStorageResult


ENABLE_FORMAL_WRITES_ENV = "LIYUTAN_ENABLE_FORMAL_WRITES"
RUNTIME_PLATFORM = sys.platform


def formal_writes_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Require the official-estimate write flag to be the exact string 1."""
    env = os.environ if environ is None else environ
    return env.get(ENABLE_FORMAL_WRITES_ENV) == "1"


@dataclass(frozen=True)
class OfficialEstimateWriteCapability:
    available: bool
    state: str
    reason: str
    root: Path | None = None
    observed_revision: int | None = None
    observed_current_version_id: str | None = None


def official_estimate_write_capability(
    result: SharedStorageResult | None,
    *,
    shared_mode_enabled: bool,
    observation: OfficialCurrentState | None,
    observation_error: OfficialEstimatePublishError | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> OfficialEstimateWriteCapability:
    """Gate production writes without conflating annual-data capabilities."""

    def unavailable(state: str, reason: str) -> OfficialEstimateWriteCapability:
        return OfficialEstimateWriteCapability(
            False,
            state,
            reason,
            root=None if result is None else result.root,
        )

    if not formal_writes_enabled(environ):
        return unavailable("feature_disabled", "正式保存功能目前尚未啟用。")
    if not shared_mode_enabled:
        return unavailable(
            "shared_mode_disabled", "共享資料模式未啟用，正式保存不可用。"
        )
    if result is None or result.root is None or not result.ok:
        return unavailable(
            "shared_state_unavailable",
            "共享資料未通過完整讀取驗證，正式保存不可用。",
        )
    if (RUNTIME_PLATFORM if platform is None else platform) != "win32":
        return unavailable(
            "platform_unsupported",
            "正式保存僅支援 Windows/SMB production environment。",
        )
    if observation_error is not None:
        state = (
            "recovery_required"
            if observation_error.code == "recovery_required"
            else "official_history_invalid"
        )
        return unavailable(
            state,
            "正式推估 current/history 未通過完整檢查，正式保存不可用。",
        )
    if observation is None:
        return unavailable(
            "official_state_unavailable",
            "無法確認正式推估 current revision，正式保存不可用。",
        )
    return OfficialEstimateWriteCapability(
        True,
        "available",
        "正式保存功能已啟用。",
        root=result.root,
        observed_revision=observation.revision,
        observed_current_version_id=observation.current_version_id,
    )


@dataclass(frozen=True)
class OfficialSaveButtonState:
    enabled: bool
    reason: str | None


def official_save_button_state(
    *,
    candidate: OfficialEstimateCandidate | None,
    candidate_current: bool,
    capability: OfficialEstimateWriteCapability,
    final_confirmation: bool,
    publish_in_progress_version_id: str | None,
    consumed_version_ids: set[str] | frozenset[str],
) -> OfficialSaveButtonState:
    """Combine session safety conditions for the final UI action."""
    if candidate is None:
        return OfficialSaveButtonState(False, "尚未產生正式保存預覽。")
    if candidate.observed_official_revision is None:
        return OfficialSaveButtonState(
            False, "這份預覽未綁定 official current revision，請重新產生。"
        )
    if not candidate_current:
        return OfficialSaveButtonState(False, "正式保存預覽已失效，請重新產生。")
    if candidate.version_id in consumed_version_ids:
        return OfficialSaveButtonState(False, "這份 candidate 已處理，不可再次正式保存。")
    if publish_in_progress_version_id == candidate.version_id:
        return OfficialSaveButtonState(False, "這份 candidate 正在正式保存中。")
    if not capability.available:
        return OfficialSaveButtonState(False, capability.reason)
    if not final_confirmation:
        return OfficialSaveButtonState(False, "請先勾選正式保存的最後確認。")
    return OfficialSaveButtonState(True, None)


@dataclass(frozen=True)
class OfficialPublishErrorPresentation:
    message: str
    clear_candidate: bool
    consume_candidate: bool


def official_publish_error_presentation(
    error: OfficialEstimatePublishError,
) -> OfficialPublishErrorPresentation:
    """Map stable publisher codes to concise, non-technical UI messages."""
    code = error.code
    if code in {"revision_conflict", "previous_version_conflict"}:
        return OfficialPublishErrorPresentation(
            "已有其他正式版本在您產生預覽後建立或切換。為避免覆蓋他人資料，"
            "這份預覽不能直接保存，請重新產生正式保存預覽。",
            True,
            False,
        )
    if code == "recovery_required":
        return OfficialPublishErrorPresentation(
            "正式推估資料目前需要復原／診斷，為保護既有正式資料，"
            "已暫停新的正式保存。",
            True,
            False,
        )
    if code in {"current_version_invalid", "annual_version_invalid"}:
        return OfficialPublishErrorPresentation(
            "正式資料完整性檢查未通過，已停止正式保存。",
            True,
            False,
        )
    if code == "version_published_current_not_switched":
        return OfficialPublishErrorPresentation(
            "新的完整正式版本已建立，但尚未成功設為目前正式版本。"
            "請勿再次按正式保存，需先進行系統診斷。",
            True,
            True,
        )
    if code == "current_switched_audit_incomplete":
        return OfficialPublishErrorPresentation(
            "新的正式版本已成為目前版本，但操作紀錄尚未完整確認。"
            "請勿再次保存，需先進行系統診斷。",
            True,
            True,
        )
    if code == "lock_timeout":
        return OfficialPublishErrorPresentation(
            "目前可能有其他同事正在正式保存，暫時無法取得正式保存鎖。"
            "請稍後再試。",
            False,
            False,
        )
    if code == "version_id_exists":
        return OfficialPublishErrorPresentation(
            "這份正式保存 candidate 的版本 ID 已存在，不能重複保存；"
            "請重新產生正式保存預覽。",
            True,
            True,
        )
    if code in {"candidate_invalid", "staging_validation_failed"}:
        return OfficialPublishErrorPresentation(
            "正式保存預覽已無法通過完整驗證，請重新產生預覽。",
            True,
            False,
        )
    return OfficialPublishErrorPresentation(
        "正式保存未完成，請確認共享資料夾連線與權限。",
        False,
        False,
    )
