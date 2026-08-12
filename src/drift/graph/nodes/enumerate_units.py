"""Finding the units a scan will read, and refusing the ones it cannot."""

from __future__ import annotations

import os

from drift.agent.repo_map import _git_ls_files
from drift.fsguard import B_DOC, classify_unit
from drift.graph.nodes.rails import _safe_journal
from drift.graph.progress import progress
from drift.graph.state import ScanState

_DOC_EXTS = (".md", ".rst", ".txt")


def enumerate_docs(repo_root: str) -> tuple[list[str], list[dict]]:
    """Repository-relative `*.md`, `*.rst` and `*.txt` paths under `repo_root`, sorted.

    Scoped to the files the repository declares as its own — a checkout also carries installed
    packages and vendored trees, which are not its documents — so a file that was never added is
    not scanned. A directory that is not a repository has nothing to declare and is walked whole.

    Returns:
        `(worklist, hazards)`, where a hazard is
        `{"unit", "disposition", "reason", "size_bytes"}`. A unit that cannot be read safely is
        `skipped` and leaves the worklist; an oversize one is `truncated` and stays in it,
        because truncation is not a skip. The caller journals and reports both.
    """
    tracked = _git_ls_files(repo_root)
    hits: list[str] = []
    hazards: list[dict] = []
    # `os.walk` does not follow directory symlinks, so only the files it lists need containment.
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            if not f.lower().endswith(_DOC_EXTS):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), repo_root)
            if tracked is not None and rel not in tracked:
                continue
            shape = classify_unit(repo_root, rel)
            if shape.skip_reason is not None:
                hazards.append(
                    {
                        "unit": rel,
                        "disposition": "skipped",
                        "reason": shape.skip_reason,
                        "size_bytes": 0,
                    }
                )
                continue
            hits.append(rel)
            if shape.oversize:
                hazards.append(
                    {
                        "unit": rel,
                        "disposition": "truncated",
                        "reason": f"over B_doc={B_DOC}",
                        "size_bytes": shape.size_bytes,
                    }
                )
    return sorted(hits), sorted(hazards, key=lambda h: h["unit"])


def _worklist_of(state: ScanState) -> tuple[list[str], list[dict]]:
    """The units this invocation scans, always the ones the frame planned.

    Nothing below the frame walks the tree, so no second enumeration can disagree with the plan
    the frame journaled — including the document filter, which the frame applied before planning.
    """
    return list(state.get("planned_worklist") or []), list(state.get("planned_hazards") or [])


def _hazard_note(hazard: dict) -> str:
    """The banner line for one enumeration hazard — a skip the report would otherwise not show."""
    if hazard["disposition"] == "skipped":
        return (
            f"{hazard['unit']}: skipped at enumeration ({hazard['reason']}); not read, not scanned."
        )
    return (
        f"{hazard['unit']}: {hazard['size_bytes']} bytes {hazard['reason']}; input truncated at "
        f"the bound — claims come from the leading {B_DOC} characters only."
    )


def make_adopt_worklist(writer):
    """Return a node that adopts the frame's worklist and dispositions its enumeration hazards.

    Not on the path a scan takes. The node loops the run's whole hazard list while the row it
    writes is per unit, so handing that list to every cell would duplicate each skipped unit's
    coverage row. The frame writes them once at plan time; this serves the tests and `build_graph`.
    """

    def adopt_worklist(state: ScanState) -> dict:
        """Node: adopt the planned units, and turn each enumeration hazard into a disposition."""
        partial_notes = list(state.get("partial_notes", []))
        worklist, hazards = _worklist_of(state)
        coverages = list(state.get("coverages", []))
        for hazard in hazards:
            partial_notes.append(_hazard_note(hazard))
            if hazard["disposition"] != "skipped":
                continue  # a truncated unit reports through its own coverage row, in `discover`
            # A skipped unit still gets a coverage row: that stream is what the report's
            # incompleteness banner names, so a unit never scanned cannot leave the denominator.
            coverage = {
                "unit": hazard["unit"],
                "doc_hash": "",
                "turns_used": 0,
                "tool_calls": 0,
                "status": "skipped",
                "detail": f"{hazard['reason']}: not read (enumeration safety)",
            }
            coverages.append(coverage)
            _safe_journal(
                writer,
                state,
                partial_notes,
                hazard["unit"],
                lambda cov=coverage: writer.write("agent", "agent_coverage", cov),
            )
        progress(f"worklist: {len(worklist)} unit(s)")
        if hazards:
            progress(
                f"enumeration: {len(hazards)} hazard(s) — "
                + "; ".join(f"{h['unit']} {h['disposition']}" for h in hazards)
            )
        return {"worklist": worklist, "partial_notes": partial_notes, "coverages": coverages}

    return adopt_worklist
