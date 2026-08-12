"""One repository scan: the run lifecycle, the wallet, cell dispatch, fan-in and the report.

Everything scoped to a run rather than to one unit of work lives here; a cell owns the rest. Cells
are broker messages unless the caller supplies a client or a dispatcher of its own.
"""

from __future__ import annotations

import sys

from langgraph.graph import END, START, StateGraph
from sqlalchemy import func

from drift.cost import DEFAULT_MODEL, require_priced_model
from drift.gate.replay import replay_check
from drift.graph import (
    cell,  # version stamps, producer constants, the git HEAD reader
    session_read,
)
from drift.graph.dispatch import (
    POLL_INTERVAL_SECONDS,
    _celery_dispatch,
    _default_inspect,
    in_process_dispatch,
    inline_liveness_probe,
)
from drift.graph.fanin import (
    _budget_or_inf,
    _dispatch_and_fan_in,
    _FanIn,
)
from drift.graph.journal_rows import (
    _disposition_hazards,
    _export_journal,
    _record_run_cost,
    _write_frame_disposition,
    _write_frame_plan,
    _write_rail_config,
)
from drift.graph.nodes import rails
from drift.graph.nodes.discover import (
    make_discover,
    make_discover_docstrings,
)
from drift.graph.nodes.enumerate_units import make_adopt_worklist
from drift.graph.nodes.gate import make_gate_replay
from drift.graph.nodes.judge import make_semantic_judge
from drift.graph.nodes.rails import StrictMeasurementAbort
from drift.graph.planning import (
    ScanPlan,
    _enforce_max_units,
    _print_preflight,
    plan_cells,
    plan_scan,
)
from drift.graph.progress import progress
from drift.graph.read_model import build_read_model
from drift.graph.state import ScanState
from drift.journal.writer import JournalWriter, Stamps
from drift.persistence.db import SessionLocal
from drift.persistence.models import ScanRun
from drift.persistence.store import ReconcileResult, SqlAlchemyIssueStore
from drift.report.render import to_markdown
from drift.runconfig import (
    DEFAULT_BUDGET_USD,
    DEFAULT_DOC_FILTER,
    DEFAULT_MAX_S_CANDIDATES,
    DEFAULT_STRICT_MEASUREMENT,
)


def reconcile_run(session, repo_root: str, run_id: int, findings) -> ReconcileResult:
    """Reconcile the run's findings into the issue lifecycle — run-scoped, after fan-in.

    Resolution replays each open issue's own stored check and never a producer's absence. That is
    what makes it flap-proof, and what makes running it once, here, safe: a missing cell can only
    fail to re-assert an issue, never close one.
    """
    store = SqlAlchemyIssueStore(session)
    result = store.reconcile_with_replay(
        run_id,
        findings,
        still_drifting=lambda check: replay_check(repo_root, check),
    )
    print(
        f"[drift] reconcile: discovered={result.discovered} resolved={result.resolved} "
        f"seen={result.seen}",
        file=sys.stderr,
        flush=True,
    )
    return result


def render_report(state) -> str:
    """Render the run's two-tier markdown from the final state — one report per run.

    The incompleteness banner has three inputs: coverage records that are not `complete`, kernel
    errors from the gate, and planned cells with no `cell_result` row. The third is what stops a
    short run being silent about it — a deferred cell never ran, so it reported nothing.
    """
    incomplete_units = [c for c in state["coverages"] if c.get("status") != "complete"]
    incomplete_units += state.get("kernel_errors", [])
    incomplete_units += state.get("cell_shortfalls", [])
    return to_markdown(
        state["findings"],
        state["ranked_entries"],
        incomplete_units,
        state.get("partial_notes", []),
    )


