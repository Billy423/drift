"""The Python symbol reader: a repository's API read statically, without importing its code."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable

import griffe

from drift.domain.findings import Location
from drift.domain.repo import RepoRef
from drift.symbols.provider import (
    DocstringClaim,
    ParamDoc,
    Parameter,
    Signature,
    Symbol,
    SymbolKind,
)

# Griffe reports undocumented parameters on its own logger. This module does its own
# comparison, so keep that chatter out of command output.
logging.getLogger("griffe").setLevel(logging.ERROR)

_SKIP_MODULES = {"setup", "conftest"}

_KIND_MAP = {
    griffe.Kind.MODULE: SymbolKind.MODULE,
    griffe.Kind.CLASS: SymbolKind.CLASS,
    griffe.Kind.FUNCTION: SymbolKind.FUNCTION,
    griffe.Kind.ATTRIBUTE: SymbolKind.ATTRIBUTE,
}


def _map_kind(obj) -> str:
    if obj.kind is griffe.Kind.FUNCTION and getattr(obj.parent, "kind", None) is griffe.Kind.CLASS:
        return SymbolKind.METHOD
    return _KIND_MAP.get(obj.kind, SymbolKind.ATTRIBUTE)


def _signature_of(func) -> Signature:
    params = tuple(
        Parameter(name=p.name, kind=p.kind.value, has_default=p.default is not None)
        for p in func.parameters
        if p.name not in ("self", "cls")
    )
    return Signature(parameters=params)


def _doc_param_names(func) -> tuple[str, ...] | None:
    """Param names a function's docstring documents, or None if it has no Parameters section."""
    if func.docstring is None:
        return None
    for section in griffe.parse_auto(func.docstring):
        if section.kind is griffe.DocstringSectionKind.parameters:
            return tuple(item.name for item in section.value)
    return None


def _walk_functions(obj):
    """Yield every function/method object reachable in a loaded module tree."""
    for member in obj.members.values():
        if isinstance(member, griffe.Alias):  # don't follow re-exports out of the repo
            continue
        if member.kind is griffe.Kind.FUNCTION:
            yield member
        elif member.kind is griffe.Kind.CLASS or member.kind is griffe.Kind.MODULE:
            yield from _walk_functions(member)


def _class_init(cls):
    """A class's `__init__`, following re-export aliases, or None.

    `.members` is own-only, so an inherited or synthesized `__init__` is simply absent, and the
    caller is then left with no signature to check a documented constructor call against.
    """
    try:
        init = cls.members.get("__init__")
        if init is None or init.kind is not griffe.Kind.FUNCTION:
            return None
    except Exception:
        return None  # unresolvable alias — cannot statically know the constructor
    return init


def _location_of(func, repo_root: str) -> Location | None:
    """Function's code location, file path relative to the repo root (None if unknown)."""
    if func.filepath is None:
        return None
    rel = os.path.relpath(str(func.filepath), repo_root)
    start = func.lineno or 1
    end = func.endlineno or start
    return Location(file=rel, start_line=start, end_line=end)


class GriffeSymbolProvider:
    """Static-mode Griffe adapter: the repo's real symbol table as the normalized model."""

    def __init__(self, repo: RepoRef) -> None:
        self._repo = repo
        self._top: dict[str, object] | None = None

    def _search_paths(self) -> list[str]:
        paths = [self._repo.path]
        src = os.path.join(self._repo.path, "src")
        if os.path.isdir(src):
            paths.insert(0, src)
        return paths

    def _discover(self, search_path: str) -> set[str]:
        names: set[str] = set()
        if not os.path.isdir(search_path):
            return names
        for entry in os.listdir(search_path):
            full = os.path.join(search_path, entry)
            if os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
                names.add(entry)
            elif entry.endswith(".py") and entry[:-3] not in _SKIP_MODULES:
                names.add(entry[:-3])
        return names

    def _loaded(self) -> dict[str, object]:
        if self._top is not None:
            return self._top
        # allow_inspection=False is the safety property, not a tuning knob: Griffe's default
        # is to import a module it cannot parse statically, and the tree here is untrusted.
        loader = griffe.GriffeLoader(search_paths=self._search_paths(), allow_inspection=False)
        top: dict[str, object] = {}
        for search_path in self._search_paths():
            for name in self._discover(search_path):
                if name in top:
                    continue
                try:
                    top[name] = loader.load(name)  # static AST visit only; see the loader above
                except griffe.LoadingError:
                    continue
        self._top = top
        return top

    def _lookup(self, dotted_name: str):
        parts = dotted_name.split(".")
        top = self._loaded().get(parts[0])
        if top is None:
            return None
        if len(parts) == 1:
            return top
        try:
            return top[".".join(parts[1:])]
        except KeyError:
            return None

    def resolve(self, dotted_name: str) -> Symbol | None:
        """Look up a dotted name in the repo's static symbol table; None means not in this repo."""
        obj = self._lookup(dotted_name)
        if obj is None:
            return None
        sig_source = _class_init(obj) if obj.kind is griffe.Kind.CLASS else obj
        is_func = sig_source is not None and sig_source.kind is griffe.Kind.FUNCTION
        signature = _signature_of(sig_source) if is_func else None
        location = _location_of(sig_source, self._repo.path) if is_func else None
        return Symbol(
            dotted_name=dotted_name,
            kind=_map_kind(obj),
            location=location,
            signature=signature,
        )

    def documented_symbols(self) -> Iterable[Symbol]:
        """Yield every documented function/method as a Symbol with signature + docstring claim."""
        for top in self._loaded().values():
            for func in _walk_functions(top):
                doc_params = _doc_param_names(func)
                if doc_params is None:
                    continue  # no Parameters section -> nothing to check
                yield Symbol(
                    dotted_name=func.canonical_path,
                    kind=_map_kind(func),
                    location=_location_of(func, self._repo.path),
                    signature=_signature_of(func),
                    docstring=DocstringClaim(documented_params=doc_params),
                )

    def documented_param_docs(self) -> Iterable[ParamDoc]:
        """Yield one record per documented parameter, carrying the doc line that names it.

        The whole doc line is the anchor, not the parameter name: a bare name occurs anywhere in
        the file, and the line is what a repair to the document deletes. The name must be the
        whole token before the separator, or `Distribution | None : …` binds to a parameter
        called `Distribution`. A parameter whose line cannot be found is skipped.
        """
        for top in self._loaded().values():
            for func in _walk_functions(top):
                doc_params = _doc_param_names(func)
                if doc_params is None or func.docstring is None or func.filepath is None:
                    continue
                value_lines = func.docstring.value.splitlines()
                rel = os.path.relpath(str(func.filepath), self._repo.path)
                # Docstring-relative, so approximate under griffe's cleaning: display only.
                base_line = func.docstring.lineno or (func.lineno or 1)
                for name in doc_params:
                    name_then_colon = re.compile(rf"^{re.escape(name)}\s*:")
                    for offset, line in enumerate(value_lines):
                        stripped = line.strip()
                        if (
                            name_then_colon.match(stripped)
                            or stripped == name
                            or stripped.startswith(f":param {name}:")
                        ):
                            yield ParamDoc(
                                dotted_name=func.canonical_path,
                                param=name,
                                doc_line=stripped,
                                file=rel,
                                line_no=base_line + offset,
                                signature=_signature_of(func),
                            )
                            break
