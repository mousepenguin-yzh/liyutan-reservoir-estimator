import os
from pathlib import Path

import pytest

from official_estimate_loader import (
    OfficialEstimateLoadError,
    OfficialEstimateLoadErrorCode,
    OfficialEstimateLoader,
    load_official_current,
    load_official_history,
    load_official_history_version,
)
from shared_storage_schema import (
    DAILY_RESULT_COLUMNS,
    OFFICIAL_CURRENT_SCHEMA,
    SCHEMA_VERSION,
    SHARED_ROOT_SCHEMA,
    SUMMARY_COLUMNS,
    deserialize_csv,
    deserialize_json,
    serialize_csv,
    serialize_json,
    sha256_bytes,
    validate_official_bundle,
)
from test_shared_storage_schema import _official_bundle


def _write_bundle(directory: Path, bundle: dict[str, bytes]) -> None:
    directory.mkdir(parents=True)
    for name, data in bundle.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _versioned_bundle(
    version_id: str,
    previous_version_id: str | None,
    *,
    created_at: str = "2026-12-15T02:45:00Z",
    operator: str = "測試操作人",
    note: str = "合成正式推估",
) -> dict[str, bytes]:
    source = _official_bundle()
    inputs_bytes = source["inputs.json"]
    summaries = deserialize_csv(source["scenario_summaries.csv"], SUMMARY_COLUMNS)
    daily_results = deserialize_csv(source["daily_results.csv"], DAILY_RESULT_COLUMNS)
    for row in summaries:
        row["version_id"] = version_id
    for row in daily_results:
        row["version_id"] = version_id
    data_files = {
        "inputs.json": inputs_bytes,
        "scenario_summaries.csv": serialize_csv(summaries, SUMMARY_COLUMNS),
        "daily_results.csv": serialize_csv(daily_results, DAILY_RESULT_COLUMNS),
    }

    manifest = deserialize_json(source["manifest.json"])
    manifest.update(
        version_id=version_id,
        previous_official_version_id=previous_version_id,
        created_at=created_at,
        operator_display_name=operator,
        note=note,
        files={name: {"sha256": sha256_bytes(data)} for name, data in data_files.items()},
    )
    manifest_bytes = serialize_json(manifest)
    committed = deserialize_json(source["COMMITTED.json"])
    committed.update(
        version_id=version_id,
        manifest_sha256=sha256_bytes(manifest_bytes),
    )
    bundle = {
        "manifest.json": manifest_bytes,
        **data_files,
        "COMMITTED.json": serialize_json(committed),
    }
    validate_official_bundle(bundle)
    return bundle


def _build_root(
    tmp_path: Path,
    *,
    versions: list[tuple[str, str | None]],
    current_version_id: str | None = None,
) -> Path:
    root = tmp_path / "shared-root"
    root.mkdir()
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
    for index, (version_id, previous_version_id) in enumerate(versions):
        _write_bundle(
            root / "official-estimates" / "versions" / version_id,
            _versioned_bundle(
                version_id,
                previous_version_id,
                created_at=f"2026-12-{15 - index:02d}T02:45:00Z",
                operator=f"操作人 {version_id}",
                note=f"備註 {version_id}",
            ),
        )
    if current_version_id is None and versions:
        current_version_id = versions[0][0]
    if current_version_id is not None:
        official_root = root / "official-estimates"
        official_root.mkdir(exist_ok=True)
        previous_by_id = dict(versions)
        (official_root / "current.json").write_bytes(
            serialize_json(
                {
                    "schema": OFFICIAL_CURRENT_SCHEMA,
                    "schema_version": SCHEMA_VERSION,
                    "revision": len(versions) or 1,
                    "current_version_id": current_version_id,
                    "previous_version_id": previous_by_id.get(current_version_id),
                    "updated_at": "2026-12-15T02:47:00Z",
                    "operator_display_name": "測試操作人",
                }
            )
        )
    return root


def _filesystem_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_single_current_loads_complete_validated_snapshot(tmp_path):
    root = _build_root(tmp_path, versions=[("estimate-current", None)])

    current = load_official_current(root)

    assert current.current["current_version_id"] == "estimate-current"
    assert current.snapshot.metadata.version_id == "estimate-current"
    assert current.snapshot.manifest["previous_official_version_id"] is None
    assert current.snapshot.inputs["batch_id"] == "batch-synthetic-1"
    assert current.snapshot.batch["projection_start_date"] == "2027-01-01"
    assert current.snapshot.official_scenario_ids == ("scenario-a", "scenario-b")
    assert len(current.snapshot.scenario_summaries) == 2
    assert len(current.snapshot.daily_results) == 4


