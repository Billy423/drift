"""Which code a scan is about: a repository on disk."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RepoRef:
    """A repository on disk. The tree at `path` is read exactly as it stands."""

    path: str
