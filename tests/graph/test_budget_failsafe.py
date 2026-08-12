"""Tests for spending caps, budget accounting, and fail-soft reporting."""

from __future__ import annotations

from drift.cost import usage_cost_usd
from drift.domain.findings import Confidence
from drift.gate.replay import GateOutcome, GateResult
from drift.graph.frame import build_graph
from drift.graph.nodes.discover import make_discover
from drift.graph.nodes.judge import make_semantic_judge
from drift.graph.nodes.rails import MAX_S_CANDIDATES
from drift.judge.semantic_judge import JudgeEmitError, SVerdict
from drift.kernels.models import Anchor, Check, EvClaim, SSlot
from drift.persistence.models import Issue, JournalRecord, ScanRun
from drift.report.render import to_markdown
from tests.fixtures.frame import finish, planned


class _FakeWriter:
    """Collect journal writes in memory."""

    def __init__(self):
        """Initialize an empty row buffer."""
        self.rows: list[tuple[str, str, dict]] = []

    def write(self, component, record_type, payload):
        """Append one journal-shaped row."""
        self.rows.append((component, record_type, payload))

    def flush(self):
        """Leave buffered rows unchanged."""


class _FakeAgent:
    """Return pre-built claims with fixed usage."""

    def __init__(self, claims_by_unit: dict[str, list[EvClaim]], usage: dict):
        """Configure the claims and usage returned by discovery."""
        self._claims = claims_by_unit
        self._usage = usage

    def discover(self, repo_root, doc_path):
        """Return the configured result for one document."""
        from drift.agent.discovery import DiscoveryResult

        return DiscoveryResult(
            claims=list(self._claims.get(doc_path, [])),
            coverage={
                "unit": doc_path,
                "doc_hash": f"hash-{doc_path}",
                "turns_used": 1,
                "tool_calls": 0,
                "status": "complete",
                "usage": dict(self._usage),
            },
        )


class _FakeJudge:
    """Return a fixed verdict and per-call usage."""

    def __init__(self, usage: dict, live: bool = True, confidence: float = 0.9):
        """Configure the verdict returned by adjudication."""
        self._usage = usage
        self._live = live
        self._confidence = confidence
        self.calls = 0

    def adjudicate(self, claim, doc_text, repo_map, repo_root):
        """Return the configured verdict and increment the call count."""
        self.calls += 1
        return SVerdict(
            live=self._live,
            reasoning="fake",
            confidence=self._confidence,
            usage=dict(self._usage),
        )


class _EmptyProducer:
    """Produce an empty, complete corpus result."""

    def produce(self, doc_filter=None):
        """Return an empty corpus with complete coverage."""
        return [], {"unit": "docstring_corpus", "status": "complete"}


def _empty_producer_factory(root):
    """Create the empty corpus producer used by graph tests."""
    return _EmptyProducer()


def _path_claim(literal: str, line: int, producer: str = "agent") -> EvClaim:
    """Create a missing-path claim that the gate certifies as refuted."""
    return EvClaim(
        anchor=Anchor(doc_path="D.md", spans=((line, line),), literal=literal),
        check=Check(
            predicate="path_exists",
            raw={"doc_path": "D.md", "literal": literal},
            normalization={"base": "repo-root"},
            normalized_args=(literal,),
        ),
        claim_class=1,
        s_slot=SSlot(note="n", confidence=0.9),
        provenance={"agent_ver": "agent/x", "producer": producer},
    )


def _m_certified_results(n: int, producer: str = "agent") -> list[GateResult]:
    """Create certified refutations in deterministic literal order."""
    return [
        GateResult(
            _path_claim(f"docs/{producer}-missing{i}.md", i + 1, producer),
            GateOutcome.M_CERTIFIED,
            "",
        )
        for i in range(n)
    ]


def _discover_state(worklist, budget, spend=0.0):
    """Build the minimal state accepted by the discovery node."""
    return {
        "repo_root": "/irrelevant",
        "worklist": list(worklist),
        "budget": budget,
        "spend": spend,
        "partial_notes": [],
    }


