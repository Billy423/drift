"""A dotted name a document mentions resolves in this repository's own symbol table."""

from __future__ import annotations

import re

from drift.kernels.pysymbols import resolve_layered
from drift.kernels.registry import Predicate, register_predicate

_DOTTED = re.compile(r"^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+$")


def _normalize(
    literal: str, doc_path: str, proposed_args: tuple[str, ...] | None = None
) -> tuple[dict, tuple[str, ...]] | None:
    """Turn a dotted-name literal into the name to resolve, or decline.

    The one `normalize` in which a proposal overrides the literal. Its siblings either ignore
    `proposed_args` or require it outright, so generalising from them gets this backwards.
    """
    norm: dict = {}
    name = literal.strip()
    if len(name) >= 2 and name.startswith("`") and name.endswith("`"):
        name = name[1:-1]
        norm["stripped"] = "backticks"
    if name.endswith("()"):
        name = name[:-2]
        norm["call_parens"] = "stripped"
    if proposed_args:  # a validated proposal wins over the raw literal
        name = proposed_args[0]
        norm["source"] = "proposed"
    if not _DOTTED.match(name):
        return None
    return norm, (name,)


def _kernel(repo_root: str, dotted_name: str) -> bool:
    """Pure check against the repository at one revision, through the layered resolver."""
    return resolve_layered(repo_root, dotted_name) is not None


SYMBOL_RESOLVES = Predicate(
    name="symbol_resolves",
    description=(
        "asserts that a dotted Python name from THIS repo's own packages resolves in its "
        "static symbol table (stdlib/third-party names do not bind — leave them unbound)"
    ),
    normalize=_normalize,
    kernel=_kernel,
)
register_predicate(SYMBOL_RESOLVES)
