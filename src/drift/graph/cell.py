"""One cell: one producer applied to one file, run as a graph and recorded in one terminal row.

The version stamps and the producer constants live here rather than at the frame because the
frame imports this module and nothing here may import it back.
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys

from langgraph.graph import END, START, StateGraph
from sqlalchemy.exc import PendingRollbackError

from drift.agent.discovery import DiscoveryAgent
from drift.cost import DEFAULT_MODEL
from drift.docstrings import DocstringProducer
from drift.graph.nodes.discover import (
    make_discover,
    make_discover_docstrings,
)
from drift.graph.nodes.gate import make_gate_replay
from drift.graph.nodes.judge import make_semantic_judge
from drift.graph.nodes.rails import StrictMeasurementAbort
from drift.graph.state import ScanState
from drift.journal.writer import JournalWriter, Stamps
from drift.judge.semantic_judge import SemanticJudge
from drift.kernels.models import PRODUCERS
from drift.persistence.db import SessionLocal
from drift.persistence.models import CellTerminalStatus, ScanRun

__all__ = [
    "AGENT_VER",
    "CELL_RESULT_STATUSES",
    "DISCOVERY_BUDGET",
    "JUDGE_VER",
    "LANE_A_PRODUCER",
    "LANE_B_PRODUCER",
    "LANE_B_UNIT_REF",
    "MODEL",
    "build_cell_graph",
    "cell_state",
    "git_rev_parse_head",
    "run_cell",
]

# Stamped on every journal row. Bump in the same commit as any model-facing change, or rows
# from two versions become indistinguishable. `AGENT_VER` covers both producers.
MODEL = "claude-sonnet-5"
AGENT_VER = "agent/0.9"
JUDGE_VER = "sjudge/0.5"
# Tool calls the discovery loop may spend on one document unit. Set much lower, and
# dense documents are truncated before they yield anything.
DISCOVERY_BUDGET = 25

#: The cell key's producer components. A key is a dispatch address, never hashed and never part
#: of a claim's identity, which is why two plain strings suffice.
LANE_A_PRODUCER = "agent"
LANE_B_PRODUCER = "docstrings"
#: The docstring producer is one corpus-wide cell, not one per file: it walks the package itself.
#: This literal is also that producer's coverage unit.
LANE_B_UNIT_REF = "docstring_corpus"

#: A cell's terminal outcomes, a closed set enforced at the write boundary. An unknown member
#: would read as "not completed" at every consumer and be indistinguishable from a bug.
CELL_RESULT_STATUSES: frozenset[str] = frozenset({"completed", "unit_error", "strict_abort"})

#: The `counts` block's keys, fixed so an aggregate over cells has one shape: a failed cell
#: reports zeros, never an absent block.
_COUNT_KEYS = (
    "claims",
    "coverages",
    "gate_results",
    "verdicts",
    "findings",
    "ranked_entries",
    "kernel_errors",
)


def git_rev_parse_head(repo_root: str) -> str | None:
    """`git rev-parse HEAD` of `repo_root`, or None when it is not a usable git worktree."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _progress(msg: str) -> None:
    """Print a progress line to stderr; stdout carries the report and nothing else."""
    print(f"[cell] {msg}", file=sys.stderr, flush=True)


