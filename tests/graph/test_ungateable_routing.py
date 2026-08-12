"""Test journaling, ranked surfacing, and candidate limits for claims the gate cannot decide."""

from drift.gate.replay import GateOutcome, GateResult
from drift.graph.nodes.gate import make_gate_replay
from drift.graph.nodes.judge import (
    _to_finding,
    make_semantic_judge,
)
from drift.graph.nodes.rails import MAX_S_CANDIDATES
from drift.judge.semantic_judge import SVerdict
from drift.kernels.models import Anchor, Check, EvClaim, SSlot


class _Writer:
    """Record journal writes in memory."""

    def __init__(self):
        """Initialize an empty row buffer."""
        self.rows = []

    def write(self, component, record_type, payload):
        """Append one journal record."""
        self.rows.append((component, record_type, payload))

    def flush(self):
        """No-op: these tests assert what was written, not when it reached disk."""


def _claim(predicate="signature_has_param", literal="x : str", producer="docstrings"):
    """Build a claim with configurable predicate and producer."""
    return EvClaim(
        anchor=Anchor(doc_path="pkg/mod.py", spans=((5, 5),), literal=literal),
        check=Check(
            predicate=predicate,
            raw={"literal": literal, "doc_path": "pkg/mod.py", "proposed_args": []},
            normalization={},
            normalized_args=("pkg.mod.f", "x"),
        ),
        claim_class=2,
        s_slot=SSlot(note="", confidence=0.5),
        provenance={"producer": producer, "agent_ver": "agent/0.3"},
    )


def _run_gate(monkeypatch, outcome_reason):
    """Replay one ungateable claim with a fixed reason."""
    claim = _claim()
    result = GateResult(claim, GateOutcome.UNGATEABLE, outcome_reason)
    monkeypatch.setattr("drift.graph.nodes.gate.replay", lambda root, claims: [result])
    writer = _Writer()
    node = make_gate_replay(writer)
    out = node({"repo_root": "/r", "claims": [claim]})
    return out, writer


def test_variadic_goes_to_comment_tier_and_journal(monkeypatch):
    """A variadic signature is journaled and shown in the ranked tier."""
    out, writer = _run_gate(monkeypatch, "variadic")
    assert len(out["ranked_entries"]) == 1
    rows = [r for r in writer.rows if r[1] == "gate_ungateable"]
    assert rows[0][2]["reason"] == "variadic"
    assert rows[0][2]["producer"] == "docstrings"


def test_external_journal_only(monkeypatch):
    """An external target is journaled without entering the ranked tier."""
    out, writer = _run_gate(monkeypatch, "external")
    assert out["ranked_entries"] == []
    assert [r[1] for r in writer.rows] == ["gate_ungateable"]


def test_no_manifest_is_comment_routed_and_manifest_unparseable_is_journal_only(monkeypatch):
    """A missing manifest surfaces, while an unparseable manifest stays journal-only."""
    out, writer = _run_gate(monkeypatch, "no-manifest")
    assert len(out["ranked_entries"]) == 1
    assert [r[1] for r in writer.rows] == ["gate_ungateable"]

    out, writer = _run_gate(monkeypatch, "manifest-unparseable")
    assert out["ranked_entries"] == []
    assert [r[1] for r in writer.rows] == ["gate_ungateable"]


def test_ungateable_not_in_gate_kill(monkeypatch):
    """An ungateable result never enters the gate-kill stream."""
    _, writer = _run_gate(monkeypatch, "gitignored")
    assert all(r[1] != "gate_kill" for r in writer.rows)


class _RailJudge:
    """Count adjudications and return a live verdict for every candidate."""

    def __init__(self):
        """Initialize the adjudication counter."""
        self.calls = 0

    def adjudicate(self, claim, doc_text, repo_map, repo_root):
        """Count and approve one candidate."""
        self.calls += 1
        return SVerdict(live=True, reasoning="", confidence=0.9, usage={})


def test_s_candidate_rail_is_fail_soft():
    """The candidate cap journals overflow and still returns a partial report."""
    claims = [_claim() for _ in range(MAX_S_CANDIDATES + 1)]
    results = [GateResult(c, GateOutcome.M_CERTIFIED, "") for c in claims]
    judge = _RailJudge()
    writer = _Writer()
    node = make_semantic_judge(judge, writer)
    state = {
        "repo_root": "/r",
        "gate_results": results,
        "coverages": [],
        "verdicts": [],
        "findings": [],
    }
    out = node(state)

    assert judge.calls == MAX_S_CANDIDATES
    assert len(out["findings"]) == MAX_S_CANDIDATES
    skipped = [r for r in writer.rows if r[1] == "s_judge_skipped"]
    assert len(skipped) == 1
    assert skipped[0][2]["reason"] == "budget_cap:max_s_candidates"
    assert any("S-candidate cap" in n for n in out["partial_notes"])


def test_to_finding_predicate_aware():
    """Finding summaries and evidence reflect their source predicate."""
    f = _to_finding(_claim())
    assert "signature_has_param" in f.summary
    assert f.evidence.code_truth != "path not found in the scanned tree"
