"""Whether a run's journal is complete enough to publish a number from.

`run_incompleteness` enumerates how a run fell short; `is_publishable` decides. Neither is called
from `drift` itself — they are a library for the tests and for offline analysis of a journal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from drift.persistence.models import JournalRecord, ScanRun

__all__ = [
    "DEFEATING_MODES",
    "FORGIVEN_MODES",
    "MODES",
    "Reason",
    "is_publishable",
    "run_incompleteness",
]

#: Every mode `run_incompleteness` can emit. A mode missing from `DEFEATING_MODES` is
#: silently forgiven, because `is_publishable` ignores names it does not recognise.
MODES: frozenset[str] = frozenset(
    {
        "run_missing",
        "rail_stop",
        "run_not_done",
        "no_units",
        "unit_error",
        "unit_truncated",
        "coverage_shortfall",
        "cells_unreported",
        "unit_zero_yield",
        "kernel_error",
        "s_judge_error",
    }
)

#: The reason the frame's wallet writes on its `rail_stop` row. Duplicated rather than
#: imported: `graph` imports `journal`, so the dependency must not run the other way.
WALLET_EXHAUSTED = "wallet-exhausted"

#: Modes that make a run unfit to publish from. `rail_stop` is a member but conditional:
#: `is_publishable` forgives it only when every rail firing was the wallet's.
DEFEATING_MODES: frozenset[str] = frozenset(
    {
        "run_missing",
        "rail_stop",
        "run_not_done",
        "no_units",
        "unit_error",
        "unit_truncated",
        "kernel_error",
        "s_judge_error",
        "cells_unreported",
    }
)

#: Modes a run may carry and still publish: deliberate shortfall is not malfunction.
#: `is_publishable` consults `DEFEATING_MODES` only, so a mode listed in both would defeat.
FORGIVEN_MODES: frozenset[str] = frozenset({"coverage_shortfall", "unit_zero_yield"})


@dataclass(frozen=True)
class Reason:
    """One way a run falls short: a stable mode name, human detail, and structured facts.

    `facts` carries what a decision reads; classifying a reason by searching `detail` is how a
    mixed rail stop comes to publish as fit. Fact values must be hashable: the dataclass is frozen.
    """

    mode: str
    detail: str
    facts: tuple[tuple[str, object], ...] = field(default=())

    def fact(self, name: str, default=None):
        """The value recorded under `name`, or `default`."""
        for key, value in self.facts:
            if key == name:
                return value
        return default


def _only_the_wallet(reason: Reason) -> bool:
    """Did every rail firing behind this `rail_stop` come from the frame's wallet?

    Every, not any: a run that also truncated its judge candidates is short in a way nothing
    designed it to be. Empty `facts` fails closed — a reason nothing can verify does not publish.
    """
    rails = reason.fact("rails")
    return bool(rails) and all(fired == WALLET_EXHAUSTED for _lane, fired in rails)


def is_publishable(reasons) -> bool:
    """Is a run carrying these reasons fit to publish a number from?

    Fit means no malfunction. A wallet that stopped dispatch at a cell boundary did its job and
    the run it stopped is short but sound; a unit that died left a hole where evidence should be.

    Args:
        reasons: The `Reason` list from `run_incompleteness`. Fitness is a pure function of it,
            so a caller holding the list never queries the database again to ask this.

    Returns:
        True for an empty list, for `FORGIVEN_MODES` alone, and for a `rail_stop` whose every
        firing was the wallet's.
    """
    for reason in reasons:
        if reason.mode not in DEFEATING_MODES:
            continue
        if reason.mode == "rail_stop" and _only_the_wallet(reason):
            continue
        return False
    return True


def _payloads(session, run_id: int, record_type: str) -> list[dict]:
    """This run's payloads for one stream, in write order."""
    rows = (
        session.query(JournalRecord)
        .filter_by(run_id=run_id, record_type=record_type)
        .order_by(JournalRecord.id)
        .all()
    )
    return [row.payload or {} for row in rows]


