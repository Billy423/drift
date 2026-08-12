"""Tests journal stamping, flushing, and rollback recovery."""

import pytest
from sqlalchemy import select

from drift.journal.writer import JournalWriter, Stamps
from drift.persistence.db import SessionLocal
from drift.persistence.models import JournalRecord, ScanRun


def _run(db_session):
    """Create and flush a running scan."""
    run = ScanRun(repo="r", commit_sha="abc", status="running")
    db_session.add(run)
    db_session.flush()
    return run


def test_write_stamps_every_record(db_session):
    """Every journal record carries its run and version stamps."""
    run = _run(db_session)
    stamps = Stamps("agent/0.1", "sjudge/0.1", "claude-sonnet-5")
    w = JournalWriter(db_session, run.id, "r", "abc", stamps)
    w.write("agent", "agent_coverage", {"unit": "CLAUDE.md", "turns_used": 3, "status": "complete"})
    w.write("gate", "gate_kill", {"kind": "binding_fail", "literal": "x.md"})
    # Scope the query because the configured database may contain unrelated committed rows.
    rows = list(db_session.scalars(select(JournalRecord).where(JournalRecord.run_id == run.id)))
    assert {r.record_type for r in rows} == {"agent_coverage", "gate_kill"}
    assert all(r.agent_ver == "agent/0.1" and r.judge_ver == "sjudge/0.1" for r in rows)
    assert all(r.model == "claude-sonnet-5" and r.run_id == run.id for r in rows)


_ROLLBACK_REPO = "/tmp/s2-rollback-recovery"


def _clean_rollback_repo():
    """Delete rows owned by the rollback-recovery fixture."""
    session = SessionLocal()
    session.query(JournalRecord).filter_by(repo=_ROLLBACK_REPO).delete()
    session.query(ScanRun).filter_by(repo=_ROLLBACK_REPO).delete()
    session.commit()
    session.close()


def test_rollback_makes_a_broken_session_usable_again():
    """Rollback after a rejected JSON payload restores the session for later writes.

    PostgreSQL rejects a NUL byte in JSONB, and claim payloads can contain untrusted document
    text, so the failure uses a production-reachable database error. A real session is required:
    the transactional fixture would roll back past the committed run row that keeps `run_id`
    valid during recovery.
    """
    _clean_rollback_repo()
    try:
        session = SessionLocal()
        try:
            run = ScanRun(repo=_ROLLBACK_REPO, commit_sha="abc", status="running")
            session.add(run)
            session.commit()  # The run must survive the later payload rollback.
            stamps = Stamps("a/1", "s/1", "m")
            writer = JournalWriter(session, run.id, _ROLLBACK_REPO, "abc", stamps)

            writer.write("agent", "agent_coverage", {"unit": "good.md", "status": "complete"})
            writer.flush()

            writer.write("agent", "agent_coverage", {"unit": "bad.md", "note": "\x00"})
            with pytest.raises(Exception):
                writer.flush()

            writer.rollback()

            writer.write("agent", "agent_coverage", {"unit": "after.md", "status": "complete"})
            writer.flush()
            run_id = run.id
        finally:
            session.close()

        survivor = SessionLocal()
        try:
            rows = (
                survivor.query(JournalRecord)
                .filter_by(run_id=run_id)
                .order_by(JournalRecord.id)
                .all()
            )
            assert [r.payload["unit"] for r in rows] == ["good.md", "after.md"]
        finally:
            survivor.close()
    finally:
        _clean_rollback_repo()
