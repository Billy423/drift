"""The frame's run-scoped journal rows, and the two figures it prints alongside them.

Separated from the frame because these are writes, not decisions: nothing here reads a row
back or changes what the run does next.
"""

from __future__ import annotations

import sys

from drift.agent.discovery import prompt_fingerprint as agent_prompt_fingerprint
from drift.cost import PRICE_TABLE_VER
from drift.fsguard import B_DOC
from drift.graph.nodes import enumerate_units, rails
from drift.graph.progress import progress
from drift.journal import run_cost
from drift.journal.export import export_run
from drift.judge.semantic_judge import prompt_fingerprint as judge_prompt_fingerprint
from drift.kernels.link_resolves import LINK_JURISDICTION_VERSION
from drift.kernels.models import PRODUCERS_VER
from drift.report.render import SUSPECTED_BAND_MAX


def _export_journal(session, run_id: int, path: str | None) -> None:
    """Write the run's journal artifact, for every run and not only a successful one.

    Never raises: it runs in the same `finally` as the cost row, and an aborted run is exactly
    the one whose artifact someone will want. One file per run — `export_run` opens with `"w"`,
    so a per-cell copy would leave only whichever cell finished last.
    """
    if path is None:
        return
    try:
        n = export_run(session, run_id, path)
    except Exception as exc:
        print(f"[drift] journal export failed: {exc!r}", file=sys.stderr, flush=True)
        return
    print(f"[drift] journal: {n} rows -> {path}", file=sys.stderr, flush=True)


def _record_run_cost(
    session, writer, run_id: int, model: str, completed: bool, unreported=()
) -> None:
    """Journal the run's cost total, then print it — one figure, two surfaces.

    Computing it from the run's own journaled rows, rather than from in-memory state an
    exception would already have destroyed, is what lets this run in a `finally`. It never
    raises: a cost line is not worth replacing the error that caused it.

    Args:
        completed: Whether every dispatched cell reported and the frame's tail finished.
            Journaled as `graph_completed`; when false the figure is a floor rather than a
            total, since a cell that outlived the frame can still journal usage afterwards.
        unreported: The cells that were dispatched and never wrote a result row.
    """
    summary = None
    try:
        summary = run_cost.summarize_run_cost(session, run_id, model, completed=completed)
    except Exception as exc:
        # The transaction is already broken, so nothing pending survives anyway.
        print(f"[drift] run_cost: session unusable ({exc!r}); resetting", file=sys.stderr)
        try:
            writer.rollback()
            summary = run_cost.summarize_run_cost(session, run_id, model, completed=completed)
        except Exception as retry_exc:
            print(f"[drift] usage: unavailable ({retry_exc!r})", file=sys.stderr, flush=True)
            return
    summary = {
        **summary,
        "spend_is_floor": not completed,
        "unreported_cells": [list(key) for key in unreported],
    }
    try:
        writer.write("run", "run_cost", summary)
        writer.flush()  # durable before the exception (if any) finishes propagating
    except Exception as exc:
        # No rollback here: reconcile's Issue rows are pending on this session until the
        # terminal commit, and the issue lifecycle is not reconstructible from the journal.
        print(
            f"[drift] run_cost: journal write FAILED ({exc!r}) — the figure below is NOT in "
            f"the artifact, and this run is not fit to publish a cost from",
            file=sys.stderr,
        )
    tokens = summary["tokens"]
    print(
        f"[drift] usage: in={tokens['input_tokens']} "
        f"cache_read={tokens['cache_read_input_tokens']} "
        f"cache_write={tokens['cache_creation_input_tokens']} "
        f"out={tokens['output_tokens']} "
        f"spend=${summary['spend_usd']:.4f}",
        file=sys.stderr,
        flush=True,
    )


def _write_run_row(writer, record_type: str, payload: dict, component: str = "run") -> None:
    """Journal one of the frame's run-scoped rows: raising on failure, flushed immediately.

    The nodes' journal path is fail-soft to protect work already paid for. These rows are written
    before the run has spent anything, so a journal that cannot take a free row here will fail
    again after a scan's worth of paid calls.

    Args:
        component: Overridden only where the frame writes a row on another component's behalf.
    """
    writer.write(component, record_type, payload)
    writer.flush()