def build_graph(
    discovery_agent,
    producer_factory,
    semantic_judge,
    writer,
    model: str = DEFAULT_MODEL,
):
    """Assemble and compile the five-node scan graph: both producers, the gate and the judge.

    Nothing in production calls this — a cell compiles its own three-node graph instead. It is
    retained and exercised by tests because it keeps reverting to a single-process pipeline a
    one-line change.

    Args:
        model: Reaches the two paid nodes, so their budget accounting prices the model the scan
            actually runs.
    """
    graph = StateGraph(ScanState)
    graph.add_node("adopt_worklist", make_adopt_worklist(writer))
    graph.add_node("discover", make_discover(discovery_agent, writer, model))
    graph.add_node("discover_docstrings", make_discover_docstrings(producer_factory, writer))
    graph.add_node("gate_replay", make_gate_replay(writer))
    graph.add_node("semantic_judge", make_semantic_judge(semantic_judge, writer, model))
    graph.add_edge(START, "adopt_worklist")
    graph.add_edge("adopt_worklist", "discover")
    graph.add_edge("discover", "discover_docstrings")
    graph.add_edge("discover_docstrings", "gate_replay")
    graph.add_edge("gate_replay", "semantic_judge")
    graph.add_edge("semantic_judge", END)
    return graph.compile()


#: The run is pinned to one revision here, and every cell re-validates its worktree against that
#: pin, so a run spread across wall-clock time still adjudicates a single revision.