def _judge_state(results, budget, spend=0.0):
    """Build the minimal state accepted by the judge node."""
    return {
        "repo_root": "/irrelevant",
        "gate_results": results,
        "coverages": [{"unit": "D.md", "doc_hash": "hash-D.md"}],
        "verdicts": [],
        "findings": [],
        "budget": budget,
        "spend": spend,
        "partial_notes": [],
        "units_discovered": 1,
    }


def test_cost_math_matches_the_price_table():
    """Calculate cost from each supported usage field."""
    assert usage_cost_usd({"input_tokens": 1_000_000}) == 3.00
    assert usage_cost_usd({"output_tokens": 1_000_000}) == 15.00
    assert round(usage_cost_usd({"cache_read_input_tokens": 1_000_000}), 6) == 0.30
    assert round(usage_cost_usd({"cache_creation_input_tokens": 1_000_000}), 6) == 3.75

    combined = {
        "input_tokens": 500_000,  # $1.50
        "output_tokens": 200_000,  # $3.00
        "cache_read_input_tokens": 1_000_000,  # $0.30
        "cache_creation_input_tokens": 400_000,  # $1.50
    }
    assert round(usage_cost_usd(combined), 6) == round(1.50 + 3.00 + 0.30 + 1.50, 6)

    # Malformed or absent usage fields must not make accounting fail.
    assert usage_cost_usd({}) == 0.0
    assert usage_cost_usd({"input_tokens": None, "output_tokens": "oops"}) == 0.0


def test_over_cap_completes_adjudicates_exactly_the_cap_and_journals_the_rest():
    """Adjudicate up to the cap and journal the stable skipped tail."""
    n = MAX_S_CANDIDATES + 10
    results = _m_certified_results(n)
    judge = _FakeJudge(usage={"output_tokens": 1})
    writer = _FakeWriter()
    node = make_semantic_judge(judge, writer)

    out = node(_judge_state(results, budget=float("inf")))

    assert judge.calls == MAX_S_CANDIDATES
    assert len(out["findings"]) == MAX_S_CANDIDATES
    assert all(f.confidence == Confidence.HIGH for f in out["findings"])

    skipped = [p for c, rt, p in writer.rows if rt == "s_judge_skipped"]
    assert len(skipped) == n - MAX_S_CANDIDATES
    assert {p["reason"] for p in skipped} == {"budget_cap:max_s_candidates"}
    # The cap always skips the tail of the stable gate order.
    assert [p["literal"] for p in skipped] == [
        f"docs/agent-missing{i}.md" for i in range(n - 10, n)
    ]

    report = to_markdown(out["findings"], [], [], out["partial_notes"])
    assert "S-candidate cap" in report
    assert f"{n - MAX_S_CANDIDATES} of {n}" in report


def test_a_funded_cell_runs_to_completion_because_the_judge_lane_has_no_dollar_gate():
    """Let a funded cell finish; the frame applies the dollar budget between cells."""
    results = _m_certified_results(10)
    per_call = {"output_tokens": 20_000}  # $0.30
    assert usage_cost_usd(per_call) == 0.30

    judge = _FakeJudge(usage=per_call)
    writer = _FakeWriter()
    node = make_semantic_judge(judge, writer)
    out = node(_judge_state(results, budget=1.00))

    assert judge.calls == 10
    assert len(out["findings"]) == 10
    assert round(out["spend"], 6) == 3.00
    assert [p for _c, rt, p in writer.rows if rt == "s_judge_skipped"] == []
    assert _rail_rows(writer) == []
    assert out["partial_notes"] == []


def test_happy_path_leaves_partial_notes_empty_and_banners_nothing(db_session):
    """Add no partial notes or banners to a complete run."""
    repo_root = _fixture_repo()
    run = ScanRun(repo=repo_root, commit_sha="deadbeef", status="running")
    db_session.add(run)
    db_session.flush()

    agent = _FakeAgent({"D.md": [_path_claim("docs/missing.md", 2)]}, usage={"output_tokens": 1})
    judge = _FakeJudge(usage={"output_tokens": 1}, live=True, confidence=0.9)

    out = _invoke_graph(agent, judge, repo_root, run.id, db_session, budget=5.0)

    assert len(out["findings"]) == 1
    assert out["partial_notes"] == []
    incomplete = [c for c in out["coverages"] if c.get("status") != "complete"]
    incomplete += out.get("kernel_errors", [])
    reference = to_markdown(out["findings"], out["ranked_entries"], incomplete)
    assert out["report_text"] == reference
    assert "PARTIAL" not in out["report_text"]
    assert "S-candidate cap" not in out["report_text"]
    assert db_session.query(Issue).filter_by(repo=repo_root).count() == 1
    assert (
        db_session.query(JournalRecord).filter_by(run_id=run.id, record_type="rail_stop").count()
        == 0
    )


