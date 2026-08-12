"""Tests the complete, run-scoped JSONL journal export."""

import json

from drift.journal.export import export_run
from drift.journal.writer import JournalWriter, Stamps
from drift.persistence.models import ScanRun


def test_export_writes_every_row_of_the_run_and_only_that_run(db_session, tmp_path):
    """The export contains only its run's rows with their agent and judge versions."""
    run = ScanRun(repo="r", commit_sha="abc", status="running")
    other = ScanRun(repo="r", commit_sha="abc", status="running")
    db_session.add_all([run, other])
    db_session.flush()
    stamps = Stamps("agent/0.7", "sjudge/0.4", "claude-sonnet-5")
    w = JournalWriter(db_session, run.id, "r", "abc", stamps)
    w.write("run", "rail_config", {"max_units": 300})
    w.write("gate", "gate_kill", {"kind": "binding_fail", "literal": "x.md"})
    w_other = JournalWriter(db_session, other.id, "r", "abc", stamps)
    w_other.write("run", "rail_config", {"max_units": 300})

    out = tmp_path / "run.jsonl"
    n = export_run(db_session, run.id, str(out))

    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert n == len(lines) == 2
    assert {r["record_type"] for r in lines} == {"rail_config", "gate_kill"}
    assert all(r["run_id"] == run.id for r in lines)
    # Stamps let the artifact identify the versions that produced each row.
    assert all(r["agent_ver"] == "agent/0.7" and r["judge_ver"] == "sjudge/0.4" for r in lines)
    assert lines[1]["payload"]["literal"] == "x.md"
