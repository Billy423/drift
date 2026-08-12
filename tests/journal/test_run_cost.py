"""Tests run-cost summaries derived entirely from persisted journal rows.

Only persisted usage can be priced, so an incomplete run may expose a cost floor.
"""

from __future__ import annotations

import json

import pytest

from drift.cost import PRICE_TABLE_VER, usage_cost_usd
from drift.journal.run_cost import summarize_run_cost
from drift.journal.writer import JournalWriter, Stamps
from drift.persistence.models import ScanRun
from tests.fixtures.frame import stub_dispatch

_STAMPS = Stamps("agent/0.8", "sjudge/0.4", "claude-sonnet-5")


def _usage(**kw):
    """Build a complete token-usage mapping with selected non-zero values."""
    base = dict.fromkeys(
        ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"),
        0,
    )
    base.update(kw)
    return base


def test_summary_totals_both_paid_lanes_and_prices_them(db_session):
    """The summary totals and prices usage from discovery and judging rows."""
    run = ScanRun(repo="r", commit_sha="abc", status="running")
    db_session.add(run)
    db_session.flush()
    w = JournalWriter(db_session, run.id, "r", "abc", _STAMPS)
    w.write(
        "agent",
        "agent_coverage",
        {
            "unit": "A.md",
            "usage": _usage(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=500),
        },
    )
    w.write(
        "agent",
        "agent_coverage",
        {
            "unit": "B.md",
            "usage": _usage(input_tokens=10, output_tokens=20, cache_read_input_tokens=4000),
        },
    )
    w.write(
        "semantic_judge",
        "s_verdict",
        {"literal": "x", "usage": _usage(input_tokens=300, output_tokens=90)},
    )

    summary = summarize_run_cost(db_session, run.id, "claude-sonnet-5")

    assert summary["tokens"] == _usage(
        input_tokens=1310,
        output_tokens=310,
        cache_read_input_tokens=4000,
        cache_creation_input_tokens=500,
    )
    assert summary["spend_usd"] == pytest.approx(
        usage_cost_usd(summary["tokens"], model="claude-sonnet-5"), abs=1e-6
    )
    assert summary["sources"] == {"agent_coverage": 2, "s_verdict": 1}
    # A reader can re-derive spend only when the pricing inputs are named.
    assert summary["models"] == ["claude-sonnet-5"]
    assert summary["price_table_ver"] == PRICE_TABLE_VER
    assert summary["graph_completed"] is True


def test_summary_counts_only_its_own_run_and_only_rows_that_were_billed(db_session):
    """The summary excludes neighbouring runs and rows without billed usage."""
    run = ScanRun(repo="r", commit_sha="abc", status="running")
    other = ScanRun(repo="r", commit_sha="abc", status="running")
    db_session.add_all([run, other])
    db_session.flush()
    JournalWriter(db_session, other.id, "r", "abc", _STAMPS).write(
        "agent", "agent_coverage", {"unit": "elsewhere.md", "usage": _usage(output_tokens=999)}
    )
    w = JournalWriter(db_session, run.id, "r", "abc", _STAMPS)
    w.write("agent", "agent_coverage", {"unit": "A.md", "usage": _usage(output_tokens=10)})
    w.write("gate", "gate_kill", {"literal": "x.md"})

    summary = summarize_run_cost(db_session, run.id, "claude-sonnet-5")

    assert summary["tokens"]["output_tokens"] == 10
    assert summary["sources"] == {"agent_coverage": 1}


def test_a_run_that_never_paid_reports_zero_not_nothing(db_session):
    """A run with no paid rows reports an explicit zero cost and empty basis."""
    run = ScanRun(repo="r", commit_sha="abc", status="running")
    db_session.add(run)
    db_session.flush()

    summary = summarize_run_cost(db_session, run.id, "claude-sonnet-5")

    assert summary["spend_usd"] == 0.0
    assert summary["sources"] == {}
    assert summary["tokens"] == _usage()


