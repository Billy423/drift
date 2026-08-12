"""Both producers, end to end: the discovery agent (path and make-target claims) and the
docstring producer (signature claims) feed the same replay gate and land in their own tiers.

What this adds over testing each producer alone is the wiring between them — that two producers
can hand one gate their claims in a single run without either displacing the other."""

from __future__ import annotations

from drift.agent.discovery import DiscoveryAgent
from drift.domain.findings import Confidence
from drift.gate.replay import GateOutcome
from drift.graph.frame import build_graph
from drift.journal.writer import JournalWriter, Stamps
from drift.judge.semantic_judge import SemanticJudge
from drift.persistence.models import Issue, JournalRecord, ScanRun
from tests.agent.test_runner import _Block, _emit, _Resp, _ScriptedClient
from tests.fixtures.frame import finish, planned

_STAMPS = Stamps("agent/0.3", "sjudge/0.1", "claude-sonnet-5")

# The mini package: `ghost` is documented-but-absent on a plain function (no **kwargs) -> the
# kernel refutes it cleanly (M_CERTIFIED). `extra` is documented-but-absent on a function that
# DOES accept **kwargs -> the kernel cannot tell whether it's silently absorbed (UNGATEABLE
# variadic).
_MOD = '''
def send(msg):
    """Send a message.

    Parameters
    ----------
    msg : str
        The message.
    ghost : str
        Documented but absent from the real signature.
    """


def send_extra(msg, **kwargs):
    """Send with pass-through extras.

    Parameters
    ----------
    msg : str
        The message.
    extra : str
        Documented but absorbed by **kwargs — the kernel can't tell.
    """
'''


