import copy
from pathlib import Path

import pytest

from shared_storage_reader import (
    DataSourceMode,
    SHARED_ROOT_ENV,
    SharedStorageReader,
    StorageErrorCode,
    decide_data_source,
    load_shared_storage,
)
from shared_storage_schema import (
    ANNUAL_CURRENT_SCHEMA,
    OFFICIAL_CURRENT_SCHEMA,
    SCHEMA_VERSION,
    SHARED_ROOT_SCHEMA,
    deserialize_json,
    serialize_csv,
    serialize_json,
    sha256_bytes,
    HYDROLOGY_COLUMNS,
)
from test_shared_storage_schema import (
    _annual_bundle,
    _official_bundle,
    _repack_annual_data_file,
    synthetic_hydrology_rows,
)


ANNUAL_ID = "annual-synthetic-2027"
OFFICIAL_ID = "estimate-synthetic-1"


def _write_bundle(directory: Path, bundle: dict[str, bytes]) -> None:
    directory.mkdir(parents=True)
    for name, data in bundle.items():
        (directory / name).write_bytes(data)


def _build_root(tmp_path: Path, *, official: bool = False, annual_bundle=None) -> Path:
    root = tmp_path / "shared-root"
    root.mkdir(parents=True)
    (root / "system.json").write_bytes(
        serialize_json(
            {
                "schema": SHARED_ROOT_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "reservoir_id": "liyutan",
                "display_name": "鯉魚潭水庫",
            }
        )
    )
    annual_current = {
        "schema": ANNUAL_CURRENT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "current_version_id": ANNUAL_ID,
        "previous_version_id": None,
        "updated_at": "2026-12-15T02:35:00Z",
        "operator_display_name": "測試操作人",
    }
    (root / "annual-data").mkdir()
    (root / "annual-data" / "current.json").write_bytes(serialize_json(annual_current))
    _write_bundle(
        root / "annual-data" / "versions" / ANNUAL_ID,
        annual_bundle or _annual_bundle(),
    )
    if official:
        official_current = {
            "schema": OFFICIAL_CURRENT_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "revision": 1,
            "current_version_id": OFFICIAL_ID,
            "previous_version_id": None,
            "updated_at": "2026-12-15T02:47:00Z",
            "operator_display_name": "測試操作人",
        }
        (root / "official-estimates").mkdir()
        (root / "official-estimates" / "current.json").write_bytes(serialize_json(official_current))
        _write_bundle(
            root / "official-estimates" / "versions" / OFFICIAL_ID,
            _official_bundle(),
        )
    return root


def _rewrite_annual_manifest(root: Path, mutate) -> None:
    version_dir = root / "annual-data" / "versions" / ANNUAL_ID
    version = deserialize_json((version_dir / "version.json").read_bytes())
    mutate(version)
    version_bytes = serialize_json(version)
    (version_dir / "version.json").write_bytes(version_bytes)
    committed = deserialize_json((version_dir / "COMMITTED.json").read_bytes())
    committed["manifest_sha256"] = sha256_bytes(version_bytes)
    (version_dir / "COMMITTED.json").write_bytes(serialize_json(committed))


def test_valid_annual_version_loads_complete_snapshot(tmp_path):
    result = load_shared_storage(_build_root(tmp_path))

    assert result.ok
    assert result.system["reservoir_id"] == "liyutan"
    assert result.annual.version["version_id"] == ANNUAL_ID
    assert result.annual.version["applicable_year"] == 2027
    assert len(result.annual.hydrology) == 36
    assert len(result.annual.outflow_demand) == 36
    assert result.annual.reservoir_parameters["shilin_diversion_limit_cms"] == 33.0


def test_environment_variable_configures_shared_root(tmp_path):
    root = _build_root(tmp_path)
    result = load_shared_storage(environ={SHARED_ROOT_ENV: str(root)})
    assert result.ok
    assert result.root == root


def test_official_estimate_current_and_summary_load(tmp_path):
    result = load_shared_storage(_build_root(tmp_path, official=True))

    assert result.ok and result.has_official_estimate
    assert result.official.version_id == OFFICIAL_ID
    assert result.official.batch_name == "合成正式推估"
    assert result.official.created_at == "2026-12-15T02:45:00Z"


def test_missing_official_current_is_normal_no_estimate_state(tmp_path):
    result = load_shared_storage(_build_root(tmp_path))

    assert result.ok
    assert result.official is None
    assert not result.has_official_estimate
    assert result.error is None


def test_invalid_official_current_is_not_misreported_as_no_estimate(tmp_path):
    root = _build_root(tmp_path, official=True)
    (root / "official-estimates" / "current.json").write_bytes(b"{")
    result = load_shared_storage(root)
    assert not result.ok
    assert result.error.code is StorageErrorCode.CURRENT_INVALID