def _coverage_rows(session, run_id: int) -> list[tuple[str, dict]]:
    """This run's `agent_coverage` rows as `(component, payload)` pairs, in write order.

    The producer is the `component` column, not a payload field, so both readers need the pair.
    One query rather than two: a cell committing between them would give them different rows.
    """
    rows = (
        session.query(JournalRecord)
        .filter_by(run_id=run_id, record_type="agent_coverage")
        .order_by(JournalRecord.id)
        .all()
    )
    return [(row.component, row.payload or {}) for row in rows]


def _disposition_row(session, run_id: int) -> dict:
    """The run's `frame_plan` disposition payload, or `{}` when the frame never wrote one.

    The frame writes this row in the dispatch phase's `finally`, so its absence means the frame
    died at or before enumeration — a run `run_not_done` already speaks for.
    """
    rows = [p for p in _payloads(session, run_id, "frame_plan") if p.get("phase") == "disposition"]
    return rows[-1] if rows else {}


def _zero_yield_units(
    coverage_rows: list[tuple[str, dict]], cell_results: list[dict]
) -> tuple[tuple[str, ...], int]:
    """The discovery-producer units that completed and emitted nothing, and how many completed.

    Both sides must agree the unit finished — the coverage row and the cell's own result. A cell
    can crash after its discover step wrote a complete coverage row, and then reports a terminal
    status with zero claims. The docstring producer is excluded on the `component` column and
    never on the unit string: its corpus-wide unit is a name a scanned document could also have.

    Returns:
        The units that emitted zero claims, and how many completed. `claims_emitted` on the
        cell's own result row is the count; a unit with no such row counts as neither, since an
        unknown count is not a zero.
    """
    emitted = {
        tuple(payload.get("cell_key") or ()): payload.get("claims_emitted")
        for payload in cell_results
        if str(payload.get("status", "")) == "completed"
    }
    complete = [
        str(payload.get("unit", ""))
        for component, payload in coverage_rows
        if component == "agent" and str(payload.get("status", "")) == "complete"
    ]
    return tuple(u for u in complete if emitted.get(("agent", u)) == 0), len(complete)


def _counted(values: list[str]) -> str:
    """'1 error, 2 truncated' — a count of each distinct value, in sorted order."""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return ", ".join(f"{counts[k]} {k}" for k in sorted(counts))


