"""Application/service layer for the annual-data create and activate workflow."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from annual_data_activation import activate_annual_data_version
from annual_data_version_writer import publish_annual_data_version
from shared_storage_reader import SharedStorageResult, StorageErrorCode
from software_provenance import SoftwareProvenanceResult, load_software_provenance


ENABLE_ANNUAL_DATA_WRITES_ENV = "LIYUTAN_ENABLE_ANNUAL_DATA_WRITES"
RUNTIME_PLATFORM = sys.platform


def annual_data_write_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Require the annual-data write feature flag to be the exact string ``1``."""
    env = os.environ if environ is None else environ
    return env.get(ENABLE_ANNUAL_DATA_WRITES_ENV) == "1"


@dataclass(frozen=True)
class AnnualDataWriteCapability:
    available: bool
    activation_available: bool
    state: str
    reason: str
    root: Path | None = None
    observed_revision: int | None = None
    observed_current_version_id: str | None = None


def annual_data_write_capability(
    result: SharedStorageResult | None,
    *,
    shared_mode_enabled: bool,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> AnnualDataWriteCapability:
    """Allow writes only for a healthy current or a validated first-version state."""
    if not annual_data_write_enabled(environ):
        return AnnualDataWriteCapability(
            False, False, "feature_disabled", "年度正式寫入功能開關目前為關閉。"
        )
    if not shared_mode_enabled:
        return AnnualDataWriteCapability(
            False, False, "shared_mode_disabled", "共享資料模式未啟用，年度正式寫入不可用。"
        )
    if result is None or result.root is None:
        return AnnualDataWriteCapability(
            False, False, "shared_state_unavailable", "共享資料根目錄或讀取結果不可用。"
        )

    if result.ok:
        current = result.annual.current
        revision = current["revision"]
        current_id = current["current_version_id"]
        state = "healthy_current"
    elif result.error is not None and result.error.code is StorageErrorCode.ANNUAL_CURRENT_MISSING:
        # The reader reaches this code only after root and system.json, including
        # reservoir_id=liyutan, have passed validation.
        revision = 0
        current_id = None
        state = "first_version"
    else:
        detail = result.error.code.value if result.error is not None else "unknown"
        return AnnualDataWriteCapability(
            False,
            False,
            "shared_state_invalid",
            f"共享正式狀態未通過完整驗證（{detail}），正常年度寫入已停止。",
            root=result.root,
        )

    windows = (RUNTIME_PLATFORM if platform is None else platform) == "win32"
    activation_reason = (
        "年度版本可建立與啟用。"
        if windows
        else "版本可建立，但正式啟用只支援 Windows/SMB；此平台不得執行 production activation。"
    )
    return AnnualDataWriteCapability(
        True,
        windows,
        state,
        activation_reason,
        root=result.root,
        observed_revision=revision,
        observed_current_version_id=current_id,
    )


class AnnualDataMaintenanceService:
    """Injectable application boundary around the existing safe backends."""

    def __init__(
        self,
        *,
        publisher: Callable = publish_annual_data_version,
        activator: Callable = activate_annual_data_version,
        provenance_loader: Callable[[], SoftwareProvenanceResult] = load_software_provenance,
        platform: str | None = None,
    ) -> None:
        self._publisher = publisher
        self._activator = activator
        self._provenance_loader = provenance_loader
        self._platform = RUNTIME_PLATFORM if platform is None else platform
        self._production_activator = activator is activate_annual_data_version

    def publish(self, **arguments):
        return self._publisher(**arguments)

    def software_provenance(self) -> SoftwareProvenanceResult:
        return self._provenance_loader()

    def activate(self, **arguments):
        if self._platform != "win32" and self._production_activator:
            raise RuntimeError(
                "正式 annual current activation 僅支援 Windows/SMB；測試須注入 backend。"
            )
        return self._activator(**arguments)
