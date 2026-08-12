"""Exercise graph routing, replay records, hazards, and filtered producer runs."""

from __future__ import annotations

import functools
import os
import stat

import pytest

from drift.agent.discovery import DiscoveryAgent
from drift.agent.runner import _EMIT_INSTRUCTION, EMIT_TOOL_NAME
from drift.docstrings import DocstringProducer
from drift.graph.frame import build_graph, run_scan
from drift.journal.writer import JournalWriter, Stamps
from drift.judge.semantic_judge import SemanticJudge
from drift.persistence.models import JournalRecord, ScanRun
from tests.fixtures.frame import finish, planned
from tests.fixtures.step2_substrate import (
    AGENT_INVENTORIES,
    EXPECTED_UNGATEABLE_REASONS,
    HAZARD_BIG_CHARS,
    SUBSTRATE_COMMIT_SHA,
    SubstrateClient,
    UnexpectedRequest,
    build_substrate_repo,
    make_substrate_client,
)

_STAMPS = Stamps("agent/0.8", "sjudge/0.4", "claude-sonnet-5")

# Matches the cache marker applied to the final user block.
_MARK = {"type": "ephemeral"}

# The constrained run must stop discovery and semantic judging independently.
RUN_R_BUDGET = 0.02
RUN_R_MAX_S_CANDIDATES = 1


def _run(db_session, repo_root: str, **overrides):
    """Run the scripted graph against the substrate inside a test transaction."""
    run = ScanRun(repo=repo_root, commit_sha="substrate", status="running")
    db_session.add(run)
    db_session.flush()
    writer = JournalWriter(db_session, run.id, repo_root, "substrate", _STAMPS)
    client = make_substrate_client()
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
        # Direct invocation receives the same plan that the frame would enumerate.
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
        "spend": 0.0,
        "units_discovered": 0,
        "partial_notes": [],
        "strict_measurement": False,
        "max_s_candidates": None,
        **overrides,
    }
    out = finish(db_session, repo_root, run.id, graph.invoke(state))
    rows = (
        db_session.query(JournalRecord)
        .filter_by(run_id=run.id)  # scoped: never asserts over an unscoped table
        .order_by(JournalRecord.id)
        .all()
    )
    streams: dict[str, list[dict]] = {}
    for row in rows:
        streams.setdefault(row.record_type, []).append(row.payload)
    return out, streams


@pytest.fixture
def substrate(tmp_path):
    """Build the deterministic repository used by graph substrate tests."""
    return str(build_substrate_repo(tmp_path))


def test_run_u_produces_the_row_types_the_doc_loop_corpus_lacks(substrate, db_session):
    """Emit passing, certified, killed, and unbound claim records in one run."""
    out, streams = _run(db_session, substrate)

    assert len(out["findings"]) >= 1, "no emitted verified findings"
    assert len(streams.get("gate_kill", [])) >= 1, "no gate_kill row"
    outcomes = {p["outcome"] for p in streams["gate_outcome"]}
    assert {"PASSING", "M_CERTIFIED"} <= outcomes
    assert any(p["check"] is None for p in streams["claim_inventory"]), "no UNBOUND claim"
    assert "rail_stop" not in streams, "the unconstrained arm must fire no rail"


def test_run_u_carries_the_docstring_lane_into_the_inventory_and_the_judge(substrate, db_session):
    """Carry docstring-producer claims through inventory and semantic judging."""
    _, streams = _run(db_session, substrate)

    lanes = {p["lane"] for p in streams["claim_inventory"]}
    assert "docstrings" in lanes
    assert any(p["producer"] == "docstrings" for p in streams["s_verdict"])


def test_run_u_exercises_the_comment_routing_split_from_both_sides(substrate, db_session):
    """A one-reason corpus cannot test a two-sided rule; this one carries five reasons."""
    _, streams = _run(db_session, substrate)

    reasons = {p["reason"] for p in streams["gate_ungateable"]}
    assert len(reasons - {"base-ambiguous"}) >= 3, reasons
    assert EXPECTED_UNGATEABLE_REASONS["comment_routed"] <= reasons
    assert EXPECTED_UNGATEABLE_REASONS["journal_only"] <= reasons


