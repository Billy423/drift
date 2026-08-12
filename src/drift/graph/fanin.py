"""Funding the cells, dispatching them, and collecting what they report.

The wallet is the fan-in loop's gate rather than a separate stage: it decides whether the
next cell is dispatched at all, and a cell already in flight is never cut.
"""

from __future__ import annotations

from sqlalchemy import func, select

from drift.graph import (
    cell,  # the model stamp and the lane names
    read_model,
    session_read,
)
from drift.graph.dispatch import (
    _await_cell_result,
    _revoke,
)
from drift.graph.journal_rows import (
    _write_run_row,
)
from drift.graph.progress import progress
from drift.journal import run_cost
from drift.persistence.models import JournalRecord


def _budget_or_inf(budget: float | None) -> float:
    """Absent means unlimited, the same rule a cell applies to the budget it is handed."""
    return float("inf") if budget is None else budget


#: The two streams accounting for a candidate the judge-candidate cap has already spent. Error
#: rows count: a failed adjudication was still billed.
_S_ADJUDICATION_STREAMS = ("s_verdict", "s_judge_skipped")


def remaining_s_allowance(session, run_id: int, cap: int) -> int:
    """How much of this run's judge-candidate cap is left — the frame's recount.

    A cell caps per graph invocation and a run is many invocations, so the frame recounts and
    threads the remainder into each cell. Adjudications are counted, not certified candidates: a
    cell can certify more claims than it adjudicates, and a rail-skipped candidate was still spent.

    Returns:
        The remainder, floored at zero. A negative one reaches a cell's `candidates[:-n]` slice
        and adjudicates all but the last n instead of none — an exhausted cap that pays again.
    """
    spent = session.scalar(
        select(func.count())
        .select_from(JournalRecord)
        .where(JournalRecord.run_id == run_id)
        .where(JournalRecord.record_type.in_(_S_ADJUDICATION_STREAMS))
    )
    return max(0, cap - int(spent or 0))


class _FanIn:
    """The dispatch phase's whole record, filled as it runs so that a raise never loses it.

    Every field feeds the plan's disposition row or the report's banner, and that row is written
    in a `finally`.
    """

    def __init__(self) -> None:
        self.funded: list[tuple[str, str]] = []
        self.deferred_by_wallet: list[tuple[str, str]] = []
        self.never_dispatched: list[tuple[str, str]] = []
        self.results: dict[tuple[str, str], dict] = {}
        self.task_ids: dict[tuple[str, str], object] = {}
        self.notes: list[str] = []
        self.aborted_cell: tuple[str, str] | None = None
        self.interrupted = False

    @property
    def unreported(self) -> list[tuple[str, str]]:
        """Dispatched, and never wrote a `cell_result` row — the run's holes.

        Derived rather than stored, so this and the plan's disposition row cannot disagree.
        """
        return [key for key in self.funded if key not in self.results]


def _spend_so_far(session_factory, run_id: int, model: str) -> float:
    """What this run has been billed, from its own journaled usage rows — the wallet's input.

    The same measure the `run_cost` row publishes: a wallet with its own definition of spend
    would gate against one number and report another. It lags by one cell under serial dispatch,
    which is why the overshoot bound is the budget plus one cell rather than the budget.
    """
    return session_read.fresh_read(
        session_factory, lambda s: run_cost.summarize_run_cost(s, run_id, model)["spend_usd"]
    )


def _wallet_rail_stop(
    writer,
    cells_done: int,
    cells_total: int,
    units_done: int,
    units_total: int,
    budget: float,
    spend: float,
) -> None:
    """The wallet's own rail firing, recorded in both denominations.

    `items_done` and `items_total` stay denominated in document units, which is what every other
    reader of a `rail_stop` row assumes; the cell counts ride alongside under their own names.
    This is the one rail that never aborts, even under `--strict-measurement` — it stops at a
    cell boundary rather than cutting work, so the run is short by design and still publishable.
    """
    _write_run_row(
        writer,
        "rail_stop",
        {
            "lane": "frame",
            "reason": "wallet-exhausted",
            "items_done": units_done,
            "items_total": units_total,
            "cells_done": cells_done,
            "cells_total": cells_total,
            "units_done": units_done,
            "units_total": units_total,
            "budget": None if budget == float("inf") else budget,
            "spend": spend,
        },
    )
    message = (
        f"wallet: ${budget:.2f} reached after {cells_done} of {cells_total} cell(s) "
        f"({units_done} of {units_total} doc unit(s)); the rest were not dispatched"
    )
    progress(message)


