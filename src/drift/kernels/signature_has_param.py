"""A parameter a document names exists on the symbol's real signature."""

from __future__ import annotations

import re

from drift.kernels.models import Ungateable
from drift.kernels.pysymbols import resolve_layered
from drift.kernels.registry import Predicate, register_predicate

_DOTTED = re.compile(r"^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+$")
_IDENT = re.compile(r"^[A-Za-z_]\w*$")
_VARIADIC_PREFIX = "variadic"


def _normalize(
    literal: str, doc_path: str, proposed_args: tuple[str, ...] | None = None
) -> tuple[dict, tuple[str, ...]] | None:
    """Validate a proposed `(symbol, param)` pair, or decline.

    Neither can be derived from arbitrary document text. The discovery agent proposes them; the
    docstring producer supplies them mechanically.
    """
    if not proposed_args or len(proposed_args) != 2:
        return None
    symbol, param = proposed_args[0].strip(), proposed_args[1].strip()
    if not _DOTTED.match(symbol) or not _IDENT.match(param):
        return None
    return {"source": "proposed"}, (symbol, param)


def _kernel(repo_root: str, dotted_name: str, param: str) -> bool:
    """Pure check against the repository at one revision: is the parameter present?

    Absent under a variadic proves nothing, so it declines rather than refutes.
    """
    symbol = resolve_layered(repo_root, dotted_name)
    if symbol is None:
        return False  # absent under a reachable parent: the claim is refuted
    if symbol.signature is None:
        # A signature can be statically underivable — an `__init__` from an unloadable base,
        # say. Absent information is never a refutation.
        raise Ungateable("no-signature")
    names = {p.name for p in symbol.signature.parameters}
    if param in names:
        return True
    if any(p.kind.startswith(_VARIADIC_PREFIX) for p in symbol.signature.parameters):
        raise Ungateable("variadic")
    return False


SIGNATURE_HAS_PARAM = Predicate(
    name="signature_has_param",
    # The class-constructor case is left unmentioned rather than excluded in words: an explicit
    # exclusion suppressed discovery. Its home is `class_has_member`.
    description=(
        "asserts that a function/method from THIS repo accepts the named "
        "parameter; args must be [dotted_symbol, param_name]"
    ),
    normalize=_normalize,
    kernel=_kernel,
)
register_predicate(SIGNATURE_HAS_PARAM)