def test_budget_zero_stops_before_first_paid_step_and_still_reports(db_session):
    """Stop before paid work at a zero budget while still producing a report."""
    repo_root = _fixture_repo()
    run = ScanRun(repo=repo_root, commit_sha="deadbeef", status="running")
    db_session.add(run)
    db_session.flush()

    agent = _FakeAgent(
        {"D.md": [_path_claim("docs/missing.md", 2)]}, usage={"output_tokens": 999_999}
    )
    judge = _FakeJudge(usage={"output_tokens": 999_999})

    out = _invoke_graph(agent, judge, repo_root, run.id, db_session, budget=0.0)

    assert judge.calls == 0
    assert out["claims"] == []
    assert out["findings"] == []
    assert out["units_discovered"] == 0
    assert out["report_text"]
    assert "$0.00" in out["report_text"]
    assert "PARTIAL" in out["report_text"]

    # The journal records the stop with a JSON-serializable payload.
    rails = list(db_session.query(JournalRecord).filter_by(run_id=run.id, record_type="rail_stop"))
    assert len(rails) == 1
    assert rails[0].payload["lane"] == "discover"
    assert rails[0].payload["items_done"] == 0
    assert rails[0].payload["items_total"] == len(out["worklist"])
    assert rails[0].payload["budget"] == 0.0


def _fixture_repo() -> str:
    """Create a repository fixture with one present and one missing path."""
    import tempfile
    from pathlib import Path

    root = Path(tempfile.mkdtemp())
    (root / "docs").mkdir()
    (root / "docs" / "present.md").write_text("x")
    (root / "D.md").write_text(
        "Notes.\nSee docs/missing.md for the guide.\nSee docs/present.md too.\n"
    )
    return str(root)


def _invoke_graph(agent, judge, repo_root, run_id, db_session, budget, model=None):
    """Run the assembled graph with precomputed frame state."""
    from drift.journal.writer import JournalWriter, Stamps

    writer = JournalWriter(
        db_session, run_id, repo_root, "deadbeef", Stamps("agent/x", "sjudge/x", "claude-sonnet-5")
    )
    kwargs = {"model": model} if model is not None else {}
    graph = build_graph(
        agent,
        _empty_producer_factory,
        judge,
        writer=writer,
        **kwargs,
    )
    return finish(
        db_session,
        repo_root,
        run_id,
        graph.invoke(
            {
                "repo_root": repo_root,
                "run_id": run_id,
                "doc_filter": None,
                "worklist": [],
                # Direct graph tests must inject the frame's precomputed enumeration.
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
                "budget": budget,
                "spend": 0.0,
                "partial_notes": [],
                "units_discovered": 0,
            }
        ),
    )


def _rail_rows(writer):
    """Return rail-stop payloads collected by a fake writer."""
    return [p for _c, rt, p in writer.rows if rt == "rail_stop"]


def test_discovery_budget_stop_journals_one_rail_stop_row():
    """Journal a discovery budget stop with completed and remaining unit counts."""
    worklist = [f"d{i}.md" for i in range(10)]
    agent = _FakeAgent({}, usage={"output_tokens": 20_000})
    writer = _FakeWriter()
    node = make_discover(agent, writer)

    out = node(_discover_state(worklist, budget=1.00))

    assert out["units_discovered"] == 4
    rails = _rail_rows(writer)
    assert len(rails) == 1
    assert rails[0]["lane"] == "discover"
    assert rails[0]["reason"] == "budget_cap:dollars"
    assert rails[0]["items_done"] == 4
    assert rails[0]["items_total"] == 10
    assert rails[0]["budget"] == 1.00
    assert round(rails[0]["spend"], 6) == 1.20
    note = out["partial_notes"][0]
    assert "after 4 unit(s)" in note and "6 of 10 doc unit(s) not scanned" in note


