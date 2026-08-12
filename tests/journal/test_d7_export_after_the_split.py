"""Tests that an interrupted scan's export remains a self-contained run artifact.

The interruption is a `KeyboardInterrupt`, which still reaches `finally`; abrupt process
termination is outside this test's scope.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from drift.graph.frame import run_scan
from drift.persistence.db import SessionLocal
from drift.persistence.models import (
    CellTerminalStatus,
    Issue,
    IssueEvent,
    JournalRecord,
    ScanRun,
)
from tests.agent.test_runner import _Block, _emit, _Resp


class _Usage:
    """Provide fixed non-zero usage on every priced token axis."""

    input_tokens = 900
    output_tokens = 400
    cache_read_input_tokens = 12000
    cache_creation_input_tokens = 3000


class _KilledMidJudge:
    """Emit three claims, complete one adjudication, then interrupt the next."""

    def __init__(self):
        """Expose the message client and reset its scripted call counter."""
        self.messages = self
        self.n = 0

    def create(self, **kwargs):
        """Return the next scripted model response or interrupt after one verdict."""
        self.n += 1
        if self.n == 1:
            resp = _Resp(
                [_Block("tool_use", id="t1", name="read_file", input={"path": "GUIDE.md"})]
            )
        elif self.n == 2:
            resp = _Resp([_Block("text", text="read the guide")])
        elif self.n == 3:
            resp = _emit(
                {
                    "claims": [
                        {
                            "literal": "assets/logo.png",
                            "predicate": "path_exists",
                            "spans": [[1, 1]],
                            "claim_class": 1,
                            "note": "missing asset",
                            "confidence": 0.04,
                        },
                        {
                            "literal": "docs/gone.md",
                            "predicate": "path_exists",
                            "spans": [[2, 2]],
                            "claim_class": 1,
                            "note": "missing doc",
                            "confidence": 0.05,
                        },
                        {
                            "literal": "README.md",
                            "predicate": "path_exists",
                            "spans": [[3, 3]],
                            "claim_class": 1,
                            "note": "present",
                            "confidence": 0.9,
                        },
                    ]
                }
            )
        elif self.n == 4:
            # The judge's cycle and emit are separate calls, so one verdict spans two responses.
            resp = _Resp([_Block("text", text="considering the guide")])
        elif self.n == 5:
            resp = _emit({"live": True, "reasoning": "the guide is current", "confidence": 0.88})
        else:
            raise KeyboardInterrupt("process killed mid-run")
        resp.usage = _Usage()
        return resp


def _repo(tmp_path):
    """Create a committed repository containing three path claims."""
    root = tmp_path / "d7repo"
    root.mkdir()
    (root / "GUIDE.md").write_text(
        "The logo lives at assets/logo.png.\nNotes moved to docs/gone.md.\nSee README.md too.\n"
    )
    (root / "README.md").write_text("hi\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
        cwd=root,
        check=True,
    )
    return str(root)


def _clean(repo_root: str) -> None:
    """Delete all persisted rows owned by the fixture repository."""
    session = SessionLocal()
    try:
        runs = [r.id for r in session.query(ScanRun).filter(ScanRun.repo == repo_root)]
        issues = [i.id for i in session.query(Issue).filter(Issue.repo == repo_root)]
        session.query(IssueEvent).filter(IssueEvent.issue_id.in_(issues)).delete(
            synchronize_session=False
        )
        session.query(Issue).filter(Issue.id.in_(issues)).delete(synchronize_session=False)
        session.query(JournalRecord).filter(JournalRecord.run_id.in_(runs)).delete(
            synchronize_session=False
        )
        # Terminal-status rows have no repo column, so scope them through their run ids.
        session.query(CellTerminalStatus).filter(CellTerminalStatus.run_id.in_(runs)).delete(
            synchronize_session=False
        )
        session.query(ScanRun).filter(ScanRun.id.in_(runs)).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()


@pytest.fixture
def killed_run_export(tmp_path):
    """Yield exported rows from a real scan interrupted during judging."""
    repo_root = _repo(tmp_path)
    out = tmp_path / "run.jsonl"
    try:
        with pytest.raises(KeyboardInterrupt):
            run_scan(
                repo_root,
                doc_filter="GUIDE.md",
                client=_KilledMidJudge(),
                journal_export=str(out),
            )
        yield [json.loads(line) for line in out.read_text().splitlines()]
    finally:
        _clean(repo_root)


def _rows(export, record_type):
    """Select exported rows of one record type."""
    return [r for r in export if r["record_type"] == record_type]


def test_a_killed_run_writes_an_artifact_at_all(killed_run_export):
    """A configured export is non-empty and contains exactly one run-cost row."""
    assert killed_run_export, "the killed run left no artifact"
    assert len(_rows(killed_run_export, "run_cost")) == 1


def test_question_a_attrition_is_readable_as_row_types(killed_run_export):
    """Row types and claim references expose attrition through gate and judge."""
    inventory = _rows(killed_run_export, "claim_inventory")
    outcomes = _rows(killed_run_export, "gate_outcome")
    verdicts = _rows(killed_run_export, "s_verdict")

    assert len(inventory) == 3
    by_outcome = {}
    for row in outcomes:
        by_outcome.setdefault(row["payload"]["outcome"], []).append(row["payload"])
    assert sorted(by_outcome) == ["M_CERTIFIED", "PASSING"]
    assert len(by_outcome["M_CERTIFIED"]) == 2 and len(by_outcome["PASSING"]) == 1
    assert all(p["detail"] for rows in by_outcome.values() for p in rows)
    assert all(
        {"predicate", "producer", "normalized_args"} <= set(p)
        for rows in by_outcome.values()
        for p in rows
    )
    # The funnel visibly narrows from three claims to two refutations and one completed verdict.
    assert len(verdicts) == 1

    # Refutation and inability to evaluate use separate record types; this fixture has no skips.
    assert "gate_ungateable" not in {r["record_type"] for r in killed_run_export}


def test_question_b_placement_is_readable_against_the_journaled_band(killed_run_export):
    """Inventory confidence and run configuration expose ranked-tier placement."""
    config = _rows(killed_run_export, "rail_config")
    assert len(config) == 1, "the run's self-description must be exactly one row"
    band = config[0]["payload"]["suspected_band_max"]

    slots = {
        r["payload"]["anchor"]["literal"]: r["payload"]["s_slot"]
        for r in _rows(killed_run_export, "claim_inventory")
    }
    assert slots["assets/logo.png"]["confidence"] <= band
    assert slots["README.md"]["confidence"] > band


def test_question_c_the_agent_reports_its_tools_and_its_read_volume(killed_run_export):
    """Discovery coverage records tool arguments, returned size, and document size."""
    coverage = [r["payload"] for r in _rows(killed_run_export, "agent_coverage")]
    # Both producers emit coverage; only the discovery row has model tool and document-read data.
    assert {c["unit"] for c in coverage} == {"GUIDE.md", "docstring_corpus"}
    agent_row = next(c for c in coverage if c["unit"] == "GUIDE.md")
    trace = agent_row["tool_trace"]
    assert [t["tool"] for t in trace] == ["read_file"]
    assert trace[0]["args"] == {"path": "GUIDE.md"}
    assert trace[0]["returned_chars"] > 0 and trace[0]["truncated"] is False
    assert agent_row["doc_chars"] > 0


def test_question_d_the_judge_reports_what_it_said_and_under_which_prompt(killed_run_export):
    """The verdict, prompt hashes, and version triple identify the judge output."""
    verdict = _rows(killed_run_export, "s_verdict")[0]["payload"]
    assert verdict["live"] is True
    assert verdict["confidence"] == 0.88
    assert verdict["reasoning"] and verdict["usage"] and verdict["doc_chars"] > 0
    assert verdict["tool_trace"] == []

    config = _rows(killed_run_export, "rail_config")[0]["payload"]
    assert config["agent_prompt_sha"] and config["judge_prompt_sha"]

    for row in killed_run_export:
        assert row["agent_ver"] and row["judge_ver"] and row["model"]


def test_the_cost_is_one_row_and_says_the_run_was_aborted(killed_run_export):
    """One cost row marks interrupted spend as incomplete and states its basis."""
    cost = _rows(killed_run_export, "run_cost")[0]["payload"]
    assert cost["graph_completed"] is False
    assert cost["spend_usd"] > 0
    assert cost["tokens"]["output_tokens"] > 0
    assert cost["sources"], "the basis of the figure is stated, not implied"
