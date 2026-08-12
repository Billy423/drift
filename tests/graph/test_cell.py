"""Exercise cell isolation, idempotency, result journaling, and terminal-write recovery.

Most tests inject the transactional database fixture, so cell commits remain inside the outer
transaction that teardown rolls back. Terminal-write recovery uses a committed session because
rolling back the transactional fixture would also remove its `ScanRun` row.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from sqlalchemy.exc import PendingRollbackError

from drift.graph import cell as cell_mod
from drift.graph.cell import (
    CELL_RESULT_STATUSES,
    LANE_A_PRODUCER,
    LANE_B_PRODUCER,
    LANE_B_UNIT_REF,
    build_cell_graph,
    run_cell,
)
from drift.graph.nodes.rails import StrictMeasurementAbort
from drift.journal.writer import JournalWriter, Stamps
from drift.persistence.db import SessionLocal
from drift.persistence.models import CellTerminalStatus, JournalRecord, ScanRun
from tests.fixtures.step2_substrate import (
    SUBSTRATE_COMMIT_SHA,
    build_substrate_repo,
    make_substrate_client,
)

_CONFIG = {
    "budget": 5.0,
    "strict_measurement": False,
    "max_s_candidates": 50,
    "doc_filter": None,
}


@pytest.fixture
def substrate(tmp_path):
    """Build the repository fixture used by cell tests."""
    return str(build_substrate_repo(tmp_path))


def _scan_run(db_session, repo_root: str, commit_sha: str = SUBSTRATE_COMMIT_SHA) -> int:
    """Insert and commit a running scan for a repository."""
    run = ScanRun(repo=repo_root, commit_sha=commit_sha, status="running")
    db_session.add(run)
    db_session.commit()
    return run.id


def _cell(db_session, run_id, repo_root, producer=LANE_A_PRODUCER, unit_ref="README.md", **cfg):
    """Run one cell with the standard test configuration."""
    config = {**_CONFIG, **cfg}
    return run_cell(
        run_id,
        producer,
        unit_ref,
        repo_root,
        config,
        client=make_substrate_client(),
        session_factory=lambda: db_session,
    )


def _rows(db_session, run_id: int) -> dict[str, list[dict]]:
    """Group a run's journal payloads by record type."""
    streams: dict[str, list[dict]] = {}
    for row in (
        db_session.query(JournalRecord).filter_by(run_id=run_id).order_by(JournalRecord.id).all()
    ):
        streams.setdefault(row.record_type, []).append(row.payload)
    return streams


def test_a_redelivered_cell_returns_the_stored_outcome_and_writes_nothing(substrate, db_session):
    """Return the stored outcome without duplicating journal or terminal rows.

    Row counts are the invariant because a second set of producer, gate, verdict, or result rows
    would double the frame's aggregates.
    """
    run_id = _scan_run(db_session, substrate)

    first = _cell(db_session, run_id, substrate)
    before = {k: len(v) for k, v in _rows(db_session, run_id).items()}
    calls_before = db_session.query(CellTerminalStatus).filter_by(run_id=run_id).count()

    second = _cell(db_session, run_id, substrate)
    after = {k: len(v) for k, v in _rows(db_session, run_id).items()}

    assert first["outcome"] == "completed"
    assert second == first, "a redelivered cell must report the STORED outcome, verbatim"
    assert after == before, f"redelivery wrote rows: {before} -> {after}"
    assert calls_before == 1
    assert db_session.query(CellTerminalStatus).filter_by(run_id=run_id).count() == 1


def test_a_redelivered_cell_does_not_pay_for_the_work_again(substrate, db_session):
    """Avoid another model call when a completed cell is redelivered."""
    run_id = _scan_run(db_session, substrate)
    client = make_substrate_client()

    def once():
        """Invoke the same cell delivery once."""
        return run_cell(
            run_id,
            LANE_A_PRODUCER,
            "README.md",
            substrate,
            _CONFIG,
            client=client,
            session_factory=lambda: db_session,
        )

    once()
    calls_after_first = len(client.calls)
    once()

    assert calls_after_first > 0
    assert len(client.calls) == calls_after_first, "the redelivered cell called the model again"


