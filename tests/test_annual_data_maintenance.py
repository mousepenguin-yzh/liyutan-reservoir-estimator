import pytest

from annual_data_maintenance import (
    ENABLE_ANNUAL_DATA_WRITES_ENV,
    AnnualDataMaintenanceService,
    annual_data_write_capability,
    annual_data_write_enabled,
)
from shared_storage_reader import load_shared_storage
from software_provenance import REPOSITORY, load_software_provenance
from test_shared_storage_reader import _build_root


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ("0", False),
        ("true", False),
        (" 1 ", False),
        ("1", True),
    ],
)
def test_annual_write_flag_requires_exact_one(value, expected):
    environ = {} if value is None else {ENABLE_ANNUAL_DATA_WRITES_ENV: value}
    assert annual_data_write_enabled(environ) is expected


def test_capability_requires_both_feature_flags_without_touching_unconfigured_root():
    assert not annual_data_write_capability(
        None,
        shared_mode_enabled=False,
        environ={ENABLE_ANNUAL_DATA_WRITES_ENV: "1"},
    ).available


def test_healthy_current_and_first_version_expose_exact_observed_state(tmp_path):
    root = _build_root(tmp_path)
    healthy = load_shared_storage(root)
    capability = annual_data_write_capability(
        healthy,
        shared_mode_enabled=True,
        environ={ENABLE_ANNUAL_DATA_WRITES_ENV: "1"},
        platform="win32",
    )
    assert capability.available and capability.activation_available
    assert (capability.observed_revision, capability.observed_current_version_id) == (
        1,
        "annual-synthetic-2027",
    )

    (root / "annual-data" / "current.json").unlink()
    missing = load_shared_storage(root)
    first = annual_data_write_capability(
        missing,
        shared_mode_enabled=True,
        environ={ENABLE_ANNUAL_DATA_WRITES_ENV: "1"},
        platform="win32",
    )
    assert first.available and first.state == "first_version"
    assert (first.observed_revision, first.observed_current_version_id) == (0, None)


def test_damaged_current_and_non_windows_activation_are_not_misrepresented(tmp_path):
    root = _build_root(tmp_path)
    (root / "annual-data" / "current.json").write_bytes(b"{")
    damaged = annual_data_write_capability(
        load_shared_storage(root),
        shared_mode_enabled=True,
        environ={ENABLE_ANNUAL_DATA_WRITES_ENV: "1"},
        platform="win32",
    )
    assert not damaged.available

    bundle_root = _build_root(tmp_path / "damaged-bundle")
    (
        bundle_root
        / "annual-data"
        / "versions"
        / "annual-synthetic-2027"
        / "COMMITTED.json"
    ).unlink()
    damaged_bundle = annual_data_write_capability(
        load_shared_storage(bundle_root),
        shared_mode_enabled=True,
        environ={ENABLE_ANNUAL_DATA_WRITES_ENV: "1"},
        platform="win32",
    )
    assert not damaged_bundle.available

    healthy_root = _build_root(tmp_path / "healthy")
    linux = annual_data_write_capability(
        load_shared_storage(healthy_root),
        shared_mode_enabled=True,
        environ={ENABLE_ANNUAL_DATA_WRITES_ENV: "1"},
        platform="linux",
    )
    assert linux.available
    assert not linux.activation_available


def test_provenance_is_read_only_derived_and_rejects_unreliable_commit(tmp_path):
    calls = []

    def runner(arguments, path):
        calls.append((tuple(arguments), path))
        if arguments[0] == "rev-parse":
            return "a" * 40 + "\n"
        return " M app.py\n"

    result = load_software_provenance(tmp_path, runner=runner)
    assert result.ok
    assert result.software == {
        "repository": REPOSITORY,
        "git_commit": "a" * 40,
        "app_version": "git-aaaaaaaaaaaa",
        "source_tree_dirty": True,
    }
    assert [call[0] for call in calls] == [
        ("rev-parse", "HEAD"),
        ("status", "--porcelain", "--untracked-files=normal"),
    ]

    failed = load_software_provenance(tmp_path, runner=lambda _args, _path: "not-a-sha")
    assert not failed.ok and failed.software is None


def test_injected_activation_backend_is_cross_platform_but_production_is_not():
    captured = {}

    def fake_activator(**arguments):
        captured.update(arguments)
        return "activated"

    service = AnnualDataMaintenanceService(activator=fake_activator, platform="linux")
    assert service.activate(observed_revision=7) == "activated"
    assert captured["observed_revision"] == 7

    production = AnnualDataMaintenanceService(platform="linux")
    with pytest.raises(RuntimeError, match="Windows/SMB"):
        production.activate()
