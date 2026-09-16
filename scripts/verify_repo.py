"""Small, shared repository verification entry point."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _run(command: list[str], *, capture_output: bool = False) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=capture_output,
    )


def _tracked_python_files() -> tuple[list[Path], str | None]:
    try:
        result = _run(
            ["git", "ls-files", "-z", "--", "*.py"],
            capture_output=True,
        )
    except OSError as exc:
        return [], f"could not run git: {exc}"

    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        return [], detail or "git ls-files failed"

    paths = [
        REPOSITORY_ROOT / Path(os.fsdecode(raw_path))
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    ]
    return paths, None


def _verify_python_syntax(paths: list[Path]) -> bool:
    failures: list[str] = []
    for path in paths:
        try:
            source = path.read_bytes()
            compile(source, str(path.relative_to(REPOSITORY_ROOT)), "exec", dont_inherit=True)
        except (OSError, SyntaxError, ValueError) as exc:
            failures.append(f"{path.relative_to(REPOSITORY_ROOT)}: {exc}")

    if failures:
        print("[quick] Python compile: FAILED", flush=True)
        for failure in failures:
            print(f"  {failure}", flush=True)
        return False

    print(f"[quick] Python compile: ok ({len(paths)} tracked files)", flush=True)
    return True


def run_quick() -> bool:
    paths, error = _tracked_python_files()
    if error is not None:
        print(f"[quick] tracked Python files: FAILED ({error})", flush=True)
        return False
    if not _verify_python_syntax(paths):
        return False

    print("[quick] git diff --check", flush=True)
    try:
        result = _run(["git", "diff", "--check"])
    except OSError as exc:
        print(f"[quick] git diff --check: FAILED ({exc})", flush=True)
        return False
    if result.returncode != 0:
        print("[quick] git diff --check: FAILED", flush=True)
        return False

    print("[quick] git diff --check: ok", flush=True)
    return True


def run_full() -> bool:
    if not run_quick():
        return False

    print("[full] pytest -q", flush=True)
    try:
        result = _run([sys.executable, "-m", "pytest", "-q"])
    except OSError as exc:
        print(f"[full] pytest -q: FAILED ({exc})", flush=True)
        return False
    if result.returncode != 0:
        print("[full] pytest -q: FAILED", flush=True)
        return False

    print("[full] pytest -q: ok", flush=True)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--quick", action="store_true", help="Compile tracked Python files and check the Git diff.")
    mode.add_argument("--full", action="store_true", help="Run quick verification, then pytest -q.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    succeeded = run_full() if args.full else run_quick()
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())