def _git_repo(tmp_path):
    """Create a committed repository for frame tests."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("x")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
        cwd=repo,
        check=True,
    )
    return repo


def _run_cost_rows(session, run_id):
    """Return run-cost payloads for one run in journal order."""
    from sqlalchemy import select

    from drift.persistence.models import JournalRecord

    return [
        r.payload
        for r in session.scalars(
            select(JournalRecord)
            .where(JournalRecord.run_id == run_id)
            .where(JournalRecord.record_type == "run_cost")
            .order_by(JournalRecord.id)
        )
    ]


def _bills_1000_output_tokens(db_session):
    """Build a stub cell that bills one thousand output tokens once per run."""
    billed: set[int] = set()

    def cell(run_id: int) -> None:
        """Write the run's paid coverage row at most once."""
        if run_id in billed:
            return
        billed.add(run_id)
        JournalWriter(db_session, run_id, "r", "abc", _STAMPS).write(
            "agent", "agent_coverage", {"unit": "A.md", "usage": _usage(output_tokens=1000)}
        )

    return cell


def _scan_with_stub_cells(tmp_path, db_session, monkeypatch, cell, fail_at_reconcile=False):
    """Prepare a real journal and stubbed cells, optionally failing frame reconciliation.

    Cell failures are isolated by dispatch, so reconciliation provides the frame-level failure
    needed to exercise the `finally` block that owns cost and export.
    """
    from drift.graph import frame

    repo = _git_repo(tmp_path)
    seen = {}

    def _hook(run_id, producer, unit_ref, repo_root, config):
        """Record the run id and invoke the configured stub cell."""
        seen["run_id"] = run_id
        cell(run_id)
        return None

    if fail_at_reconcile:

        def _boom(*a, **k):
            """Simulate reconciliation failure after cell dispatch."""
            raise RuntimeError("connection reset by peer")

        monkeypatch.setattr(frame, "reconcile_run", _boom)
    return repo, seen, frame, stub_dispatch(db_session, hook=_hook)


def test_the_run_cost_row_lands_on_a_completed_run(tmp_path, db_session, monkeypatch, capsys):
    """A completed frame writes one cost row matching its stderr spend."""
    repo, seen, frame, dispatch = _scan_with_stub_cells(
        tmp_path, db_session, monkeypatch, _bills_1000_output_tokens(db_session)
    )
    frame.run_scan(
        str(repo),
        client=object(),
        session_factory=lambda: db_session,
        dispatch=dispatch,
        poll_interval=0,
    )

    rows = _run_cost_rows(db_session, seen["run_id"])
    assert len(rows) == 1
    assert rows[0]["tokens"]["output_tokens"] == 1000
    assert rows[0]["spend_usd"] > 0
    assert f"spend=${rows[0]['spend_usd']:.4f}" in capsys.readouterr().err


def test_an_aborted_run_still_records_what_it_spent(tmp_path, db_session, monkeypatch, capsys):
    """A frame aborted after dispatch still records and prints persisted spend."""
    import pytest

    repo, seen, frame, dispatch = _scan_with_stub_cells(
        tmp_path,
        db_session,
        monkeypatch,
        _bills_1000_output_tokens(db_session),
        fail_at_reconcile=True,
    )
    with pytest.raises(RuntimeError):
        frame.run_scan(
            str(repo),
            client=object(),
            session_factory=lambda: db_session,
            dispatch=dispatch,
            poll_interval=0,
        )

    rows = _run_cost_rows(db_session, seen["run_id"])
    assert len(rows) == 1
    assert rows[0]["tokens"]["output_tokens"] == 1000
    assert rows[0]["spend_usd"] > 0
    assert f"spend=${rows[0]['spend_usd']:.4f}" in capsys.readouterr().err


def test_an_aborted_run_still_writes_its_export_artifact(tmp_path, db_session, monkeypatch):
    """A frame aborted after dispatch still exports paid coverage and cost rows."""
    import pytest

    repo, seen, frame, dispatch = _scan_with_stub_cells(
        tmp_path,
        db_session,
        monkeypatch,
        _bills_1000_output_tokens(db_session),
        fail_at_reconcile=True,
    )
    out = tmp_path / "run.jsonl"
    with pytest.raises(RuntimeError):
        frame.run_scan(
            str(repo),
            client=object(),
            session_factory=lambda: db_session,
            journal_export=str(out),
            dispatch=dispatch,
            poll_interval=0,
        )

    exported = [json.loads(line) for line in out.read_text().splitlines()]
    types = [r["record_type"] for r in exported]
    assert "run_cost" in types and "agent_coverage" in types
    cost = next(r["payload"] for r in exported if r["record_type"] == "run_cost")
    assert cost["tokens"]["output_tokens"] == 1000 and cost["spend_usd"] > 0


