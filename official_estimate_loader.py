"""Read-only loading for current and historical official estimates.

Normal continuation history is defined exclusively by the validated
``official-estimates/current.json`` pointer and each validated manifest's
``previous_official_version_id``.  This module never inventories sibling
version directories, so orphan, staging, and temporary artifacts cannot enter
the normal history returned to callers.
"""

from __future__ import annotations

import os
import stat as stat_module
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, NoReturn

from shared_storage_schema import (
    StorageValidationError,
    deserialize_json,
    validate_official_bundle,
    validate_official_current,
    validate_safe_id,
    validate_system,
)


ReadBytes = Callable[[Path], bytes]


class OfficialEstimateLoadErrorCode(str, Enum):
    """Stable failure categories for a later UI or recovery workflow."""

    ROOT_NOT_FOUND = "root_not_found"
    ROOT_INVALID = "root_invalid"
    SYSTEM_MISSING = "system_missing"
    SYSTEM_INVALID = "system_invalid"
    CURRENT_MISSING = "current_missing"
    CURRENT_INVALID = "current_invalid"
    BUNDLE_NOT_FOUND = "bundle_not_found"
    BUNDLE_VALIDATION_FAILED = "bundle_validation_failed"
    BROKEN_HISTORY_LINK = "broken_history_link"
    HISTORY_CYCLE = "history_cycle"
    CURRENT_CHANGED = "current_changed"
    VERSION_NOT_IN_HISTORY = "version_not_in_history"
    READ_FAILED = "read_failed"