def run_incompleteness(session, run_id: int) -> list[Reason]:
    """Every way this run falls short of publishable, in a stable order. `[]` means clean."""
    run = session.get(ScanRun, run_id)
    if run is None:
        # Never `[]`: an unknown id would read as clean, and clean is what gets published.
        return [Reason("run_missing", f"no scan_run row for run_id {run_id}")]

    reasons: list[Reason] = []

    rails = _payloads(session, run_id, "rail_stop")
    if rails:
        pairs = tuple((str(p.get("lane", "?")), str(p.get("reason", "?"))) for p in rails)
        fired = _counted([f"{lane}/{fired_reason}" for lane, fired_reason in pairs])
        reasons.append(
            Reason("rail_stop", f"{len(rails)} rail firing(s): {fired}", (("rails", pairs),))
        )

    # `!= "done"` rather than `== "failed"`: a process killed mid-scan leaves the row
    # saying `running` forever, with its committed journal rows still there to read.
    if run.status != "done":
        reasons.append(Reason("run_not_done", f"scan_run.status = {run.status!r}"))

    coverage_rows = _coverage_rows(session, run_id)
    coverages = [payload for _component, payload in coverage_rows]
    if not coverages:
        reasons.append(Reason("no_units", "no agent_coverage records: the run scanned nothing"))
    # A dead unit and a budget-clipped one stay separate modes: a false-positive rate over
    # what was emitted survives truncation, but a recall-shaped reading must union the two.
    statuses = [str(p.get("status", "")) for p in coverages]
    lost = [s for s in statuses if s not in ("complete", "truncated", "skipped")]
    clipped = [s for s in statuses if s == "truncated"]
    # A skip is the frame refusing an enumeration hazard, a symlink out of the tree or
    # a FIFO: short by design, and unbounded — an all-hazard repository publishes as fit.
    skipped = [s for s in statuses if s == "skipped"]
    if lost:
        reasons.append(
            Reason(
                "unit_error",
                f"{len(lost)} of {len(coverages)} unit(s) produced nothing: {_counted(lost)}",
            )
        )
    if clipped:
        reasons.append(
            Reason(
                "unit_truncated",
                f"{len(clipped)} of {len(coverages)} unit(s) hit the discovery budget and "
                f"emitted a clipped inventory",
            )
        )

    # The disposition row is the only place that knows what the wallet refused: a deferred
    # cell writes no coverage row, so nothing else in this function can see one.
    disposition = _disposition_row(session, run_id)
    deferred = [tuple(key) for key in (disposition.get("deferred_by_wallet") or [])]
    funded = [tuple(key) for key in (disposition.get("funded") or [])]
    if deferred or skipped:
        parts = []
        if deferred:
            total = len(funded) + len(deferred) + len(disposition.get("never_dispatched") or [])
            parts.append(
                f"{len(deferred)} of {total} cell(s) were not dispatched (the wallet stopped at "
                f"a cell boundary): {_counted([f'{p}:{u}' for p, u in deferred])}"
            )
        if skipped:
            parts.append(
                f"{len(skipped)} of {len(coverages)} unit(s) were skipped by enumeration safety"
            )
        reasons.append(
            Reason(
                "coverage_shortfall",
                "; ".join(parts),
                (
                    ("cells_funded", len(funded)),
                    ("cells_deferred", len(deferred)),
                    ("units_skipped", len(skipped)),
                ),
            )
        )

    # Unreported cells are derived, never stored, so the disposition row and the result rows
    # cannot disagree. No `funded` list means the frame died: `run_not_done` covers that.
    cell_results = _payloads(session, run_id, "cell_result")
    if funded:
        reported = {tuple(p.get("cell_key") or ()) for p in cell_results}
        holes = tuple(key for key in funded if key not in reported)
        if holes:
            reasons.append(
                Reason(
                    "cells_unreported",
                    f"{len(holes)} of {len(funded)} dispatched cell(s) never reported: "
                    + ", ".join(f"{producer}:{unit}" for producer, unit in holes),
                    (("cells_unreported", holes),),
                )
            )

    # Not a malfunction claim: a document with nothing checkable in it correctly yields
    # nothing. It says money was spent and nothing came out.
    empty_units, complete_units = _zero_yield_units(coverage_rows, cell_results)
    if empty_units:
        reasons.append(
            Reason(
                "unit_zero_yield",
                f"{len(empty_units)} of {complete_units} complete unit(s) emitted zero claims: "
                + ", ".join(empty_units),
                (("units", empty_units),),
            )
        )

    # Only `kernel_error`: a `binding_fail` on this stream is the gate working correctly.
    kills = _payloads(session, run_id, "gate_kill")
    kernel_errors = [p for p in kills if p.get("kind") == "kernel_error"]
    if kernel_errors:
        reasons.append(
            Reason(
                "kernel_error",
                f"{len(kernel_errors)} bound claim(s) died on a kernel error",
            )
        )

    # The judge is fail-isolated per candidate: an exception writes `error: True` here
    # and leaves no trace elsewhere, so this row is the only sign the work was lost.
    verdicts = _payloads(session, run_id, "s_verdict")
    failed = [p for p in verdicts if p.get("error") is True]
    if failed:
        reasons.append(
            Reason(
                "s_judge_error",
                f"{len(failed)} of {len(verdicts)} adjudication(s) failed in the judge",
            )
        )

    return reasons
