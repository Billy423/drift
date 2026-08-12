"""Test that the journal read model reproduces inline graph results."""

from __future__ import annotations

import functools
import re

import pytest

from drift.agent.discovery import DiscoveryAgent
from drift.docstrings import DocstringProducer
from drift.graph.frame import build_graph, run_scan
from drift.graph.read_model import build_read_model, claim_from_payload
from drift.journal.serialize import claim_payload
from drift.journal.writer import JournalWriter, Stamps
from drift.judge.semantic_judge import S_THRESHOLD, SemanticJudge
from drift.kernels.models import Anchor, Check, EvClaim, SSlot
from drift.persistence.models import JournalRecord, ScanRun
from tests.fixtures.frame import finish, planned
from tests.fixtures.step2_substrate import (
    SUB_THRESHOLD_CLAIM,
    build_substrate_repo,
    make_substrate_client,
)

_STAMPS = Stamps("agent/0.8", "sjudge/0.4", "claude-sonnet-5")


@pytest.fixture
def substrate(tmp_path):
    """Build the shared read-model fixture repository."""
    return str(build_substrate_repo(tmp_path))


def _high_entries(report_text: str) -> list[tuple[str, str]]:
    """Extract sorted high-confidence report entries."""
    return sorted(
        (m.group(1), m.group(2))
        for m in re.finditer(r"^- \*\*(\w+)\*\* · `([^`]+)`", report_text, re.M)
    )


def _ranked_membership(report_text: str) -> list[tuple[str, str]]:
    """Extract sorted ranked-tier members."""
    start = report_text.find("## Ranked tier")
    body = report_text[start:] if start != -1 else ""
    return sorted((m.group(1), m.group(2)) for m in re.finditer(r"^- `([^`]+)`: (.*)$", body, re.M))


def _ranked_sections(report_text: str) -> dict[str, int]:
    """Extract ranked-tier section counts."""
    start = report_text.find("## Ranked tier")
    body = report_text[start:] if start != -1 else ""
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"^### (.*?) — (\d+)$", body, re.M)}


def _inline_report(db_session, repo_root: str, *, sub_threshold: bool = False) -> str:
    """Render a report from one direct graph invocation."""
    run = ScanRun(repo=repo_root, commit_sha="substrate", status="running")
    db_session.add(run)
    db_session.flush()
    writer = JournalWriter(db_session, run.id, repo_root, "substrate", _STAMPS)
    client = make_substrate_client(sub_threshold)
    graph = build_graph(
        DiscoveryAgent(client, agent_ver=_STAMPS.agent_ver),
        functools.partial(DocstringProducer, agent_ver=_STAMPS.agent_ver),
        SemanticJudge(client, judge_ver=_STAMPS.judge_ver),
        writer=writer,
    )
    state = {
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
        "budget": 5.0,
        "spend": 0.0,
        "units_discovered": 0,
        "partial_notes": [],
        "strict_measurement": False,
        "max_s_candidates": None,
    }
    return finish(db_session, repo_root, run.id, graph.invoke(state))["report_text"]


def _frame_report(db_session, repo_root: str, *, sub_threshold: bool = False) -> str:
    """Render a report through the frame and its in-process cells."""
    _run_id, report = run_scan(
        repo_root,
        client=make_substrate_client(sub_threshold),
        session_factory=lambda: db_session,
        poll_interval=0,
    )
    return report


def test_the_frame_reproduces_the_inline_pipelines_high_set_and_ranked_membership(
    substrate, db_session
):
    """Frame and inline execution produce the same findings and ranked membership."""
    inline = _inline_report(db_session, substrate)
    framed = _frame_report(db_session, substrate)

    assert _high_entries(framed) == _high_entries(inline)
    assert _high_entries(framed), "the substrate must emit at least one HIGH or this proves little"
    assert _ranked_membership(framed) == _ranked_membership(inline)
    assert _ranked_sections(framed) == _ranked_sections(inline)


def test_the_frames_findings_carry_the_stored_check_reconcile_will_replay(substrate, db_session):
    """Frame findings retain the complete replayable check."""
    _frame_report(db_session, substrate)
    run_id = (
        db_session.query(ScanRun).filter_by(repo=substrate).order_by(ScanRun.id.desc()).first().id
    )

    model = build_read_model(db_session, run_id)

    assert model.findings
    for finding in model.findings:
        assert set(finding.check) == {"predicate", "raw", "normalization", "normalized_args"}
        assert finding.check["predicate"] == finding.check_id
        assert finding.check["normalized_args"]
        assert finding.identity[0] == finding.doc_location.file


