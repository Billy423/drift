"""Deciding what a scan will do, before it does any of it.

Planning is outside the run: a document this repository cannot scan is refused with no run
row and no cost row for work that never happened.
"""

from __future__ import annotations

import difflib
import os
from collections.abc import Sequence
from dataclasses import dataclass

from drift.agent.repo_map import _git_ls_files
from drift.docstrings import _PYTHON_SUFFIXES
from drift.fsguard import B_DOC
from drift.graph import (
    cell,  # the dispatch vocabulary
)
from drift.graph.nodes import rails
from drift.graph.nodes.enumerate_units import enumerate_docs
from drift.graph.progress import progress
from drift.kernels.models import PRODUCERS
from drift.symbols.griffe_provider import _SKIP_MODULES


def _enforce_max_units(worklist: list[str]) -> None:
    """The coarse pre-spend gate: an uncapped scan runs one paid model loop per document unit.

    It raises rather than journaling a rail stop, because a rail that fires before any work
    started is a refusal to run and not the truncation of a run. Denominated in document units,
    so the docstring producer's single corpus-wide cell is not one.
    """
    cap = rails.MAX_UNITS
    if len(worklist) > cap:
        raise RuntimeError(
            f"worklist has {len(worklist)} doc units (cap {cap}): an uncapped scan runs one "
            f"paid discovery loop per unit. Scan a single document with `drift check`, or "
            f"raise MAX_UNITS deliberately."
        )


def plan_cells(worklist: list[str]) -> list[tuple[str, str]]:
    """The run's cell list: the docstring producer's one cell, then one per document unit.

    A cell key is a dispatch address, never hashed and never part of a claim's identity. The
    docstring producer leads because its discovery half costs nothing, so a short wallet must not
    be able to drop it. This order is also dispatch order: the first cell dispatched takes its
    judge allowance out of the per-run candidate cap before any other cell sees the remainder.
    """
    cells = [(cell.LANE_B_PRODUCER, cell.LANE_B_UNIT_REF)]
    cells += [(cell.LANE_A_PRODUCER, unit_ref) for unit_ref in worklist]
    for producer, unit_ref in cells:
        if producer not in PRODUCERS:
            raise ValueError(
                f"cell {(producer, unit_ref)!r} names producer {producer!r}, which is not in "
                f"the closed vocabulary {sorted(PRODUCERS)}. A dispatch address may not invent "
                f"a producer; the set is closed (kernels/models.PRODUCERS)."
            )
    return cells


#: Dollars per input character, for the pre-flight estimate only. Total cost over total characters,
#: not the mean of the per-unit rates, which comes out several times higher.
DOLLARS_PER_CHAR = 1.344560e-05


def _estimate_usd(repo_root: str, worklist: list[str]) -> float:
    """The run's pre-flight dollar estimate: input characters × `DOLLARS_PER_CHAR`.

    A unit larger than `B_DOC` contributes `B_DOC`: the read is bounded there, so charging for
    bytes the pipeline will never send would over-warn on exactly the oversized documents the
    bound exists for.
    """
    total = 0
    for unit_ref in worklist:
        try:
            total += min(os.path.getsize(os.path.join(repo_root, unit_ref)), B_DOC)
        except OSError:
            continue  # a unit that vanished between enumeration and here estimates as zero
    return total * DOLLARS_PER_CHAR


@dataclass(frozen=True)
class ScanPlan:
    """What a scan will do, decided before anything is written down.

    Attributes:
        worklist: The units this run scans. Empty for a Python target, whose producer walks the
            symbol table instead of a document list.
        hazards: The units enumeration refused or truncated, each with its reason.
        enumerated_count: How many units the repository holds — deliberately not the size of
            `worklist`. An error message quotes it back, and narrowing to one document must not
            change what the repository is said to contain.
        doc_filter: The resolved document, repository-relative, or None for a whole repository.
        kind: "repo", "doc" or "py". An empty worklist is normal for a Python target and
            impossible for a document one.
    """

    worklist: list[str]
    hazards: list[dict]
    enumerated_count: int
    estimate_usd: float
    doc_filter: str | None
    # Classified but not yet read: the pre-flight line is the same for either empty
    # worklist, and whether it should differ is a question about published output.
    kind: str


class DocumentNotResolvable(Exception):
    """The named document is not something this repository can scan.

    Carries what a message needs rather than a formatted message: the caller that renders it owns
    every other user-facing string.
    """

    def __init__(
        self,
        spelling: str,
        candidates_tried: list[str],
        hazard_reason: str | None,
        enumerated_count: int,
        near_misses: list[str],
    ) -> None:
        super().__init__(spelling)
        self.spelling = spelling
        self.candidates_tried = candidates_tried
        self.hazard_reason = hazard_reason
        self.enumerated_count = enumerated_count
        self.near_misses = near_misses


