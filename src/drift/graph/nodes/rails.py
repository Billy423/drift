"""The limits every node shares, and the two ways a node gives up.

A rail stop is loud and journaled; a journal failure is fail-soft unless the run asked for
strict measurement. Both live here because every node reaches for them.
"""

from __future__ import annotations

from drift.graph.progress import progress
from drift.graph.state import ScanState
from drift.journal.serialize import claim_payload
from drift.kernels.models import require_admitted_producer

# One paid discovery loop per document unit, so an unbounded worklist is an unbounded bill.
# The frame refuses to start a run above this, rather than truncating one.
MAX_UNITS = 300

# Fail-soft: candidates past this are journaled and named in the report rather than raising,
# because a scan that has already spent money on discovery must still produce one.
MAX_S_CANDIDATES = 50


def _cap_of(state: ScanState) -> int:
    """The judge's candidate cap for this run: the state's value, or the module default."""
    cap = state.get("max_s_candidates")
    return MAX_S_CANDIDATES if cap is None else int(cap)


def _budget_of(state: ScanState) -> float:
    """The scan's dollar ceiling; absent or None means unlimited."""
    budget = state.get("budget")
    return float("inf") if budget is None else budget


class StrictMeasurementAbort(RuntimeError):
    """A soft rail fired, or the journal failed, during a strict-measurement run.

    The rails fail soft by default so that an unattended scan still emits a report. Strict
    measurement inverts that: a partial run is not a smaller measurement, it is a wrong one.
    """


def _rail_stop(
    writer,
    state: ScanState,
    component: str,
    lane: str,
    reason: str,
    message: str,
    items_done: int,
    items_total: int,
    budget: float,
    spend: float,
) -> None:
    """Journal one soft rail's firing, log it, and abort if the run is a measurement run.

    Every rail routes through here, so a rail added later cannot silently keep failing soft. The
    journal row is written before the abort, so a strict run that dies on a rail still says why.
    """
    _journal_rail_stop(writer, component, lane, reason, items_done, items_total, budget, spend)
    progress(message)
    if state.get("strict_measurement"):
        raise StrictMeasurementAbort(
            f"--strict-measurement: rail {reason!r} fired in lane {lane!r} "
            f"({items_done}/{items_total} done) — {message}. A measurement run must not produce "
            f"a partial report; re-run with a higher rail, or without --strict-measurement."
        )


def _journal_rail_stop(
    writer,
    component: str,
    lane: str,
    reason: str,
    items_done: int,
    items_total: int,
    budget: float,
    spend: float,
) -> None:
    """Write the row that makes a truncated run distinguishable from a complete one.

    Without it a short run reads as an ordinary one: the run status says done and the journal
    holds a smaller but otherwise normal set of rows.

    Args:
        items_total: The size of the firing rail's own work list — document units for discovery,
            mechanically-refuted candidates for the judge, cells for the frame's wallet.
        budget: Journaled as null when unlimited, JSON having no infinity.
    """
    writer.write(
        component,
        "rail_stop",
        {
            "lane": lane,
            "reason": reason,
            "items_done": items_done,
            "items_total": items_total,
            "budget": None if budget == float("inf") else budget,
            "spend": spend,
        },
    )


def _safe_journal(writer, state: ScanState, partial_notes: list[str], unit: str, emit) -> None:
    """Write one unit's journal rows: abort under strict measurement, else roll back and go on.

    The rollback is not optional — a failed flush leaves the session unusable, so every later
    unit would fail too. It also discards the unit's own coverage row, so the loss survives only
    in `partial_notes`, which no completeness check reads.
    """
    try:
        emit()
        writer.flush()
    except Exception as exc:
        if state.get("strict_measurement"):
            raise StrictMeasurementAbort(
                f"--strict-measurement: journal write failed on doc unit {unit!r} ({exc!r}). "
                f"A measurement run's evidence must be complete; aborting rather than "
                f"continuing with a gap."
            ) from exc
        try:
            writer.rollback()
        except Exception:  # noqa: BLE001 - a failed rollback must not become the fatal error
            pass
        partial_notes.append(
            f"journal write failed on doc unit {unit!r} ({exc!r}); that unit's records are lost "
            f"and the scan continued — this run is NOT fit to publish a number from."
        )
        progress(f"journal: WRITE FAILED on {unit} — {exc!r} (continuing; run marked partial)")


def _journal_claim_inventory(writer, lane: str, claims, doc_hash: str) -> None:
    """Record every claim a producer emitted — bound or not, certified or not.

    The only stream that keeps a claim the gate will never certify, and the only trace of one
    that bound to nothing. Observational: it returns nothing, so no branch downstream can read
    it back, and an inventory row can neither mint a finding nor suppress one.
    """
    for claim in claims:
        writer.write(lane, "claim_inventory", claim_payload(claim, doc_hash, lane))


def _admit_producers(claims, unit: str) -> None:
    """Reject any claim whose producer is outside the closed vocabulary, before it enters state.

    Called outside both the producer's `try` and the journal wrapper on purpose: the raise must
    be identical in every mode, and must land before a claim can be gated, judged or rendered.
    An unadmitted name is a bug here rather than a data condition, so it is not fail-isolated.
    """
    for claim in claims:
        require_admitted_producer(claim, where=f"claim ingress, unit {unit!r}")