def run_scan(
    path: str,
    doc_filter: str | None = DEFAULT_DOC_FILTER,
    client=None,
    session_factory=None,
    budget: float = DEFAULT_BUDGET_USD,
    strict_measurement: bool = DEFAULT_STRICT_MEASUREMENT,
    max_s_candidates: int | None = DEFAULT_MAX_S_CANDIDATES,
    journal_export: str | None = None,
    dispatch=None,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    inspect_factory=None,
    plan: ScanPlan | None = None,
) -> tuple[int, str]:
    """Run one scan end-to-end: create the run, plan it, dispatch cells, fan in, report.

    Cells cross the broker, so Redis is a hard dependency of this call unless a `client` or a
    `dispatch` is injected.

    Args:
        doc_filter: Restrict the run to one repository-relative document.
        client: Selects `in_process_dispatch`, which is the only way a scripted or offline
            caller gets a client into a cell.
        session_factory: The frame's session only — a cell always opens its own — so nothing a
            cell writes is inside the caller's transaction.
        budget: Dollar ceiling. The next cell is dispatched only while spend is below it, and a
            cell is never cut, so the overshoot is bounded by one cell. Nothing inside a cell
            gates on dollars, and a scan that has spent money still emits its report.
        strict_measurement: Abort loudly on a soft rail or a journal failure instead of
            reporting partially, the wallet excepted. A partial report is worse than none when
            the output is a measurement rather than a scan.
        max_s_candidates: The run's judge-candidate cap; None takes the built-in rail.
        dispatch: `(run_id, producer, unit_ref, repo_root, config)` returning a task id.
            Overrides both seams above.
        poll_interval: The fan-in's clock, in seconds.
        inspect_factory: The fan-in's liveness oracle, injectable so the probe can be exercised
            offline and instantly.
        plan: A plan already produced by `plan_scan`, so a caller that priced the work first
            does not enumerate twice.

    Returns:
        The run id and the rendered report.
    """
    # An unpriceable model has no working budget gate; finding that out after the first paid
    # call is too late, so it is checked before the session and the run row.
    require_priced_model(cell.MODEL)

    # Planning is outside the run: a document this repository cannot scan is refused with no
    # run row, no journal rows and no cost row for work that never happened.
    if plan is None:
        plan = plan_scan(path, [doc_filter] if doc_filter is not None else ())

    factory = session_factory or SessionLocal
    session = factory()
    run = None
    try:
        commit_sha = cell.git_rev_parse_head(path)
        run = ScanRun(repo=path, commit_sha=commit_sha, status="running")
        session.add(run)
        # Committed, not flushed: a journal rollback discards the whole in-flight transaction,
        # and an uncommitted run row would go with it.
        session.commit()
        run_id = run.id

        stamps = Stamps(agent_ver=cell.AGENT_VER, judge_ver=cell.JUDGE_VER, model=cell.MODEL)
        writer = JournalWriter(session, run_id, path, commit_sha or "", stamps)

        if dispatch is None:
            dispatch = (
                _celery_dispatch if client is None else in_process_dispatch(client, session_factory)
            )
        # Keyed on the decision, not on how it was reached — a supplied dispatcher counts too.
        if dispatch is not _celery_dispatch:
            progress("dispatch: IN-PROCESS; cells are NOT broker messages on this run")
        if inspect_factory is None:
            # Only the broker path gets the broker's oracle: an inline run has no worker to
            # ask, and asking anyway turns a lost cell into an indefinite wait.
            inspect_factory = (
                _default_inspect if dispatch is _celery_dispatch else inline_liveness_probe
            )

        fanin = _FanIn()
        report_text = ""
        completed = False
        try:
            # `rail_config` precedes the cap gate — a run the cap refuses still owes its
            # configuration — and `frame_plan` follows it, since a refused run plans nothing.
            worklist, hazards = plan.worklist, plan.hazards
            doc_filter = plan.doc_filter
            cap = rails.MAX_S_CANDIDATES if max_s_candidates is None else int(max_s_candidates)
            _write_rail_config(writer, _budget_or_inf(budget), strict_measurement, cap)
            _enforce_max_units(worklist)
            cells = plan_cells(worklist)
            _write_frame_plan(writer, cells, len(worklist), doc_filter)
            frame_notes = _disposition_hazards(writer, hazards)
            _print_preflight(path, worklist, doc_filter)

            try:
                _dispatch_and_fan_in(
                    fanin,
                    writer=writer,
                    session_factory=session_factory,
                    dispatch=dispatch,
                    run_id=run_id,
                    repo_root=path,
                    cells=cells,
                    unit_count=len(worklist),
                    budget=_budget_or_inf(budget),
                    cap=cap,
                    doc_filter=doc_filter,
                    strict_measurement=strict_measurement,
                    poll_interval=poll_interval,
                    inspect_factory=inspect_factory,
                )
            finally:
                # Always: a strict abort, an interrupt and a wallet stop each owe this row.
                _write_frame_disposition(
                    writer, fanin.funded, fanin.deferred_by_wallet, fanin.never_dispatched
                )
            for key in fanin.unreported:
                progress(f"cell {key[0]}:{key[1]} NEVER REPORTED — this run has a hole")
            progress(
                f"fan-in: {len(fanin.results)} of {len(fanin.funded)} dispatched cell(s) "
                f"reported ({len(fanin.deferred_by_wallet)} deferred by the wallet, "
                f"{len(fanin.never_dispatched)} never dispatched)"
            )
            if fanin.interrupted:
                raise KeyboardInterrupt("interrupted; dispatch stopped")
            if fanin.aborted_cell is not None:
                raise StrictMeasurementAbort(
                    f"--strict-measurement: cell {fanin.aborted_cell} aborted; the run stopped "
                    f"dispatching. A measurement run must not produce a partial report."
                )

            # The tail runs inside this `try` so that reconcile's Issue rows are still pending
            # on the session when the cost row below is written.
            model = session_read.fresh_read(session_factory, lambda s: build_read_model(s, run_id))
            reconcile_run(session, path, run_id, model.findings)
            report_text = render_report(model.as_state(frame_notes + fanin.notes))
            completed = not fanin.unreported
        finally:
            _record_run_cost(
                session, writer, run_id, cell.MODEL, completed, unreported=fanin.unreported
            )
            _export_journal(session, run_id, journal_export)

        run.status = "done"
        run.finished_at = func.now()
        # This commit carries the terminal status, which is what makes `status == "done"` the
        # marker of a run that reached its end; the journal was already flushed per cell.
        session.commit()
        return run_id, report_text
    except Exception as exc:
        if run is not None:
            try:
                run.status = "failed"
                run.error = str(exc)
                run.finished_at = func.now()
                session.commit()
            except Exception:
                session.rollback()
        raise
    finally:
        session.close()