def test_history_follows_publication_chain_not_names_or_mtime(tmp_path):
    versions = [
        ("estimate-z-current", "estimate-a-middle"),
        ("estimate-a-middle", "estimate-m-oldest"),
        ("estimate-m-oldest", None),
    ]
    root = _build_root(tmp_path, versions=versions)
    versions_root = root / "official-estimates" / "versions"
    # Make timestamp order disagree with both publication and lexical order.
    os.utime(versions_root / "estimate-z-current", (100, 100))
    os.utime(versions_root / "estimate-a-middle", (300, 300))
    os.utime(versions_root / "estimate-m-oldest", (200, 200))

    history = load_official_history(root)

    assert [item.version_id for item in history.versions] == [item[0] for item in versions]
    assert history.current_version_id == "estimate-z-current"


def test_history_metadata_contains_dates_scenarios_and_publication_fields(tmp_path):
    root = _build_root(tmp_path, versions=[("estimate-current", None)])

    metadata = load_official_history(root).versions[0]

    assert metadata.version_id == "estimate-current"
    assert metadata.previous_official_version_id is None
    assert metadata.derived_from_official_version_id is None
    assert metadata.batch_id == "batch-synthetic-1"
    assert metadata.batch_name == "合成正式推估"
    assert metadata.annual_data_version_id == "annual-synthetic-2027"
    assert metadata.created_at == "2026-12-15T02:45:00Z"
    assert metadata.operator_display_name == "操作人 estimate-current"
    assert metadata.note == "備註 estimate-current"
    assert metadata.display_start_date == "2027-01-01"
    assert metadata.projection_start_date == "2027-01-01"
    assert metadata.projection_end_date == "2027-01-03"
    assert metadata.official_scenario_ids == ("scenario-a", "scenario-b")
    assert [(item.name, item.order) for item in metadata.scenarios] == [
        ("合成情境 1", 0),
        ("合成情境 2", 1),
    ]


def test_loads_complete_inputs_for_specified_history_version(tmp_path):
    root = _build_root(
        tmp_path,
        versions=[("estimate-current", "estimate-old"), ("estimate-old", None)],
    )

    snapshot = load_official_history_version(root, "estimate-old")

    assert snapshot.metadata.version_id == "estimate-old"
    assert snapshot.annual_data_version_id == "annual-synthetic-2027"
    assert snapshot.inputs["official_scenario_ids"] == ["scenario-a", "scenario-b"]
    assert [item["scenario_id"] for item in snapshot.batch["scenarios"]] == [
        "scenario-a",
        "scenario-b",
    ]
    assert snapshot.manifest["derived_from_official_version_id"] is None
    assert snapshot.scenario_summaries[0]["calculation_status"] == "success"
    assert snapshot.daily_results[0]["date"] == "2027-01-01"


def test_missing_previous_version_is_a_diagnosable_broken_link(tmp_path):
    root = _build_root(
        tmp_path,
        versions=[("estimate-current", "estimate-missing")],
    )

    with pytest.raises(OfficialEstimateLoadError) as captured:
        load_official_history(root)

    error = captured.value
    assert error.code is OfficialEstimateLoadErrorCode.BROKEN_HISTORY_LINK
    assert error.cause_code is OfficialEstimateLoadErrorCode.BUNDLE_NOT_FOUND
    assert error.version_id == "estimate-missing"
    assert error.referenced_by_version_id == "estimate-current"
    assert "estimate-current" in str(error) and "estimate-missing" in str(error)


def test_corrupt_previous_version_is_a_diagnosable_broken_link(tmp_path):
    root = _build_root(
        tmp_path,
        versions=[("estimate-current", "estimate-old"), ("estimate-old", None)],
    )
    target = root / "official-estimates" / "versions" / "estimate-old" / "inputs.json"
    target.write_bytes(b"{}")

    with pytest.raises(OfficialEstimateLoadError) as captured:
        load_official_history(root)

    error = captured.value
    assert error.code is OfficialEstimateLoadErrorCode.BROKEN_HISTORY_LINK
    assert error.cause_code is OfficialEstimateLoadErrorCode.BUNDLE_VALIDATION_FAILED
    assert "bundle validation" in str(error)