def build_cell_graph(
    producer: str,
    discovery_agent,
    producer_factory,
    semantic_judge,
    writer,
    model: str = DEFAULT_MODEL,
):
    """Compile the cell's graph: one producer, then `gate_replay`, then `semantic_judge`.

    These are the whole-run graph's own node closures, reused rather than reimplemented. Its two
    other nodes are absent by design: a cell runs exactly one producer, and hazard disposition
    consumes the run's whole hazard list, which no single cell may be handed.

    Args:
        model: Prices the two paid nodes' spend, so it must be the model the cell runs.

    Raises:
        ValueError: If `producer` is outside the closed producer vocabulary.
    """
    if producer == LANE_A_PRODUCER:
        first, node = "discover", make_discover(discovery_agent, writer, model)
    elif producer == LANE_B_PRODUCER:
        first, node = "discover_docstrings", make_discover_docstrings(producer_factory, writer)
    else:
        raise ValueError(
            f"cell producer {producer!r} is not in the closed vocabulary {sorted(PRODUCERS)}; "
            f"a dispatch address may not invent a producer (kernels/models.PRODUCERS)."
        )
    graph = StateGraph(ScanState)
    graph.add_node(first, node)
    graph.add_node("gate_replay", make_gate_replay(writer))
    graph.add_node("semantic_judge", make_semantic_judge(semantic_judge, writer, model))
    graph.add_edge(START, first)
    graph.add_edge(first, "gate_replay")
    graph.add_edge("gate_replay", "semantic_judge")
    graph.add_edge("semantic_judge", END)
    return graph.compile()


def cell_state(run_id: int, producer: str, unit_ref: str, repo_root: str, config: dict) -> dict:
    """The initial `ScanState` for one cell.

    The discovery producer gets a one-element worklist, its own unit; the docstring producer gets
    an empty one and self-enumerates. `spend` starts at zero against the run's whole budget, so
    the in-graph dollar gate cannot fire: funding is decided between cells, never inside one.

    Args:
        config: Run knobs. `max_s_candidates` is this cell's allowance, already reduced by what
            the run has adjudicated, rather than the run's cap; `doc_filter` rides in the payload
            because the same unit under two filters is still one dispatch address.
    """
    worklist = [unit_ref] if producer == LANE_A_PRODUCER else []
    return {
        "repo_root": repo_root,
        "run_id": run_id,
        "doc_filter": config.get("doc_filter"),
        "worklist": worklist,
        "planned_worklist": worklist,
        "planned_hazards": [],
        "claims": [],
        "coverages": [],
        "gate_results": [],
        "verdicts": [],
        "findings": [],
        "ranked_entries": [],
        "kernel_errors": [],
        "result": None,
        "report_text": "",
        "budget": config.get("budget"),
        "spend": 0.0,
        "units_discovered": 0,
        "partial_notes": [],
        "strict_measurement": bool(config.get("strict_measurement", False)),
        "max_s_candidates": config.get("max_s_candidates"),
    }


def _classify(exc: BaseException) -> str:
    """Which terminal status an escaping exception is."""
    return "strict_abort" if isinstance(exc, StrictMeasurementAbort) else "unit_error"


def _terminal_payload(
    cell_key: tuple[str, str],
    status: str,
    claims_emitted: int,
    error: str | None,
    partial_notes: list[str],
    counts: dict[str, int],
) -> dict:
    """Build the `cell_result` payload, checking both closed vocabularies as it goes.

    Checked here, at the write boundary, and before anything reaches the session, so a rejected
    payload leaves no half-written cell behind. Any counts in `partial_notes` are per cell.

    Raises:
        ValueError: If `status` or the cell key's producer is outside its closed set.
    """
    if status not in CELL_RESULT_STATUSES:
        raise ValueError(
            f"cell_result status {status!r} is not in the closed set "
            f"{sorted(CELL_RESULT_STATUSES)}; the frame branches on this value."
        )
    if cell_key[0] not in PRODUCERS:
        raise ValueError(
            f"cell key {cell_key!r} names producer {cell_key[0]!r}, which is not in the closed "
            f"vocabulary {sorted(PRODUCERS)} (kernels/models.PRODUCERS)."
        )
    return {
        "cell_key": [cell_key[0], cell_key[1]],
        "status": status,
        "claims_emitted": int(claims_emitted),
        "error": error,
        "partial_notes": list(partial_notes),
        "counts": {key: int(counts.get(key, 0)) for key in _COUNT_KEYS},
    }


