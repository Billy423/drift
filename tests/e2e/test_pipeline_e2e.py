"""End-to-end resolution: an issue closes because the gate replayed its own stored check.

This is the property worth proving. An issue must not close because a producer failed to
mention the claim again — that would make a scan which simply did not look indistinguishable
from a fix, and issues would flap as coverage varied.

Successive runs are simulated by invoking the compiled graph directly, with scripted discovery
and judge clients, against the `db_session` fixture, chaining several runs and issues inside one
rolled-back transaction. `run_scan` opens its own session and would not see this fixture's
uncommitted rows, so these tests bypass it deliberately; `run_scan` is exercised separately
against a real committed session with explicit row cleanup.
"""

from pathlib import Path

from drift.agent.discovery import DiscoveryAgent
from drift.domain.findings import IssueStatus
from drift.graph.frame import build_graph, run_scan
from drift.journal.writer import JournalWriter, Stamps
from drift.judge.semantic_judge import SemanticJudge
from drift.persistence.db import SessionLocal
from drift.persistence.models import CellTerminalStatus, Issue, JournalRecord, ScanRun
from tests.agent.test_runner import _Block, _emit, _Resp, _ScriptedClient
from tests.fixtures.frame import finish, planned

_STAMPS = Stamps("agent/0.1", "sjudge/0.1", "claude-sonnet-5")


class _EmptyProducer:
    """Trivial producer-#2 stand-in: no docstring claims, always-complete coverage."""

    def produce(self, doc_filter=None):
        return [], {"unit": "docstring_corpus", "status": "complete"}


def _empty_producer_factory(root):
    return _EmptyProducer()


def _repo_with_missing_asset(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    (root / "GUIDE.md").write_text("See assets/logo.png for the logo.\n")
    return str(root)


def _one_claim_inventory():
    return {
        "claims": [
            {
                "literal": "assets/logo.png",
                "predicate": "path_exists",
                "spans": [[1, 1]],
                "claim_class": 1,
                "note": "asset ref",
                "confidence": 0.9,
            }
        ]
    }


def _empty_inventory():
    return {"claims": []}


# A scripted emit turn is an `emit_result` tool call whose arguments are the payload (`_emit`),
# not a JSON text block. The payloads are byte-identical as data to the form they had before the
# emit path changed, so the resolution chain below is still chained over the same runs.
def _scripted_discovery(inventory):
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="reading the doc directly")]),
            _emit(inventory),
        ]
    )
    return DiscoveryAgent(client, agent_ver=_STAMPS.agent_ver)


def _scripted_judge(live, confidence):
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="thinking")]),
            _emit({"live": live, "reasoning": "r", "confidence": confidence}),
        ]
    )
    return SemanticJudge(client, judge_ver=_STAMPS.judge_ver)


def _judge_never_called():
    """A judge whose client has no scripted responses — proves adjudicate() is never invoked
    when there are zero M-certified candidates (the empty-inventory runs below)."""
    return SemanticJudge(_ScriptedClient([]), judge_ver=_STAMPS.judge_ver)


def _run_once(db_session, repo_root, discovery_agent, semantic_judge, commit_sha="sha"):
    run = ScanRun(repo=repo_root, commit_sha=commit_sha, status="running")
    db_session.add(run)
    db_session.flush()
    writer = JournalWriter(db_session, run.id, repo_root, commit_sha, _STAMPS)
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
                # The frame is the only enumerator, so a direct graph invocation has to stand
                # in for it — same `enumerate_docs`, which makes this a mirror and not a fork.
                **planned(repo_root),
                "claims": [],
                "coverages": [],
                "gate_results": [],
                "verdicts": [],
                "findings": [],
                "ranked_entries": [],
                "result": None,
                "report_text": "",
            }
        ),
    )
    return run, out