def test_a_cell_whose_run_id_does_not_exist_is_a_unit_error_with_no_rows(substrate, db_session):
    """Reject a cell whose scan run does not exist without writing rows."""
    result = _cell(db_session, 10**9, substrate)

    assert result["outcome"] == "unit_error"
    assert "10000000" in result["error"] or "no scan_run" in result["error"]
    assert db_session.query(JournalRecord).filter_by(run_id=10**9).count() == 0
    assert db_session.query(CellTerminalStatus).filter_by(run_id=10**9).count() == 0


def test_a_cell_pointed_at_another_repos_run_writes_no_foreign_rows(substrate, db_session):
    """The message and the `ScanRun` row disagree about the repo — the run is someone else's."""
    run_id = _scan_run(db_session, "/tmp/some-other-repo")

    result = _cell(db_session, run_id, substrate)

    assert result["outcome"] == "unit_error"
    assert "/tmp/some-other-repo" in result["error"]
    assert db_session.query(JournalRecord).filter_by(run_id=run_id).count() == 0
    assert db_session.query(CellTerminalStatus).filter_by(run_id=run_id).count() == 0


def test_a_cell_observing_a_different_head_is_a_unit_error(substrate, db_session, tmp_path):
    """Reject a cell when the repository head no longer matches the run."""
    run_id = _scan_run(db_session, substrate)
    (tmp_path / "step2_substrate" / "NEW.md").write_text("# new\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=substrate, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "moved"],
        cwd=substrate,
        check=True,
    )

    result = _cell(db_session, run_id, substrate)

    assert result["outcome"] == "unit_error"
    assert SUBSTRATE_COMMIT_SHA[:12] in result["error"]
    assert db_session.query(JournalRecord).filter_by(run_id=run_id).count() == 0
    assert db_session.query(CellTerminalStatus).filter_by(run_id=run_id).count() == 0


def test_a_matching_message_is_not_rejected(substrate, db_session):
    """The negative tests above are only meaningful if the positive one passes the same gate."""
    run_id = _scan_run(db_session, substrate)

    assert _cell(db_session, run_id, substrate)["outcome"] == "completed"


def test_the_cell_graph_is_three_nodes_and_runs_one_producer():
    """Build exactly the three processing nodes for the selected producer."""
    lane_a = build_cell_graph(LANE_A_PRODUCER, None, None, None, None)
    lane_b = build_cell_graph(LANE_B_PRODUCER, None, None, None, None)

    def nodes(graph):
        """Return the graph's non-sentinel node names."""
        return sorted(n for n in graph.get_graph().nodes if not n.startswith("__"))

    assert nodes(lane_a) == ["discover", "gate_replay", "semantic_judge"]
    assert nodes(lane_b) == ["discover_docstrings", "gate_replay", "semantic_judge"]


def test_the_cell_graph_refuses_a_producer_outside_the_closed_vocabulary():
    """Reject graph construction for an unknown producer."""
    with pytest.raises(ValueError, match="closed vocabulary"):
        build_cell_graph("scout", None, None, None, None)


def test_run_cell_refuses_a_producer_outside_the_closed_vocabulary(substrate, db_session):
    """A dispatch address may not invent a producer — the same law `plan_cells` enforces."""
    run_id = _scan_run(db_session, substrate)

    with pytest.raises(ValueError, match="closed vocabulary"):
        _cell(db_session, run_id, substrate, producer="scout")


def test_a_lane_a_cell_journals_what_the_read_model_reads(substrate, db_session):
    """Journal the inventory, gate rows, verdicts, and result for one discovery cell."""
    run_id = _scan_run(db_session, substrate)

    result = _cell(db_session, run_id, substrate)
    streams = _rows(db_session, run_id)

    assert result["outcome"] == "completed"
    assert result["claims_emitted"] == len(streams["claim_inventory"])
    assert {p["lane"] for p in streams["claim_inventory"]} == {"agent"}
    assert {p["unit"] for p in streams["agent_coverage"]} == {"README.md"}
    assert {"PASSING", "M_CERTIFIED"} <= {p["outcome"] for p in streams["gate_outcome"]}
    assert streams["gate_kill"], "the hallucinated anchor's BINDING_FAIL row is missing"
    assert streams["gate_ungateable"]
    assert streams["s_verdict"], "the SemanticJudge runs in the cell"
    assert len(streams["cell_result"]) == 1


