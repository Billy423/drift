"""Tests that each discovery unit is durable before the next unit begins."""

from __future__ import annotations

import pytest

from drift.graph.nodes.discover import make_discover
from drift.journal.writer import JournalWriter, Stamps
from drift.persistence.db import SessionLocal
from drift.persistence.models import JournalRecord, ScanRun

_STAMPS = Stamps("agent/x", "sjudge/x", "claude-sonnet-5")
_REPO = "/tmp/s1-commit-cadence"


class _CrashOnSecondUnit:
    """Complete the first discovery call and interrupt the second."""

    def __init__(self):
        """Start with no discovery calls recorded."""
        self.calls = 0

    def discover(self, repo_root, doc_path):
        """Return coverage once, then simulate process interruption."""
        from drift.agent.discovery import DiscoveryResult

        self.calls += 1
        if self.calls > 1:
            raise KeyboardInterrupt("process killed mid-unit")
        return DiscoveryResult(
            claims=[],
            coverage={
                "unit": doc_path,
                "doc_hash": "hash-1",
                "turns_used": 1,
                "tool_calls": 0,
                "status": "complete",
                "usage": {"output_tokens": 1},
            },
        )


class _CountingWriter:
    """Record journal writes and flushes in call order."""

    def __init__(self):
        """Start with an empty call log."""
        self.calls: list[str] = []

    def write(self, component, record_type, payload):
        """Record the written record type."""
        self.calls.append(record_type)

    def flush(self):
        """Record a flush boundary."""
        self.calls.append("FLUSH")


def _state(worklist):
    """Build the minimal discovery-node state for a worklist."""
    return {
        "repo_root": _REPO,
        "worklist": list(worklist),
        "budget": float("inf"),
        "spend": 0.0,
        "partial_notes": [],
    }


def _clean():
    """Delete rows owned by the commit-cadence fixture."""
    session = SessionLocal()
    session.query(JournalRecord).filter_by(repo=_REPO).delete()
    session.query(ScanRun).filter_by(repo=_REPO).delete()
    session.commit()
    session.close()


def test_unit_one_survives_a_crash_during_unit_two():
    """The first unit remains committed after the second interrupts the process."""
    _clean()
    try:
        session = SessionLocal()
        run = ScanRun(repo=_REPO, commit_sha="sha", status="running")
        session.add(run)
        session.flush()
        run_id = run.id
        writer = JournalWriter(session, run_id, _REPO, "sha", _STAMPS)
        node = make_discover(_CrashOnSecondUnit(), writer)

        with pytest.raises(KeyboardInterrupt):
            node(_state(["a.md", "b.md"]))

        session.rollback()  # Discard anything that was not durable before interruption.
        session.close()

        survivor = SessionLocal()
        try:
            rows = survivor.query(JournalRecord).filter_by(run_id=run_id).all()
            assert [r.record_type for r in rows] == ["agent_coverage"]
            assert rows[0].payload["unit"] == "a.md"
            # The first unit's commit also makes the run row durable.
            assert survivor.get(ScanRun, run_id) is not None
        finally:
            survivor.close()
    finally:
        _clean()


def test_the_discover_node_flushes_after_every_unit():
    """The discovery node flushes immediately after a unit's coverage row."""
    writer = _CountingWriter()
    node = make_discover(_CrashOnSecondUnit(), writer)

    with pytest.raises(KeyboardInterrupt):
        node(_state(["a.md", "b.md"]))

    assert writer.calls == ["agent_coverage", "FLUSH"]


def test_an_errored_unit_is_also_flushed():
    """A fail-isolated unit flushes its error coverage before continuing."""

    class _ErroringAgent:
        """Raise a recoverable error on every discovery call."""

        def discover(self, repo_root, doc_path):
            """Simulate a failed model request."""
            raise RuntimeError("connection reset")

    writer = _CountingWriter()
    node = make_discover(_ErroringAgent(), writer)

    node(_state(["a.md"]))

    assert writer.calls == ["agent_coverage", "FLUSH"]