def _dispatch_and_fan_in(
    fanin: _FanIn,
    *,
    writer,
    session_factory,
    dispatch,
    run_id: int,
    repo_root: str,
    cells: list[tuple[str, str]],
    unit_count: int,
    budget: float,
    cap: int,
    doc_filter: str | None,
    strict_measurement: bool,
    poll_interval: float,
    inspect_factory,
) -> None:
    """Serial dispatch with the wallet gate: one cell in flight, fanning in on result rows.

    A cell is never cut. Once `spent` reaches `budget` the paid cells left are deferred and the
    run publishes what completed, while the docstring producer's cell is dispatched anyway with
    an allowance of zero, its discovery half being free. An inline cell that raises surfaces here
    rather than on a worker and counts as unreported, so control flow matches in either mode.
    """
    total = len(cells)
    units_funded = 0
    # The verdict latches: `spent` sums journaled usage and only grows, so once it is over
    # budget it can never come back under. Saves one journal aggregate per remaining cell.
    wallet_exhausted = False
    spent = 0.0
    for index, cell_key in enumerate(cells, 1):
        producer, unit_ref = cell_key
        if fanin.aborted_cell is not None or fanin.interrupted:
            fanin.never_dispatched.append(cell_key)
            continue

        if not wallet_exhausted:
            spent = _spend_so_far(session_factory, run_id, cell.MODEL)
            wallet_exhausted = not (spent < budget)
        fits = not wallet_exhausted
        lane_b = producer == cell.LANE_B_PRODUCER
        if not fits and not lane_b:
            fanin.deferred_by_wallet.append(cell_key)
            continue
        # The docstring producer is dispatched past an exhausted wallet at allowance zero: its
        # discovery half is free, and at zero no paid call inside the cell is reachable.
        allowance = (
            0
            if not fits
            else session_read.fresh_read(
                session_factory, lambda s: remaining_s_allowance(s, run_id, cap)
            )
        )
        if not fits:
            progress(
                f"cell {index}/{total}: {producer}:{unit_ref} — wallet exhausted "
                f"(${spent:.4f} of ${budget:.2f}); dispatching the docstring producer anyway "
                f"with S allowance 0 (its discovery half is free)"
            )
        else:
            progress(
                f"cell {index}/{total}: {producer}:{unit_ref} … "
                f"(spent ${spent:.4f} of ${budget:.2f}, S allowance {allowance})"
            )

        config = {
            "doc_filter": doc_filter,
            # This dict is serialized as a broker message, and JSON has no Infinity.
            "budget": None if budget == float("inf") else budget,
            "strict_measurement": bool(strict_measurement),
            "max_s_candidates": allowance,
        }
        fanin.funded.append(cell_key)
        if not lane_b:
            units_funded += 1
        dispatch_error = None
        try:
            fanin.task_ids[cell_key] = dispatch(run_id, producer, unit_ref, repo_root, config)
        except KeyboardInterrupt:
            fanin.interrupted = True
            progress(f"{cell_key}: interrupted during dispatch; no further cells")
            continue
        except Exception as exc:  # noqa: BLE001 - an inline cell's failure surfaces here
            dispatch_error = exc
            fanin.task_ids[cell_key] = None

        if dispatch_error is not None:
            row = session_read.fresh_read(
                session_factory, lambda s: read_model.cell_results_of(s, run_id)
            ).get(cell_key)
            if row is None:
                fanin.notes.append(
                    f"cell {producer}:{unit_ref} failed at dispatch ({dispatch_error!r}) and "
                    f"wrote no cell_result row; its work is missing from this run."
                )
                progress(f"{cell_key}: dispatch raised {dispatch_error!r}; no row — unreported")
                continue
        else:
            row, interrupted = _await_cell_result(
                session_factory,
                run_id,
                cell_key,
                fanin.task_ids[cell_key],
                fanin.notes,
                poll_interval,
                inspect_factory,
            )
            if interrupted:
                fanin.interrupted = True
        if row is None:
            fanin.notes.append(
                f"cell {producer}:{unit_ref} was dispatched and never reported (no worker holds "
                f"it); its work is missing from this run."
            )
            continue
        fanin.results[cell_key] = row
        for note in row.get("partial_notes") or []:
            # Cell-scoped denominators, left alone: a re-denominated number is a different number.
            fanin.notes.append(f"[cell {producer}:{unit_ref}] {note}")
        if row.get("status") == "strict_abort":
            fanin.aborted_cell = cell_key
            progress(f"{cell_key}: strict_abort — stopping dispatch")
            # `.get`, not `[]`: an interrupt can land between the `funded` append and the
            # `task_ids` assignment, and `_revoke` already drops falsy ids.
            _revoke([fanin.task_ids.get(key) for key in fanin.unreported])

    if fanin.deferred_by_wallet:
        _wallet_rail_stop(
            writer,
            cells_done=len(fanin.funded),
            cells_total=total,
            units_done=units_funded,
            units_total=unit_count,
            budget=budget,
            spend=_spend_so_far(session_factory, run_id, cell.MODEL),
        )
        fanin.notes.append(
            f"budget ${budget:.2f} reached: {len(fanin.deferred_by_wallet)} of {total} cell(s) "
            f"were not dispatched ({unit_count - units_funded} of {unit_count} doc unit(s) not "
            f"scanned); scan is partial."
        )
