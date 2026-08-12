"""Tests for frame enumeration, planning, disposition, and zero-document scans."""

from __future__ import annotations

import subprocess

import pytest

from drift.graph import cell, planning
from drift.graph.frame import run_scan
from drift.journal.completeness import run_incompleteness
from drift.kernels.models import PRODUCERS
from drift.persistence.models import JournalRecord, ScanRun
from tests.fixtures.frame import frame_run, stub_dispatch

LANE_B_CELL = ["docstrings", "docstring_corpus"]


def _git_repo(tmp_path, name="repo"):
    """Initialize an empty Git repository for a frame test."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def _commit(repo):
    """Commit the repository's current contents."""
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
        cwd=repo,
        check=True,
    )


def _plan_rows(session, run_id, phase="plan"):
    """Return one run's frame-plan payloads for a phase in journal order."""
    return [
        r.payload
        for r in session.query(JournalRecord)
        .filter_by(run_id=run_id, record_type="frame_plan")
        .order_by(JournalRecord.id)
        .all()
        if r.payload.get("phase") == phase
    ]


def test_the_plan_row_has_the_fixed_schema_with_lane_b_first(tmp_path, db_session, monkeypatch):
    """Keep the plan schema exact and put the docstring producer first."""
    repo = _git_repo(tmp_path)
    (repo / "README.md").write_text("x")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.rst").write_text("y")
    _commit(repo)

    run_id, _ = frame_run(repo, db_session, monkeypatch)

    assert _plan_rows(db_session, run_id) == [
        {
            "phase": "plan",
            # The docstring corpus is prepended to discovery cells in the plan.
            "cells": [LANE_B_CELL, ["agent", "README.md"], ["agent", "docs/guide.rst"]],
            "cell_count": 3,
            "unit_count": 2,  # Document units, excluding the corpus cell.
            "lane_b_first": True,
            "doc_filter": None,
            "max_units": 300,
        }
    ]


def test_one_plan_row_per_run_and_it_precedes_the_first_dispatch(tmp_path, db_session, monkeypatch):
    """Persist exactly one plan row before the first cell can incur cost."""
    repo = _git_repo(tmp_path)
    (repo / "README.md").write_text("x")
    _commit(repo)
    seen: dict = {}

    def _hook(run_id, producer, unit_ref, repo_root, config):
        """Observe plan durability at the first dispatch."""
        seen.setdefault("rows_at_first_dispatch", len(_plan_rows(db_session, run_id)))
        return None

    run_id, _ = frame_run(
        repo, db_session, monkeypatch, dispatch=stub_dispatch(db_session, hook=_hook)
    )

    assert seen["rows_at_first_dispatch"] == 1
    assert len(_plan_rows(db_session, run_id)) == 1
    assert len(_plan_rows(db_session, run_id, phase="disposition")) == 1


def test_the_disposition_row_reports_every_cell_as_funded_when_the_wallet_never_binds(
    tmp_path, db_session, monkeypatch
):
    """Record every dispatched cell as funded when the wallet never binds."""
    repo = _git_repo(tmp_path)
    (repo / "README.md").write_text("x")
    _commit(repo)

    run_id, _ = frame_run(repo, db_session, monkeypatch)

    assert _plan_rows(db_session, run_id, phase="disposition") == [
        {
            "phase": "disposition",
            "funded": [LANE_B_CELL, ["agent", "README.md"]],
            "deferred_by_wallet": [],
            "never_dispatched": [],
        }
    ]


def test_the_doc_filter_narrows_lane_a_and_is_recorded(tmp_path, db_session, monkeypatch):
    """Record the document filter without changing the docstring cell's address."""
    repo = _git_repo(tmp_path)
    (repo / "README.md").write_text("x")
    (repo / "OTHER.md").write_text("y")
    _commit(repo)

    run_id, _ = frame_run(repo, db_session, monkeypatch, doc_filter="README.md")

    plan = _plan_rows(db_session, run_id)[0]
    assert plan["cells"] == [LANE_B_CELL, ["agent", "README.md"]]
    assert (plan["unit_count"], plan["doc_filter"]) == (1, "README.md")


