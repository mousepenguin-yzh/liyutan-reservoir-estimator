"""Read-only software provenance for formal annual-data activation."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


REPOSITORY = "mousepenguin-yzh/liyutan-reservoir-estimator"
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
CommandRunner = Callable[[Sequence[str], Path], str]


@dataclass(frozen=True)
class SoftwareProvenanceResult:
    ok: bool
    software: dict[str, object] | None = None
    error: str | None = None


def _run_git(arguments: Sequence[str], repository_path: Path) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=5,
    )
    return completed.stdout


def load_software_provenance(
    repository_path: str | Path | None = None,
    *,
    runner: CommandRunner = _run_git,
) -> SoftwareProvenanceResult:
    """Return validated Git metadata without performing any Git write operation."""
    path = Path(repository_path) if repository_path is not None else Path(__file__).resolve().parent
    try:
        commit = runner(("rev-parse", "HEAD"), path).strip().lower()
        if not _GIT_SHA.fullmatch(commit):
            return SoftwareProvenanceResult(
                False,
                error="Git 無法提供可靠的 40 字元 commit SHA；年度版本啟用已停用。",
            )
        dirty_output = runner(("status", "--porcelain", "--untracked-files=normal"), path)
    except (OSError, subprocess.SubprocessError) as exc:
        return SoftwareProvenanceResult(
            False,
            error=f"無法可靠取得 Git software provenance；年度版本啟用已停用：{exc}",
        )
    software = {
        "repository": REPOSITORY,
        "git_commit": commit,
        # The deployed commit is the stable, automatically maintained application version.
        "app_version": f"git-{commit[:12]}",
        "source_tree_dirty": bool(dirty_output.strip()),
    }
    return SoftwareProvenanceResult(True, software=software)
