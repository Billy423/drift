"""Test graph enumeration, journaling, failure isolation, and full-pipeline wiring."""

import pytest
from sqlalchemy import select

from drift.agent.discovery import DiscoveryAgent, DiscoveryResult
from drift.domain.findings import Confidence
from drift.gate.replay import GateOutcome
from drift.graph.frame import build_graph
from drift.graph.nodes.discover import make_discover
from drift.graph.nodes.enumerate_units import enumerate_docs
from drift.graph.nodes.gate import make_gate_replay
from drift.graph.planning import DocumentNotResolvable
from drift.journal.writer import JournalWriter, Stamps
from drift.judge.semantic_judge import SemanticJudge
from drift.kernels.models import Anchor, Check, EvClaim, SSlot
from drift.kernels.registry import Predicate, predicate_registry
from drift.persistence.models import Issue, JournalRecord, ScanRun
from tests.agent.test_runner import _Block, _emit, _Resp, _ScriptedClient
from tests.fixtures.frame import finish, frame_repo, frame_run, planned

_STAMPS = Stamps("agent/0.1", "sjudge/0.1", "claude-sonnet-5")


class _EmptyProducer:
    """Return no claims and complete docstring coverage."""

    def produce(self, doc_filter=None):
        """Return the fixed empty producer result."""
        return [], {"unit": "docstring_corpus", "status": "complete"}


def _empty_producer_factory(root):
    """Build an empty docstring producer."""
    return _EmptyProducer()


def _frame_plan(db_session, run_id):
    """Return the run's unique planning-phase frame record."""
    rows = [
        r.payload
        for r in db_session.query(JournalRecord)
        .filter_by(run_id=run_id, record_type="frame_plan")
        .order_by(JournalRecord.id)
        .all()
    ]
    plans = [p for p in rows if p.get("phase") == "plan"]
    assert len(plans) == 1
    return plans[0]


def test_enumerate_docs_finds_all_doc_units_and_the_frames_filter_narrows(
    tmp_path, db_session, monkeypatch
):
    """The frame enumerates documents and narrows its plan with a document filter."""
    repo = frame_repo(
        tmp_path,
        files={"A.md": "a", "B.rst": "b", "skip.py": "x"},
    )
    (repo / ".git" / "C.md").write_text("c")

    full, _hazards = enumerate_docs(str(repo))
    assert full == ["A.md", "B.rst"]

    run_id, _ = frame_run(repo, db_session, monkeypatch)
    plan = _frame_plan(db_session, run_id)
    assert plan["doc_filter"] is None
    assert plan["unit_count"] == 2
    assert [cell[1] for cell in plan["cells"] if cell[0] == "agent"] == ["A.md", "B.rst"]

    run_id, _ = frame_run(repo, db_session, monkeypatch, doc_filter="B.rst")
    plan = _frame_plan(db_session, run_id)
    assert plan["doc_filter"] == "B.rst"
    assert plan["unit_count"] == 1
    assert [cell[1] for cell in plan["cells"] if cell[0] == "agent"] == ["B.rst"]

    with pytest.raises(DocumentNotResolvable):
        frame_run(repo, db_session, monkeypatch, doc_filter="nope.md")


class _FakeWriter:
    """Record journal writes in memory."""

    def __init__(self):
        """Initialize an empty call buffer."""
        self.calls: list[tuple[str, str, dict]] = []

    def write(self, component, record_type, payload):
        """Append one journal call."""
        self.calls.append((component, record_type, payload))

    def flush(self):
        """No-op: these tests assert what was written, not when it reached disk."""


def _claim(check, claim_class=1, literal="x.md"):
    """Build a claim for graph routing tests."""
    return EvClaim(
        anchor=Anchor(doc_path="d.md", spans=((1, 1),), literal=literal),
        check=check,
        claim_class=claim_class,
        s_slot=SSlot(note="n", confidence=0.9),
        provenance={"agent_ver": "agent/0.1", "producer": "agent"},
    )