def test_an_enumeration_skip_never_becomes_a_cell(tmp_path, db_session, monkeypatch):
    """Exclude enumeration hazards from the cell plan."""
    repo = _git_repo(tmp_path)
    (repo / "README.md").write_text("x")
    (repo / "escape.md").symlink_to(tmp_path / "outside.md")
    (tmp_path / "outside.md").write_text("secret")
    _commit(repo)

    run_id, _ = frame_run(repo, db_session, monkeypatch)

    plan = _plan_rows(db_session, run_id)[0]
    assert plan["cells"] == [LANE_B_CELL, ["agent", "README.md"]]
    assert plan["unit_count"] == 1


def test_every_planned_cell_names_an_admitted_producer(tmp_path, monkeypatch):
    """Require every cell address to name an admitted producer."""
    assert {c[0] for c in planning.plan_cells([])} <= PRODUCERS

    monkeypatch.setattr(cell, "LANE_A_PRODUCER", "agents")
    with pytest.raises(ValueError, match="closed vocabulary"):
        planning.plan_cells(["README.md"])


def test_a_zero_unit_repo_plans_reports_and_reaches_terminal_status(tmp_path, db_session):
    """Complete and report a scan whose only planned cell is the docstring corpus."""
    repo = _git_repo(tmp_path, "empty")
    (repo / "Makefile").write_text("all:\n\t@true\n")
    _commit(repo)

    run_id, report_text = run_scan(str(repo), client=object(), session_factory=lambda: db_session)

    plan = _plan_rows(db_session, run_id)[0]
    assert (plan["cells"], plan["unit_count"], plan["cell_count"]) == ([LANE_B_CELL], 0, 1)
    assert report_text
    assert db_session.get(ScanRun, run_id).status == "done"
    # Corpus coverage currently prevents the zero-document incompleteness mode.
    assert [r.mode for r in run_incompleteness(db_session, run_id)] == []
    assert (
        db_session.query(JournalRecord).filter_by(run_id=run_id, record_type="rail_config").count()
        == 1
    )


class _FakeWriter:
    """Collect journal calls in memory."""

    def __init__(self):
        """Initialize an empty call buffer."""
        self.calls: list[tuple[str, str, dict]] = []

    def write(self, component, record_type, payload):
        """Append one journal-shaped call."""
        self.calls.append((component, record_type, payload))

    def flush(self):
        """Leave buffered calls unchanged."""


def test_the_node_takes_the_frames_worklist_and_does_not_re_enumerate(tmp_path):
    """Adopt the frame's worklist without walking the repository again."""
    from drift.graph.nodes.enumerate_units import make_adopt_worklist

    (tmp_path / "disk.md").write_text("x")
    node = make_adopt_worklist(_FakeWriter())

    out = node(
        {
            "repo_root": str(tmp_path),
            "doc_filter": None,
            "planned_worklist": ["planned.md"],
            "planned_hazards": [],
        }
    )

    assert out["worklist"] == ["planned.md"]


def test_an_unplanned_invocation_scans_nothing_rather_than_enumerating_for_itself(tmp_path):
    """Scan nothing when the frame provides no plan."""
    from drift.graph.nodes.enumerate_units import make_adopt_worklist

    (tmp_path / "disk.md").write_text("x")

    out = make_adopt_worklist(_FakeWriter())({"repo_root": str(tmp_path), "doc_filter": None})

    assert out["worklist"] == []


def test_the_frames_hazards_reach_the_report_and_the_journal(tmp_path):
    """Report and journal hazards supplied by the frame's enumeration."""
    from drift.graph.nodes.enumerate_units import make_adopt_worklist

    writer = _FakeWriter()
    hazard = {
        "unit": "escape.md",
        "disposition": "skipped",
        "reason": "escapes-repo",
        "size_bytes": 0,
    }

    out = make_adopt_worklist(writer)(
        {
            "repo_root": str(tmp_path),
            "doc_filter": None,
            "planned_worklist": ["README.md"],
            "planned_hazards": [hazard],
        }
    )

    assert any("escape.md" in note for note in out["partial_notes"])
    assert [c["unit"] for c in out["coverages"]] == ["escape.md"]
    assert [rt for _c, rt, _p in writer.calls] == ["agent_coverage"]