def test_a_completed_run_exports_once_with_the_cost_row_in_it(tmp_path, db_session, monkeypatch):
    """A completed frame exports exactly one run-cost row."""
    repo, seen, frame, dispatch = _scan_with_stub_cells(
        tmp_path, db_session, monkeypatch, _bills_1000_output_tokens(db_session)
    )
    out = tmp_path / "run.jsonl"
    frame.run_scan(
        str(repo),
        client=object(),
        session_factory=lambda: db_session,
        journal_export=str(out),
        dispatch=dispatch,
        poll_interval=0,
    )

    exported = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["record_type"] for r in exported].count("run_cost") == 1


def test_a_new_paid_stream_is_counted_without_being_listed_anywhere(db_session):
    """Any journal stream carrying non-empty usage contributes to the run's bill."""
    run = ScanRun(repo="r", commit_sha="abc", status="running")
    db_session.add(run)
    db_session.flush()
    w = JournalWriter(db_session, run.id, "r", "abc", _STAMPS)
    w.write("agent", "agent_coverage", {"unit": "A.md", "usage": _usage(output_tokens=10)})
    w.write("router", "router_decision", {"unit": "B.md", "usage": _usage(output_tokens=90)})
    w.write("router", "router_decision", {"unit": "C.md", "usage": {}})  # empty = not billed

    summary = summarize_run_cost(db_session, run.id, "claude-sonnet-5")

    assert summary["tokens"]["output_tokens"] == 100
    assert summary["sources"] == {"agent_coverage": 1, "router_decision": 1}


def test_each_row_is_priced_at_the_model_that_produced_it(db_session):
    """Multiple rows carrying the same model stamp aggregate under that model's price."""
    run = ScanRun(repo="r", commit_sha="abc", status="running")
    db_session.add(run)
    db_session.flush()
    for _ in range(2):
        JournalWriter(db_session, run.id, "r", "abc", _STAMPS).write(
            "agent", "agent_coverage", {"unit": "A.md", "usage": _usage(output_tokens=1000)}
        )

    summary = summarize_run_cost(db_session, run.id, "claude-sonnet-5")

    expected = 2 * usage_cost_usd(_usage(output_tokens=1000), model="claude-sonnet-5")
    assert summary["spend_usd"] == pytest.approx(expected, abs=1e-6)
    assert summary["models"] == ["claude-sonnet-5"]
    assert summary["unpriced"] == {}


def test_an_unpriced_row_is_disclosed_rather_than_raising(db_session):
    """An unknown model is disclosed while priceable rows still produce a cost floor."""
    run = ScanRun(repo="r", commit_sha="abc", status="running")
    db_session.add(run)
    db_session.flush()
    JournalWriter(db_session, run.id, "r", "abc", _STAMPS).write(
        "agent", "agent_coverage", {"unit": "A.md", "usage": _usage(output_tokens=1000)}
    )
    JournalWriter(
        db_session,
        run.id,
        "r",
        "abc",
        Stamps("agent/0.8", "sjudge/0.4", "some-future-model"),
    ).write("agent", "agent_coverage", {"unit": "B.md", "usage": _usage(output_tokens=500)})

    summary = summarize_run_cost(db_session, run.id, "claude-sonnet-5")

    # Token totals include every row; spend includes only models the price table can price.
    assert summary["tokens"]["output_tokens"] == 1500
    assert summary["spend_usd"] == pytest.approx(
        usage_cost_usd(_usage(output_tokens=1000), model="claude-sonnet-5"), abs=1e-6
    )
    assert summary["unpriced"] == {"some-future-model": 1}
    assert summary["models"] == ["claude-sonnet-5"]


