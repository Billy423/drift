"""A class from this repository carries the named member.

Fits "ClassName has a configurable X". Where that surface is class attributes rather than
`__init__` parameters — traitlets, attrs, pydantic — a signature check answers the wrong question.
"""

from __future__ import annotations

import re

import griffe

from drift.kernels.models import Ungateable
from drift.kernels.pysymbols import bases_closure, own_top_levels, provider_for, walk_node
from drift.kernels.registry import Predicate, register_predicate

_DOTTED = re.compile(r"^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+$")
_IDENT = re.compile(r"^[A-Za-z_]\w*$")


def _normalize(
    literal: str, doc_path: str, proposed_args: tuple[str, ...] | None = None
) -> tuple[dict, tuple[str, ...]] | None:
    """Validate a proposed `(class, member)` pair, or decline.

    Neither can be derived from arbitrary document text, so both must be proposed; this is where
    they are checked for shape.
    """
    if not proposed_args or len(proposed_args) != 2:
        return None
    cls, member = proposed_args[0].strip(), proposed_args[1].strip()
    if not _DOTTED.match(cls) or not _IDENT.match(member):
        return None
    return {"source": "proposed"}, (cls, member)


def _kernel(repo_root: str, dotted_class: str, member_name: str) -> bool:
    """Pure check against the repository at one revision: is the member present?

    Present on the class or on an in-package base is True. Absent with every base resolvable is
    a refutation; absent under a base that cannot be read is not, and declines instead.
    Collapsing those two is the failure this predicate exists to avoid.
    """
    if dotted_class.split(".")[0] not in own_top_levels(repo_root):
        raise Ungateable("external")
    provider = provider_for(repo_root)
    node = walk_node(provider, dotted_class)
    if node is None:
        raise Ungateable("module-unreachable")
    try:
        kind = node.kind
    except Exception:
        raise Ungateable("module-unreachable") from None
    if kind is not griffe.Kind.CLASS:
        raise Ungateable("not-a-class")

    own = getattr(node, "members", None) or {}
    if member_name in own:
        return True
    bases, unresolvable = bases_closure(provider, node)
    for base in bases:
        try:
            members = getattr(base, "members", None) or {}
        except Exception:  # alias to an unloadable target: unreadable, not absent
            unresolvable = True
            continue
        if member_name in members:
            return True
    if unresolvable:
        raise Ungateable("external-base")
    return False


CLASS_HAS_MEMBER = Predicate(
    name="class_has_member",
    description=(
        "asserts that a class from THIS repo carries the named member — a configurable "
        "option, attribute, or method (e.g. 'ServerApp has a configurable port'); "
        "args must be [dotted_class, member_name]"
    ),
    normalize=_normalize,
    kernel=_kernel,
    grade="preview",
)
register_predicate(CLASS_HAS_MEMBER)
