"""Test that each emitted claim is preserved in the journal inventory."""

from __future__ import annotations

from drift.gate.replay import GateOutcome, replay
from drift.graph.frame import build_graph
from drift.journal.writer import JournalWriter, Stamps
from drift.judge.semantic_judge import SVerdict
from drift.kernels.models import Anchor, Check, EvClaim, SSlot
from drift.persistence.models import JournalRecord, ScanRun
from drift.report.render import to_markdown
from tests.fixtures.frame import finish, planned

_DOC = "Notes.\nSee docs/missing.md for the guide.\nThe scanner retries on failure.\n"


def _repo(tmp_path) -> str:
    """Create a minimal repository with one documentation file."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "D.md").write_text(_DOC)
    return str(root)


def _bound_claim(literal: str, line: int) -> EvClaim:
    """Build a replayable path claim anchored in the fixture document."""
    return EvClaim(
        anchor=Anchor(doc_path="D.md", spans=((line, line),), literal=literal),
        check=Check(
            predicate="path_exists",
            raw={"doc_path": "D.md", "literal": literal},
            normalization={"base": "repo-root"},
            normalized_args=(literal,),
        ),
        claim_class=1,
        s_slot=SSlot(note="looks stale", confidence=0.8),
        provenance={"agent_ver": "agent/x", "producer": "agent"},
    )


def _unbound_claim() -> EvClaim:
    """Build a behavioral claim with no matching predicate."""
    return EvClaim(
        anchor=Anchor(doc_path="D.md", spans=((3, 3),), literal="The scanner retries on failure."),
        check=None,
        claim_class=3,
        s_slot=SSlot(note="cannot mechanize; reads plausible", confidence=0.55),
        provenance={"agent_ver": "agent/x", "producer": "agent"},
    )


def _docstring_claim() -> EvClaim:
    """Build a bound claim produced from a Python docstring."""
    return EvClaim(
        anchor=Anchor(doc_path="pkg/mod.py", spans=((12, 12),), literal="timeout"),
        check=Check(
            predicate="signature_has_param",
            raw={"doc_path": "pkg/mod.py", "literal": "timeout"},
            normalization={},
            normalized_args=("pkg.mod.fn", "timeout"),
        ),
        claim_class=2,
        s_slot=SSlot(note="documented param", confidence=0.7),
        provenance={"agent_ver": "agent/x", "producer": "docstrings"},
    )


class _FakeAgent:
    """Return fixed discovery claims and coverage."""

    def __init__(self, claims):
        """Store the claims to return."""
        self._claims = claims

    def discover(self, repo_root, doc_path):
        """Return fixed claims with complete document coverage."""
        from drift.agent.discovery import DiscoveryResult

        return DiscoveryResult(
            claims=list(self._claims),
            coverage={
                "unit": doc_path,
                "doc_hash": "hash-D.md",
                "turns_used": 1,
                "tool_calls": 0,
                "status": "complete",
                "usage": {"output_tokens": 1},
            },
        )


class _FakeProducer:
    """Return fixed docstring claims and coverage."""

    def __init__(self, claims):
        """Store the claims to return."""
        self._claims = claims

    def produce(self, doc_filter=None):
        """Return fixed claims with complete corpus coverage."""
        return list(self._claims), {
            "unit": "docstring_corpus",
            "symbols_walked": 1,
            "claims_emitted": len(self._claims),
            "status": "complete",
        }


class _FakeJudge:
    """Return a live semantic verdict for every claim."""

    def adjudicate(self, claim, doc_text, repo_map, repo_root):
        """Approve one candidate."""
        return SVerdict(live=True, reasoning="r", confidence=0.9, usage={"output_tokens": 1})


def _scan(tmp_path, db_session, agent_claims, docstring_claims):
    """Run the graph with fixed producer outputs."""
    repo_root = _repo(tmp_path)
    run = ScanRun(repo=repo_root, commit_sha="deadbeef", status="running")
    db_session.add(run)
    db_session.flush()
    writer = JournalWriter(
        db_session, run.id, repo_root, "deadbeef", Stamps("agent/x", "sjudge/x", "claude-sonnet-5")
    )
    graph = build_graph(
        _FakeAgent(agent_claims),
        lambda root: _FakeProducer(docstring_claims),
        _FakeJudge(),
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
                "budget": 5.0,
                "spend": 0.0,
                "units_discovered": 0,
                "partial_notes": [],
            }
        ),
    )
    return run, out, repo_root


def _rows(db_session, run_id, record_type):
    """Return one run's journal records of the requested type."""
    return list(
        db_session.query(JournalRecord)
        .filter_by(run_id=run_id, record_type=record_type)
        .order_by(JournalRecord.id)
    )


