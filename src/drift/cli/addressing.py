"""Document addressing: what a user typed, as the repo-relative spellings a scan can use.

Decides shape, never membership: whether a candidate is a real scannable unit is answered later,
against the frame's address spaces.
"""

from __future__ import annotations

import os

from drift.fsguard import safe_join


def candidates_for(spelling: str, repo_root: str) -> list[str]:
    """Repo-relative candidates for `spelling`, most likely first, contained ones only.

    The working directory is tried before the repository root, so a bare name typed in a shell
    means the file the user can see. Existence is not consulted: `os.path.exists("readme.md")` is
    true on a case-insensitive filesystem holding only `README.md`.
    """
    root = os.path.realpath(repo_root)
    out: list[str] = []
    for base in (os.getcwd(), repo_root):
        absolute = os.path.abspath(os.path.join(base, spelling))
        # `safe_join` documents a repo-relative second argument; an absolute one works because
        # `os.path.join` discards what precedes it. Wanted here: its containment and symlink check.
        contained = safe_join(root, absolute)
        if contained is None:
            continue
        rel = os.path.relpath(contained, root)
        if rel not in out:
            out.append(rel)
    return out