def test_a_claim_inventory_row_round_trips_to_the_claim_that_wrote_it(substrate, db_session):
    """Claim payload decoding preserves every producer-supplied field."""
    claim = EvClaim(
        anchor=Anchor(doc_path="README.md", spans=((3, 4), (9, 9)), literal="docs/x.md"),
        check=Check(
            predicate="path_exists",
            raw={"doc_path": "README.md", "literal": "docs/x.md"},
            normalization={"base": "repo-root", "stripped": "backticks"},
            normalized_args=("docs/x.md",),
        ),
        claim_class=2,
        s_slot=SSlot(note="a note that must survive whole", confidence=0.04),
        provenance={"producer": "agent", "agent_ver": "agent/0.8", "bind": {"outcome": "bound"}},
    )

    rebuilt = claim_from_payload(claim_payload(claim, "deadbeef", "agent"))

    assert rebuilt == claim


def _rows_for(db_session, repo_root, *, sub_threshold: bool = False):
    """Run the frame over the substrate and return `(run_id, its read model)`."""
    _frame_report(db_session, repo_root, sub_threshold=sub_threshold)
    run_id = (
        db_session.query(ScanRun).filter_by(repo=repo_root).order_by(ScanRun.id.desc()).first().id
    )
    return run_id, build_read_model(db_session, run_id)


def test_the_gates_own_refutations_never_reach_the_ranked_tier(substrate, db_session):
    """Gate refutations never return as ranked-tier leads."""
    _run_id, model = _rows_for(db_session, substrate)

    literals = {e.claim.anchor.literal for e in model.ranked_entries}
    assert "docs/ghost.md" not in literals


def test_the_comment_routed_ungateable_reasons_do_surface_and_the_others_do_not(
    substrate, db_session
):
    """Only user-actionable ungateable reasons enter the ranked tier."""
    _run_id, model = _rows_for(db_session, substrate)

    literals = {e.claim.anchor.literal for e in model.ranked_entries}
    assert "make build" in literals
    assert "render(verbose=True)" in literals
    assert "dist/bundle.js" not in literals
    assert "logo.png" not in literals
    assert "requests.get" not in literals


def test_unbound_claims_are_the_tiers_floor_and_adjudicated_ones_leave_it(substrate, db_session):
    """Unbound claims remain ranked, while mechanically adjudicated claims leave the tier."""
    _run_id, model = _rows_for(db_session, substrate)

    literals = {e.claim.anchor.literal for e in model.ranked_entries}
    assert "The scanner degrades gracefully when the network is slow." in literals
    assert "substrate_pkg/core.py" not in literals
    assert "assets/logo.png" not in literals
    assert "docs/ROADMAP.md" not in literals
    assert "docs/CHANGELOG.md" not in literals
    assert "docs/CHANGELOG.md" in {f.evidence.doc_claim for f in model.findings}


def test_a_preview_kernels_annotation_is_rebuilt_from_its_own_row(substrate, db_session):
    """A preview claim's annotation is rebuilt from its journal row."""
    _run_id, model = _rows_for(db_session, substrate)

    annotated = [e for e in model.ranked_entries if e.annotation]
    assert annotated, "the substrate's preview claim must reach the tier annotated"
    assert any("preview `class_has_member`" in e.annotation for e in annotated)
    assert all("cannot certify a finding" in e.annotation for e in annotated)


def _sub_threshold_verdict(db_session, run_id):
    """Return the verdict row for the sub-threshold claim."""
    doc_path, literal = SUB_THRESHOLD_CLAIM
    rows = [
        r.payload
        for r in db_session.query(JournalRecord)
        .filter_by(run_id=run_id, record_type="s_verdict")
        .order_by(JournalRecord.id)
        .all()
    ]
    return next(
        (p for p in rows if p.get("doc_path") == doc_path and p.get("literal") == literal), None
    )


def test_a_live_verdict_below_the_confidence_threshold_mints_nothing(substrate, db_session):
    """A live verdict below the confidence threshold produces neither finding nor lead."""
    run_id, model = _rows_for(db_session, substrate, sub_threshold=True)
    doc_path, literal = SUB_THRESHOLD_CLAIM

    verdict = _sub_threshold_verdict(db_session, run_id)
    assert verdict is not None, "the claim never reached the judge; the fixture proves nothing"
    assert verdict["live"] is True and verdict["error"] is False
    assert verdict["confidence"] < S_THRESHOLD

    assert literal not in {f.evidence.doc_claim for f in model.findings}
    assert (doc_path, literal) not in {
        (e.claim.anchor.doc_path, e.claim.anchor.literal) for e in model.ranked_entries
    }
    assert "docs/CHANGELOG.md" in {f.evidence.doc_claim for f in model.findings}


def test_the_frames_high_rule_agrees_with_production_s_passed_on_the_threshold(
    substrate, db_session
):
    """Frame and inline paths apply the same semantic confidence threshold."""
    inline = _inline_report(db_session, substrate, sub_threshold=True)
    framed = _frame_report(db_session, substrate, sub_threshold=True)

    assert _high_entries(framed) == _high_entries(inline)
    assert _ranked_membership(framed) == _ranked_membership(inline)
