"""Test that preview predicates can neither mint findings nor suppress candidates."""

from __future__ import annotations

import json

import pytest

from drift.agent.discovery import DiscoveryAgent
from drift.gate.replay import GateOutcome, GateResult
from drift.graph.nodes.gate import make_gate_replay
from drift.graph.nodes.judge import make_semantic_judge
from drift.graph.nodes.rails import MAX_S_CANDIDATES
from drift.judge.semantic_judge import SVerdict
from drift.kernels.models import Anchor, Check, EvClaim, SSlot


class _Writer:
    """Record journal writes in memory."""

    def __init__(self):
        """Initialize an empty row buffer."""
        self.rows: list[tuple[str, str, dict]] = []

    def write(self, component, record_type, payload):
        """Append one journal record."""
        self.rows.append((component, record_type, payload))

    def flush(self):
        """No-op: these tests assert what was written, not when it reached disk."""


class _AlwaysLiveJudge:
    """Approve every candidate while recording calls."""

    def __init__(self):
        """Initialize call count and observed literals."""
        self.calls = 0
        self.seen: list[str] = []

    def adjudicate(self, claim, doc_text, repo_map, repo_root):
        """Record and approve one candidate."""
        self.calls += 1
        self.seen.append(claim.anchor.literal)
        return SVerdict(live=True, reasoning="", confidence=0.9, usage={})


def _claim(predicate: str, literal: str, claim_class: int = 1, producer: str = "agent"):
    """Build a claim for routing tests."""
    return EvClaim(
        anchor=Anchor(doc_path="README.md", spans=((1, 1),), literal=literal),
        check=Check(
            predicate=predicate,
            raw={"literal": literal, "doc_path": "README.md", "proposed_args": []},
            normalization={},
            normalized_args=("package.json", "scripts.build"),
        ),
        claim_class=claim_class,
        s_slot=SSlot(note="n", confidence=0.5),
        provenance={"producer": producer, "agent_ver": "agent/x"},
    )


def _gate(monkeypatch, results):
    """Run gate routing with fixed replay results."""
    monkeypatch.setattr("drift.graph.nodes.gate.replay", lambda root, claims: results)
    writer = _Writer()
    node = make_gate_replay(writer)
    out = node({"repo_root": "/r", "claims": [r.claim for r in results]})
    return out, writer


ALL_OUTCOMES = [o for o in GateOutcome]


@pytest.mark.parametrize("outcome", ALL_OUTCOMES)
def test_a_preview_bound_claim_never_reaches_the_s_judge_pool(monkeypatch, outcome):
    """A preview-bound claim never enters the semantic-judge pool."""
    preview = _claim("manifest_key_exists", "npm run build")
    out, _writer = _gate(monkeypatch, [GateResult(preview, outcome, "detail")])

    assert out["gate_results"] == []
    judge = _AlwaysLiveJudge()
    judged = make_semantic_judge(judge, _Writer())(
        {
            "repo_root": "/r",
            "gate_results": out["gate_results"],
            "coverages": [],
            "verdicts": [],
            "findings": [],
        }
    )
    assert judge.calls == 0
    assert judged["findings"] == []


@pytest.mark.parametrize("outcome", ALL_OUTCOMES)
def test_a_preview_bound_claim_always_lands_in_the_ranked_tier(monkeypatch, outcome):
    """Every preview outcome still leaves the claim in the ranked tier."""
    preview = _claim("manifest_key_exists", "npm run build")
    out, _writer = _gate(monkeypatch, [GateResult(preview, outcome, "no-manifest")])

    assert [e.claim.anchor.literal for e in out["ranked_entries"]] == ["npm run build"]
    assert out["ranked_entries"][0].annotation is not None


@pytest.mark.parametrize("outcome", ALL_OUTCOMES)
def test_every_preview_outcome_is_journaled(monkeypatch, outcome):
    """Every preview outcome records its predicate, literal, and normalized arguments."""
    preview = _claim("manifest_key_exists", "npm run build")
    _out, writer = _gate(monkeypatch, [GateResult(preview, outcome, "detail")])

    rows = [p for _c, rt, p in writer.rows if rt == "preview_verdict"]
    assert len(rows) == 1
    assert rows[0]["predicate"] == "manifest_key_exists"
    assert rows[0]["outcome"] == outcome.value
    assert rows[0]["literal"] == "npm run build"
    assert rows[0]["normalized_args"] == ["package.json", "scripts.build"]


def test_the_two_way_annotation_says_absent_and_present(monkeypatch):
    """Preview annotations distinguish absent and present mechanical results."""
    fired, _ = _gate(
        monkeypatch,
        [GateResult(_claim("manifest_key_exists", "npm run build"), GateOutcome.M_CERTIFIED, "")],
    )
    passed, _ = _gate(
        monkeypatch,
        [GateResult(_claim("manifest_key_exists", "npm run build"), GateOutcome.PASSING, "")],
    )
    assert "mechanical check: absent" in fired["ranked_entries"][0].annotation
    assert "mechanical check: present" in passed["ranked_entries"][0].annotation


def test_grade_routing_takes_precedence_over_reason_routing(monkeypatch):
    """Preview grade routing takes precedence over ungateable-reason routing."""
    preview = _claim("manifest_key_exists", "npm run build")
    out, writer = _gate(monkeypatch, [GateResult(preview, GateOutcome.UNGATEABLE, "no-manifest")])

    assert len(out["ranked_entries"]) == 1
    assert "no-manifest" in out["ranked_entries"][0].annotation
    assert [rt for _c, rt, _p in writer.rows] == ["preview_verdict"]