def _counts_of(out: dict | None) -> dict[str, int]:
    """Per-cell counts from the final state; all zeros when the cell never produced one."""
    if out is None:
        return dict.fromkeys(_COUNT_KEYS, 0)
    return {
        "claims": len(out.get("claims") or []),
        "coverages": len(out.get("coverages") or []),
        "gate_results": len(out.get("gate_results") or []),
        "verdicts": len(out.get("verdicts") or []),
        "findings": len(out.get("findings") or []),
        "ranked_entries": len(out.get("ranked_entries") or []),
        "kernel_errors": len(out.get("kernel_errors") or []),
    }


def _validate_against_run(session, run_id: int, repo_root: str) -> str | None:
    """Whether this message belongs to this run: a reason string, or None when it does.

    The commit check is what holds every cell of a run to one revision across the wall clock of a
    fan-out; a cell that ran against a tree the other cells never saw would write rows nothing
    could attribute. Paths are also compared through `realpath`, since a worker that resolves
    symlinks is not looking at a different repository.
    """
    run = session.get(ScanRun, run_id)
    if run is None:
        return f"no scan_run row for run_id {run_id}"
    if run.repo != repo_root and os.path.realpath(run.repo) != os.path.realpath(repo_root):
        return f"run {run_id} is a scan of {run.repo!r}, not of {repo_root!r}"
    pinned, head = run.commit_sha or "", git_rev_parse_head(repo_root) or ""
    if pinned != head:
        return (
            f"run {run_id} is pinned at commit {pinned!r} but {repo_root!r} is now at "
            f"{head!r}; a cell must not scan a tree the run was not pinned to"
        )
    return None


def _pending_evidence(session) -> int:
    """How much unflushed work a `rollback()` would destroy, taken before the terminal write."""
    return len(session.new) + len(session.dirty) + len(session.deleted)


def _reset_is_safe(session, exc: BaseException, pending_before: int) -> bool:
    """Whether the terminal write may reset the session and retry, or must let this cell be a hole.

    Safe only when a rollback can destroy nothing recoverable: the transaction is already dead, or
    nothing was pending. Otherwise it would discard the judge's verdict rows, which reach this
    session unflushed, and the retry would commit counts for rows that no longer exist.
    """
    # SQLAlchemy discards the pending state before it raises `PendingRollbackError`.
    if isinstance(exc, PendingRollbackError) or not session.is_active:
        return True
    return pending_before == 0


def _record_terminal(
    session,
    writer,
    run_id: int,
    cell_key: tuple[str, str],
    payload: dict,
) -> None:
    """Write the cell's terminal record: the store row and the journal row, in one transaction.

    One transaction, because a cell with the store row and no journal row is invisible to the
    frame while the reverse is re-runnable. A failed write retries once where `_reset_is_safe`
    allows it and otherwise propagates, leaving the frame to report the cell as unreported.
    """
    pending_before = _pending_evidence(session)

    def _attempt() -> None:
        # Built inside the attempt: `rollback()` expunges it, and re-adding it would not insert.
        session.add(
            CellTerminalStatus(
                run_id=run_id,
                producer=cell_key[0],
                unit_ref=cell_key[1],
                status=payload["status"],
                claims_emitted=payload["claims_emitted"],
                error=payload["error"],
            )
        )
        writer.write("cell", "cell_result", payload)
        writer.flush()

    try:
        _attempt()
    except Exception as exc:
        if not _reset_is_safe(session, exc, pending_before):
            _progress(
                f"terminal write failed ({exc!r}) on a session that is still usable and holds "
                f"{pending_before} unflushed row(s); NOT resetting — this cell reports as an "
                f"unreported hole rather than a mis-counted success"
            )
            raise
        _progress(f"terminal write failed ({exc!r}); resetting the session and retrying once")
        session.rollback()
        _attempt()


