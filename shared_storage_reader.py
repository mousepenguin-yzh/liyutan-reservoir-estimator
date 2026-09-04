"""Safe, read-only loading for the Li-Yu-Tan shared data root.

The reader owns filesystem access but has no Streamlit or water-balance
dependency.  It reads immutable version bundles into memory and delegates all
artifact validation to :mod:`shared_storage_schema`.
"""

from __future__ import annotations

import datetime as dt
import os
import stat as stat_module
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping

from shared_storage_schema import (
    ANNUAL_REQUIRED_FILES,
    OFFICIAL_REQUIRED_FILES,
    StorageValidationError,
    deserialize_json,
    validate_annual_bundle,
    validate_annual_current,
    validate_official_bundle,
    validate_official_current,
    validate_safe_id,
    validate_system,
)


SHARED_ROOT_ENV = "LIYUTAN_SHARED_ROOT"
ENABLE_SHARED_STORAGE_ENV = "LIYUTAN_ENABLE_SHARED_STORAGE"


class StorageErrorCode(str, Enum):
    NOT_CONFIGURED = "not_configured"
    ROOT_NOT_FOUND = "root_not_found"
    ROOT_NOT_DIRECTORY = "root_not_directory"
    PERMISSION_DENIED = "permission_denied"
    SYSTEM_MISSING = "system_missing"
    SYSTEM_INVALID = "system_invalid"
    RESERVOIR_MISMATCH = "reservoir_mismatch"
    ANNUAL_CURRENT_MISSING = "annual_current_missing"
    CURRENT_INVALID = "current_invalid"
    UNSAFE_VERSION_ID = "unsafe_version_id"
    VERSION_DIRECTORY_MISSING = "version_directory_missing"
    REQUIRED_FILE_MISSING = "required_file_missing"
    JSON_INVALID = "json_invalid"
    CSV_INVALID = "csv_invalid"
    MANIFEST_FILE_LIST_INVALID = "manifest_file_list_invalid"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    VERSION_ID_MISMATCH = "version_id_mismatch"
    CURRENT_CHANGED = "current_changed"
    READ_FAILED = "read_failed"


@dataclass(frozen=True)
class StorageError:
    code: StorageErrorCode
    message: str
    detail: str | None = None


@dataclass(frozen=True)
class AnnualDataSnapshot:
    current: dict
    version: dict
    hydrology: tuple[dict, ...]
    outflow_demand: tuple[dict, ...]
    reservoir_parameters: dict
    parameter_metadata: dict[str, dict]


@dataclass(frozen=True)
class OfficialEstimateSummary:
    current: dict
    version_id: str
    batch_id: str
    batch_name: str
    annual_data_version_id: str
    created_at: str
    operator_display_name: str
    note: str


@dataclass(frozen=True)
class SharedStorageResult:
    ok: bool
    root: Path | None
    read_at: str
    system: dict | None = None
    annual: AnnualDataSnapshot | None = None
    official: OfficialEstimateSummary | None = None
    error: StorageError | None = None

    @property
    def has_official_estimate(self) -> bool:
        return self.ok and self.official is not None


class DataSourceMode(str, Enum):
    COMPATIBILITY = "compatibility"
    OFFICIAL = "official"
    UNAVAILABLE = "unavailable"
    BUILTIN_FALLBACK = "builtin_fallback"
    SESSION_UPLOAD = "session_upload"


@dataclass(frozen=True)
class DataSourceDecision:
    mode: DataSourceMode
    can_calculate: bool
    shared_storage_readable: bool
    formal_write_available: bool
    label: str

    @property
    def formal_operations_available(self) -> bool:
        """Legacy flag; 2-4C1 is not an enabled end-user write workflow."""
        return False