def test_unconfigured_path_does_not_guess_a_location():
    result = load_shared_storage(environ={})
    assert not result.ok
    assert result.root is None
    assert result.error.code is StorageErrorCode.NOT_CONFIGURED


def test_nonexistent_root(tmp_path):
    result = load_shared_storage(tmp_path / "missing")
    assert not result.ok
    assert result.error.code is StorageErrorCode.ROOT_NOT_FOUND


def test_configured_root_must_be_a_directory(tmp_path):
    root_file = tmp_path / "not-a-directory"
    root_file.write_text("synthetic", encoding="utf-8")
    result = load_shared_storage(root_file)
    assert not result.ok
    assert result.error.code is StorageErrorCode.ROOT_NOT_DIRECTORY


def test_permission_error_is_reported_without_changing_permissions(tmp_path):
    root = _build_root(tmp_path)

    def denied(path):
        raise PermissionError("synthetic denial")

    result = SharedStorageReader(root, read_bytes=denied).load()
    assert not result.ok
    assert result.error.code is StorageErrorCode.PERMISSION_DENIED
    assert "權限" in result.error.message


def test_missing_system_file_and_invalid_system_json(tmp_path):
    missing_root = _build_root(tmp_path / "missing-case")
    (missing_root / "system.json").unlink()
    missing = load_shared_storage(missing_root)
    assert missing.error.code is StorageErrorCode.SYSTEM_MISSING

    invalid_root = _build_root(tmp_path / "invalid-case")
    (invalid_root / "system.json").write_bytes(b"{")
    invalid = load_shared_storage(invalid_root)
    assert invalid.error.code is StorageErrorCode.SYSTEM_INVALID


def test_wrong_reservoir_id_is_rejected(tmp_path):
    root = _build_root(tmp_path)
    system = deserialize_json((root / "system.json").read_bytes())
    system["reservoir_id"] = "deji"
    (root / "system.json").write_bytes(serialize_json(system))

    result = load_shared_storage(root)
    assert result.error.code is StorageErrorCode.RESERVOIR_MISMATCH


@pytest.mark.parametrize("mode", ["missing", "invalid"])
def test_annual_current_missing_or_invalid(tmp_path, mode):
    root = _build_root(tmp_path)
    pointer = root / "annual-data" / "current.json"
    pointer.unlink() if mode == "missing" else pointer.write_bytes(b"not-json")

    result = load_shared_storage(root)
    expected = (
        StorageErrorCode.ANNUAL_CURRENT_MISSING
        if mode == "missing"
        else StorageErrorCode.CURRENT_INVALID
    )
    assert result.error.code is expected


def test_pointed_version_directory_must_exist(tmp_path):
    root = _build_root(tmp_path)
    current_path = root / "annual-data" / "current.json"
    current = deserialize_json(current_path.read_bytes())
    current["current_version_id"] = "annual-does-not-exist"
    current_path.write_bytes(serialize_json(current))

    result = load_shared_storage(root)
    assert result.error.code is StorageErrorCode.VERSION_DIRECTORY_MISSING


def test_missing_required_version_file(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "versions" / ANNUAL_ID / "outflow_demand.csv").unlink()

    result = load_shared_storage(root)
    assert result.error.code is StorageErrorCode.REQUIRED_FILE_MISSING


def test_damaged_json_inside_bundle(tmp_path):
    bundle = _annual_bundle(parameters={"broken": True})
    # Replace with syntactically invalid JSON while keeping the checksum and
    # manifest checksum internally current, so JSON validation is reached.
    bundle = _repack_annual_data_file(bundle, "reservoir_parameters.json", b"{")
    result = load_shared_storage(_build_root(tmp_path, annual_bundle=bundle))
    assert result.error.code is StorageErrorCode.JSON_INVALID


def test_invalid_csv_schema_or_contents(tmp_path):
    rows = synthetic_hydrology_rows()
    rows[0]["q05_cms"] = -1
    bad_csv = serialize_csv(rows, HYDROLOGY_COLUMNS)
    bundle = _repack_annual_data_file(_annual_bundle(), "hydrology_q.csv", bad_csv)

    result = load_shared_storage(_build_root(tmp_path, annual_bundle=bundle))
    assert result.error.code is StorageErrorCode.CSV_INVALID


def test_checksum_mismatch(tmp_path):
    root = _build_root(tmp_path)
    file_path = root / "annual-data" / "versions" / ANNUAL_ID / "hydrology_q.csv"
    file_path.write_bytes(file_path.read_bytes() + b"\n")

    result = load_shared_storage(root)
    assert result.error.code is StorageErrorCode.CHECKSUM_MISMATCH