def _check(literal, normalized_args):
    """Build a replayable path check."""
    return Check(
        predicate="path_exists",
        raw={"doc_path": "d.md", "literal": literal},
        normalization={"base": "repo-root"},
        normalized_args=normalized_args,
    )


def test_gate_replay_journals_every_adjudicated_outcome(tmp_path, monkeypatch):
    """Gate replay journals every mechanically adjudicated outcome."""
    (tmp_path / "d.md").write_text("present.md boom.md live.md")
    (tmp_path / "present.md").write_text("x")

    original = predicate_registry["path_exists"]

    def _kernel_maybe_raise(repo_root, rel_path):
        """Raise for one path and delegate all others to the real kernel."""
        if rel_path == "boom.md":
            raise RuntimeError("boom")
        return original.kernel(repo_root, rel_path)

    monkeypatch.setitem(
        predicate_registry,
        "path_exists",
        Predicate(
            name=original.name,
            description=original.description,
            normalize=original.normalize,
            kernel=_kernel_maybe_raise,
        ),
    )

    unbound = _claim(None, claim_class=3, literal="make lint")
    kernel_err = _claim(_check("boom.md", ("boom.md",)), literal="boom.md")
    binding_fail = _claim(_check("ghost.md", ("ghost.md",)), literal="ghost.md")
    passing = _claim(_check("present.md", ("present.md",)), literal="present.md")
    m_certified = _claim(_check("live.md", ("live.md",)), literal="live.md")

    writer = _FakeWriter()
    node = make_gate_replay(writer)
    out = node(
        {
            "repo_root": str(tmp_path),
            "claims": [unbound, kernel_err, binding_fail, passing, m_certified],
        }
    )

    outcomes = {gr.claim.anchor.literal: gr.outcome for gr in out["gate_results"]}
    assert outcomes["boom.md"] == GateOutcome.KERNEL_ERROR
    assert outcomes["ghost.md"] == GateOutcome.BINDING_FAIL
    assert outcomes["present.md"] == GateOutcome.PASSING
    assert outcomes["live.md"] == GateOutcome.M_CERTIFIED
    assert outcomes["make lint"] == GateOutcome.UNBOUND

    assert [e.claim for e in out["ranked_entries"]] == [unbound]

    kinds = {p["literal"]: p["kind"] for _c, rt, p in writer.calls if rt == "gate_kill"}
    assert kinds["boom.md"] == "kernel_error"
    assert kinds["ghost.md"] == "binding_fail"
    gate_outcome_rows = {p["literal"]: p for _c, rt, p in writer.calls if rt == "gate_outcome"}
    assert {lit: p["outcome"] for lit, p in gate_outcome_rows.items()} == {
        "present.md": "PASSING",
        "live.md": "M_CERTIFIED",
    }
    assert gate_outcome_rows["live.md"]["predicate"] == "path_exists"
    assert gate_outcome_rows["live.md"]["producer"] == "agent"
    assert gate_outcome_rows["live.md"]["normalized_args"] == ["live.md"]
    assert len(writer.calls) == 4


class _FlakyDiscoveryAgent:
    """Raises on the first doc it sees, succeeds on every later one."""

    def __init__(self, claim_for_second_doc):
        """Store the claim returned after the first call."""
        self._claim = claim_for_second_doc
        self._calls = 0

    def discover(self, repo_root, doc_path):
        """Fail the first document and return a claim for later documents."""
        self._calls += 1
        if self._calls == 1:
            raise RuntimeError("boom")
        return DiscoveryResult(
            claims=[self._claim],
            coverage={
                "unit": doc_path,
                "doc_hash": "deadbeef",
                "turns_used": 1,
                "tool_calls": 0,
                "status": "complete",
            },
        )