def shared_storage_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether the explicit shared-storage feature flag is enabled."""
    env = os.environ if environ is None else environ
    return env.get(ENABLE_SHARED_STORAGE_ENV) == "1"


def compatibility_data_source(*, session_upload: bool = False) -> DataSourceDecision:
    """Describe the transitional pre-shared-storage application mode."""
    if session_upload:
        return DataSourceDecision(
            DataSourceMode.SESSION_UPLOAD,
            True,
            False,
            False,
            "工作階段上傳資料（非正式）",
        )
    return DataSourceDecision(
        DataSourceMode.COMPATIBILITY,
        True,
        False,
        False,
        "內建年度資料（相容模式／過渡用途）",
    )


def decide_data_source(
    result: SharedStorageResult,
    *,
    builtin_fallback_requested: bool = False,
    session_upload: bool = False,
) -> DataSourceDecision:
    """Select a source without ever silently falling back to embedded data."""
    if session_upload and (result.ok or builtin_fallback_requested):
        return DataSourceDecision(
            DataSourceMode.SESSION_UPLOAD,
            True,
            result.ok,
            False,
            "工作階段上傳資料（非正式）",
        )
    if result.ok:
        return DataSourceDecision(
            DataSourceMode.OFFICIAL,
            True,
            True,
            False,
            "共享正式年度資料",
        )
    if builtin_fallback_requested:
        return DataSourceDecision(
            DataSourceMode.BUILTIN_FALLBACK,
            True,
            False,
            False,
            "內建備援資料（非正式）",
        )
    return DataSourceDecision(
        DataSourceMode.UNAVAILABLE,
        False,
        False,
        False,
        "正式資料來源不可用",
    )


class _ReadFailure(Exception):
    def __init__(self, error: StorageError):
        super().__init__(error.message)
        self.error = error


class _CurrentChanged(Exception):
    pass


class SharedStorageReader:
    """Load one internally consistent snapshot from a shared root.

    ``read_bytes`` is injectable solely to make permission and concurrent
    pointer-change behavior testable without touching machine permissions.
    """

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        read_bytes: Callable[[Path], bytes] | None = None,
        max_attempts: int = 2,
    ) -> None:
        env = os.environ if environ is None else environ
        configured = root if root is not None else env.get(SHARED_ROOT_ENV)
        self.root = Path(configured) if configured is not None and str(configured).strip() else None
        self._read_bytes_impl = read_bytes or (lambda path: path.read_bytes())
        self.max_attempts = max(1, int(max_attempts))

    def load(self) -> SharedStorageResult:
        read_at = dt.datetime.now(dt.timezone.utc).isoformat()
        if self.root is None:
            return self._failure(
                read_at,
                StorageErrorCode.NOT_CONFIGURED,
                "尚未設定共享資料來源。請由系統維護人員設定 LIYUTAN_SHARED_ROOT。",
            )
        try:
            self._validate_root()
            for attempt in range(self.max_attempts):
                try:
                    return self._load_once(read_at)
                except _CurrentChanged:
                    if attempt + 1 == self.max_attempts:
                        raise _ReadFailure(
                            StorageError(
                                StorageErrorCode.CURRENT_CHANGED,
                                "共享資料版本在讀取期間發生變動，未載入任何混合版本資料。請稍後重試；若持續發生，請聯絡系統維護人員。",
                            )
                        )
        except _ReadFailure as exc:
            return SharedStorageResult(False, self.root, read_at, error=exc.error)
        except PermissionError as exc:
            return self._failure(
                read_at,
                StorageErrorCode.PERMISSION_DENIED,
                "沒有讀取共享資料來源的權限。請聯絡系統維護人員或資訊單位確認權限。",
                str(exc),
            )
        except OSError as exc:
            return self._failure(
                read_at,
                StorageErrorCode.READ_FAILED,
                "讀取共享資料來源時發生系統錯誤。請確認磁碟連線，並聯絡系統維護人員。",
                str(exc),
            )
        raise AssertionError("unreachable")

    def _failure(
        self,
        read_at: str,
        code: StorageErrorCode,
        message: str,
        detail: str | None = None,
    ) -> SharedStorageResult:
        return SharedStorageResult(False, self.root, read_at, error=StorageError(code, message, detail))

    def _validate_root(self) -> None:
        assert self.root is not None
        try:
            root_stat = self.root.stat()
        except FileNotFoundError as exc:
            raise _ReadFailure(
                StorageError(
                    StorageErrorCode.ROOT_NOT_FOUND,
                    "共享資料路徑或磁碟機不存在。請確認網路磁碟已連線，並聯絡系統維護人員。",
                )
            ) from exc
        except PermissionError as exc:
            raise _ReadFailure(
                StorageError(
                    StorageErrorCode.PERMISSION_DENIED,
                    "沒有讀取共享資料來源的權限。請聯絡系統維護人員或資訊單位確認權限。",
                )
            ) from exc
        if not stat_module.S_ISDIR(root_stat.st_mode):
            raise _ReadFailure(
                StorageError(
                    StorageErrorCode.ROOT_NOT_DIRECTORY,
                    "設定的共享資料來源不是資料夾。請聯絡系統維護人員檢查設定。",
                )
            )

    def _load_once(self, read_at: str) -> SharedStorageResult:
        assert self.root is not None
        system_path = self.root / "system.json"
        system_bytes = self._read_file(system_path, StorageErrorCode.SYSTEM_MISSING, "system.json 不存在")
        try:
            system = validate_system(deserialize_json(system_bytes))
        except StorageValidationError as exc:
            code = (
                StorageErrorCode.RESERVOIR_MISMATCH
                if "reservoir_id 必須是 liyutan" in str(exc)
                else StorageErrorCode.SYSTEM_INVALID
            )
            raise self._invalid(code, "system.json 格式或內容錯誤", exc) from exc

        annual_pointer_path = self.root / "annual-data" / "current.json"
        annual_pointer_bytes = self._read_file(
            annual_pointer_path,
            StorageErrorCode.ANNUAL_CURRENT_MISSING,
            "年度資料 current pointer 不存在",
        )
        annual_current = self._validate_current(annual_pointer_bytes, annual=True)
        annual_bundle = self._read_version_bundle(
            "annual-data",
            annual_current["current_version_id"],
            ANNUAL_REQUIRED_FILES,
        )
        annual_data = self._validate_bundle(annual_bundle, annual=True)
        if annual_data["version"]["version_id"] != annual_current["current_version_id"]:
            raise _ReadFailure(
                StorageError(
                    StorageErrorCode.VERSION_ID_MISMATCH,
                    "年度資料版本 ID 與 current pointer 不一致。請聯絡系統維護人員修復共享資料。",
                )
            )

        official_pointer_path = self.root / "official-estimates" / "current.json"
        official_pointer_bytes = self._read_optional_file(official_pointer_path)
        official_summary = None
        if official_pointer_bytes is not None:
            official_current = self._validate_current(official_pointer_bytes, annual=False)
            official_bundle = self._read_version_bundle(
                "official-estimates",
                official_current["current_version_id"],
                OFFICIAL_REQUIRED_FILES,
            )
            official_data = self._validate_bundle(official_bundle, annual=False)
            manifest = official_data["manifest"]
            if manifest["version_id"] != official_current["current_version_id"]:
                raise _ReadFailure(
                    StorageError(
                        StorageErrorCode.VERSION_ID_MISMATCH,
                        "正式推估版本 ID 與 current pointer 不一致。請聯絡系統維護人員修復共享資料。",
                    )
                )
            official_summary = OfficialEstimateSummary(
                current=official_current,
                version_id=manifest["version_id"],
                batch_id=manifest["batch_id"],
                batch_name=manifest["batch_name"],
                annual_data_version_id=manifest["annual_data_version_id"],
                created_at=manifest["created_at"],
                operator_display_name=manifest["operator_display_name"],
                note=manifest["note"],
            )

        # Compare the exact pointer bytes after every artifact has been read.
        # Missing official current is a legitimate snapshot state, but its
        # appearance during this read still triggers a clean retry.
        try:
            annual_after = self._read_file(
                annual_pointer_path,
                StorageErrorCode.ANNUAL_CURRENT_MISSING,
                "年度資料 current pointer 不存在",
            )
        except _ReadFailure as exc:
            if exc.error.code is StorageErrorCode.ANNUAL_CURRENT_MISSING:
                raise _CurrentChanged from exc
            raise
        official_after = self._read_optional_file(official_pointer_path)
        if annual_after != annual_pointer_bytes or official_after != official_pointer_bytes:
            raise _CurrentChanged

        annual_snapshot = AnnualDataSnapshot(
            current=annual_current,
            version=annual_data["version"],
            hydrology=tuple(annual_data["hydrology"]),
            outflow_demand=tuple(annual_data["outflow_demand"]),
            reservoir_parameters=annual_data["reservoir_parameters"],
            parameter_metadata=annual_data["parameter_metadata"],
        )
        return SharedStorageResult(
            True,
            self.root,
            read_at,
            system=system,
            annual=annual_snapshot,
            official=official_summary,
        )

    def _validate_current(self, data: bytes, *, annual: bool) -> dict:
        label = "年度資料" if annual else "正式推估"
        try:
            parsed = deserialize_json(data)
            return validate_annual_current(parsed) if annual else validate_official_current(parsed)
        except StorageValidationError as exc:
            code = (
                StorageErrorCode.UNSAFE_VERSION_ID
                if "current_version_id" in str(exc) and "只能包含" in str(exc)
                else StorageErrorCode.CURRENT_INVALID
            )
            raise self._invalid(code, f"{label} current pointer 內容錯誤", exc) from exc

    def _read_version_bundle(
        self,
        category: str,
        version_id: str,
        required_files: tuple[str, ...],
    ) -> dict[str, bytes]:
        try:
            safe_id = validate_safe_id(version_id, "current_version_id")
        except StorageValidationError as exc:
            raise self._invalid(StorageErrorCode.UNSAFE_VERSION_ID, "版本 ID 不安全", exc) from exc
        versions_root = self.root / category / "versions"  # type: ignore[operator]
        version_dir = versions_root / safe_id
        self._assert_contained(versions_root, version_dir)
        try:
            if not version_dir.is_dir():
                raise _ReadFailure(
                    StorageError(
                        StorageErrorCode.VERSION_DIRECTORY_MISSING,
                        f"current pointer 指向的版本資料夾不存在：{safe_id}。請聯絡系統維護人員。",
                    )
                )
        except PermissionError as exc:
            raise _ReadFailure(
                StorageError(
                    StorageErrorCode.PERMISSION_DENIED,
                    "沒有讀取版本資料夾的權限。請聯絡系統維護人員或資訊單位確認權限。",
                )
            ) from exc
        bundle = {}
        try:
            paths = tuple(path for path in version_dir.rglob("*") if path.is_file())
        except PermissionError as exc:
            raise _ReadFailure(
                StorageError(
                    StorageErrorCode.PERMISSION_DENIED,
                    "沒有讀取版本資料夾的權限。請聯絡系統維護人員或資訊單位確認權限。",
                )
            ) from exc
        for path in paths:
            self._assert_contained(version_dir, path)
            filename = path.relative_to(version_dir).as_posix()
            bundle[filename] = self._read_file(
                path,
                StorageErrorCode.REQUIRED_FILE_MISSING,
                f"版本 {safe_id} 缺少必要檔案：{filename}",
            )
        for filename in required_files:
            if filename not in bundle:
                raise _ReadFailure(
                    StorageError(
                        StorageErrorCode.REQUIRED_FILE_MISSING,
                        f"版本 {safe_id} 缺少必要檔案：{filename}",
                    )
                )
        return bundle

    def _assert_contained(self, base: Path, candidate: Path) -> None:
        try:
            base_resolved = base.resolve(strict=False)
            candidate_resolved = candidate.resolve(strict=False)
            if os.path.commonpath((str(base_resolved), str(candidate_resolved))) != str(base_resolved):
                raise ValueError
        except (OSError, ValueError) as exc:
            raise _ReadFailure(
                StorageError(
                    StorageErrorCode.UNSAFE_VERSION_ID,
                    "版本路徑可能跳脫共享資料版本目錄，已拒絕讀取。請聯絡系統維護人員。",
                )
            ) from exc

    def _validate_bundle(self, bundle: dict[str, bytes], *, annual: bool) -> dict:
        label = "年度資料版本" if annual else "正式推估版本"
        try:
            return validate_annual_bundle(bundle) if annual else validate_official_bundle(bundle)
        except StorageValidationError as exc:
            detail = str(exc)
            if "檔案集合" in detail or "未知檔名" in detail or "恰好包含正式資料檔" in detail:
                code = StorageErrorCode.MANIFEST_FILE_LIST_INVALID
            elif "checksum" in detail:
                code = StorageErrorCode.CHECKSUM_MISMATCH
            elif "CSV" in detail or ".csv" in detail:
                code = StorageErrorCode.CSV_INVALID
            elif "JSON 格式" in detail or "JSON 必須使用 UTF-8" in detail:
                code = StorageErrorCode.JSON_INVALID
            elif "version_id" in detail and "只能包含" in detail:
                code = StorageErrorCode.UNSAFE_VERSION_ID
            else:
                code = StorageErrorCode.JSON_INVALID
            raise self._invalid(code, f"{label}驗證失敗", exc) from exc

    def _read_file(self, path: Path, missing_code: StorageErrorCode, missing_message: str) -> bytes:
        try:
            return self._read_bytes_impl(path)
        except FileNotFoundError as exc:
            raise _ReadFailure(
                StorageError(missing_code, f"{missing_message}。請聯絡系統維護人員處理。")
            ) from exc
        except PermissionError as exc:
            raise _ReadFailure(
                StorageError(
                    StorageErrorCode.PERMISSION_DENIED,
                    f"沒有權限讀取 {path.name}。請聯絡系統維護人員或資訊單位確認權限。",
                )
            ) from exc
        except OSError as exc:
            raise _ReadFailure(
                StorageError(
                    StorageErrorCode.READ_FAILED,
                    f"讀取 {path.name} 時發生錯誤。請確認磁碟連線並聯絡系統維護人員。",
                    str(exc),
                )
            ) from exc

    def _read_optional_file(self, path: Path) -> bytes | None:
        try:
            return self._read_bytes_impl(path)
        except FileNotFoundError:
            return None
        except PermissionError as exc:
            raise _ReadFailure(
                StorageError(
                    StorageErrorCode.PERMISSION_DENIED,
                    f"沒有權限讀取 {path.name}。請聯絡系統維護人員或資訊單位確認權限。",
                )
            ) from exc
        except OSError as exc:
            raise _ReadFailure(
                StorageError(
                    StorageErrorCode.READ_FAILED,
                    f"讀取 {path.name} 時發生錯誤。請確認磁碟連線並聯絡系統維護人員。",
                    str(exc),
                )
            ) from exc

    @staticmethod
    def _invalid(code: StorageErrorCode, prefix: str, exc: Exception) -> _ReadFailure:
        return _ReadFailure(
            StorageError(
                code,
                f"{prefix}：{exc}。正式資料未載入，請聯絡系統維護人員。",
            )
        )


def load_shared_storage(
    root: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    read_bytes: Callable[[Path], bytes] | None = None,
    max_attempts: int = 2,
) -> SharedStorageResult:
    return SharedStorageReader(
        root,
        environ=environ,
        read_bytes=read_bytes,
        max_attempts=max_attempts,
    ).load()
