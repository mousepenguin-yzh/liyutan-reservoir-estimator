"""Synthetic local installs only; no production root or remote network access."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
import desktop_runtime as desktop

OLD = "a" * 40
NEW = "b" * 40


def installed(tmp_path, monkeypatch, runner):
    runtime = desktop.DesktopRuntime(tmp_path / "desktop", runner=runner)
    previous = {"commit": OLD, "release": "c" * 32}
    repo = runtime.repository(previous)
    repo.mkdir(parents=True)
    (repo / "old.txt").write_text("usable", encoding="utf-8")
    (runtime.root / "active.json").write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr(runtime, "validate_active", lambda active: None)
    return runtime, previous


@pytest.mark.parametrize("failure", ["clone", "checkout", "rev-parse", "venv", "install", "check", "-c", "replace"])
def test_each_failure_preserves_current_and_old_files(tmp_path, monkeypatch, failure):
    def runner(command, cwd, timeout=120):
        if failure in command:
            raise desktop.DesktopError("injected failure")
        if "clone" in command:
            Path(command[-1]).mkdir()
        return NEW if "rev-parse" in command else ""
    runtime, previous = installed(tmp_path, monkeypatch, runner)
    before = (runtime.root / "active.json").read_bytes()
    if failure == "replace":
        def fail_replace(*args):
            raise OSError("injected pointer failure")
        runtime.replace = fail_replace
    with pytest.raises((desktop.DesktopError, OSError)):
        runtime.install(NEW)
    assert runtime.active() == previous
    assert (runtime.root / "active.json").read_bytes() == before
    assert (runtime.repository(previous) / "old.txt").read_text() == "usable"


def test_success_switches_only_after_verification(tmp_path, monkeypatch):
    calls = []
    def runner(command, cwd, timeout=120):
        calls.append(command)
        assert runtime.active()["commit"] == OLD
        if "clone" in command:
            Path(command[-1]).mkdir()
        return NEW if "rev-parse" in command else ""
    runtime, previous = installed(tmp_path, monkeypatch, runner)
    current = runtime.install(NEW)
    assert current == runtime.active()
    assert current["commit"] == NEW
    assert runtime.repository(previous).is_dir()
    assert any("-c" in command for command in calls)
    assert runtime.install(NEW) == current


@pytest.mark.parametrize("output", ["", f"{NEW}\trefs/heads/other", "invalid\trefs/heads/main", f"{NEW}\trefs/heads/main\n{OLD}\trefs/heads/main"])
def test_invalid_remote_does_not_create_pointer(tmp_path, output):
    runtime = desktop.DesktopRuntime(tmp_path, runner=lambda *args: output)
    with pytest.raises(desktop.DesktopError):
        runtime.available()
    assert runtime.active() is None


def test_available_is_exact_main(tmp_path):
    calls = []
    def runner(*args):
        calls.append(args)
        return f"{NEW}\trefs/heads/main\n"
    runtime = desktop.DesktopRuntime(tmp_path, runner=runner)
    assert runtime.available() == NEW
    assert calls[0][0] == ["git", "ls-remote", desktop.REMOTE, "refs/heads/main"]


@pytest.mark.parametrize("value", [r"U:\must-not-be-accessed", r"\\server\share", "//server/share"])
def test_reject_shared_location_before_filesystem_access(monkeypatch, value):
    def forbidden(*args):
        pytest.fail("filesystem must not be inspected")
    monkeypatch.setattr(Path, "is_symlink", forbidden)
    with pytest.raises(desktop.DesktopError):
        desktop.local_path(value)


def test_reject_redirected_ancestor(tmp_path, monkeypatch):
    redirect = tmp_path / "redirect"
    seen = []
    def is_link(path):
        seen.append(path)
        return path == redirect
    monkeypatch.setattr(Path, "is_symlink", is_link)
    with pytest.raises(desktop.DesktopError):
        desktop.local_path(redirect / "never-inspect")
    assert redirect / "never-inspect" not in seen


@pytest.mark.parametrize("value", [{"commit": NEW, "release": "../outside"}, None, {"commit": "bad", "release": "c" * 32}])
def test_bad_pointer_rejected(tmp_path, value):
    runtime = desktop.DesktopRuntime(tmp_path)
    (tmp_path / "active.json").write_text(json.dumps(value))
    with pytest.raises(desktop.DesktopError):
        runtime.active()


def test_update_environment_removes_every_shared_capability(monkeypatch):
    monkeypatch.setenv("LIYUTAN_SHARED_ROOT", "never-access-this")
    monkeypatch.setenv("LIYUTAN_ENABLE_FORMAL_WRITES", "1")
    monkeypatch.setenv("LIYUTAN_ENABLE_ANNUAL_WRITES", "1")
    monkeypatch.setenv("PYTHONPATH", "untrusted")
    monkeypatch.setenv("GIT_DIR", "untrusted")
    env = desktop.build_environment()
    assert {k: v for k, v in env.items() if k.startswith("LIYUTAN_")} == {"LIYUTAN_ENABLE_SHARED_STORAGE": "0"}
    assert "PYTHONPATH" not in env and "GIT_DIR" not in env
    launch = desktop.build_environment(application=True)
    assert launch["LIYUTAN_SHARED_ROOT"] == "never-access-this"
    assert launch["LIYUTAN_ENABLE_FORMAL_WRITES"] == "1"


def test_run_has_bounded_noninteractive_environment(tmp_path, monkeypatch):
    def fake(command, **kwargs):
        assert kwargs["timeout"] == 12
        assert kwargs["env"]["LIYUTAN_ENABLE_SHARED_STORAGE"] == "0"
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        raise subprocess.TimeoutExpired(command, 12, stderr="secret-token")
    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(desktop.DesktopError) as error:
        desktop.run(["git", "fetch"], tmp_path, 12)
    assert "secret-token" not in str(error.value)


def test_controller_lock_blocks_second_process_and_releases(tmp_path):
    script = "from pathlib import Path; from desktop_runtime import LocalLock; " + f"\nwith LocalLock(Path({str(tmp_path)!r})): pass"
    with desktop.LocalLock(tmp_path):
        result = subprocess.run([sys.executable, "-c", script], capture_output=True)
        assert result.returncode != 0
    result = subprocess.run([sys.executable, "-c", script], capture_output=True)
    assert result.returncode == 0, result.stderr


def test_real_git_version_and_dirty_guard(tmp_path):
    runtime = desktop.DesktopRuntime(tmp_path / "desktop")
    active = {"commit": OLD, "release": "c" * 32}
    repo = runtime.repository(active)
    repo.mkdir(parents=True)
    for args in (["init"], ["config", "user.email", "synthetic@example.invalid"], ["config", "user.name", "Synthetic"]):
        desktop.run(["git", *args], repo)
    (repo / "app.py").write_text("# synthetic\n")
    desktop.run(["git", "add", "app.py"], repo)
    desktop.run(["git", "commit", "-m", "synthetic"], repo)
    active["commit"] = desktop.run(["git", "rev-parse", "HEAD"], repo).strip()
    python = runtime.python(active)
    python.parent.mkdir(parents=True)
    python.write_text("synthetic executable")
    runtime.validate_active(active)
    command = runtime.launch_command(active, 12345)
    assert "--server.address=127.0.0.1" in command
    (repo / "app.py").write_text("# changed\n")
    with pytest.raises(desktop.DesktopError):
        runtime.validate_active(active)


def test_app_displays_version(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from software_provenance import SoftwareProvenanceResult
    monkeypatch.setenv("LIYUTAN_ENABLE_SHARED_STORAGE", "0")
    monkeypatch.setattr("software_provenance.load_software_provenance", lambda: SoftwareProvenanceResult(
        True, {"git_commit": NEW, "app_version": "git-" + NEW[:12], "source_tree_dirty": False}))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=30).run()
    assert not app.exception
    assert any(NEW in item.value for item in app.sidebar.caption)



def test_local_git_candidate_smoke_uses_no_shared_environment(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text(
        "import os\nimport streamlit as st\n"
        "assert os.environ['LIYUTAN_ENABLE_SHARED_STORAGE'] == '0'\n"
        "assert 'LIYUTAN_SHARED_ROOT' not in os.environ\n"
        "assert 'LIYUTAN_ENABLE_FORMAL_WRITES' not in os.environ\n"
        "st.title('Synthetic candidate')\n", encoding="utf-8")
    (source / "requirements.txt").write_text("")
    for args in (["init"], ["config", "user.email", "synthetic@example.invalid"],
                 ["config", "user.name", "Synthetic"], ["add", "."], ["commit", "-m", "synthetic"]):
        desktop.run(["git", *args], source)
    commit = desktop.run(["git", "rev-parse", "HEAD"], source).strip()
    monkeypatch.setenv("LIYUTAN_SHARED_ROOT", "never-access-this")
    monkeypatch.setenv("LIYUTAN_ENABLE_FORMAL_WRITES", "1")
    monkeypatch.setenv("LIYUTAN_ENABLE_SHARED_STORAGE", "1")
    def runner(command, cwd, timeout=120):
        if "clone" in command:
            command = [str(source) if arg == desktop.REMOTE else arg for arg in command]
        if "venv" in command or "pip" in command:
            return ""  # Reuse CI's installed dependencies; never access a package server.
        return desktop.run(command, cwd, timeout)
    runtime = desktop.DesktopRuntime(tmp_path / "installed", runner=runner)
    monkeypatch.setattr(runtime, "python", lambda active: Path(sys.executable))
    with desktop.LocalLock(runtime.root):
        result = runtime.install(commit)
    assert runtime.active() == result
    runtime.validate_active(result)


def test_candidate_version_mismatch_never_activates(tmp_path):
    def runner(command, cwd, timeout=120):
        if "clone" in command:
            Path(command[-1]).mkdir()
        return OLD if "rev-parse" in command else ""
    runtime = desktop.DesktopRuntime(tmp_path, runner=runner)
    with pytest.raises(desktop.DesktopError, match="commit"):
        runtime.install(NEW)
    assert runtime.active() is None


def test_modified_existing_install_blocks_all_downloads(tmp_path, monkeypatch):
    def forbidden(*args):
        pytest.fail("must not download before checking existing install")
    runtime, previous = installed(tmp_path, monkeypatch, forbidden)
    def invalid(*args):
        raise desktop.DesktopError("modified")
    monkeypatch.setattr(runtime, "validate_active", invalid)
    with pytest.raises(desktop.DesktopError):
        runtime.install(NEW)
    assert runtime.active() == previous