def test_discover_isolates_a_per_unit_exception_and_continues_the_worklist():
    """A document-level discovery failure does not stop later documents."""
    good_claim = _claim(None, claim_class=3, literal="second doc's claim")
    agent = _FlakyDiscoveryAgent(good_claim)
    writer = _FakeWriter()
    node = make_discover(agent, writer)

    out = node({"repo_root": "/irrelevant", "worklist": ["first.md", "second.md"]})

    assert out["claims"] == [good_claim]
    assert len(out["coverages"]) == 2
    assert out["coverages"][0]["status"] == "error"
    assert out["coverages"][0]["unit"] == "first.md"
    assert out["coverages"][1]["status"] == "complete"
    assert out["coverages"][1]["unit"] == "second.md"

    coverage_calls = [p for c, rt, p in writer.calls if rt == "agent_coverage"]
    assert len(coverage_calls) == 2
    error_row = coverage_calls[0]
    assert error_row["status"] == "error"
    assert error_row["unit"] == "first.md"
    assert error_row["doc_hash"] == ""
    assert error_row["turns_used"] == 0
    assert error_row["tool_calls"] == 0
    assert "boom" in error_row["detail"]
    assert coverage_calls[1]["status"] == "complete"


def _discovery_inventory():
    """Return the scripted discovery inventory."""
    return {
        "claims": [
            {
                "literal": "config/settings.py",
                "predicate": "path_exists",
                "spans": [[2, 2]],
                "claim_class": 1,
                "note": "present target",
                "confidence": 0.9,
            },
            {
                "literal": "docs/missing.md",
                "predicate": "path_exists",
                "spans": [[3, 3]],
                "claim_class": 1,
                "note": "looks live",
                "confidence": 0.9,
            },
            {
                "literal": "docs/gone.md",
                "predicate": "path_exists",
                "spans": [[4, 4]],
                "claim_class": 1,
                "note": "maybe stale",
                "confidence": 0.5,
            },
            {
                "literal": "phantom.md",
                "predicate": "path_exists",
                "spans": [[5, 5]],
                "claim_class": 1,
                "note": "hallucinated anchor",
                "confidence": 0.3,
            },
            {
                "literal": "make lint",
                "predicate": "none",
                "spans": [[6, 6]],
                "claim_class": 3,
                "note": "command, unbindable",
                "confidence": 0.5,
            },
        ]
    }