def _write_rail_config(writer, budget: float, strict_measurement: bool, cap: int) -> None:
    """Journal the run's self-description — one row per run, written before any cell exists.

    A run's rail settings are a claim about how any number it produced came about, so a run that
    dies on a rail still has to carry them.

    Args:
        cap: The run's configured candidate cap, not the allowance any one cell receives. The
            two diverge as soon as cells report, and this row must go on saying what the run was
            configured with.
    """
    _write_run_row(
        writer,
        "rail_config",
        {
            "max_units": rails.MAX_UNITS,
            "max_s_candidates": cap,
            # null, not Infinity: JSON has no Infinity and PostgreSQL rejects the literal.
            "budget": None if budget == float("inf") else budget,
            "strict_measurement": bool(strict_measurement),
            "price_table_ver": PRICE_TABLE_VER,
            # Content hashes of the two prompt surfaces: two prompt variants can otherwise
            # ship under one hand-typed version stamp with nothing to tell the runs apart.
            "agent_prompt_sha": agent_prompt_fingerprint(),
            "judge_prompt_sha": judge_prompt_fingerprint(),
            "link_jurisdiction_ver": LINK_JURISDICTION_VERSION,
            "suspected_band_max": SUSPECTED_BAND_MAX,
            "producers_ver": PRODUCERS_VER,
            "b_doc": B_DOC,
        },
    )


def _write_frame_plan(writer, cells, unit_count: int, doc_filter: str | None) -> None:
    """Journal what this run intends to do, once, before it does any of it.

    The field names are fixed: the read model and the report both consume this row, and its
    disposition sibling turns "planned" into "funded, deferred or never dispatched".

    Args:
        unit_count: Document units, not cells — this is the `MAX_UNITS` denominator, and the
            docstring producer's one corpus-wide cell is not a document unit.
    """
    _write_run_row(
        writer,
        "frame_plan",
        {
            "phase": "plan",
            "cells": [list(cell) for cell in cells],
            "cell_count": len(cells),
            "unit_count": unit_count,
            "lane_b_first": True,
            "doc_filter": doc_filter,
            "max_units": rails.MAX_UNITS,
        },
    )


def _write_frame_disposition(writer, funded, deferred_by_wallet, never_dispatched) -> None:
    """Journal what the run actually did with its plan — the plan row's second half.

    Only `funded` is forgiven. `deferred_by_wallet` did not fit the wallet and is coverage-short
    by design; `never_dispatched` is what an abort or an interrupt left, and is coverage-short by
    malfunction. Collapsing the two would make an aborted run indistinguishable from a
    budget-stopped one, which is what run fitness turns on.
    """
    _write_run_row(
        writer,
        "frame_plan",
        {
            "phase": "disposition",
            "funded": [list(key) for key in funded],
            "deferred_by_wallet": [list(key) for key in deferred_by_wallet],
            "never_dispatched": [list(key) for key in never_dispatched],
        },
    )


def _disposition_hazards(writer, hazards: list[dict]) -> list[str]:
    """Turn enumeration hazards into report lines and coverage rows — once, at the frame.

    A skipped unit has no cell to speak for it, and a hazard list handed to every cell instead
    would write one duplicate coverage row per cell into a stream the report's banner counts.
    """
    notes: list[str] = []
    for hazard in hazards:
        notes.append(enumerate_units._hazard_note(hazard))
        if hazard["disposition"] != "skipped":
            continue  # a truncated unit reports through its own coverage row, inside its cell
        # The coverage stream already means "this unit produced nothing, and here is why".
        _write_run_row(
            writer,
            "agent_coverage",
            {
                "unit": hazard["unit"],
                "doc_hash": "",
                "turns_used": 0,
                "tool_calls": 0,
                "status": "skipped",
                "detail": f"{hazard['reason']}: not read (enumeration safety)",
            },
            component="agent",
        )
    if hazards:
        progress(
            f"enumeration: {len(hazards)} hazard(s) — "
            + "; ".join(f"{h['unit']} {h['disposition']}" for h in hazards)
        )
    return notes