def _build_repo(tmp_path):
    """`README.md` is the ONLY *.md/.rst/.txt file in this repo — a second doc file (e.g. a real
    docs/guide.md) would itself enter the agent-lane worklist and need its own scripted
    discover() turn, which isn't the point of this fixture; the live path claim below points at
    a non-doc asset file instead."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "assets").mkdir()
    (root / "assets" / "logo.png").write_text("PNG")
    (root / "README.md").write_text(
        "Project guide.\n"
        "See assets/logo.png for the logo.\n"
        "See docs/gone.md for the archived notes.\n"
        "Run `make gone` to clean generated files.\n"
    )
    (root / "Makefile").write_text("build:\n\techo hi\n")
    pkg = root / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(_MOD)
    return str(root)


def _agent_inventory():
    return {
        "claims": [
            {
                "literal": "assets/logo.png",
                "predicate": "path_exists",
                "spans": [[2, 2]],
                "claim_class": 1,
                "note": "live path reference",
                "confidence": 0.9,
                "args": [],
            },
            {
                "literal": "docs/gone.md",
                "predicate": "path_exists",
                "spans": [[3, 3]],
                "claim_class": 1,
                "note": "looks stale",
                "confidence": 0.6,
                "args": [],
            },
            {
                "literal": "make gone",
                "predicate": "make_target_exists",
                "spans": [[4, 4]],
                "claim_class": 1,
                "note": "no such target in the root Makefile",
                "confidence": 0.6,
                "args": ["gone"],
            },
        ]
    }


# A scripted emit turn is an `emit_result` tool call whose arguments are the payload (`_emit`),
# not a JSON text block. The payloads are byte-identical as data to the form they had before the
# emit path changed, so both producers still hand the gate the same claims — the only thing this
# file is here to prove.
def _agent_client():
    return _ScriptedClient(
        [
            _Resp([_Block("text", text="reading the doc directly")]),
            _emit(_agent_inventory()),
        ]
    )


def _judge_verdict_pair(live: bool, confidence: float, reasoning: str) -> list:
    return [
        _Resp([_Block("text", text="thinking")]),
        _emit({"live": live, "reasoning": reasoning, "confidence": confidence}),
    ]


def _judge_client_all_live():
    """One live=True verdict per M-certified candidate: docs/gone.md, make gone, ghost — in
    that order (agent claims precede docstring claims in `state["claims"]`; the `extra` claim
    never reaches the judge — it's UNGATEABLE before the S-judge lane)."""
    responses = []
    responses += _judge_verdict_pair(True, 0.9, "still a live reference")
    responses += _judge_verdict_pair(True, 0.9, "still a live make target reference")
    responses += _judge_verdict_pair(True, 0.9, "still a live documented parameter")
    return _ScriptedClient(responses)


def test_two_producers_feed_one_gate_and_land_per_tier(tmp_path, db_session):
    repo_root = _build_repo(tmp_path)

    run = ScanRun(repo=repo_root, commit_sha="deadbeef", status="running")
    db_session.add(run)
    db_session.flush()

    writer = JournalWriter(db_session, run.id, repo_root, "deadbeef", _STAMPS)
    discovery_agent = DiscoveryAgent(_agent_client(), agent_ver=_STAMPS.agent_ver)
    semantic_judge = SemanticJudge(_judge_client_all_live(), judge_ver=_STAMPS.judge_ver)

    def producer_factory(root):
        from drift.docstrings import DocstringProducer

        return DocstringProducer(root, agent_ver=_STAMPS.agent_ver)

    graph = build_graph(
        discovery_agent,
        producer_factory,
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
                "kernel_errors": [],
                "result": None,
                "report_text": "",
            }
        ),
    )

    # --- gate outcomes: both producers' claims replayed through the SAME gate ---
    outcomes = {gr.claim.anchor.literal: gr.outcome for gr in out["gate_results"]}
    assert outcomes["assets/logo.png"] == GateOutcome.PASSING  # live path: both legs hold
    assert outcomes["docs/gone.md"] == GateOutcome.M_CERTIFIED  # dead path
    assert outcomes["make gone"] == GateOutcome.M_CERTIFIED  # dead make target
    assert outcomes["ghost : str"] == GateOutcome.M_CERTIFIED  # docstring ghost param
    ghost_outcomes = {
        gr.claim.check.normalized_args[1]: gr.outcome
        for gr in out["gate_results"]
        if gr.claim.check and gr.claim.check.predicate == "signature_has_param"
    }
    assert ghost_outcomes["extra"] == GateOutcome.UNGATEABLE

    extra_result = next(
        gr
        for gr in out["gate_results"]
        if gr.claim.check and gr.claim.check.normalized_args[1:] == ("extra",)
    )
    assert extra_result.detail == "variadic"

    # --- HIGH findings: the dead path, the dead make target, and the ghost param all pass S ---
    high_identities = {f.identity for f in out["findings"]}
    assert ("README.md", "docs/gone.md") in high_identities
    assert ("README.md", "gone") in high_identities
    ghost_finding = next(f for f in out["findings"] if f.check_id == "signature_has_param")
    assert ghost_finding.identity[-1] == "ghost"
    assert all(f.confidence == Confidence.HIGH for f in out["findings"])
    assert len(out["findings"]) == 3

    # --- ranked tier: the variadic-absorbed `extra` param, and nothing else ---
    ranked_literals = {e.claim.anchor.literal for e in out["ranked_entries"]}
    assert ranked_literals == {"extra : str"}

    # --- reconcile: three new issues discovered ---
    assert out["result"].discovered == 3
    issues = {i.check_id: i for i in db_session.query(Issue).filter_by(repo=repo_root).all()}
    assert set(issues) == {"path_exists", "make_target_exists", "signature_has_param"}

    # --- journal: gate_ungateable carries the variadic reason + producer=docstring ---
    rows = db_session.query(JournalRecord).filter_by(run_id=run.id).all()
    ungateable_rows = [r for r in rows if r.record_type == "gate_ungateable"]
    assert len(ungateable_rows) == 1
    assert ungateable_rows[0].payload["reason"] == "variadic"
    assert ungateable_rows[0].payload["producer"] == "docstrings"
    assert ungateable_rows[0].payload["predicate"] == "signature_has_param"

    # --- journal: s_verdict rows carry `producer`, one per M-certified candidate lane ---
    s_verdict_rows = [r for r in rows if r.record_type == "s_verdict"]
    assert len(s_verdict_rows) == 3
    producers_by_literal = {r.payload["literal"]: r.payload["producer"] for r in s_verdict_rows}
    assert producers_by_literal["docs/gone.md"] == "agent"
    assert producers_by_literal["make gone"] == "agent"
    assert producers_by_literal["ghost : str"] == "docstrings"
    assert all(r.payload["live"] is True for r in s_verdict_rows)

    # --- journal: two agent_coverage rows (README.md via discover + docstring_corpus sibling) ---
    coverage_rows = [r for r in rows if r.record_type == "agent_coverage"]
    assert {r.payload["unit"] for r in coverage_rows} == {"README.md", "docstring_corpus"}

    # --- report: HIGH section + comment-tier section both populated ---
    report = out["report_text"]
    assert "## Verified findings" in report
    assert "docs/gone.md" in report
    assert "make gone" in report
    assert "ghost : str" in report
    assert "## Ranked tier (candidates — UNVERIFIED)" in report
    assert "extra : str" in report
