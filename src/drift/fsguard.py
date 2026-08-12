"""Guards for reading a file out of an untrusted repository: containment, shape, read bound.

A scanned repository is attacker-shaped input: the pipeline walks a tree it does not control and
puts what it reads into a model prompt and into the journal.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass

__all__ = [
    "B_DOC",
    "SKIP_REASONS",
    "DocRead",
    "UnitShape",
    "classify_unit",
    "read_doc_bytes",
    "safe_join",
]

#: The per-unit input bound, in characters. Containment says nothing about size, so the read
#: is bounded too: an unbounded read buys a huge first model call that nothing can stop.
B_DOC = 400_000

#: Closed set, one reason per cause. A skipped unit is journalled and reported under exactly one.
SKIP_REASONS = frozenset({"escapes-repo", "not-regular", "unreadable"})


def safe_join(repo_root: str, rel: str) -> str | None:
    """Resolve a repo-relative path under `repo_root`; None when it escapes.

    Containment is by realpath on both sides, because a lexical check such as `normpath` cannot
    see through a symlink, and an in-repo `vendor/ -> /etc` is enough to pull host files into a
    model prompt.

    Returns:
        The contained real path, which callers pass on rather than re-deriving from `rel`.
    """
    target = os.path.realpath(os.path.join(repo_root, rel))
    root = os.path.realpath(repo_root)
    return target if (target == root or target.startswith(root + os.sep)) else None


@dataclass(frozen=True)
class UnitShape:
    """What the pre-open checks decided about one candidate file."""

    #: The contained real path, or None when the unit is skipped.
    path: str | None
    #: A member of `SKIP_REASONS`, or None when the unit is scannable.
    skip_reason: str | None
    #: `st_size` of the resolved regular file; 0 for a skipped unit.
    size_bytes: int

    @property
    def oversize(self) -> bool:
        """Larger than `B_DOC`. Such a unit is truncated, never skipped."""
        return self.skip_reason is None and self.size_bytes > B_DOC


@dataclass(frozen=True)
class DocRead:
    """One unit's bounded read: the bytes the pipeline may use, and whether that is all of them."""

    data: bytes
    truncated: bool
    #: The file's real size, so a truncated unit can report what it was truncated from.
    size_bytes: int


def classify_unit(repo_root: str, rel: str) -> UnitShape:
    """Containment and shape for one candidate unit. Never opens the file, so it cannot block.

    Shape must be settled before any `open()`: a FIFO named `notes.md` makes `open()` wait
    forever for a writer, and `lstat` cannot block. A dangling link, a vanished file and a
    permission error all become `unreadable` rather than raising, since enumeration walks a tree
    it does not control.
    """
    target = safe_join(repo_root, rel)
    if target is None:
        return UnitShape(None, "escapes-repo", 0)
    try:
        st = os.lstat(target)
    except OSError:
        return UnitShape(None, "unreadable", 0)
    if not stat.S_ISREG(st.st_mode):
        return UnitShape(None, "not-regular", 0)
    return UnitShape(target, None, st.st_size)


def read_doc_bytes(repo_root: str, rel: str, bound: int = B_DOC) -> DocRead | None:
    """Read at most `bound` bytes of a contained regular file; None when it must not be read.

    The bound is stated in characters but applied to the read: a UTF-8 character costs at least
    one byte, so the cut is exact for ASCII and conservative for anything else. `bound + 1` bytes
    are read, so truncation is decided by what came back rather than by an earlier `stat`.
    """
    shape = classify_unit(repo_root, rel)
    if shape.skip_reason is not None or shape.path is None:
        return None
    try:
        with open(shape.path, "rb") as fh:
            data = fh.read(bound + 1)
    except OSError:
        return None
    if len(data) > bound:
        return DocRead(data[:bound], True, shape.size_bytes)
    return DocRead(data, False, shape.size_bytes)
