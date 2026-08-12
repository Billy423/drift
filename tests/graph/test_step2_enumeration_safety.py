"""Test that document enumeration cannot hang, escape the repository, or read without bounds."""

from __future__ import annotations

import functools
import json
import os

import pytest

from drift.agent.discovery import DiscoveryAgent
from drift.docstrings import DocstringProducer
from drift.fsguard import B_DOC
from drift.graph.frame import build_graph
from drift.graph.nodes.judge import _read_doc_text
from drift.journal.writer import JournalWriter, Stamps
from drift.judge.semantic_judge import SemanticJudge
from drift.persistence.models import JournalRecord, ScanRun
from tests.fixtures.deadline import deadline as _deadline
from tests.fixtures.frame import finish, planned
from tests.fixtures.step2_substrate import SubstrateClient, build_substrate_repo

_STAMPS = Stamps("agent/0.8", "sjudge/0.4", "claude-sonnet-5")


class HazardClient(SubstrateClient):
    """Return an empty inventory for the oversized hazard document."""

    def _emit_text(self, kwargs: dict) -> str:
        """Handle the oversized document before delegating other scripted responses."""
        texts = self._texts(kwargs)
        if "You are a cartographer" in "\n".join(texts):
            if self._field(texts, "Document path:") == "hazard_big.md":
                return json.dumps({"claims": []})
        return super()._emit_text(kwargs)


def _run(db_session, repo_root: str, client=None, **overrides):
    """Run the full scripted graph inside the test transaction."""
    client = client or HazardClient()
    run = ScanRun(repo=repo_root, commit_sha="substrate", status="running")
    db_session.add(run)
    db_session.flush()
    writer = JournalWriter(db_session, run.id, repo_root, "substrate", _STAMPS)
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
        "spend": 0.0,
        "units_discovered": 0,
        "partial_notes": [],
        "strict_measurement": False,
        "max_s_candidates": None,
        **overrides,
    }
    out = finish(db_session, repo_root, run.id, graph.invoke(state))
    rows = db_session.query(JournalRecord).filter_by(run_id=run.id).order_by(JournalRecord.id).all()
    streams: dict[str, list[dict]] = {}
    for row in rows:
        streams.setdefault(row.record_type, []).append(row.payload)
    return out, streams, client


@pytest.fixture
def hazards(tmp_path):
    """Build a repository containing each supported filesystem hazard."""
    return str(build_substrate_repo(tmp_path, include_hazards=True, commit_hazards=True))


def _coverage(streams, unit: str) -> dict:
    """Return coverage for one document unit."""
    for payload in streams["agent_coverage"]:
        if payload.get("unit") == unit:
            return payload
    raise AssertionError(f"no agent_coverage row for {unit!r}")


def test_a_fifo_unit_never_blocks_a_read(tmp_path):
    """A FIFO is classified without opening it, so enumeration cannot block."""
    from drift.fsguard import read_doc_bytes
    from drift.graph.nodes.enumerate_units import enumerate_docs

    (tmp_path / "README.md").write_text("safe")
    os.mkfifo(tmp_path / "hazard_fifo.md")

    with _deadline():
        worklist, hazards_seen = enumerate_docs(str(tmp_path))
        assert read_doc_bytes(str(tmp_path), "hazard_fifo.md") is None

    assert worklist == ["README.md"]
    assert [(h["unit"], h["reason"]) for h in hazards_seen] == [("hazard_fifo.md", "not-regular")]


def test_an_untracked_hazard_is_out_of_scope_before_it_is_ever_classified(hazards, db_session):
    """An untracked FIFO stays outside the planned worklist."""
    with _deadline():
        out, streams, _ = _run(db_session, hazards)

    assert out["report_text"], "the scan produced no report"
    assert "hazard_fifo.md" not in out["worklist"]
    assert "hazard_fifo.md" not in {p.get("unit") for p in streams["agent_coverage"]}


def test_the_hazard_repo_scans_to_completion(hazards, db_session):
    """Safe documents still scan when hazardous entries are present."""
    with _deadline():
        out, streams, _ = _run(db_session, hazards)

    assert out["worklist"] == ["GUIDE.md", "README.md", "hazard_big.md"]
    assert len(out["findings"]) >= 1, "no emitted HIGH — the hazards took the whole scan with them"
    assert {"README.md", "GUIDE.md"} <= {p["unit"] for p in streams["agent_coverage"]}


def test_every_skip_and_truncation_is_journalled(hazards, db_session):
    """Every skipped or truncated unit has a coverage record."""
    with _deadline():
        _, streams, _ = _run(db_session, hazards)

    skipped = {p["unit"]: p for p in streams["agent_coverage"] if p.get("status") == "skipped"}
    assert set(skipped) == {"hazard_escape.md"}
    assert skipped["hazard_escape.md"]["detail"].startswith("escapes-repo")

    big = _coverage(streams, "hazard_big.md")
    assert big["doc_truncated"] is True
    assert big["doc_chars"] <= B_DOC
    assert big["doc_bytes_total"] > B_DOC


def test_every_skip_and_truncation_is_named_in_the_report(hazards, db_session):
    """The report names every in-scope skipped or truncated unit."""
    with _deadline():
        out, _, _ = _run(db_session, hazards)

    report = out["report_text"]
    for unit in ("hazard_escape.md", "hazard_big.md"):
        assert unit in report, f"{unit} is invisible to the reader of the report"
    assert "escapes-repo" in report
    assert "truncated" in report
    # Git cannot index a FIFO, so this untracked entry was never in scope.
    assert "hazard_fifo.md" not in report


def test_the_escaping_symlink_never_reaches_the_model(hazards, db_session):
    """Content behind a repository-escaping symlink never reaches model input."""
    with _deadline():
        _, _, client = _run(db_session, hazards)

    for call in client.calls:
        assert "I live outside the repo" not in json.dumps(call, default=str)


def test_the_oversize_unit_is_truncated_at_the_bound_and_not_dropped(hazards, db_session):
    """An oversized unit is scanned with bounded input rather than dropped."""
    with _deadline():
        out, _, client = _run(db_session, hazards)

    assert "hazard_big.md" in out["worklist"]
    prompts = [
        text
        for call in client.calls
        for text in client._texts(call)
        if text.startswith("Document path: hazard_big.md")
    ]
    assert prompts, "the oversize unit was never dispatched — it was dropped, not truncated"
    for text in prompts:
        assert len(text) <= B_DOC + 1_000, "the agent was handed an unbounded doc"


def test_judge_context_reads_are_sandboxed_and_bounded(hazards):
    """Judge context reads stay inside the repository and within the input bound."""
    with _deadline():
        assert _read_doc_text(hazards, "../outside_the_repo.md") == ""
        assert _read_doc_text(hazards, "hazard_fifo.md") == ""
        assert 0 < len(_read_doc_text(hazards, "hazard_big.md")) <= B_DOC
        assert _read_doc_text(hazards, "README.md").startswith("# Substrate")


def test_a_hazard_free_scan_gains_no_skips_and_no_notes(tmp_path, db_session):
    """A hazard-free scan gains no skip records or partial notes."""
    safe = str(build_substrate_repo(tmp_path))
    with _deadline():
        out, streams, _ = _run(db_session, safe, client=SubstrateClient())

    assert out["partial_notes"] == []
    assert all(p.get("status") != "skipped" for p in streams["agent_coverage"])
    assert all("doc_truncated" not in p for p in streams["agent_coverage"])