class OfficialEstimateLoadError(RuntimeError):
    """A diagnosable failure that never repairs or changes shared storage."""

    def __init__(
        self,
        code: OfficialEstimateLoadErrorCode,
        message: str,
        *,
        evidence_path: Path | None = None,
        version_id: str | None = None,
        referenced_by_version_id: str | None = None,
        cause_code: OfficialEstimateLoadErrorCode | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.evidence_path = evidence_path
        self.version_id = version_id
        self.referenced_by_version_id = referenced_by_version_id
        self.cause_code = cause_code


@dataclass(frozen=True)
class OfficialScenarioMetadata:
    """Small, display-safe identity for one scenario in an official snapshot."""

    scenario_id: str
    name: str
    order: int


@dataclass(frozen=True)
class OfficialVersionMetadata:
    """Metadata needed to present one normal continuation-history entry."""

    version_id: str
    previous_official_version_id: str | None
    derived_from_official_version_id: str | None
    batch_id: str
    batch_name: str
    annual_data_version_id: str
    created_at: str
    operator_display_name: str
    note: str
    display_start_date: str
    projection_start_date: str
    projection_end_date: str
    scenarios: tuple[OfficialScenarioMetadata, ...]

    @property
    def official_scenario_ids(self) -> tuple[str, ...]:
        return tuple(scenario.scenario_id for scenario in self.scenarios)


@dataclass(frozen=True)
class OfficialEstimateSnapshot:
    """A fully validated immutable-version snapshot loaded into memory.

    ``scenario_summaries`` and ``daily_results`` are historical reference
    results only.  This loader does not convert them into active working
    results or mutate the saved batch.
    """

    metadata: OfficialVersionMetadata
    manifest: dict
    inputs: dict
    official_scenario_ids: tuple[str, ...]
    scenario_summaries: tuple[dict, ...]
    daily_results: tuple[dict, ...]
    committed: dict

    @property
    def batch(self) -> dict:
        return self.inputs["batch"]

    @property
    def annual_data_version_id(self) -> str:
        return self.metadata.annual_data_version_id


@dataclass(frozen=True)
class OfficialCurrentSnapshot:
    """The validated current pointer together with its complete snapshot."""

    current: dict
    snapshot: OfficialEstimateSnapshot


@dataclass(frozen=True)
class OfficialEstimateHistory:
    """Current-first metadata for the only normal continuation history."""

    current: dict
    versions: tuple[OfficialVersionMetadata, ...]

    @property
    def current_version_id(self) -> str:
        return self.current["current_version_id"]


class OfficialEstimateLoader:
    """Load official snapshots without writing or repairing shared storage.

    The class deliberately exposes no raw/admin read-by-ID method.  A caller
    can load a specified version only through :meth:`load_history_version`,
    which first proves that the version belongs to the current publication
    chain and that every link in that chain is valid.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        read_bytes: ReadBytes | None = None,
    ) -> None:
        self.root = Path(root)
        self._read_bytes = read_bytes or (lambda path: path.read_bytes())
        self._official_root = self.root / "official-estimates"
        self._versions_root = self._official_root / "versions"
        self._current_path = self._official_root / "current.json"

    def load_current(self) -> OfficialCurrentSnapshot:
        """Load and validate the current pointer and its complete bundle."""

        current, snapshots = self._load_chain(include_history=False)
        return OfficialCurrentSnapshot(current=current, snapshot=snapshots[0])

    def load_history(self) -> OfficialEstimateHistory:
        """Return validated metadata ordered current, previous, previous..."""

        current, snapshots = self._load_chain(include_history=True)
        return OfficialEstimateHistory(
            current=current,
            versions=tuple(snapshot.metadata for snapshot in snapshots),
        )

    def load_history_version(self, version_id: str) -> OfficialEstimateSnapshot:
        """Load a complete snapshot only if it belongs to normal history."""

        try:
            requested_id = validate_safe_id(version_id, "requested official version_id")
        except StorageValidationError as exc:
            raise OfficialEstimateLoadError(
                OfficialEstimateLoadErrorCode.VERSION_NOT_IN_HISTORY,
                f"指定的正式版本 ID 不合法，不能作為正常接續版本：{exc}",
                version_id=str(version_id),
            ) from exc
        _, snapshots = self._load_chain(include_history=True)
        for snapshot in snapshots:
            if snapshot.metadata.version_id == requested_id:
                return snapshot
        raise OfficialEstimateLoadError(
            OfficialEstimateLoadErrorCode.VERSION_NOT_IN_HISTORY,
            f"指定正式版本不在 current publication history chain：{requested_id}",
            evidence_path=self._versions_root / requested_id,
            version_id=requested_id,
        )

    def _load_chain(
        self, *, include_history: bool
    ) -> tuple[dict, tuple[OfficialEstimateSnapshot, ...]]:
        self._validate_shared_root()
        pointer_bytes = self._read_current_bytes()
        current = self._validate_current(pointer_bytes)
        current_id = current["current_version_id"]
        snapshots: list[OfficialEstimateSnapshot] = []
        seen: set[str] = set()
        next_id: str | None = current_id
        referenced_by: str | None = None

        while next_id is not None:
            if next_id in seen:
                chain = " -> ".join(
                    [snapshot.metadata.version_id for snapshot in snapshots] + [next_id]
                )
                raise OfficialEstimateLoadError(
                    OfficialEstimateLoadErrorCode.HISTORY_CYCLE,
                    f"正式 publication history 發現 cycle：{chain}",
                    evidence_path=self._versions_root / next_id,
                    version_id=next_id,
                    referenced_by_version_id=referenced_by,
                )
            seen.add(next_id)
            snapshot = self._load_snapshot(next_id, referenced_by=referenced_by)
            snapshots.append(snapshot)
            if not include_history:
                break
            referenced_by = next_id
            next_id = snapshot.metadata.previous_official_version_id

        try:
            pointer_after = self._read_bytes(self._current_path)
        except OSError as exc:
            raise OfficialEstimateLoadError(
                OfficialEstimateLoadErrorCode.CURRENT_CHANGED,
                "正式 current pointer 在讀取期間消失或無法重讀；未回傳混合版本資料。",
                evidence_path=self._current_path,
            ) from exc
        if pointer_after != pointer_bytes:
            raise OfficialEstimateLoadError(
                OfficialEstimateLoadErrorCode.CURRENT_CHANGED,
                "正式 current pointer 在讀取期間發生變動；未回傳混合版本資料。",
                evidence_path=self._current_path,
            )
        return current, tuple(snapshots)

    def _validate_shared_root(self) -> None:
        try:
            root_stat = self.root.stat()
        except FileNotFoundError as exc:
            raise OfficialEstimateLoadError(
                OfficialEstimateLoadErrorCode.ROOT_NOT_FOUND,
                "共享資料根目錄不存在。",
                evidence_path=self.root,
            ) from exc
        except OSError as exc:
            raise OfficialEstimateLoadError(
                OfficialEstimateLoadErrorCode.READ_FAILED,
                f"無法讀取共享資料根目錄：{exc}",
                evidence_path=self.root,
            ) from exc
        if not stat_module.S_ISDIR(root_stat.st_mode) or self.root.is_symlink():
            raise OfficialEstimateLoadError(
                OfficialEstimateLoadErrorCode.ROOT_INVALID,
                "共享資料根目錄不是可信任的實體資料夾。",
                evidence_path=self.root,
            )

        system_path = self.root / "system.json"
        try:
            system_bytes = self._read_bytes(system_path)
        except FileNotFoundError as exc:
            raise OfficialEstimateLoadError(
                OfficialEstimateLoadErrorCode.SYSTEM_MISSING,
                "system.json 不存在，不能確認正式共享資料來源。",
                evidence_path=system_path,
            ) from exc
        except OSError as exc:
            raise OfficialEstimateLoadError(
                OfficialEstimateLoadErrorCode.READ_FAILED,
                f"無法讀取 system.json：{exc}",
                evidence_path=system_path,
            ) from exc
        try:
            validate_system(deserialize_json(system_bytes))
        except StorageValidationError as exc:
            raise OfficialEstimateLoadError(
                OfficialEstimateLoadErrorCode.SYSTEM_INVALID,
                f"system.json 無法通過驗證：{exc}",
                evidence_path=system_path,
            ) from exc

    def _read_current_bytes(self) -> bytes:
        try:
            return self._read_bytes(self._current_path)
        except FileNotFoundError as exc:
            raise OfficialEstimateLoadError(
                OfficialEstimateLoadErrorCode.CURRENT_MISSING,
                "official-estimates/current.json 不存在，沒有可供正常接續的正式版本。",
                evidence_path=self._current_path,
            ) from exc
        except OSError as exc:
            raise OfficialEstimateLoadError(
                OfficialEstimateLoadErrorCode.READ_FAILED,
                f"無法讀取 official-estimates/current.json：{exc}",
                evidence_path=self._current_path,
            ) from exc

    def _validate_current(self, pointer_bytes: bytes) -> dict:
        try:
            return validate_official_current(deserialize_json(pointer_bytes))
        except StorageValidationError as exc:
            raise OfficialEstimateLoadError(
                OfficialEstimateLoadErrorCode.CURRENT_INVALID,
                f"official-estimates/current.json 無法通過驗證：{exc}",
                evidence_path=self._current_path,
            ) from exc

    def _load_snapshot(
        self, version_id: str, *, referenced_by: str | None
    ) -> OfficialEstimateSnapshot:
        version_path = self._versions_root / version_id
        try:
            bundle = self._read_version_bundle(version_path, version_id)
        except FileNotFoundError as exc:
            self._raise_bundle_failure(
                version_id,
                referenced_by,
                OfficialEstimateLoadErrorCode.BUNDLE_NOT_FOUND,
                f"正式版本資料夾不存在：{version_id}",
                version_path,
                exc,
            )
        except OSError as exc:
            self._raise_bundle_failure(
                version_id,
                referenced_by,
                OfficialEstimateLoadErrorCode.READ_FAILED,
                f"正式版本無法完整讀取：{version_id}（{exc}）",
                version_path,
                exc,
            )

        try:
            validated = validate_official_bundle(bundle)
        except StorageValidationError as exc:
            self._raise_bundle_failure(
                version_id,
                referenced_by,
                OfficialEstimateLoadErrorCode.BUNDLE_VALIDATION_FAILED,
                f"正式版本無法通過完整 bundle validation：{version_id}（{exc}）",
                version_path,
                exc,
            )

        manifest = validated["manifest"]
        if manifest["version_id"] != version_id:
            exc = StorageValidationError(
                "版本目錄名稱與 manifest.json version_id 不一致"
            )
            self._raise_bundle_failure(
                version_id,
                referenced_by,
                OfficialEstimateLoadErrorCode.BUNDLE_VALIDATION_FAILED,
                f"正式版本 ID 不一致：目錄 {version_id}，manifest {manifest['version_id']}",
                version_path,
                exc,
            )
        return self._build_snapshot(validated)

    def _read_version_bundle(self, version_path: Path, version_id: str) -> dict[str, bytes]:
        try:
            safe_id = validate_safe_id(version_id, "official version_id")
        except StorageValidationError as exc:
            raise OSError(f"正式版本 ID 不安全：{exc}") from exc
        if safe_id != version_id:
            raise OSError("正式版本 ID normalization 不一致")
        self._assert_contained(self._versions_root, version_path)
        if not version_path.is_dir() or version_path.is_symlink():
            raise FileNotFoundError(version_path)
        try:
            paths = tuple(version_path.rglob("*"))
        except OSError:
            raise
        bundle: dict[str, bytes] = {}
        for path in paths:
            if path.is_symlink():
                raise OSError(f"正式版本內含 symbolic link：{path}")
            if not path.is_file():
                continue
            self._assert_contained(version_path, path)
            relative_name = path.relative_to(version_path).as_posix()
            bundle[relative_name] = self._read_bytes(path)
        return bundle

    @staticmethod
    def _build_snapshot(validated: dict) -> OfficialEstimateSnapshot:
        manifest = validated["manifest"]
        inputs = validated["inputs"]
        batch = inputs["batch"]
        scenario_lookup = {
            scenario["scenario_id"]: scenario for scenario in batch["scenarios"]
        }
        scenarios = tuple(
            OfficialScenarioMetadata(
                scenario_id=scenario_id,
                name=scenario_lookup[scenario_id]["name"],
                order=int(scenario_lookup[scenario_id]["order"]),
            )
            for scenario_id in manifest["official_scenario_ids"]
        )
        metadata = OfficialVersionMetadata(
            version_id=manifest["version_id"],
            previous_official_version_id=manifest["previous_official_version_id"],
            derived_from_official_version_id=manifest[
                "derived_from_official_version_id"
            ],
            batch_id=manifest["batch_id"],
            batch_name=manifest["batch_name"],
            annual_data_version_id=manifest["annual_data_version_id"],
            created_at=manifest["created_at"],
            operator_display_name=manifest["operator_display_name"],
            note=manifest["note"],
            display_start_date=batch["display_start_date"],
            projection_start_date=batch["projection_start_date"],
            projection_end_date=batch["projection_end_date"],
            scenarios=scenarios,
        )
        return OfficialEstimateSnapshot(
            metadata=metadata,
            manifest=manifest,
            inputs=inputs,
            official_scenario_ids=tuple(manifest["official_scenario_ids"]),
            scenario_summaries=tuple(validated["scenario_summaries"]),
            daily_results=tuple(validated["daily_results"]),
            committed=validated["committed"],
        )

    def _assert_contained(self, base: Path, candidate: Path) -> None:
        try:
            base_resolved = base.resolve(strict=False)
            candidate_resolved = candidate.resolve(strict=False)
            if os.path.commonpath((str(base_resolved), str(candidate_resolved))) != str(
                base_resolved
            ):
                raise ValueError
        except (OSError, ValueError) as exc:
            raise OSError("正式版本路徑可能跳脫 versions 目錄") from exc

    def _raise_bundle_failure(
        self,
        version_id: str,
        referenced_by: str | None,
        cause_code: OfficialEstimateLoadErrorCode,
        detail: str,
        evidence_path: Path,
        cause: Exception,
    ) -> NoReturn:
        if referenced_by is None:
            raise OfficialEstimateLoadError(
                cause_code,
                detail,
                evidence_path=evidence_path,
                version_id=version_id,
            ) from cause
        raise OfficialEstimateLoadError(
            OfficialEstimateLoadErrorCode.BROKEN_HISTORY_LINK,
            (
                f"正式 history link 已中斷：{referenced_by} 的 "
                f"previous_official_version_id 指向 {version_id}；{detail}"
            ),
            evidence_path=evidence_path,
            version_id=version_id,
            referenced_by_version_id=referenced_by,
            cause_code=cause_code,
        ) from cause


def load_official_current(
    root: str | os.PathLike[str], *, read_bytes: ReadBytes | None = None
) -> OfficialCurrentSnapshot:
    """Convenience wrapper for :meth:`OfficialEstimateLoader.load_current`."""

    return OfficialEstimateLoader(root, read_bytes=read_bytes).load_current()


def load_official_history(
    root: str | os.PathLike[str], *, read_bytes: ReadBytes | None = None
) -> OfficialEstimateHistory:
    """Convenience wrapper for :meth:`OfficialEstimateLoader.load_history`."""

    return OfficialEstimateLoader(root, read_bytes=read_bytes).load_history()


def load_official_history_version(
    root: str | os.PathLike[str],
    version_id: str,
    *,
    read_bytes: ReadBytes | None = None,
) -> OfficialEstimateSnapshot:
    """Load a complete version only after proving current-chain membership."""

    return OfficialEstimateLoader(root, read_bytes=read_bytes).load_history_version(
        version_id
    )
