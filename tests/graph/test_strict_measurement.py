"""Verify strict-measurement rails, configuration records, and journal failures."""

from __future__ import annotations

import pytest

from drift.cost import PRICE_TABLE_VER
from drift.gate.replay import GateOutcome, GateResult
from drift.graph.frame import build_graph
from drift.graph.nodes.discover import make_discover
from drift.graph.nodes.enumerate_units import enumerate_docs
from drift.graph.nodes.judge import make_semantic_judge
from drift.graph.nodes.rails import (
    MAX_S_CANDIDATES,
    MAX_UNITS,
    StrictMeasurementAbort,
)
from drift.judge.semantic_judge import SVerdict
from drift.kernels.models import Anchor, Check, EvClaim, SSlot
from drift.persistence.models import JournalRecord, ScanRun
from tests.fixtures.frame import finish, frame_repo, frame_run


class _Writer:
    """Collect journal writes and rollback calls in memory."""

    def __init__(self):
        """Initialize empty write and rollback histories."""
        self.rows: list[tuple[str, str, dict]] = []
        self.rollbacks = 0

    def write(self, component, record_type, payload):
        """Append one journal record to the in-memory history."""
        self.rows.append((component, record_type, payload))

    def flush(self):
        """No-op: these tests assert what was written, not when it reached disk."""

    def rollback(self):
        """Record one simulated transaction rollback."""
        self.rollbacks += 1


class _FlushFails(_Writer):
    """Simulate a journal whose durability step fails."""

    def __init__(self, boom="OperationalError('server closed the connection')"):
        """Initialize the writer with the failure message to raise."""
        super().__init__()
        self._boom = boom

    def flush(self):
        """Raise the configured durability failure."""
        raise RuntimeError(self._boom)