def _build_fixture_repo(tmp_path):
    """Build the repository used by full-pipeline tests."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.py").write_text("SETTING = 1\n")
    (tmp_path / "NOTES.md").write_text(
        "Notes doc.\n"
        "See config/settings.py for config.\n"
        "See docs/missing.md for the guide.\n"
        "See docs/gone.md for the old guide.\n"
        "(no mention of the ghost reference by that name anywhere here)\n"
        "Run `make lint` before committing.\n"
    )
    return str(tmp_path)


def _discovery_client():
    """Build the scripted discovery client."""
    return _ScriptedClient(
        [
            _Resp([_Block("text", text="reading the doc directly")]),
            _emit(_discovery_inventory()),
        ]
    )


def _judge_client():
    """Build the scripted semantic-judge client."""
    return _ScriptedClient(
        [
            _Resp([_Block("text", text="thinking")]),
            _emit(
                {
                    "live": True,
                    "reasoning": "still a live reference to a current guide",
                    "confidence": 0.9,
                }
            ),
            _Resp([_Block("text", text="thinking")]),
            _emit(
                {
                    "live": False,
                    "reasoning": "this reads as a changelog note about a removed doc",
                    "confidence": 0.9,
                }
            ),
        ]
    )


def test_full_pipeline_high_only_for_live_missing_comment_tier_and_no_s_failed_in_report(
    tmp_path, db_session
):
    """The full pipeline emits only live missing claims and preserves journal evidence."""
    repo_root = _build_fixture_repo(tmp_path)

    run = ScanRun(repo=repo_root, commit_sha="deadbeef", status="running")
    db_session.add(run)
    db_session.flush()

    writer = JournalWriter(db_session, run.id, repo_root, "deadbeef", _STAMPS)
    discovery_agent = DiscoveryAgent(_discovery_client(), agent_ver=_STAMPS.agent_ver)
    semantic_judge = SemanticJudge(_judge_client(), judge_ver=_STAMPS.judge_ver)

    graph = build_graph(
        discovery_agent,
        _empty_producer_factory,
        semantic_judge,
        writer=writer,
    )
    out = finish(
        db_session,
        repo_root,
        run.id,
        graph.invoke(
            {
                "repo_root": repo_root,
                "run_id": run.id,
                "doc_filter": None,
                "worklist": [],
                **planned(repo_root),
                "claims": [],
                "coverages": [],
                "gate_results": [],
                "verdicts": [],
                "findings": [],
                "ranked_entries": [],
                "kernel_errors": [],
                "result": None,
                "report_text": "",
            }
        ),
    )

    assert out["worklist"] == ["NOTES.md"]

    outcomes = {gr.claim.anchor.literal: gr.outcome for gr in out["gate_results"]}
    assert outcomes["config/settings.py"] == GateOutcome.PASSING
    assert outcomes["docs/missing.md"] == GateOutcome.M_CERTIFIED
    assert outcomes["docs/gone.md"] == GateOutcome.M_CERTIFIED
    assert outcomes["phantom.md"] == GateOutcome.BINDING_FAIL
    assert outcomes["make lint"] == GateOutcome.UNBOUND

    assert len(out["findings"]) == 1
    finding = out["findings"][0]
    assert finding.check_id == "path_exists"
    assert finding.identity == ("NOTES.md", "docs/missing.md")
    assert finding.confidence == Confidence.HIGH

    assert [e.claim.anchor.literal for e in out["ranked_entries"]] == ["make lint"]

    assert out["result"].discovered == 1
    issue = db_session.query(Issue).filter_by(repo=repo_root).one()
    assert issue.check_id == "path_exists"

    report = out["report_text"]
    assert "## Verified findings" in report
    assert "docs/missing.md" in report
    assert "## Ranked tier (candidates — UNVERIFIED)" in report
    assert "make lint" in report
    assert "docs/gone.md" not in report

    rows = list(db_session.scalars(select(JournalRecord).where(JournalRecord.run_id == run.id)))
    record_types = {r.record_type for r in rows}
    assert record_types == {
        "agent_coverage",
        "gate_kill",
        "gate_outcome",
        "s_verdict",
        "claim_inventory",
    }
    inventory = [r for r in rows if r.record_type == "claim_inventory"]
    assert len(inventory) == 5
    assert all(
        r.agent_ver == _STAMPS.agent_ver
        and r.judge_ver == _STAMPS.judge_ver
        and r.model == _STAMPS.model
        for r in rows
    )

    gate_kill_rows = [r for r in rows if r.record_type == "gate_kill"]
    assert len(gate_kill_rows) == 1
    assert gate_kill_rows[0].payload["literal"] == "phantom.md"
    assert gate_kill_rows[0].payload["kind"] == "binding_fail"
    assert gate_kill_rows[0].payload["predicate"] == "path_exists"
    assert gate_kill_rows[0].payload["producer"] == "agent"
    assert gate_kill_rows[0].payload["normalized_args"] == ["phantom.md"]

    s_verdict_rows = {r.payload["literal"]: r.payload for r in rows if r.record_type == "s_verdict"}
    assert set(s_verdict_rows) == {"docs/missing.md", "docs/gone.md"}
    assert s_verdict_rows["docs/missing.md"]["predicate"] == "path_exists"
    assert s_verdict_rows["docs/missing.md"]["live"] is True
    assert s_verdict_rows["docs/missing.md"]["error"] is False
    assert s_verdict_rows["docs/gone.md"]["live"] is False
    assert s_verdict_rows["docs/gone.md"]["error"] is False
    assert s_verdict_rows["docs/missing.md"]["doc_chars"] > 0

    missing_hash = s_verdict_rows["docs/missing.md"]["doc_hash"]
    assert missing_hash
    assert missing_hash == s_verdict_rows["docs/gone.md"]["doc_hash"]
    assert s_verdict_rows["docs/missing.md"]["normalized_args"] == ["docs/missing.md"]
    assert s_verdict_rows["docs/gone.md"]["normalized_args"] == ["docs/gone.md"]

    scout_rows = [
        r for r in rows if r.record_type == "agent_coverage" and r.payload["unit"] == "NOTES.md"
    ]
    assert len(scout_rows) == 1
    assert scout_rows[0].payload["unit"] == "NOTES.md"


def test_report_marks_incomplete_when_gate_hits_a_kernel_error(tmp_path, db_session, monkeypatch):
    """A kernel error marks the report incomplete without creating a finding or lead."""
    repo_root = str(tmp_path)
    (tmp_path / "NOTES.md").write_text("See boom.md for details.\n")

    original = predicate_registry["path_exists"]

    def _kernel_raise(repo_root, rel_path):
        """Simulate a kernel failure."""
        raise RuntimeError("kernel boom")

    monkeypatch.setitem(
        predicate_registry,
        "path_exists",
        Predicate(
            name=original.name,
            description=original.description,
            normalize=original.normalize,
            kernel=_kernel_raise,
        ),
    )

    inventory = {
        "claims": [
            {
                "literal": "boom.md",
                "predicate": "path_exists",
                "spans": [[1, 1]],
                "claim_class": 1,
                "note": "target",
                "confidence": 0.9,
            }
        ]
    }
    discovery_client = _ScriptedClient(
        [
            _Resp([_Block("text", text="reading the doc directly")]),
            _emit(inventory),
        ]
    )

    run = ScanRun(repo=repo_root, commit_sha="deadbeef", status="running")
    db_session.add(run)
    db_session.flush()

    writer = JournalWriter(db_session, run.id, repo_root, "deadbeef", _STAMPS)
    discovery_agent = DiscoveryAgent(discovery_client, agent_ver=_STAMPS.agent_ver)
    semantic_judge = SemanticJudge(_ScriptedClient([]), judge_ver=_STAMPS.judge_ver)

    graph = build_graph(
        discovery_agent,
        _empty_producer_factory,
        semantic_judge,
        writer=writer,
    )
    out = finish(
        db_session,
        repo_root,
        run.id,
        graph.invoke(
            {
                "repo_root": repo_root,
                "run_id": run.id,
                "doc_filter": None,
                "worklist": [],
                **planned(repo_root),
                "claims": [],
                "coverages": [],
                "gate_results": [],
                "verdicts": [],
                "findings": [],
                "ranked_entries": [],
                "kernel_errors": [],
                "result": None,
                "report_text": "",
            }
        ),
    )

    assert out["findings"] == []
    assert out["ranked_entries"] == []
    assert "INCOMPLETE" in out["report_text"]
    assert "NOTES.md" in out["report_text"]


def test_worklist_above_cap_aborts_loudly(tmp_path, db_session, monkeypatch):
    """An oversized worklist aborts, while a filter narrows before the unit cap applies."""
    from drift.graph.nodes import rails

    repo = frame_repo(tmp_path, files={f"doc{i:03d}.md": "x" for i in range(3)})
    monkeypatch.setattr(rails, "MAX_UNITS", 1)

    with pytest.raises(RuntimeError, match="drift check"):
        frame_run(repo, db_session, monkeypatch)

    run_id, _ = frame_run(repo, db_session, monkeypatch, doc_filter="doc000.md")
    plan = _frame_plan(db_session, run_id)
    assert plan["unit_count"] == 1
    assert [cell[1] for cell in plan["cells"] if cell[0] == "agent"] == ["doc000.md"]
    assert plan["max_units"] == 1