def test_a_lane_a_cell_scans_only_its_own_unit(substrate, db_session):
    """One cell, one file. A cell that enumerated for itself would re-scan the whole repo."""
    run_id = _scan_run(db_session, substrate)

    _cell(db_session, run_id, substrate, unit_ref="GUIDE.md")
    streams = _rows(db_session, run_id)

    assert {p["unit"] for p in streams["agent_coverage"]} == {"GUIDE.md"}
    assert {p["anchor"]["doc_path"] for p in streams["claim_inventory"]} == {"GUIDE.md"}


def test_a_lane_b_cell_runs_the_corpus_walk(substrate, db_session):
    """`("docstrings", "docstring_corpus")` — one corpus-wide cell, its producer self-enumerates."""
    run_id = _scan_run(db_session, substrate)

    result = _cell(
        db_session, run_id, substrate, producer=LANE_B_PRODUCER, unit_ref=LANE_B_UNIT_REF
    )
    streams = _rows(db_session, run_id)

    assert result["outcome"] == "completed"
    assert result["cell_key"] == [LANE_B_PRODUCER, LANE_B_UNIT_REF]
    assert {p["lane"] for p in streams["claim_inventory"]} == {"docstrings"}
    assert any(p["producer"] == "docstrings" for p in streams["s_verdict"])


def test_a_cell_receives_no_enumeration_hazards(substrate, db_session):
    """Keep repository-enumeration hazards out of cells.

    Passing the run's full hazard list into every cell would duplicate skipped-unit coverage rows
    and corrupt run-level incompleteness counts.
    """
    run_id = _scan_run(db_session, substrate)

    _cell(db_session, run_id, substrate)
    streams = _rows(db_session, run_id)

    assert [p["unit"] for p in streams["agent_coverage"]] == ["README.md"]
    assert all(p["status"] != "skipped" for p in streams["agent_coverage"])


def test_the_cell_result_row_carries_the_fixed_schema(substrate, db_session):
    """Write every required field in the cell-result payload."""
    run_id = _scan_run(db_session, substrate)

    result = _cell(db_session, run_id, substrate)
    row = _rows(db_session, run_id)["cell_result"][0]

    assert set(row) == {
        "cell_key",
        "status",
        "claims_emitted",
        "error",
        "partial_notes",
        "counts",
    }
    assert row["cell_key"] == [LANE_A_PRODUCER, "README.md"]
    assert row["status"] in CELL_RESULT_STATUSES
    assert row["error"] is None
    assert isinstance(row["partial_notes"], list)
    assert row["counts"]["claims"] == result["claims_emitted"]
    assert row["counts"]["findings"] >= 1


def test_the_row_is_authoritative_and_the_return_value_agrees_with_it(substrate, db_session):
    """Keep the returned result consistent with the authoritative stored row."""
    run_id = _scan_run(db_session, substrate)

    result = _cell(db_session, run_id, substrate)
    row = _rows(db_session, run_id)["cell_result"][0]
    stored = db_session.query(CellTerminalStatus).filter_by(run_id=run_id).one()

    assert result["outcome"] == row["status"] == stored.status
    assert result["claims_emitted"] == row["claims_emitted"] == stored.claims_emitted
    assert result["cell_key"] == row["cell_key"] == [stored.producer, stored.unit_ref]


def test_the_return_value_is_json_primitives_only(substrate, db_session):
    """It crosses a broker message; anything else is a serialization failure at dispatch."""
    run_id = _scan_run(db_session, substrate)

    result = _cell(db_session, run_id, substrate)

    assert json.loads(json.dumps(result)) == result