def test_resolution_via_replay_is_producer_independent(tmp_path, db_session):
    repo_root = _repo_with_missing_asset(tmp_path, "repo_a")

    # run 1: scout finds the claim, S-judge says live -> HIGH -> DISCOVERED
    run1, out1 = _run_once(
        db_session,
        repo_root,
        _scripted_discovery(_one_claim_inventory()),
        _scripted_judge(live=True, confidence=0.9),
    )
    assert out1["result"].discovered == 1
    issue = db_session.query(Issue).filter_by(repo=repo_root).one()
    assert issue.status == IssueStatus.DISCOVERED
    check = issue.payload["check"]
    assert check["predicate"] == "path_exists"
    assert check["normalized_args"] == ["assets/logo.png"]

    # repair: the file now exists at the current revision
    (Path(repo_root) / "assets").mkdir()
    (Path(repo_root) / "assets" / "logo.png").write_text("PNG")

    # run 2: scout returns an EMPTY inventory — resolution must come from the replay gate
    # replaying the issue's OWN stored check, not from the scout noticing the repair
    run2, out2 = _run_once(
        db_session,
        repo_root,
        _scripted_discovery(_empty_inventory()),
        _judge_never_called(),
    )
    assert out2["findings"] == []
    assert out2["result"].resolved == 1
    run2_journal = db_session.query(JournalRecord).filter_by(run_id=run2.id).all()
    assert not any(r.record_type == "s_verdict" for r in run2_journal)  # judge never adjudicated

    issue = db_session.query(Issue).filter_by(repo=repo_root).one()
    assert issue.status == IssueStatus.RESOLVED


def test_unrepaired_repo_stays_open_despite_empty_inventory(tmp_path, db_session):
    repo_root = _repo_with_missing_asset(tmp_path, "repo_b")

    run1, out1 = _run_once(
        db_session,
        repo_root,
        _scripted_discovery(_one_claim_inventory()),
        _scripted_judge(live=True, confidence=0.9),
    )
    assert out1["result"].discovered == 1

    # NO repair this time — the target is still absent at replay time
    run2, out2 = _run_once(
        db_session,
        repo_root,
        _scripted_discovery(_empty_inventory()),
        _judge_never_called(),
    )
    assert out2["result"].resolved == 0

    issue = db_session.query(Issue).filter_by(repo=repo_root).one()
    assert issue.status == IssueStatus.DISCOVERED  # stayed open: absence alone never closes it


# --- run_scan: the real production entrypoint (own SessionLocal, real commit) ---


def _clean(repo):
    session = SessionLocal()
    session.query(Issue).filter_by(repo=repo).delete()
    session.query(JournalRecord).filter_by(repo=repo).delete()
    # Real cells write a `(run_id, cell_key)` terminal-status row, and its foreign key to
    # `scan_run` makes the run row undeletable until that row goes. The table has no `repo`
    # column, so it is scoped through the run ids belonging to this repo.
    runs = [r.id for r in session.query(ScanRun).filter_by(repo=repo)]
    session.query(CellTerminalStatus).filter(CellTerminalStatus.run_id.in_(runs)).delete(
        synchronize_session=False
    )
    session.query(ScanRun).filter_by(repo=repo).delete()
    session.commit()
    session.close()


def test_run_evolve_scan_end_to_end_against_real_session(tmp_path):
    repo_root = _repo_with_missing_asset(tmp_path, "repo_prod")
    _clean(repo_root)
    try:
        client = _ScriptedClient(
            [
                _Resp([_Block("text", text="reading the doc directly")]),
                _emit(_one_claim_inventory()),
                _Resp([_Block("text", text="thinking")]),
                _emit({"live": True, "reasoning": "r", "confidence": 0.9}),
            ]
        )
        run_id, report_text = run_scan(repo_root, client=client)

        assert "## Verified findings" in report_text
        assert "assets/logo.png" in report_text

        session = SessionLocal()
        try:
            run = session.get(ScanRun, run_id)
            assert run.status == "done"
            assert run.repo == repo_root
            issue = session.query(Issue).filter_by(repo=repo_root).one()
            assert issue.status == IssueStatus.DISCOVERED
            journal_rows = session.query(JournalRecord).filter_by(run_id=run_id).all()
            assert {r.record_type for r in journal_rows} == {
                # The frame's plan row: what this run enumerated and intends to dispatch,
                # written before the graph is invoked. The exact-set style is deliberate — a
                # record type appearing or vanishing is a decision, not a detail.
                "frame_plan",
                # One per cell: the authoritative terminal record the frame fans in on.
                "cell_result",
                "rail_config",
                "agent_coverage",
                "gate_outcome",  # the certified claim's gate row
                "s_verdict",
                "claim_inventory",
                "run_cost",  # every run states its own bill
            }
        finally:
            session.close()
    finally:
        _clean(repo_root)