def test_preview_claims_do_not_consume_the_s_candidate_budget(monkeypatch):
    """Preview claims do not consume the semantic-candidate allowance."""
    high = [
        GateResult(_claim("path_exists", f"docs/missing{i}.md"), GateOutcome.M_CERTIFIED, "")
        for i in range(3)
    ]
    preview = [
        GateResult(_claim("manifest_key_exists", f"npm run s{i}"), GateOutcome.M_CERTIFIED, "")
        for i in range(MAX_S_CANDIDATES + 5)
    ]
    out, _writer = _gate(monkeypatch, preview + high)

    judge = _AlwaysLiveJudge()
    judged = make_semantic_judge(judge, _Writer())(
        {
            "repo_root": "/r",
            "gate_results": out["gate_results"],
            "coverages": [],
            "verdicts": [],
            "findings": [],
            "partial_notes": [],
        }
    )
    assert judge.calls == 3
    assert sorted(judge.seen) == ["docs/missing0.md", "docs/missing1.md", "docs/missing2.md"]
    assert judged["partial_notes"] == []


def test_high_grade_routing_is_unchanged(monkeypatch):
    """Finding-grade predicates retain their normal routing."""
    m_cert = GateResult(_claim("path_exists", "docs/gone.md"), GateOutcome.M_CERTIFIED, "")
    unbound_claim = EvClaim(
        anchor=Anchor(doc_path="README.md", spans=((2, 2),), literal="something prose"),
        check=None,
        claim_class=3,
        s_slot=SSlot(note="n", confidence=0.2),
        provenance={"producer": "agent"},
    )
    unbound = GateResult(unbound_claim, GateOutcome.UNBOUND, "check is None")
    out, writer = _gate(monkeypatch, [m_cert, unbound])

    assert [gr.claim.anchor.literal for gr in out["gate_results"]] == [
        "docs/gone.md",
        "something prose",
    ]
    assert [e.claim.anchor.literal for e in out["ranked_entries"]] == ["something prose"]
    assert all(rt != "preview_verdict" for _c, rt, _p in writer.rows)


def _assemble(predicate: str, claim_class: int, literal: str = "npm run build"):
    """Assemble a discovery claim with the requested predicate and class."""
    agent = DiscoveryAgent(client=None)
    return agent._assemble(
        {
            "literal": literal,
            "predicate": predicate,
            "spans": [[1, 1]],
            "claim_class": claim_class,
            "note": "n",
            "confidence": 0.5,
            "args": [],
        },
        "README.md",
    )


def test_a_preview_bind_does_not_coerce_class_three_down():
    """Binding to a preview predicate preserves a semantic-only claim class."""
    claim = _assemble("manifest_key_exists", 3)
    assert claim.check is not None
    assert claim.claim_class == 3


def test_a_high_grade_bind_still_coerces_class_three_down():
    """Binding to a finding-grade predicate marks a claim mechanically decidable."""
    claim = _assemble("path_exists", 3, literal="docs/guide.md")
    assert claim.check is not None
    assert claim.claim_class == 1


def test_a_preview_bind_leaves_ordinary_classes_alone():
    """Preview binding preserves ordinary claim classes."""
    for cls in (1, 2):
        assert _assemble("manifest_key_exists", cls).claim_class == cls


def _real_gate(repo_root, claims):
    """Run real gate replay and return output with journal writes."""
    writer = _Writer()
    return make_gate_replay(writer)({"repo_root": repo_root, "claims": claims}), writer


def _real_preview_claim(literal="npm run build"):
    """Build a normalized manifest preview claim."""
    from drift.kernels.manifest_key_exists import MANIFEST_KEY_EXISTS

    norm, args = MANIFEST_KEY_EXISTS.normalize(literal, "README.md", None)
    return EvClaim(
        anchor=Anchor(doc_path="README.md", spans=((1, 1),), literal=literal),
        check=Check(
            predicate="manifest_key_exists",
            raw={"literal": literal, "doc_path": "README.md", "proposed_args": []},
            normalization=norm,
            normalized_args=args,
        ),
        claim_class=1,
        s_slot=SSlot(note="n", confidence=0.7),
        provenance={"producer": "agent", "agent_ver": "agent/x"},
    )


def test_end_to_end_a_real_absent_script_is_a_candidate_not_a_high(tmp_path):
    """A missing script remains a ranked candidate and never becomes a finding."""
    (tmp_path / "README.md").write_text("Run `npm run build` to build.\n")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))

    out, writer = _real_gate(str(tmp_path), [_real_preview_claim()])

    assert out["gate_results"] == []
    assert "mechanical check: absent" in out["ranked_entries"][0].annotation
    assert [p["outcome"] for _c, rt, p in writer.rows if rt == "preview_verdict"] == ["M_CERTIFIED"]


def test_end_to_end_a_repo_with_no_manifest_still_shows_the_candidate(tmp_path):
    """A repository without a manifest still shows the preview candidate."""
    (tmp_path / "README.md").write_text("Run `npm run build` to build.\n")

    out, writer = _real_gate(str(tmp_path), [_real_preview_claim()])

    assert len(out["ranked_entries"]) == 1
    assert "no-manifest" in out["ranked_entries"][0].annotation
    assert [p["detail"] for _c, rt, p in writer.rows if rt == "preview_verdict"] == ["no-manifest"]
