import copy

import pytest

from shared_storage_reader import (
    AnnualDataVersionLoadError,
    StorageErrorCode,
    load_annual_data_version,
)
from test_official_estimate_loader import _filesystem_snapshot
from test_shared_storage_reader import ANNUAL_ID, _build_root, _write_bundle
from test_shared_storage_schema import _annual_bundle


def test_exact_immutable_annual_version_loads_without_current_membership(tmp_path):
    root = _build_root(tmp_path)
    historical_id = "annual-historical-a"
    bundle = _annual_bundle(
        version_mutator=lambda value: value.update(version_id=historical_id)
    )
    _write_bundle(root / "annual-data" / "versions" / historical_id, bundle)
    before = _filesystem_snapshot(root)

    snapshot = load_annual_data_version(root, historical_id)

    assert snapshot.version["version_id"] == historical_id
    assert snapshot.current == {}
    assert len(snapshot.hydrology) == 36
    assert len(snapshot.outflow_demand) == 36
    assert _filesystem_snapshot(root) == before


def test_exact_annual_loader_does_not_silently_fall_back_to_current(tmp_path):
    root = _build_root(tmp_path)

    with pytest.raises(AnnualDataVersionLoadError) as captured:
        load_annual_data_version(root, "annual-missing")

    assert captured.value.code is StorageErrorCode.VERSION_DIRECTORY_MISSING
    assert ANNUAL_ID not in captured.value.message


def test_exact_annual_loader_rejects_unsafe_id_before_path_escape(tmp_path):
    root = _build_root(tmp_path)

    with pytest.raises(AnnualDataVersionLoadError) as captured:
        load_annual_data_version(root, "../outside")

    assert captured.value.code is StorageErrorCode.UNSAFE_VERSION_ID


def test_exact_annual_loader_rejects_corrupt_bundle_without_repair(tmp_path):
    root = _build_root(tmp_path)
    target = root / "annual-data" / "versions" / ANNUAL_ID / "hydrology_q.csv"
    before_current = (root / "annual-data" / "current.json").read_bytes()
    target.write_bytes(target.read_bytes() + b"\ncorrupt")

    with pytest.raises(AnnualDataVersionLoadError) as captured:
        load_annual_data_version(root, ANNUAL_ID)

    assert captured.value.code is StorageErrorCode.CHECKSUM_MISMATCH
    assert (root / "annual-data" / "current.json").read_bytes() == before_current