def test_an_unknown_cell_result_status_raises_at_the_write_boundary():
    """Reject unknown statuses and producers at the result construction boundary.

    Consumers branch on the stored status, so validating it only at read time would admit an
    ambiguous terminal row.
    """
    with pytest.raises(ValueError, match="closed"):
        cell_mod._terminal_payload((LANE_A_PRODUCER, "README.md"), "finished", 0, None, [], {})
    with pytest.raises(ValueError, match="closed vocabulary"):
        cell_mod._terminal_payload(("scout", "README.md"), "completed", 0, None, [], {})


def test_the_status_vocabulary_is_exactly_the_three_ruled_values():
    """Keep the cell-result status vocabulary closed to three outcomes."""
    assert CELL_RESULT_STATUSES == frozenset({"completed", "unit_error", "strict_abort"})


def test_a_strict_measurement_abort_becomes_a_terminal_row(substrate, db_session):
    """Persist both the strict-abort result and the rail-stop row.

    The candidate cap triggers inside a cell after the rail row is journaled, so both records must
    survive the abort.
    """
    run_id = _scan_run(db_session, substrate)

    result = _cell(db_session, run_id, substrate, strict_measurement=True, max_s_candidates=0)
    streams = _rows(db_session, run_id)
    stored = db_session.query(CellTerminalStatus).filter_by(run_id=run_id).one()

    assert result["outcome"] == "strict_abort"
    assert "strict-measurement" in result["error"]
    assert stored.status == "strict_abort"
    assert streams["cell_result"][0]["status"] == "strict_abort"
    assert streams["rail_stop"][0]["reason"] == "budget_cap:max_s_candidates"


def test_a_strict_abort_is_returned_not_raised(substrate, db_session):
    """Return a strict-abort result instead of raising it to the dispatcher.

    The frame consumes terminal rows. Raising in eager mode would make its control flow depend on
    whether dispatch is local or queued.
    """
    run_id = _scan_run(db_session, substrate)

    result = _cell(db_session, run_id, substrate, strict_measurement=True, max_s_candidates=0)

    assert result["outcome"] == "strict_abort"


def test_a_cell_whose_graph_dies_is_a_unit_error_with_a_terminal_row(
    substrate, db_session, monkeypatch
):
    """The cell must never fail silently: an unreported cell is a hole the frame cannot see."""
    run_id = _scan_run(db_session, substrate)

    class _Exploding:
        """Stand in for a graph that fails during invocation."""

        def invoke(self, state):
            """Raise the simulated graph failure."""
            raise RuntimeError("boom")

    monkeypatch.setattr(cell_mod, "build_cell_graph", lambda *a, **k: _Exploding())

    result = _cell(db_session, run_id, substrate)
    streams = _rows(db_session, run_id)

    assert result["outcome"] == "unit_error"
    assert "boom" in result["error"]
    assert streams["cell_result"][0]["status"] == "unit_error"
    assert streams["cell_result"][0]["claims_emitted"] == 0
    assert db_session.query(CellTerminalStatus).filter_by(run_id=run_id).one().status == (
        "unit_error"
    )


def test_a_failed_cell_is_still_terminal_for_redelivery(substrate, db_session, monkeypatch):
    """A `unit_error` cell that re-ran on redelivery would pay again for work already given up."""
    run_id = _scan_run(db_session, substrate)

    class _Exploding:
        """Stand in for a graph that fails during invocation."""

        def invoke(self, state):
            """Raise the simulated graph failure."""
            raise RuntimeError("boom")

    monkeypatch.setattr(cell_mod, "build_cell_graph", lambda *a, **k: _Exploding())
    first = _cell(db_session, run_id, substrate)
    second = _cell(db_session, run_id, substrate)

    assert first == second
    assert len(_rows(db_session, run_id)["cell_result"]) == 1