def test_the_s_judge_cap_journals_its_own_rail_stop_row():
    """Journal the count cap and every candidate it leaves unadjudicated."""
    writer = _FakeWriter()
    n = MAX_S_CANDIDATES + 10
    node = make_semantic_judge(_FakeJudge(usage={"output_tokens": 1}), writer)
    node(_judge_state(_m_certified_results(n), budget=float("inf")))
    rails = _rail_rows(writer)
    assert len(rails) == 1
    assert rails[0]["lane"] == "semantic_judge"
    assert rails[0]["reason"] == "budget_cap:max_s_candidates"
    assert rails[0]["items_done"] == MAX_S_CANDIDATES and rails[0]["items_total"] == n
    assert rails[0]["budget"] is None  # JSON cannot encode infinity.
    assert len([p for _c, rt, p in writer.rows if rt == "s_judge_skipped"]) == 10


def test_a_complete_run_journals_no_rail_stop_row():
    """Emit no rail-stop row when both paid stages complete."""
    writer = _FakeWriter()
    discover = make_discover(_FakeAgent({}, usage={"output_tokens": 1}), writer)
    out = discover(_discover_state(["a.md", "b.md"], budget=5.00))
    assert out["units_discovered"] == 2

    judge_node = make_semantic_judge(_FakeJudge(usage={"output_tokens": 1}), writer)
    judge_out = judge_node(_judge_state(_m_certified_results(3), budget=5.00))
    assert len(judge_out["findings"]) == 3

    assert _rail_rows(writer) == []
    assert out["partial_notes"] == [] and judge_out["partial_notes"] == []


def test_the_count_cap_is_the_only_rail_left_in_the_judge_lane():
    """Apply only the count cap inside a funded judge cell."""
    n = MAX_S_CANDIDATES + 10
    writer = _FakeWriter()
    node = make_semantic_judge(_FakeJudge(usage={"output_tokens": 20_000}), writer)

    out = node(_judge_state(_m_certified_results(n), budget=1.00))

    rails = {p["reason"]: p for p in _rail_rows(writer)}
    assert set(rails) == {"budget_cap:max_s_candidates"}
    assert rails["budget_cap:max_s_candidates"]["items_total"] == n
    assert rails["budget_cap:max_s_candidates"]["items_done"] == MAX_S_CANDIDATES

    skips: dict[str, list[str]] = {}
    for _c, rt, p in writer.rows:
        if rt == "s_judge_skipped":
            skips.setdefault(p["reason"], []).append(p["literal"])
    assert set(skips) == {"budget_cap:max_s_candidates"}
    assert len(skips["budget_cap:max_s_candidates"]) == 10
    assert len(out["findings"]) == MAX_S_CANDIDATES
    assert len(out["partial_notes"]) == 1


class _RaisingJudge:
    """Raise a judge error that carries usage billed before failure."""

    def __init__(self, usage: dict):
        """Configure the usage attached to each raised error."""
        self._usage = usage
        self.calls = 0

    def adjudicate(self, claim, doc_text, repo_map, repo_root):
        """Raise with the configured billed usage."""
        self.calls += 1
        raise JudgeEmitError(f"no usable verdict for {claim.anchor.literal!r}", dict(self._usage))


def test_a_judge_error_after_billed_calls_costs_the_accountant_and_journals_its_usage():
    """Include failed judge calls in spend and their journal rows."""
    per_call = {"output_tokens": 20_000}
    judge = _RaisingJudge(per_call)
    writer = _FakeWriter()
    node = make_semantic_judge(judge, writer)

    out = node(_judge_state(_m_certified_results(3), budget=5.00))

    assert judge.calls == 3
    assert out["findings"] == []
    assert round(out["spend"], 6) == 0.90
    errors = [p for _c, rt, p in writer.rows if rt == "s_verdict" and p["error"]]
    assert len(errors) == 3
    assert errors[0]["usage"] == per_call


