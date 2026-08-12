"""The tool set a model may call while inventorying a repository, and the bounds on it."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable
from dataclasses import dataclass

from drift.fsguard import classify_unit


@dataclass(frozen=True)
class ToolSpec:
    """One tool the model may call: its schema, and the sandboxed local function behind it."""

    name: str
    description: str
    input_schema: dict
    fn: Callable[..., str]
    # The belt's shared ledger; a caller reads the delta across the call it just made.
    # Optional, so a hand-built spec stays valid and its caller measures the result itself.
    ledger: list | None = None

    def to_sdk(self) -> dict:
        """The tool's schema in the shape the API expects."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


# A tool result is re-sent on every later turn, so an unbounded read is the largest
# cost lever in the loop. `glob` needs no cap: its own hit limit bounds it.
_READ_CHAR_CAP = 12_000  # one read: a source file's structure, not a data dump
_UNIT_CHAR_BUDGET = 120_000  # all of one unit's reads together


def make_toolbelt(
    repo_root: str,
    names: tuple[str, ...] = ("read_file", "glob"),
    read_char_cap: int = _READ_CHAR_CAP,
    unit_char_budget: int = _UNIT_CHAR_BUDGET,
) -> list[ToolSpec]:
    """Build the read-only, repository-sandboxed toolbelt: no shell, no network, no execution.

    Reads are capped per call and against a cumulative budget for the belt as a whole, held in a
    closure. Once that budget is gone, a read returns a notice instead of content and the model
    goes on with what it already gathered.

    Args:
        names: Which tools to build, in the order the caller wants them returned.
    """
    consumed = {"chars": 0}
    ledger: list = []

    def _record(returned_chars: int, truncated: bool, total_chars: int | None) -> None:
        """Append one call's extent: what was handed over, what was withheld, and out of what.

        `returned_chars` excludes the in-band truncation marker. A failed read is zero characters
        and not truncated: "read nothing" and "was cut off" must not collapse into one fact.
        """
        ledger.append(
            {
                "returned_chars": returned_chars,
                "truncated": truncated,
                "total_chars": total_chars,
            }
        )

    def read_file(path: str) -> str:
        """Read a repository-relative file under `fsguard`'s containment and shape guards.

        The model chooses this path itself, out of the same untrusted tree enumeration walks, so
        `classify_unit` settles shape by `lstat` before any `open`: a FIFO named `notes.md` would
        block the scan forever. Every refusal is returned as an `error: …` string, never raised.
        """
        shape = classify_unit(repo_root, path)
        if shape.skip_reason == "escapes-repo":
            _record(0, False, None)
            return "error: path escapes the repo"
        if shape.skip_reason is not None or shape.path is None:
            # Refused without being opened: nothing read, nothing withheld, no denominator.
            _record(0, False, None)
            return f"error: not a readable regular file ({shape.skip_reason})"
        remaining = unit_char_budget - consumed["chars"]
        if remaining <= 0:
            _record(0, True, None)
            return "error: per-unit tool-output budget exhausted; decide from gathered context"
        bound = min(read_char_cap, remaining)
        try:
            with open(shape.path, "rb") as fh:
                # One byte over the bound, so truncation is decided by what came back
                # rather than by a `stat` taken a moment earlier.
                data = fh.read(bound + 1)
        except OSError as exc:
            _record(0, False, None)
            return f"error: {exc}"
        truncated = len(data) > bound
        # `errors="replace"`: the bound can cut a multibyte sequence, and a tool must not die.
        chunk = data[:bound].decode("utf-8", errors="replace")
        consumed["chars"] += len(chunk)
        # A truncated read never saw the whole file, so its denominator is the file's byte
        # size: an upper bound on characters, which understates coverage rather than the reverse.
        _record(len(chunk), truncated, shape.size_bytes if truncated else len(chunk))
        if truncated:
            chunk += "\n…[truncated]"
        return chunk

    def glob(pattern: str) -> str:
        """List repository files matching `pattern`, sorted, at most 500 of them."""
        hits = []
        for dirpath, dirnames, filenames in os.walk(repo_root):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for f in filenames:
                rel = os.path.relpath(os.path.join(dirpath, f), repo_root)
                if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(f, pattern):
                    hits.append(rel)
        listing = sorted(hits)
        shown = listing[:500]
        out = "\n".join(shown) or "(no matches)"
        # Summed rather than joined: the join would exist only to be measured, over an
        # untrusted repository's whole file list. The no-matches text counts as zero content.
        total = sum(len(p) for p in listing) + max(len(listing) - 1, 0)
        _record(sum(len(p) for p in shown) + max(len(shown) - 1, 0), len(hits) > 500, total)
        return out

    specs = {
        "read_file": ToolSpec(
            "read_file",
            "Read a repo-relative text file.",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            read_file,
            ledger,
        ),
        "glob": ToolSpec(
            "glob",
            "List repo files matching a glob pattern (e.g. *.md, docs/*.rst).",
            {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
            glob,
            ledger,
        ),
    }
    return [specs[n] for n in names]