class _FlakyWriter:
    """Simulate a writer whose first configured flush attempts fail."""

    def __init__(self, session, run_id: int, fail_times: int) -> None:
        """Store the session and remaining number of flush failures."""
        self._session, self._run_id, self._left = session, run_id, fail_times
        self.written: list[tuple] = []

    def write(self, component, record_type, payload):
        """Capture a journal write without persisting it."""
        self.written.append((component, record_type, payload))

    def flush(self):
        """Fail while configured, then commit the session."""
        if self._left > 0:
            self._left -= 1
            raise RuntimeError("session is in a broken transaction")
        self._session.commit()


@pytest.fixture
def real_run():
    """Yield a committed scan on an independent session and delete its rows afterwards.

    These tests need a real rollback. Rolling back the transactional fixture would deassociate
    its outer transaction and remove the scan row that recovery needs.
    """
    repo = "/tmp/cell-terminal-write-doctrine"
    session = SessionLocal()
    run = ScanRun(repo=repo, commit_sha="abc", status="running")
    session.add(run)
    session.commit()
    run_id = run.id
    try:
        yield session, run_id
    finally:
        session.rollback()
        session.query(CellTerminalStatus).filter_by(run_id=run_id).delete()
        session.query(JournalRecord).filter_by(repo=repo).delete()
        session.query(ScanRun).filter_by(repo=repo).delete()
        session.commit()
        session.close()


def test_the_terminal_write_retries_once_after_a_broken_session(real_run):
    """Retry the terminal write once when the session holds no pending evidence."""
    session, run_id = real_run
    writer = _FlakyWriter(session, run_id, fail_times=1)
    payload = cell_mod._terminal_payload(
        (LANE_A_PRODUCER, "README.md"), "strict_abort", 0, "aborted", [], {}
    )

    cell_mod._record_terminal(session, writer, run_id, (LANE_A_PRODUCER, "README.md"), payload)

    rows = session.query(CellTerminalStatus).filter_by(run_id=run_id).all()
    assert len(rows) == 1, "the rollback must expunge the first attempt's pending row, not keep it"
    assert rows[0].status == "strict_abort"


def test_a_terminal_write_that_fails_twice_is_not_reported_as_success(real_run):
    """Propagate a repeated terminal-write failure instead of reporting success.

    Returning `completed` without a row would make an unreported cell indistinguishable from a
    successfully recorded one.
    """
    session, run_id = real_run
    writer = _FlakyWriter(session, run_id, fail_times=2)
    payload = cell_mod._terminal_payload(
        (LANE_A_PRODUCER, "README.md"), "completed", 1, None, [], {}
    )

    with pytest.raises(RuntimeError, match="broken transaction"):
        cell_mod._record_terminal(session, writer, run_id, (LANE_A_PRODUCER, "README.md"), payload)

    session.rollback()
    assert session.query(CellTerminalStatus).filter_by(run_id=run_id).count() == 0


def test_strict_abort_is_reported_by_the_helper_that_classifies_it():
    """The classifier is a pure function, so its two branches are pinned without a run."""
    assert cell_mod._classify(StrictMeasurementAbort("x")) == "strict_abort"
    assert cell_mod._classify(RuntimeError("x")) == "unit_error"


class _StubSession:
    """Provide the minimal session state read by `_reset_is_safe`."""

    def __init__(self, is_active: bool) -> None:
        """Set whether the simulated transaction remains active."""
        self.is_active = is_active


def test_a_still_usable_session_holding_evidence_must_not_be_reset():
    """Preserve pending evidence when the session is still usable."""
    assert cell_mod._reset_is_safe(_StubSession(True), RuntimeError("deadlock"), 3) is False


def test_the_reset_is_allowed_on_a_dead_transaction_or_an_empty_one():
    """Two ways a rollback destroys nothing: everything is already lost, or nothing was pending."""
    assert cell_mod._reset_is_safe(_StubSession(True), PendingRollbackError("x"), 3) is True
    assert cell_mod._reset_is_safe(_StubSession(False), RuntimeError("deadlock"), 3) is True
    assert cell_mod._reset_is_safe(_StubSession(True), RuntimeError("deadlock"), 0) is True