class _Recorder:
    """Fail writes and record whether recovery attempted a rollback."""

    def __init__(self):
        """Start with no rollback recorded."""
        self.rolled_back = False

    def write(self, component, record_type, payload):
        """Simulate a failed journal statement."""
        raise RuntimeError("statement timeout")

    def flush(self):
        """Fail if called after the write error."""
        raise AssertionError("never reached")

    def rollback(self):
        """Record an attempted rollback."""
        self.rolled_back = True


def test_a_failed_cost_write_never_rolls_back_someone_elses_evidence(db_session, capsys):
    """A failed cost write is reported without rolling back pending run evidence.

    Reconciliation issue rows and terminal run state share the session and await the terminal
    commit. Cost-record failure and the derived spend remain operator-visible without risking
    that pending evidence.
    """
    from drift.graph import frame

    run = ScanRun(repo="r", commit_sha="abc", status="running")
    db_session.add(run)
    db_session.flush()
    rec = _Recorder()

    frame._record_run_cost(db_session, rec, run.id, "claude-sonnet-5", completed=True)

    assert rec.rolled_back is False
    err = capsys.readouterr().err
    assert "FAILED" in err
    assert "spend=$" in err


def test_a_poisoned_session_is_reset_so_the_artifact_survives_the_abort(monkeypatch, capsys):
    """Reset a poisoned real session so an aborted run can record its cost row.

    Previously flushed units remain readable, but the transactional fixture cannot model the
    rollback behavior under test, so this case uses an explicitly cleaned real session.
    """
    from drift.graph import journal_rows
    from drift.journal import run_cost as run_cost_module
    from drift.persistence.db import SessionLocal
    from drift.persistence.models import JournalRecord

    session = SessionLocal()
    run = ScanRun(repo="/tmp/d7-poisoned-session", commit_sha="abc", status="running")
    session.add(run)
    session.commit()  # Keep the run row valid across the recovery rollback.
    try:
        writer = JournalWriter(session, run.id, run.repo, "abc", _STAMPS)
        writer.write(
            "agent", "agent_coverage", {"unit": "A.md", "usage": _usage(output_tokens=1000)}
        )
        writer.flush()

        calls = {"n": 0}
        original = run_cost_module.summarize_run_cost

        def _poisoned_once(*a, **kw):
            """Fail the first summary query and delegate later attempts."""
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("current transaction is aborted")
            return original(*a, **kw)

        # The function has consumers in two modules, so the substitution goes to its owner.
        monkeypatch.setattr(run_cost_module, "summarize_run_cost", _poisoned_once)
        journal_rows._record_run_cost(session, writer, run.id, "claude-sonnet-5", completed=False)

        rows = _run_cost_rows(session, run.id)
        assert len(rows) == 1
        assert rows[0]["tokens"]["output_tokens"] == 1000
        assert rows[0]["graph_completed"] is False
        assert "resetting" in capsys.readouterr().err
    finally:
        session.rollback()
        session.query(JournalRecord).filter(JournalRecord.run_id == run.id).delete(
            synchronize_session=False
        )
        session.query(ScanRun).filter(ScanRun.id == run.id).delete(synchronize_session=False)
        session.commit()
        session.close()


def test_an_aborted_runs_artifact_says_it_was_aborted(tmp_path, db_session, monkeypatch):
    """An aborted run marks its exported cost as incomplete."""
    import pytest

    repo, seen, frame, dispatch = _scan_with_stub_cells(
        tmp_path,
        db_session,
        monkeypatch,
        _bills_1000_output_tokens(db_session),
        fail_at_reconcile=True,
    )
    out = tmp_path / "run.jsonl"
    with pytest.raises(RuntimeError):
        frame.run_scan(
            str(repo),
            client=object(),
            session_factory=lambda: db_session,
            journal_export=str(out),
            dispatch=dispatch,
            poll_interval=0,
        )

    exported = [json.loads(line) for line in out.read_text().splitlines()]
    cost = next(r["payload"] for r in exported if r["record_type"] == "run_cost")
    assert cost["graph_completed"] is False