@pytest.mark.parametrize("mode", ["extra", "missing"])
def test_manifest_must_list_exact_files(tmp_path, mode):
    root = _build_root(tmp_path)

    def mutate(version):
        if mode == "extra":
            version["files"]["extra.json"] = {"sha256": "0" * 64}
        else:
            version["files"].pop("outflow_demand.csv")

    _rewrite_annual_manifest(root, mutate)
    result = load_shared_storage(root)
    assert result.error.code is StorageErrorCode.MANIFEST_FILE_LIST_INVALID


@pytest.mark.parametrize("unsafe_id", ["../escape", r"..\escape", r"C:\escape", "/absolute"])
def test_unsafe_current_version_id_and_path_escape_are_rejected(tmp_path, unsafe_id):
    root = _build_root(tmp_path)
    current_path = root / "annual-data" / "current.json"
    current = deserialize_json(current_path.read_bytes())
    current["current_version_id"] = unsafe_id
    current_path.write_bytes(serialize_json(current))

    result = load_shared_storage(root)
    assert result.error.code is StorageErrorCode.UNSAFE_VERSION_ID


def test_version_manifest_id_must_match_current_and_directory(tmp_path):
    bundle = _annual_bundle(version_mutator=lambda version: version.update(version_id="annual-other"))
    result = load_shared_storage(_build_root(tmp_path, annual_bundle=bundle))
    assert result.error.code is StorageErrorCode.VERSION_ID_MISMATCH


def test_current_change_during_read_retries_then_reports_without_mixing(tmp_path):
    root = _build_root(tmp_path)
    pointer = root / "annual-data" / "current.json"
    calls = 0

    def changing_reader(path):
        nonlocal calls
        data = path.read_bytes()
        if path == pointer:
            calls += 1
            parsed = deserialize_json(data)
            parsed["revision"] = calls
            return serialize_json(parsed)
        return data

    result = SharedStorageReader(root, read_bytes=changing_reader, max_attempts=2).load()
    assert not result.ok
    assert result.annual is None
    assert result.error.code is StorageErrorCode.CURRENT_CHANGED
    assert calls == 4


def test_current_disappearing_during_read_is_reported_as_change(tmp_path):
    root = _build_root(tmp_path)
    pointer = root / "annual-data" / "current.json"
    calls = 0

    def disappearing_reader(path):
        nonlocal calls
        if path == pointer:
            calls += 1
            if calls % 2 == 0:
                raise FileNotFoundError(path)
        return path.read_bytes()

    result = SharedStorageReader(root, read_bytes=disappearing_reader, max_attempts=2).load()
    assert not result.ok
    assert result.error.code is StorageErrorCode.CURRENT_CHANGED
    assert calls == 4


def test_reader_never_writes_to_shared_root(tmp_path, monkeypatch):
    root = _build_root(tmp_path)

    def unexpected_write(*args, **kwargs):
        raise AssertionError("reader attempted a write")

    monkeypatch.setattr(Path, "write_bytes", unexpected_write)
    result = load_shared_storage(root)
    assert result.ok


def test_failed_load_never_automatically_uses_builtin_data():
    failed = load_shared_storage(environ={})
    decision = decide_data_source(failed)
    assert decision.mode is DataSourceMode.UNAVAILABLE
    assert not decision.can_calculate
    assert not decision.formal_operations_available
    stale_upload = decide_data_source(failed, session_upload=True)
    assert stale_upload.mode is DataSourceMode.UNAVAILABLE
    assert not stale_upload.can_calculate


def test_builtin_fallback_requires_explicit_choice_and_remains_nonofficial():
    failed = load_shared_storage(environ={})
    decision = decide_data_source(failed, builtin_fallback_requested=True)
    assert decision.mode is DataSourceMode.BUILTIN_FALLBACK
    assert decision.can_calculate
    assert not decision.formal_operations_available

    uploaded = decide_data_source(
        failed, builtin_fallback_requested=True, session_upload=True
    )
    assert uploaded.mode is DataSourceMode.SESSION_UPLOAD
    assert uploaded.can_calculate
    assert not uploaded.formal_operations_available


def test_session_upload_is_always_nonofficial_even_when_shared_data_is_valid(tmp_path):
    loaded = load_shared_storage(_build_root(tmp_path))
    decision = decide_data_source(loaded, session_upload=True)
    assert decision.mode is DataSourceMode.SESSION_UPLOAD
    assert decision.can_calculate
    assert not decision.formal_operations_available
