"""The repository file listing that goes into the discovery prompt."""

from __future__ import annotations

import os
import subprocess

__all__ = ["build_repo_map"]


def build_repo_map(repo_root: str, limit: int = 400) -> str:
    """Deterministic file listing: the tracked files if git can list them, otherwise a walk.

    The subprocess call is the harness's own scaffolding, run before the model is reached. It is
    not one of the tools the model may call; those are read-only and execute nothing.
    """
    files = _git_ls_files(repo_root)
    if files is None:
        files = _walk_files(repo_root)
    files = sorted(files)
    lines = files[:limit]
    if len(files) > limit:
        lines = [*lines, "… (truncated)"]
    return "\n".join(lines)


def _git_ls_files(repo_root: str) -> list[str] | None:
    """Tracked-file listing via git, or None if `repo_root` isn't a usable git worktree."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_root, "ls-files"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line]


def _walk_files(repo_root: str) -> list[str]:
    """Plain filesystem walk, skipping `.git`, for non-git or git-unavailable directories."""
    hits = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            hits.append(os.path.relpath(os.path.join(dirpath, f), repo_root))
    return hits
