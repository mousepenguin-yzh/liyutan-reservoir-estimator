"""Local-only, side-by-side application installation. Never opens shared storage."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import sys
import uuid

from software_provenance import REPOSITORY, load_software_provenance

REMOTE = f"https://github.com/{REPOSITORY}.git"
SHA = re.compile(r"[0-9a-f]{40}")


class DesktopError(RuntimeError):
    pass


def local_path(value: str | Path) -> Path:
    """Reject network drives and reparse ancestors before creating local artifacts."""
    raw = str(value)
    windows = PureWindowsPath(raw)
    if raw.startswith(("\\\\", "//")) or windows.drive.upper() == "U:":
        raise DesktopError("安裝位置必須在本機磁碟，不能使用 U: 或網路路徑。")
    path = Path(value).absolute()
    if os.name == "nt" and ctypes.windll.kernel32.GetDriveTypeW(path.anchor) != 3:
        raise DesktopError("安裝位置必須在固定本機磁碟。")
    # Walk from root down: never inspect descendants of a redirected ancestor.
    for part in [*reversed(path.parents), path]:
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise DesktopError("安裝路徑不可包含 symbolic link 或 junction。")
    return path


def build_environment(*, application: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if upper.startswith(("PYTHON", "STREAMLIT_", "GIT_", "PIP_")) or (
            not application and upper.startswith("LIYUTAN_")
        ):
            del env[key]
    env.update(
        GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never", PYTHONUTF8="1",
        PIP_CONFIG_FILE=os.devnull, PIP_NO_INPUT="1", PIP_NO_CACHE_DIR="1",
    )
    if not application:
        env["LIYUTAN_ENABLE_SHARED_STORAGE"] = "0"
    return env


def run(command: list[str], cwd: Path, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            command, cwd=cwd, env=build_environment(), check=True,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        # Do not expose subprocess output: pip/proxy errors may contain credentials.
        raise DesktopError(
            f"{Path(command[0]).name} 執行失敗（{type(exc).__name__}）。"
            "請維護者檢查 Git/Python、GitHub 登入、網路、套件來源及磁碟空間後重試。"
        ) from exc


class LocalLock:
    """OS lock released even if the desktop controller terminates unexpectedly."""

    def __init__(self, root: Path):
        self.path = local_path(root / "controller.lock")
        self.file = None

    def __enter__(self):
        self.file = self.path.open("a+b")
        self.file.seek(0, os.SEEK_END)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise DesktopError("啟動器已開啟；請使用原視窗。") from exc
        return self

    def __exit__(self, *args):
        self.file.close()


class DesktopRuntime:
    def __init__(self, root: Path, *, runner=run, replace=os.replace):
        self.root = local_path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.replace = replace

    def active(self) -> dict | None:
        pointer = local_path(self.root / "active.json")
        if not pointer.exists():
            return None
        try:
            data = json.loads(pointer.read_text(encoding="utf-8"))
            if not SHA.fullmatch(data["commit"]) or not re.fullmatch(r"[0-9a-f]{32}", data["release"]):
                raise ValueError("invalid identity")
            local_path(self.root / "releases" / data["release"])
            return data
        except (ValueError, KeyError, TypeError) as exc:
            raise DesktopError("本機啟動版本記錄損壞，請維護者處理；不自動猜測版本。") from exc

    def repository(self, active: dict) -> Path:
        return local_path(self.root / "releases" / active["release"] / "repository")

    def python(self, active: dict) -> Path:
        return local_path(self.root / "releases" / active["release"] / "runtime" /
                          ("Scripts/python.exe" if os.name == "nt" else "bin/python"))

    def available(self) -> str:
        output = self.runner(["git", "ls-remote", REMOTE, "refs/heads/main"], self.root)
        rows = [line.split() for line in output.splitlines() if line.strip()]
        if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != "refs/heads/main" or not SHA.fullmatch(rows[0][0]):
            raise DesktopError("無法確認遠端 main 版本；目前版本保持不變。")
        return rows[0][0]

    def validate_active(self, active: dict):
        result = load_software_provenance(
            self.repository(active), runner=lambda args, cwd: self.runner(["git", *args], cwd)
        )
        if (not result.ok or result.software["git_commit"] != active["commit"]
                or result.software["source_tree_dirty"] or not self.python(active).is_file()):
            raise DesktopError("本機版本缺檔或有人工修改，請維護者處理；不覆蓋或重設檔案。")

    def install(self, commit: str, progress=lambda text: None) -> dict:
        """Caller holds LocalLock. All fallible preparation precedes pointer replace."""
        if not SHA.fullmatch(commit):
            raise DesktopError("遠端 commit 格式無效。")
        previous = self.active()
        if previous:
            self.validate_active(previous)
            if previous["commit"] == commit:
                return previous
        candidate = {"commit": commit, "release": uuid.uuid4().hex}
        repo = self.repository(candidate)
        repo.parent.mkdir(parents=True)
        progress("下載選定版本…")
        self.runner(["git", "clone", "--no-checkout", "--", REMOTE, str(repo)], self.root)
        self.runner(["git", "checkout", "--detach", commit], repo)
        actual = self.runner(["git", "rev-parse", "HEAD"], repo).strip()
        if actual != commit:
            raise DesktopError("下載版本與選定 commit 不符；目前版本保持不變。")
        progress("建立獨立 Python 環境與安裝套件…")
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        self.runner([str(executable), "-m", "venv", "--copies", str(repo.parent / "runtime")], repo, 180)
        python = str(self.python(candidate))
        self.runner([python, "-m", "pip", "install", "-r", "requirements.txt"], repo, 900)
        self.runner([python, "-m", "pip", "check"], repo)
        progress("驗證程式啟動（共享功能關閉）…")
        # Runs actual Streamlit script without a browser or any shared-data capability.
        smoke = (
            "from pathlib import Path; from streamlit.testing.v1 import AppTest; "
            "a=AppTest.from_file(Path('app.py').resolve(), default_timeout=60).run(); "
            "assert not a.exception, str(a.exception)"
        )
        self.runner([python, "-c", smoke], repo, 120)
        self.validate_active(candidate)
        temporary = local_path(self.root / f"active-{uuid.uuid4().hex}.tmp")
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(candidate, file)
            file.flush()
            os.fsync(file.fileno())
        self.replace(temporary, local_path(self.root / "active.json"))
        return candidate

    def launch_command(self, active: dict, port: int) -> list[str]:
        self.validate_active(active)
        return [str(self.python(active)), "-m", "streamlit", "run", "app.py",
                "--server.address=127.0.0.1", f"--server.port={port}",
                "--server.headless=true", "--browser.gatherUsageStats=false"]