def test_paid_and_lost_adjudications_reach_the_wallet_through_their_own_rows():
    """Record failed-call usage in node spend and journal-shaped verdict rows."""
    judge = _RaisingJudge({"output_tokens": 20_000})
    writer = _FakeWriter()
    node = make_semantic_judge(judge, writer)

    out = node(_judge_state(_m_certified_results(10), budget=1.00))

    assert judge.calls == 10
    assert round(out["spend"], 6) == 3.00
    assert _rail_rows(writer) == []
    errors = [p for _c, rt, p in writer.rows if rt == "s_verdict" and p["error"]]
    assert len(errors) == 10
    assert all(p["usage"] == {"output_tokens": 20_000} for p in errors)


def test_the_discovery_node_bills_a_failed_unit_s_usage():
    """Include a failed discovery unit's usage in spend."""

    class _CrashedUnitAgent:
        """Return failed discovery results with billed usage."""

        def discover(self, repo_root, doc_path):
            """Return a failed result for one billed unit."""
            from drift.agent.discovery import DiscoveryResult

            return DiscoveryResult(
                [],
                {
                    "unit": doc_path,
                    "doc_hash": "h",
                    "status": "error",
                    "detail": "RuntimeError('connection reset')",
                    "usage": {"output_tokens": 20_000},
                },
            )

    writer = _FakeWriter()
    node = make_discover(_CrashedUnitAgent(), writer)

    out = node(_discover_state(["a.md", "b.md"], budget=5.00))

    assert round(out["spend"], 6) == 0.60
    assert [c["status"] for c in out["coverages"]] == ["error", "error"]


_PRICEY = {
    "input_tokens": 15.00 / 1_000_000,
    "output_tokens": 75.00 / 1_000_000,
    "cache_read_input_tokens": 1.50 / 1_000_000,
    "cache_creation_input_tokens": 18.75 / 1_000_000,
}


def test_the_judge_lane_prices_the_scan_s_actual_model(monkeypatch):
    """Price judge usage with the model configured for the scan."""
    monkeypatch.setitem(_price_table(), "pricey-model", dict(_PRICEY))
    per_call = {"output_tokens": 20_000}
    results = _m_certified_results(10)

    writer = _FakeWriter()
    node = make_semantic_judge(_FakeJudge(usage=per_call), writer, model="pricey-model")
    out = node(_judge_state(results, budget=1.00))

    assert len(out["findings"]) == 10
    assert round(out["spend"], 6) == 15.00


def test_the_graph_threads_the_scan_model_into_the_discovery_lane(monkeypatch, db_session):
    """Pass the scan's model to the discovery node for usage pricing."""
    monkeypatch.setitem(_price_table(), "pricey-model", dict(_PRICEY))
    repo_root = _fixture_repo()
    run = ScanRun(repo=repo_root, commit_sha="deadbeef", status="running")
    db_session.add(run)
    db_session.flush()

    agent = _FakeAgent({}, usage={"output_tokens": 20_000})
    out = _invoke_graph(
        agent,
        _FakeJudge(usage={}),
        repo_root,
        run.id,
        db_session,
        budget=1.00,
        model="pricey-model",
    )

    # The configured model's price permits only one unit.
    assert out["units_discovered"] == 1
    assert round(out["spend"], 6) == 1.50
    rails = list(db_session.query(JournalRecord).filter_by(run_id=run.id, record_type="rail_stop"))
    assert len(rails) == 1 and rails[0].payload["lane"] == "discover"


def _price_table():
    """Return the mutable price table used by the cost calculator."""
    from drift import cost

    return cost._PER_TOKEN_USD


def test_the_cap_note_carries_the_per_producer_breakdown():
    """Report skipped candidates by producer when the cap truncates the ordered tail."""
    candidates = _m_certified_results(MAX_S_CANDIDATES + 4) + _m_certified_results(6, "docstrings")
    writer = _FakeWriter()
    node = make_semantic_judge(_FakeJudge(usage={"output_tokens": 1}), writer)

    out = node(_judge_state(candidates, budget=float("inf")))

    note = next(n for n in out["partial_notes"] if "S-candidate cap" in n)
    assert "10 skipped: 4 agent, 6 docstrings" in note
    skipped = [p for _c, rt, p in writer.rows if rt == "s_judge_skipped"]
    assert sum(1 for p in skipped if p["producer"] == "docstrings") == 6