def test_run_u_keeps_killed_and_journal_only_claims_out_of_the_ranked_tier(substrate, db_session):
    """Exclude killed and journal-only claims from the user-facing ranked tier."""
    out, streams = _run(db_session, substrate)

    ranked = {(e.claim.anchor.doc_path, e.claim.anchor.literal) for e in out["ranked_entries"]}
    for kill in streams["gate_kill"]:
        assert (kill["doc_path"], kill["literal"]) not in ranked
    journal_only = [
        p
        for p in streams["gate_ungateable"]
        if p["reason"] in EXPECTED_UNGATEABLE_REASONS["journal_only"]
    ]
    assert journal_only, "the journal-only side of the split is not represented"
    for row in journal_only:
        assert (row["doc_path"], row["literal"]) not in ranked
    comment_routed = [
        p
        for p in streams["gate_ungateable"]
        if p["reason"] in EXPECTED_UNGATEABLE_REASONS["comment_routed"]
    ]
    assert comment_routed, "the comment-routed side of the split is not represented"
    for row in comment_routed:
        assert (row["doc_path"], row["literal"]) in ranked


def test_run_u_produces_one_s_failed_verdict(substrate, db_session):
    """Exclude a semantically rejected claim from findings and ranked entries."""
    out, streams = _run(db_session, substrate)

    assert any(p["live"] is False and p["error"] is False for p in streams["s_verdict"])
    high_literals = {f.evidence.doc_claim for f in out["findings"]}
    assert "docs/ROADMAP.md" not in high_literals
    ranked = {e.claim.anchor.literal for e in out["ranked_entries"]}
    assert "docs/ROADMAP.md" not in ranked, "an S-failed candidate is a verdict, not a lead"


def test_run_r_fires_the_rails_that_survive_and_skips_candidates(substrate, db_session):
    """Fire both graph-local rails and record skipped semantic candidates."""
    _, streams = _run(
        db_session,
        substrate,
        budget=RUN_R_BUDGET,
        max_s_candidates=RUN_R_MAX_S_CANDIDATES,
    )

    fired = {f"{p['lane']}/{p['reason']}" for p in streams["rail_stop"]}
    assert fired == {
        "discover/budget_cap:dollars",
        "semantic_judge/budget_cap:max_s_candidates",
    }
    assert len(streams["s_judge_skipped"]) >= 1


def test_run_r_keeps_the_docstring_lane_through_a_budget_short_scan(substrate, db_session):
    """Keep docstring claims when the discovery producer exhausts its budget."""
    _, streams = _run(
        db_session,
        substrate,
        budget=RUN_R_BUDGET,
        max_s_candidates=RUN_R_MAX_S_CANDIDATES,
    )

    assert any(p["lane"] == "docstrings" for p in streams["claim_inventory"])


def test_scripted_client_answers_by_content_not_by_call_order(substrate, db_session):
    """Return the same inventory regardless of earlier scripted requests."""
    _, streams_a = _run(db_session, substrate)
    _, streams_b = _run(db_session, substrate)

    def signature(streams):
        """Return the stable claim identity projected from journal streams."""
        return sorted(
            (p["anchor"]["doc_path"], p["anchor"]["literal"], p["lane"])
            for p in streams["claim_inventory"]
        )

    assert signature(streams_a) == signature(streams_b)


# An emit-shaped request is valid only when the runner offered its strict emit tool.
_SDK_TOOLS = [{"name": "read_file"}, {"name": "glob"}, {"name": EMIT_TOOL_NAME, "strict": True}]


def _discovery_request(doc_path: str, emit: bool, doc_text: str = "", tools=None) -> dict:
    """Build the runner request shape for a discovery or emit turn."""
    doc_block = {"type": "text", "text": f"Document path: {doc_path}\n\n{doc_text}"}
    messages: list = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Vocabulary + repo map", "cache_control": _MARK},
                # The cache marker follows the last user block as the conversation grows.
                {**doc_block, "cache_control": _MARK} if not emit else doc_block,
            ],
        }
    ]
    if emit:
        messages.append({"role": "assistant", "content": "no repo exploration needed"})
        messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": _EMIT_INSTRUCTION, "cache_control": _MARK}],
            }
        )
    return {
        "system": [{"type": "text", "text": "You are a cartographer of one doc's claims."}],
        "messages": messages,
        "tools": _SDK_TOOLS if tools is None else tools,
    }


def test_scripted_client_raises_on_an_unscripted_doc():
    """Reject a document absent from the scripted inventory."""
    client = SubstrateClient()
    with pytest.raises(UnexpectedRequest):
        client.create(**_discovery_request("NOT-A-SUBSTRATE-DOC.md", emit=True))