class _FakeAgent:
    """Return complete discovery coverage with configurable usage."""

    def __init__(self, usage=None):
        """Store the usage returned by each discovery call."""
        self._usage = usage or {"output_tokens": 1}

    def discover(self, repo_root, doc_path):
        """Return an empty complete discovery result for one document."""
        from drift.agent.discovery import DiscoveryResult

        return DiscoveryResult(
            claims=[],
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
    """Return a live semantic verdict and count adjudications."""

    def __init__(self, usage=None):
        """Initialize usage and the adjudication counter."""
        self._usage = usage or {"output_tokens": 1}
        self.calls = 0

    def adjudicate(self, claim, doc_text, repo_map, repo_root):
        """Return a live verdict for the supplied claim."""
        self.calls += 1
        return SVerdict(live=True, reasoning="f", confidence=0.9, usage=dict(self._usage))


class _EmptyProducer:
    """Represent a complete docstring producer with no claims."""

    def produce(self, doc_filter=None):
        """Return an empty complete corpus result."""
        return [], {"unit": "docstring_corpus", "status": "complete"}


def _claim(literal):
    """Build one mechanically certified test claim."""
    return EvClaim(
        anchor=Anchor(doc_path="D.md", spans=((1, 1),), literal=literal),
        check=Check(
            predicate="path_exists",
            raw={"doc_path": "D.md", "literal": literal},
            normalization={"base": "repo-root"},
            normalized_args=(literal,),
        ),
        claim_class=1,
        s_slot=SSlot(note="n", confidence=0.9),
        provenance={"producer": "agent", "agent_ver": "agent/x"},
    )


def _m_certified(n):
    """Build the requested number of mechanically certified gate results."""
    return [
        GateResult(_claim(f"docs/missing{i}.md"), GateOutcome.M_CERTIFIED, "") for i in range(n)
    ]


def _discover_state(worklist, budget, strict, spend=0.0):
    """Build the graph state consumed by the discovery node."""
    return {
        "repo_root": "/irrelevant",
        "worklist": list(worklist),
        "budget": budget,
        "spend": spend,
        "partial_notes": [],
        "strict_measurement": strict,
    }


def _judge_state(results, budget, strict):
    """Build the graph state consumed by the semantic-judge node."""
    return {
        "repo_root": "/irrelevant",
        "gate_results": results,
        "coverages": [{"unit": "D.md", "doc_hash": "h"}],
        "verdicts": [],
        "findings": [],
        "budget": budget,
        "spend": 0.0,
        "partial_notes": [],
        "units_discovered": 1,
        "strict_measurement": strict,
    }


def test_the_discovery_budget_stop_aborts_under_strict():
    """Abort strict discovery after recording a dollar-budget rail stop."""
    writer = _Writer()
    node = make_discover(_FakeAgent({"output_tokens": 20_000}), writer)  # $0.30/unit
    with pytest.raises(StrictMeasurementAbort) as exc:
        node(_discover_state([f"d{i}.md" for i in range(10)], budget=1.00, strict=True))
    assert "budget_cap:dollars" in str(exc.value)
    assert [rt for _c, rt, _p in writer.rows if rt == "rail_stop"] == ["rail_stop"]


def test_the_s_candidate_cap_aborts_under_strict():
    """Abort strict semantic judging when the candidate cap is exceeded."""
    writer = _Writer()
    node = make_semantic_judge(_FakeJudge(), writer)
    with pytest.raises(StrictMeasurementAbort) as exc:
        node(_judge_state(_m_certified(MAX_S_CANDIDATES + 1), budget=float("inf"), strict=True))
    assert "budget_cap:max_s_candidates" in str(exc.value)


def test_the_wallets_stop_is_loud_but_never_aborts_not_even_under_strict(
    tmp_path, db_session, monkeypatch
):
    """Report wallet exhaustion at a cell boundary without aborting strict mode."""
    from drift.journal.writer import JournalWriter, Stamps
    from tests.fixtures.frame import stub_dispatch

    repo = _frame_repo(tmp_path, files={"A.md": "a", "B.md": "b"})
    stamps = Stamps("agent/0.8", "sjudge/0.4", "claude-sonnet-5")
    usage_30c = {"output_tokens": 20_000}  # $0.30 at sonnet-5 list rates

    def _expensive(run_id, producer, unit_ref, repo_root, config):
        """Record usage greater than the run budget for one discovery cell."""
        if producer == "agent":
            w = JournalWriter(db_session, run_id, "r", "abc", stamps)
            w.write("agent", "agent_coverage", {"unit": unit_ref, "usage": usage_30c})
            w.flush()
        return None

    run_id, report = _frame_run(
        repo,
        db_session,
        monkeypatch,
        budget=0.10,
        strict_measurement=True,
        dispatch=stub_dispatch(db_session, hook=_expensive),
    )

    assert "# drift report" in report
    assert db_session.get(ScanRun, run_id).status == "done"
    rails = [
        r.payload
        for r in db_session.query(JournalRecord)  # scoped: never asserts over an unscoped table
        .filter_by(run_id=run_id, record_type="rail_stop")
        .order_by(JournalRecord.id)
        .all()
    ]
    assert rails and rails[-1]["reason"] == "wallet-exhausted"
    # The payload preserves both document-unit and cell counts for existing consumers.
    assert rails[-1]["items_total"] == 2 and rails[-1]["cells_total"] == 3
    assert rails[-1]["units_done"] == 1 and rails[-1]["cells_done"] == 2


def test_without_the_flag_unit_a_fail_soft_is_unchanged():
    """Preserve fail-soft behavior when strict measurement is disabled."""
    writer = _Writer()
    out = make_discover(_FakeAgent({"output_tokens": 20_000}), writer)(
        _discover_state([f"d{i}.md" for i in range(10)], budget=1.00, strict=False)
    )
    assert out["units_discovered"] == 4
    assert any("scan is partial" in n for n in out["partial_notes"])

    writer = _Writer()
    judged = make_semantic_judge(_FakeJudge(), writer)(
        _judge_state(_m_certified(MAX_S_CANDIDATES + 1), budget=float("inf"), strict=False)
    )
    assert len(judged["findings"]) == MAX_S_CANDIDATES


def test_a_strict_run_that_hits_no_rail_behaves_normally():
    """Strict is not a stricter budget — an untruncated run is untouched by it."""
    writer = _Writer()
    out = make_discover(_FakeAgent(), writer)(
        _discover_state(["a.md", "b.md"], budget=5.00, strict=True)
    )
    assert out["units_discovered"] == 2
    assert out["partial_notes"] == []


# Shared helpers keep these tests on the production frame path.
_frame_repo = frame_repo
_frame_run = frame_run


def _rail_configs(db_session, run_id):
    """Read rail-configuration payloads for one run in journal order."""
    from drift.persistence.models import JournalRecord

    return [
        r.payload
        for r in db_session.query(JournalRecord)  # scoped: never an unscoped table
        .filter_by(run_id=run_id, record_type="rail_config")
        .order_by(JournalRecord.id)
        .all()
    ]


def test_the_rail_config_is_journaled_with_the_strict_flag(tmp_path, db_session, monkeypatch):
    """Journal the complete run configuration with strict mode enabled."""
    run_id, _ = _frame_run(
        _frame_repo(tmp_path), db_session, monkeypatch, budget=40.0, strict_measurement=True
    )
    config = _rail_configs(db_session, run_id)
    assert len(config) == 1
    # Exact equality prevents the run's self-description from changing silently.
    from drift.agent.discovery import prompt_fingerprint as agent_fp
    from drift.fsguard import B_DOC
    from drift.judge.semantic_judge import prompt_fingerprint as judge_fp
    from drift.kernels.link_resolves import LINK_JURISDICTION_VERSION
    from drift.kernels.models import PRODUCERS_VER

    assert config[0] == {
        "max_units": MAX_UNITS,
        "max_s_candidates": MAX_S_CANDIDATES,
        "budget": 40.0,
        "strict_measurement": True,
        "price_table_ver": PRICE_TABLE_VER,
        # Content hashes identify the prompt-building surfaces used by the run.
        "agent_prompt_sha": agent_fp(),
        "judge_prompt_sha": judge_fp(),
        "link_jurisdiction_ver": LINK_JURISDICTION_VERSION,
        # The configuration records the confidence-band boundary rendered in the report.
        "suspected_band_max": 0.2,
        # The producer vocabulary changes independently from the agent version.
        "producers_ver": PRODUCERS_VER,
        # The input bound determines how much of an oversized document can yield claims.
        "b_doc": B_DOC,
    }


def test_the_rail_config_is_journaled_even_when_the_unit_cap_aborts(
    tmp_path, db_session, monkeypatch
):
    """Journal configuration even when the unit cap aborts before frame planning."""
    from drift.persistence.models import JournalRecord

    monkeypatch.setattr("drift.graph.nodes.rails.MAX_UNITS", 0)
    repo = _frame_repo(tmp_path)

    with pytest.raises(RuntimeError, match="drift check"):
        _frame_run(repo, db_session, monkeypatch, budget=5.0)

    run_id = db_session.query(ScanRun).filter_by(repo=str(repo)).one().id  # scoped, not `.desc()`
    types = [
        r.record_type
        for r in db_session.query(JournalRecord)
        .filter_by(run_id=run_id)
        .order_by(JournalRecord.id)
        .all()
    ]
    assert types == ["rail_config", "run_cost"]  # config before the raise; cost from the finally


def test_an_unlimited_budget_journals_as_null(tmp_path, db_session, monkeypatch):
    """JSON has no Infinity and PostgreSQL rejects the literal (same rule as `rail_stop`)."""
    run_id, _ = _frame_run(_frame_repo(tmp_path), db_session, monkeypatch, budget=None)
    assert _rail_configs(db_session, run_id)[0]["budget"] is None


def test_a_journal_failure_aborts_under_strict():
    """Abort strict discovery without rolling back its failed journal write."""
    writer = _FlushFails()
    node = make_discover(_FakeAgent(), writer)
    with pytest.raises(StrictMeasurementAbort) as exc:
        node(_discover_state(["a.md", "b.md"], budget=5.00, strict=True))
    assert "journal" in str(exc.value).lower()
    assert writer.rollbacks == 0  # strict does not tidy up; the transaction is the evidence


def test_a_journal_failure_without_the_flag_is_rolled_back_bannered_and_survived():
    """Continue ordinary discovery after rolling back each failed journal write."""
    writer = _FlushFails()
    node = make_discover(_FakeAgent(), writer)

    out = node(_discover_state(["a.md", "b.md"], budget=5.00, strict=False))

    assert out["units_discovered"] == 2
    assert writer.rollbacks == 2
    assert len(out["partial_notes"]) == 2
    assert "journal write failed" in out["partial_notes"][0]
    assert "a.md" in out["partial_notes"][0]


def test_the_run_still_reaches_the_report_node_when_the_journal_dies(db_session, tmp_path):
    """Reach the report node after a non-strict journal failure."""
    (tmp_path / "D.md").write_text("Notes.\nSee docs/missing.md.\n")
    run = ScanRun(repo=str(tmp_path), commit_sha="deadbeef", status="running")
    db_session.add(run)
    db_session.flush()

    writer = _FlushFails()
    graph = build_graph(
        _FakeAgent(),
        lambda root: _EmptyProducer(),
        _FakeJudge(),
        writer=writer,
    )
    planned_worklist, planned_hazards = enumerate_docs(str(tmp_path))
    out = finish(
        db_session,
        str(tmp_path),
        run.id,
        graph.invoke(
            {
                "repo_root": str(tmp_path),
                "run_id": run.id,
                "doc_filter": None,
                "worklist": [],
                # Direct invocation supplies the plan normally owned by the frame.
                "planned_worklist": planned_worklist,
                "planned_hazards": planned_hazards,
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
            }
        ),
    )

    assert out["report_text"]
    assert "PARTIAL" in out["report_text"]
    assert "journal write failed" in out["report_text"]


def test_a_journal_failure_on_the_error_path_is_handled_too():
    """Survive a journal failure while recording an agent error."""

    class _CrashingAgent:
        """Raise a discovery error for every document."""

        def discover(self, repo_root, doc_path):
            """Raise the simulated agent failure."""
            raise RuntimeError("agent exploded")

    writer = _FlushFails()
    out = make_discover(_CrashingAgent(), writer)(
        _discover_state(["a.md"], budget=5.00, strict=False)
    )
    assert out["units_discovered"] == 1
    assert any("journal write failed" in n for n in out["partial_notes"])


def test_a_per_run_cap_overrides_the_module_rail_and_is_what_gets_journaled(
    tmp_path, db_session, monkeypatch
):
    """Use the per-run semantic cap for judging and journal configuration."""
    writer = _Writer()
    state = _judge_state(_m_certified(5), budget=float("inf"), strict=False)
    state["max_s_candidates"] = 3
    out = make_semantic_judge(_FakeJudge(), writer)(state)
    assert len(out["findings"]) == 3
    assert any("cap 3" in n for n in out["partial_notes"])

    run_id, _ = _frame_run(
        _frame_repo(tmp_path),
        db_session,
        monkeypatch,
        budget=40.0,
        strict_measurement=True,
        max_s_candidates=100,
    )
    assert _rail_configs(db_session, run_id)[0]["max_s_candidates"] == 100


def test_run_evolve_scan_threads_the_cli_cap_into_graph_state(tmp_path, db_session, monkeypatch):
    """Thread the per-run semantic cap into every cell and its journal record."""
    from tests.fixtures.frame import stub_dispatch

    captured: list[dict] = []
    run_id, _ = _frame_run(
        _frame_repo(tmp_path),
        db_session,
        monkeypatch,
        max_s_candidates=77,
        dispatch=stub_dispatch(
            db_session,
            hook=lambda run_id, producer, unit_ref, repo_root, config: captured.append(config),
        ),
    )

    assert [c["max_s_candidates"] for c in captured] == [77, 77]
    assert _rail_configs(db_session, run_id)[0]["max_s_candidates"] == 77
