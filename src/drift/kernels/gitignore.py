"""A pure matcher for the repository's root `.gitignore` — deliberately a subset.

Comments, basename globs, directory patterns, root-anchored patterns, and `!` negations by
last-match-wins. `**` and git's rule that an excluded directory cannot be re-entered are absent.
"""

from __future__ import annotations

import fnmatch
import os
import posixpath


def _patterns(repo_root: str) -> list[tuple[bool, str]]:
    """The entries as `(negated, pattern)` in file order, which is what last-match-wins reads."""
    try:
        with open(os.path.join(repo_root, ".gitignore"), encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        out.append((negated, line))
    return out


def is_ignored(repo_root: str, rel_path: str) -> bool:
    """Is the repo-relative `rel_path` ignored by the root `.gitignore`?

    Both unmodelled cases resolve to not-ignored, which is the safe direction: a kernel that
    declines on ignored paths would otherwise suppress a claim it should have let through.
    """
    rel = rel_path.strip("/")
    segments = rel.split("/")
    # A directory pattern must match a parent, so every prefix is tried: `doc/html`, `doc`.
    prefixes = ["/".join(segments[: i + 1]) for i in range(len(segments))]
    ignored = False
    for negated, raw in _patterns(repo_root):
        pattern = raw.rstrip("/")
        anchored = pattern.startswith("/")
        pattern = pattern.lstrip("/")
        for prefix in prefixes:
            candidate = prefix if anchored or "/" in pattern else posixpath.basename(prefix)
            if fnmatch.fnmatch(candidate, pattern):
                ignored = not negated
                break
    return ignored