def test_a_transient_failure_with_verdicts_pending_propagates_instead_of_lying(real_run):
    """Propagate a transient failure rather than discard pending verdict rows.

    Verdict rows remain pending when the terminal write runs. Rolling back an active session to
    retry would discard them and could commit a result whose verdict count exceeds the stored
    verdict rows.
    """
    session, run_id = real_run
    repo = session.get(ScanRun, run_id).repo
    stamps = Stamps(
        agent_ver=cell_mod.AGENT_VER, judge_ver=cell_mod.JUDGE_VER, model=cell_mod.MODEL
    )
    judge_writer = JournalWriter(session, run_id, repo, "abc", stamps)
    judge_writer.write("judge", "s_verdict", {"claim_ref": "pending-and-unflushed"})  # no flush

    writer = _FlakyWriter(session, run_id, fail_times=1)
    payload = cell_mod._terminal_payload(
        (LANE_A_PRODUCER, "README.md"), "completed", 3, None, [], {"verdicts": 3}
    )

    with pytest.raises(RuntimeError, match="broken transaction"):
        cell_mod._record_terminal(session, writer, run_id, (LANE_A_PRODUCER, "README.md"), payload)

    assert any(
        isinstance(obj, JournalRecord) and obj.record_type == "s_verdict" for obj in session.new
    ), "the pending verdict row was discarded — that is precisely what must not happen"

    probe = SessionLocal()
    try:
        assert probe.query(CellTerminalStatus).filter_by(run_id=run_id).count() == 0
        assert (
            probe.query(JournalRecord).filter_by(run_id=run_id, record_type="cell_result").count()
            == 0
        ), "a cell that could not safely write must not leave a `completed` row behind"
    finally:
        probe.close()


@pytest.mark.filterwarnings("ignore:Session's state has been changed:sqlalchemy.exc.SAWarning")
def test_a_genuinely_invalid_session_still_lands_its_terminal_row(real_run):
    """Recover a terminal write after SQLAlchemy invalidates the transaction.

    SQLAlchemy discards pending state when a flush deactivates the transaction. Resetting that
    session therefore loses no recoverable evidence and allows the terminal row to land.
    """
    session, run_id = real_run
    session.add(
        CellTerminalStatus(
            run_id=10**9,  # no such scan_run — the FK fails the flush
            producer=LANE_A_PRODUCER,
            unit_ref="orphan.md",
            status="completed",
            claims_emitted=0,
            error=None,
        )
    )
    with pytest.raises(Exception):
        session.flush()
    assert session.is_active is False
    assert len(session.new) == 0, "a dead transaction has already lost whatever was pending"

    writer = _FlakyWriter(session, run_id, fail_times=0)
    payload = cell_mod._terminal_payload(
        (LANE_A_PRODUCER, "README.md"), "strict_abort", 0, "aborted", [], {}
    )

    cell_mod._record_terminal(session, writer, run_id, (LANE_A_PRODUCER, "README.md"), payload)

    rows = session.query(CellTerminalStatus).filter_by(run_id=run_id).all()
    assert [r.status for r in rows] == ["strict_abort"]


def test_a_completed_cell_redelivered_after_head_moved_keeps_its_completed_row(
    substrate, db_session, tmp_path
):
    """Preserve the stored completion when a later redelivery fails head validation.

    Re-validation precedes the idempotency lookup. The later return may therefore be a unit error,
    but the frame polls the authoritative terminal row and the completed row must not change.
    """
    run_id = _scan_run(db_session, substrate)
    first = _cell(db_session, run_id, substrate)
    before = {k: len(v) for k, v in _rows(db_session, run_id).items()}

    (tmp_path / "step2_substrate" / "NEW.md").write_text("# new\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=substrate, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "moved"],
        cwd=substrate,
        check=True,
    )

    second = _cell(db_session, run_id, substrate)

    assert first["outcome"] == "completed"
    assert second["outcome"] == "unit_error", "re-validation runs first, by design"
    stored = db_session.query(CellTerminalStatus).filter_by(run_id=run_id).all()
    assert [r.status for r in stored] == ["completed"], "the ROW is the authority, and it stands"
    assert {k: len(v) for k, v in _rows(db_session, run_id).items()} == before