def _symbol_corpus_members(repo_root: str) -> frozenset[str]:
    """Tracked Python files the symbol walk would actually load.

    Tracked is necessary and not sufficient: the walk reaches only files under a directory
    carrying an `__init__.py` and admitted top-level modules, so accepting anything else gives a
    run that completes clean having scanned nothing. It reads the tracked list rather than the
    filesystem because that list is case-exact — `isfile("Foo.py")` is true when only `foo.py` is.
    """
    tracked = _git_ls_files(repo_root)
    if tracked is None:
        return frozenset()

    members = set()
    for rel in tracked:
        if not rel.endswith(_PYTHON_SUFFIXES):
            continue
        for base in ("", "src"):
            if base and not rel.startswith(base + "/"):
                continue
            remainder = rel[len(base) + 1 :] if base else rel
            parts = remainder.split("/")
            head = os.path.join(repo_root, base, parts[0])
            if len(parts) > 1:
                if os.path.isfile(os.path.join(head, "__init__.py")):
                    members.add(rel)
                    break
            elif remainder.endswith(".py") and remainder[:-3] not in _SKIP_MODULES:
                # Stub files load inside a package but are never discovered as top-level modules.
                members.add(rel)
                break
    return frozenset(members)


def plan_scan(repo_root: str, candidates: Sequence[str] = ()) -> ScanPlan:
    """Enumerate the repository once, resolve the requested document, and price the work.

    Nothing here writes: there is no session, no writer and no run, which is what lets an
    unresolvable document be refused without leaving a trace of a run that never happened.

    Args:
        candidates: Repository-relative spellings in preference order; the first that names
            something scannable wins.

    Raises:
        DocumentNotResolvable: If no candidate names a scannable unit. One that names a unit
            enumeration deliberately skipped carries that reason rather than reading as absent.
    """
    worklist, hazards = enumerate_docs(repo_root)
    unit_count = len(worklist)

    if not candidates:
        return ScanPlan(
            worklist=worklist,
            hazards=hazards,
            enumerated_count=unit_count,
            estimate_usd=_estimate_usd(repo_root, worklist),
            doc_filter=None,
            kind="repo",
        )

    units = set(worklist)
    corpus: frozenset[str] | None = None
    for candidate in candidates:
        if candidate in units:
            narrowed = [candidate]
            return ScanPlan(
                worklist=narrowed,
                hazards=[h for h in hazards if h["unit"] == candidate],
                enumerated_count=unit_count,
                estimate_usd=_estimate_usd(repo_root, narrowed),
                doc_filter=candidate,
                kind="doc",
            )
        if candidate.endswith(_PYTHON_SUFFIXES):
            if corpus is None:
                corpus = _symbol_corpus_members(repo_root)
            if candidate in corpus:
                return ScanPlan(
                    worklist=[],
                    hazards=[],
                    enumerated_count=unit_count,
                    estimate_usd=0.0,
                    doc_filter=candidate,
                    kind="py",
                )

    skipped = {h["unit"]: h["reason"] for h in hazards if h["disposition"] == "skipped"}
    for candidate in candidates:
        if candidate in skipped:
            raise DocumentNotResolvable(
                candidate, list(candidates), skipped[candidate], unit_count, []
            )
    raise DocumentNotResolvable(
        candidates[0],
        list(candidates),
        None,
        unit_count,
        difflib.get_close_matches(candidates[0], sorted(units), n=3),
    )


def _print_preflight(repo_root: str, worklist: list[str], doc_filter: str | None = None) -> None:
    """Print a one-line cost estimate for the planned scan. Advisory: it never blocks a run.

    An empty worklist under a document filter prints its own line — the filter matches by exact
    string, and a misspelled path otherwise produces a run that completes looking clean.
    """
    if not worklist:
        if doc_filter is not None:
            progress(
                f"0 doc unit(s) matched {doc_filter} — the discovery lane scans nothing "
                f"this run (the docstring lane still runs)."
            )
        return
    progress(
        f"pre-flight estimate: ~${_estimate_usd(repo_root, worklist):.2f} for "
        f"{len(worklist)} doc unit(s) — a WARNING, not a limit. Basis: input chars × "
        f"${DOLLARS_PER_CHAR:.2e}/char over 157 measured unit-runs, whose correlation is WEAK "
        f"(spearman = 0.6785). Treat it as an order of magnitude, not a quote."
    )