def run_cell(
    run_id: int,
    producer: str,
    unit_ref: str,
    repo_root: str,
    config: dict,
    *,
    client=None,
    session_factory=None,
) -> dict:
    """Run one cell and return its advisory outcome, owning a session for the cell's lifetime.

    A work failure is a terminal status, never an exception: the frame polls `cell_result` rows,
    so raising would say nothing the row does not. An escaping `BaseException` is deliberately not
    caught — the frame then sees a hole — while the `finally` still commits the judge's rows.

    Args:
        config: Run knobs, including this cell's `max_s_candidates` allowance.
        client: An Anthropic client, constructed here when absent. A test seam, as
            `session_factory` is; neither can cross a broker message, so tests call this
            function rather than the queued task.

    Returns:
        JSON primitives only, the value having to cross a broker. Advisory: the `cell_result`
        journal row is the authoritative record of what this cell did.

    Raises:
        ValueError: If `producer` is outside the closed producer vocabulary. A terminal write that
            cannot safely land also propagates, as whatever the database raised.
    """
    if producer not in PRODUCERS:
        raise ValueError(
            f"cell producer {producer!r} is not in the closed vocabulary {sorted(PRODUCERS)}; "
            f"a dispatch address may not invent a producer (kernels/models.PRODUCERS)."
        )
    cell_key = (producer, unit_ref)
    session = (session_factory or SessionLocal)()
    try:
        # Validation precedes the idempotency lookup below: nothing may be written, and no other
        # run's rows read, until the message is established as this run's.
        mismatch = _validate_against_run(session, run_id, repo_root)
        if mismatch is not None:
            # No row of any kind. Rows attributed to another run are worse than a missing cell.
            _progress(f"{cell_key}: refusing — {mismatch}")
            return {
                "cell_key": [producer, unit_ref],
                "outcome": "unit_error",
                "claims_emitted": 0,
                "error": mismatch,
            }

        stored = (
            session.query(CellTerminalStatus)
            .filter_by(run_id=run_id, producer=producer, unit_ref=unit_ref)
            .one_or_none()
        )
        if stored is not None:
            # A redelivered cell is free: report what the first invocation concluded, write
            # nothing. Re-running would pay for the unit twice and double every row.
            _progress(f"{cell_key}: already terminal ({stored.status}); no work, no rows")
            return {
                "cell_key": [producer, unit_ref],
                "outcome": stored.status,
                "claims_emitted": stored.claims_emitted,
                "error": stored.error,
            }

        run = session.get(ScanRun, run_id)
        writer = JournalWriter(
            session,
            run_id,
            repo_root,
            run.commit_sha or "",
            Stamps(agent_ver=AGENT_VER, judge_ver=JUDGE_VER, model=MODEL),
        )
        if client is None:
            import anthropic

            client = anthropic.Anthropic()

        graph = build_cell_graph(
            producer,
            DiscoveryAgent(client, model=MODEL, agent_ver=AGENT_VER, budget=DISCOVERY_BUDGET),
            functools.partial(DocstringProducer, agent_ver=AGENT_VER),
            SemanticJudge(client, model=MODEL, judge_ver=JUDGE_VER),
            writer,
            MODEL,
        )
        out, status, error = None, "completed", None
        try:
            out = graph.invoke(cell_state(run_id, producer, unit_ref, repo_root, config))
        except Exception as exc:
            status, error = _classify(exc), repr(exc)
            _progress(f"{cell_key}: {status} — {exc!r}")
        payload = _terminal_payload(
            cell_key,
            status,
            len(out.get("claims") or []) if out is not None else 0,
            error,
            list(out.get("partial_notes") or []) if out is not None else [],
            _counts_of(out),
        )
        _record_terminal(session, writer, run_id, cell_key, payload)
        _progress(
            f"{cell_key}: {payload['status']} — {payload['claims_emitted']} claim(s), "
            f"{payload['counts']['findings']} HIGH"
        )
        return {
            "cell_key": payload["cell_key"],
            "outcome": payload["status"],
            "claims_emitted": payload["claims_emitted"],
            "error": payload["error"],
        }
    finally:
        # Commit before closing. A no-op on every ordinary path; it exists for the escaping
        # `BaseException`, whose unflushed verdict rows `close()` would otherwise discard.
        try:
            session.commit()
        except Exception as exc:  # noqa: BLE001 - a failed commit must not replace the real error
            _progress(f"final commit failed ({exc!r}); rolling back")
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
        session.close()
