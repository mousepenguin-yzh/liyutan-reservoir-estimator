"""Controller guards without opening a real desktop window."""
from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import shutil

import pytest

from desktop_launcher import Launcher
import desktop_launcher


def test_update_refuses_running_app(monkeypatch):
    notices = []
    monkeypatch.setattr(desktop_launcher.messagebox, "showinfo", lambda *args: notices.append(args))
    launcher = Launcher.__new__(Launcher)
    launcher.process = SimpleNamespace(poll=lambda: None)
    launcher.runtime = SimpleNamespace(install=lambda *args: pytest.fail("must not install"))
    launcher.update()
    assert notices and "先停止" in notices[0][0]


def test_update_requires_successful_check(monkeypatch):
    notices = []
    monkeypatch.setattr(desktop_launcher.messagebox, "showinfo", lambda *args: notices.append(args))
    launcher = Launcher.__new__(Launcher)
    launcher.process = None
    launcher.remote = None
    launcher.update()
    assert notices and "檢查" in notices[0][0]


def test_cancel_stop_preserves_process(monkeypatch):
    monkeypatch.setattr(desktop_launcher.messagebox, "askyesno", lambda *args: False)
    launcher = Launcher.__new__(Launcher)
    launcher.process = SimpleNamespace(poll=lambda: None, terminate=lambda: pytest.fail("must not stop"))
    launcher.stop()


@pytest.mark.skipif(os.name != "nt", reason="Windows shortcut COM integration")
def test_installer_creates_shortcut_in_synthetic_directory(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/install_desktop_shortcut.ps1"
    result = subprocess.run(
        [shutil.which("pwsh") or "powershell.exe", "-NoProfile", "-File", str(script), "-DestinationDirectory", str(tmp_path)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    shortcut = tmp_path / "Liyutan Estimator.lnk"
    assert shortcut.is_file()
    # COM reads the synthetic shortcut; never touches the user's real desktop.
    command = "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('" + str(shortcut).replace("'", "''") + "'); $s.TargetPath; $s.Arguments; $s.WorkingDirectory"
    result = subprocess.run([shutil.which("pwsh") or "powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "pythonw.exe" in result.stdout
    assert "desktop_launcher.py" in result.stdout
    assert str(script.parent.parent) in result.stdout