def test_a_doc_that_quotes_the_emit_instruction_is_still_a_loop_turn():
    """Treat a document quoting the emit instruction as an ordinary loop turn."""
    client = SubstrateClient()
    quoting_doc = (
        f"# On the emit protocol\n\nThe runner ends a unit by saying: {_EMIT_INSTRUCTION}\n"
    )

    turn_1 = client.create(**_discovery_request("README.md", emit=False, doc_text=quoting_doc))

    assert [b.type for b in turn_1.content] == ["text"]
    assert turn_1.content[0].text == "no repo exploration needed"


def test_an_emit_shaped_request_that_never_offered_the_emit_tool_raises():
    """Reject an emit-shaped request that did not offer the emit tool."""
    client = SubstrateClient()
    beltless = [{"name": "read_file"}]

    with pytest.raises(UnexpectedRequest):
        client.create(**_discovery_request("README.md", emit=True, tools=beltless))


def test_scripted_client_answers_the_emit_turn_with_an_emit_tool_call():
    """Answer loop turns with text and emit turns with a complete tool call."""
    client = SubstrateClient()

    loop_turn = client.create(**_discovery_request("README.md", emit=False))
    assert [b.type for b in loop_turn.content] == ["text"]

    emit_turn = client.create(**_discovery_request("README.md", emit=True))
    block = emit_turn.content[0]
    assert block.type == "tool_use" and block.name == EMIT_TOOL_NAME
    assert block.input == AGENT_INVENTORIES["README.md"]
    # The truncation guard accepts only a completed tool call.
    assert emit_turn.stop_reason == "tool_use"


def test_hazards_are_absent_by_default(substrate):
    """Omit hazardous filesystem members from the default substrate."""
    for name in ("hazard_escape.md", "hazard_fifo.md", "hazard_big.md"):
        assert not os.path.lexists(os.path.join(substrate, name))


def test_hazards_exist_behind_the_flag(tmp_path):
    """Create escape, FIFO, and oversized-file hazards when requested."""
    root = build_substrate_repo(tmp_path, include_hazards=True)

    escape = root / "hazard_escape.md"
    assert escape.is_symlink()
    assert not str(os.path.realpath(escape)).startswith(str(root) + os.sep)
    assert stat.S_ISFIFO(os.stat(root / "hazard_fifo.md").st_mode)
    assert len((root / "hazard_big.md").read_text(encoding="utf-8")) > HAZARD_BIG_CHARS


def test_substrate_commit_sha_is_deterministic_and_pinned(tmp_path):
    """Build a deterministic tree whose commit matches the fixture's pinned identity."""
    import subprocess

    shas = []
    for i in range(2):
        root = build_substrate_repo(tmp_path / f"build{i}")
        shas.append(
            subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    assert shas[0] == shas[1]
    assert shas[0] == SUBSTRATE_COMMIT_SHA


def _framed(db_session, repo_root: str, **kwargs):
    """Run the substrate through the frame with in-process cell dispatch."""
    run_id, report = run_scan(
        repo_root,
        client=make_substrate_client(),
        session_factory=lambda: db_session,
        poll_interval=0,
        **kwargs,
    )
    rows = (
        db_session.query(JournalRecord)
        .filter_by(run_id=run_id)  # scoped: never asserts over an unscoped table
        .order_by(JournalRecord.id)
        .all()
    )
    streams: dict[str, list[dict]] = {}
    for row in rows:
        streams.setdefault(row.record_type, []).append(row.payload)
    return report, streams


def test_a_doc_filter_naming_a_python_file_contributes_that_files_claims(substrate, db_session):
    """Use only docstring-producer claims when filtering to a Python file."""
    _report, streams = _framed(db_session, substrate, doc_filter="substrate_pkg/core.py")

    inventory = streams["claim_inventory"]
    assert inventory, "the run contributed nothing at all"
    assert {p["lane"] for p in inventory} == {"docstrings"}
    assert {p["anchor"]["doc_path"] for p in inventory} == {"substrate_pkg/core.py"}


def test_a_doc_filter_naming_a_markdown_doc_contributes_no_docstring_claims(substrate, db_session):
    """Short-circuit the Python corpus when filtering to a Markdown document."""
    _report, streams = _framed(db_session, substrate, doc_filter="README.md")

    assert {p["lane"] for p in streams["claim_inventory"]} == {"agent"}
    corpus = [p for p in streams["agent_coverage"] if p["unit"] == "docstring_corpus"]
    assert len(corpus) == 1
    assert corpus[0]["status"] == "complete"
    assert corpus[0]["claims_emitted"] == 0
    assert corpus[0]["doc_filter"] == "README.md"
    assert corpus[0]["symbols_walked"] == 0
    assert "no Python corpus walked" in corpus[0]["detail"]