def test_publication_chain_cycle_is_detected(tmp_path):
    root = _build_root(
        tmp_path,
        versions=[
            ("estimate-current", "estimate-previous"),
            ("estimate-previous", "estimate-current"),
        ],
    )

    with pytest.raises(OfficialEstimateLoadError) as captured:
        load_official_history(root)

    error = captured.value
    assert error.code is OfficialEstimateLoadErrorCode.HISTORY_CYCLE
    assert "estimate-current -> estimate-previous -> estimate-current" in str(error)


def test_orphan_version_is_not_in_normal_history_or_loadable_by_normal_api(tmp_path):
    root = _build_root(
        tmp_path,
        versions=[("estimate-current", "estimate-old"), ("estimate-old", None)],
    )
    _write_bundle(
        root / "official-estimates" / "versions" / "estimate-orphan",
        _versioned_bundle("estimate-orphan", None),
    )

    history = OfficialEstimateLoader(root).load_history()

    assert [item.version_id for item in history.versions] == [
        "estimate-current",
        "estimate-old",
    ]
    with pytest.raises(OfficialEstimateLoadError) as captured:
        OfficialEstimateLoader(root).load_history_version("estimate-orphan")
    assert captured.value.code is OfficialEstimateLoadErrorCode.VERSION_NOT_IN_HISTORY


def test_loader_does_not_write_to_shared_root(tmp_path):
    root = _build_root(
        tmp_path,
        versions=[("estimate-current", "estimate-old"), ("estimate-old", None)],
    )
    before = _filesystem_snapshot(root)

    loader = OfficialEstimateLoader(root)
    loader.load_current()
    loader.load_history()
    loader.load_history_version("estimate-old")

    assert _filesystem_snapshot(root) == before


def test_missing_invalid_current_and_missing_current_bundle_have_distinct_codes(tmp_path):
    root = _build_root(tmp_path, versions=[])
    with pytest.raises(OfficialEstimateLoadError) as missing:
        load_official_history(root)
    assert missing.value.code is OfficialEstimateLoadErrorCode.CURRENT_MISSING

    official_root = root / "official-estimates"
    official_root.mkdir()
    (official_root / "current.json").write_bytes(b"{")
    with pytest.raises(OfficialEstimateLoadError) as invalid:
        load_official_history(root)
    assert invalid.value.code is OfficialEstimateLoadErrorCode.CURRENT_INVALID

    (official_root / "current.json").write_bytes(
        serialize_json(
            {
                "schema": OFFICIAL_CURRENT_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "revision": 1,
                "current_version_id": "estimate-missing",
                "previous_version_id": None,
                "updated_at": "2026-12-15T02:47:00Z",
                "operator_display_name": "測試操作人",
            }
        )
    )
    with pytest.raises(OfficialEstimateLoadError) as no_bundle:
        load_official_history(root)
    assert no_bundle.value.code is OfficialEstimateLoadErrorCode.BUNDLE_NOT_FOUND


def test_invalid_current_bundle_has_validation_failure_code(tmp_path):
    root = _build_root(tmp_path, versions=[("estimate-current", None)])
    target = root / "official-estimates" / "versions" / "estimate-current" / "inputs.json"
    target.write_bytes(b"{}")

    with pytest.raises(OfficialEstimateLoadError) as captured:
        load_official_current(root)

    assert captured.value.code is OfficialEstimateLoadErrorCode.BUNDLE_VALIDATION_FAILED


def test_current_change_during_history_read_is_rejected(tmp_path):
    root = _build_root(tmp_path, versions=[("estimate-current", None)])
    current_path = root / "official-estimates" / "current.json"
    original = current_path.read_bytes()
    changed = serialize_json({**deserialize_json(original), "revision": 2})
    current_reads = 0

    def changing_reader(path: Path) -> bytes:
        nonlocal current_reads
        if path == current_path:
            current_reads += 1
            return original if current_reads == 1 else changed
        return path.read_bytes()

    with pytest.raises(OfficialEstimateLoadError) as captured:
        load_official_history(root, read_bytes=changing_reader)

    assert captured.value.code is OfficialEstimateLoadErrorCode.CURRENT_CHANGED