def test_both_producers_write_exactly_one_inventory_row_per_claim(tmp_path, db_session):
    """Both producers write exactly one inventory row per emitted claim."""
    agent_claims = [_bound_claim("docs/missing.md", 2), _unbound_claim()]
    run, out, _ = _scan(tmp_path, db_session, agent_claims, [_docstring_claim()])

    rows = _rows(db_session, run.id, "claim_inventory")
    assert len(rows) == len(out["claims"]) == 3
    assert [r.component for r in rows] == ["agent", "agent", "docstrings"]
    assert [r.payload["lane"] for r in rows] == ["agent", "agent", "docstrings"]
    assert [r.payload["anchor"]["literal"] for r in rows] == [
        "docs/missing.md",
        "The scanner retries on failure.",
        "timeout",
    ]
    assert rows[0].payload["anchor"]["spans"] == [[2, 2]]
    assert rows[0].payload["claim_class"] == 1
    assert rows[0].payload["provenance"]["producer"] == "agent"


def test_an_unbound_claim_lands_with_a_null_check(tmp_path, db_session):
    """An unbound claim records a null check for predicate-demand queries."""
    run, _out, _ = _scan(tmp_path, db_session, [_unbound_claim()], [])

    rows = _rows(db_session, run.id, "claim_inventory")
    assert len(rows) == 1
    assert rows[0].payload["check"] is None
    assert rows[0].payload["claim_class"] == 3
    unbound = [r for r in rows if r.payload["check"] is None]
    assert [r.payload["anchor"]["literal"] for r in unbound] == ["The scanner retries on failure."]


def test_a_bound_claim_records_the_full_replayable_check(tmp_path, db_session):
    """A bound claim records every field needed for replay."""
    run, _out, _ = _scan(tmp_path, db_session, [_bound_claim("docs/missing.md", 2)], [])

    check = _rows(db_session, run.id, "claim_inventory")[0].payload["check"]
    assert check["predicate"] == "path_exists"
    assert check["normalized_args"] == ["docs/missing.md"]
    assert check["normalization"] == {"base": "repo-root"}
    assert check["raw"] == {"doc_path": "D.md", "literal": "docs/missing.md"}


def test_s_slot_round_trips_note_and_confidence(tmp_path, db_session):
    """A claim's semantic note and numeric confidence survive journaling."""
    agent_claims = [_bound_claim("docs/missing.md", 2), _unbound_claim()]
    run, _out, _ = _scan(tmp_path, db_session, agent_claims, [])

    slots = [r.payload["s_slot"] for r in _rows(db_session, run.id, "claim_inventory")]
    assert slots == [
        {"note": "looks stale", "confidence": 0.8},
        {"note": "cannot mechanize; reads plausible", "confidence": 0.55},
    ]
    assert sorted(s["confidence"] for s in slots) == [0.55, 0.8]


def test_doc_hash_is_populated_agent_side_and_empty_docstring_side(tmp_path, db_session):
    """Document discovery records a hash while corpus discovery records no document hash."""
    run, _out, _ = _scan(
        tmp_path, db_session, [_bound_claim("docs/missing.md", 2)], [_docstring_claim()]
    )

    by_lane = {r.payload["lane"]: r.payload for r in _rows(db_session, run.id, "claim_inventory")}
    assert by_lane["agent"]["doc_hash"] == "hash-D.md"
    # Corpus claims are not keyed by one documentation file, so no document hash exists.
    assert by_lane["docstrings"]["doc_hash"] == ""


def test_the_anchor_reference_is_byte_identical_across_streams(tmp_path, db_session):
    """Inventory, verdict, and kill streams share identical literal and document-path fields."""
    certified = _bound_claim("docs/missing.md", 2)
    hallucinated = _bound_claim("docs/ghost.md", 9)
    run, _out, repo_root = _scan(tmp_path, db_session, [certified, hallucinated], [])

    gated = replay(repo_root, [certified, hallucinated])
    outcomes = {gr.claim.anchor.literal: gr.outcome for gr in gated}
    assert outcomes["docs/missing.md"] == GateOutcome.M_CERTIFIED
    assert outcomes["docs/ghost.md"] == GateOutcome.BINDING_FAIL

    inventory_rows = _rows(db_session, run.id, "claim_inventory")
    inventory = {r.payload["anchor"]["literal"]: r.payload for r in inventory_rows}
    shared = ("literal", "doc_path")

    verdict = _rows(db_session, run.id, "s_verdict")[0].payload
    inv = inventory["docs/missing.md"]["anchor"]
    assert {k: verdict[k] for k in shared} == {k: inv[k] for k in shared}

    kill = _rows(db_session, run.id, "gate_kill")[0].payload
    inv = inventory["docs/ghost.md"]["anchor"]
    assert {k: kill[k] for k in shared} == {k: inv[k] for k in shared}


def test_the_inventory_changes_nothing_the_scan_reports(tmp_path, db_session):
    """Inventory journaling neither adds nor suppresses report findings."""
    agent_claims = [_bound_claim("docs/missing.md", 2), _unbound_claim()]
    run, out, _ = _scan(tmp_path, db_session, agent_claims, [_docstring_claim()])

    assert _rows(db_session, run.id, "claim_inventory")

    incomplete = [c for c in out["coverages"] if c.get("status") != "complete"]
    incomplete += out.get("kernel_errors", [])
    reference = to_markdown(
        out["findings"], out["ranked_entries"], incomplete, out.get("partial_notes", [])
    )
    assert out["report_text"] == reference
    assert "claim_inventory" not in out["report_text"]
    assert len(out["findings"]) == 1
    assert out["findings"][0].evidence.doc_claim == "docs/missing.md"
