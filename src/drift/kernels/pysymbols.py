"""Shared static-analysis access for the symbol kernels, memoized per repository."""

from __future__ import annotations

import griffe

from drift.domain.repo import RepoRef
from drift.kernels.models import Ungateable
from drift.symbols.griffe_provider import GriffeSymbolProvider

_providers: dict[str, GriffeSymbolProvider] = {}


def provider_for(repo_root: str) -> GriffeSymbolProvider:
    """The repository's symbol provider, built once per process and reused.

    Keyed on the path, not on a revision: a scan reads one checkout, but a tree that changed
    under a long-lived process would keep the load from the first scan.
    """
    if repo_root not in _providers:
        _providers[repo_root] = GriffeSymbolProvider(RepoRef(path=repo_root))
    return _providers[repo_root]


def own_top_levels(repo_root: str) -> set[str]:
    """The repository's own importable top-level names; anything else is external."""
    p = provider_for(repo_root)
    names: set[str] = set()
    for search_path in p._search_paths():
        names |= p._discover(search_path)
    return names


def walk_node(provider: GriffeSymbolProvider, dotted: str):
    """Walk to a loaded node without resolving aliases; None if any part is missing.

    Alias resolution raises on an external re-export, and this walk has to be able to fail
    quietly instead.
    """
    node = provider._loaded().get(dotted.split(".")[0])
    for part in dotted.split(".")[1:]:
        if node is None:
            return None
        try:
            node = (getattr(node, "members", None) or {}).get(part)
        except Exception:
            return None
    return node


def bases_closure(provider: GriffeSymbolProvider, cls) -> tuple[list, bool]:
    """A class's resolvable base nodes, transitively, and whether any base was unresolvable.

    The bool is the load-bearing half: an unresolvable base means the member's truth lives in a
    package this kernel does not load, which is a decline and never a refutation.
    """
    resolved, unresolvable = [], False
    seen, queue = set(), list(getattr(cls, "bases", []) or [])
    while queue:
        base = queue.pop(0)
        try:
            name = getattr(base, "canonical_path", None) or str(base)
        except Exception:
            unresolvable = True
            continue
        if name in seen:
            continue
        seen.add(name)
        node = walk_node(provider, name)
        if node is None:
            unresolvable = True  # external or otherwise unreachable
            continue
        try:
            # An alias to an unloadable target raises on any attribute access, and getattr's
            # default swallows only AttributeError: unreadable is unresolvable, not absent.
            resolved.append(node)
            queue.extend(getattr(node, "bases", []) or [])
        except Exception:
            resolved.pop()
            unresolvable = True
    return resolved, unresolvable


def resolve_layered(repo_root: str, dotted_name: str):
    """Resolve a dotted name through the layers the symbol kernels share.

    A top-level name that is not this repository's is `external`. A missing intermediate part,
    or an own top-level that failed to load, is `module-unreachable`: generated code is not
    statically expectable, so this declines rather than guesses. A final attribute missing on a
    class falls to base traversal. None means the absence was earned, and the claim is refuted.
    """
    parts = dotted_name.split(".")
    if parts[0] not in own_top_levels(repo_root):
        raise Ungateable("external")
    provider = provider_for(repo_root)
    top = provider._loaded().get(parts[0])
    if top is None:  # discovered on disk but not loadable
        raise Ungateable("module-unreachable")
    obj = top
    for i, part in enumerate(parts[1:], start=1):
        members = getattr(obj, "members", None)
        nxt = members.get(part) if members is not None else None
        if nxt is None:
            if i < len(parts) - 1:
                raise Ungateable("module-unreachable")
            return _resolve_inherited(provider, obj, part)
        obj = nxt
    return provider.resolve(dotted_name)


def _resolve_inherited(provider: GriffeSymbolProvider, obj, part: str):
    """Consult in-package bases before calling a missing final attribute a refutation.

    `.members` is own-only, so an inherited member is invisible without this walk.
    """
    try:
        is_class = obj.kind is griffe.Kind.CLASS
    except Exception:
        is_class = False
    if not is_class:
        return None  # non-class parent: absence is a refutation
    bases, unresolvable = bases_closure(provider, obj)
    for base in bases:
        try:
            members = getattr(base, "members", None) or {}
        except Exception:  # alias to an unloadable target
            unresolvable = True
            continue
        if part in members:
            try:
                resolved = provider.resolve(f"{base.canonical_path}.{part}")
            except Exception:
                resolved = None
            if resolved is not None:
                return resolved
            # Seen on a base but no symbol can be produced for it: absent information,
            # never a refutation.
            raise Ungateable("external-base")
    if unresolvable:
        raise Ungateable("external-base")
    return None  # every base was readable, so the refutation is earned
