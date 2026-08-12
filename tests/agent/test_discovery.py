"""Exercise discovery parsing, prompt layout, coverage, and failure accounting."""

import json

from drift.agent.discovery import DiscoveryAgent

# The emitted inventory arrives as arguments to the strict `emit_result` tool call.
from tests.agent.test_runner import (
    _Block,
    _emit,
    _Resp,
    _ScriptedClient,
    _Usage,
)


def test_discover_parses_and_normalizes(tmp_path):
    """Parse emitted claims and normalize every mechanically bound check."""
    (tmp_path / "CLAUDE.md").write_text("read architecture/ARCH.md and ./notes.md; run make lint")
    inventory = {
        "claims": [
            {
                "literal": "architecture/ARCH.md",
                "predicate": "path_exists",
                "spans": [[1, 1]],
                "claim_class": 1,
                "note": "live ref",
                "confidence": 0.9,
            },
            {
                "literal": "./notes.md",
                "predicate": "path_exists",
                "spans": [[1, 1]],
                "claim_class": 1,
                "note": "live ref",
                "confidence": 0.8,
            },
            {
                "literal": "make lint",
                "predicate": "none",
                "spans": [[1, 1]],
                "claim_class": 3,
                "note": "command claim, no predicate",
                "confidence": 0.5,
            },
        ]
    }
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="glob", input={"pattern": "*.md"})]),
            _Resp([_Block("text", text="ok")]),
            _emit(inventory),
        ]
    )
    result = DiscoveryAgent(client).discover(str(tmp_path), "CLAUDE.md")
    bound = [c for c in result.claims if c.check is not None]
    unbound = [c for c in result.claims if c.check is None]
    assert len(bound) == 2 and len(unbound) == 1
    assert bound[0].check.normalized_args == ("architecture/ARCH.md",)
    assert bound[1].check.normalization == {"base": "doc-relative"}
    assert bound[0].check.raw == {
        "literal": "architecture/ARCH.md",
        "doc_path": "CLAUDE.md",
        "proposed_args": [],
    }
    assert unbound[0].claim_class == 3
    assert result.coverage["status"] == "complete" and result.coverage["tool_calls"] == 1
    assert "doc_hash" in result.coverage
    # Expose the registered predicate vocabulary in the discovery prompt.
    first_call = client.calls[0]
    assert "path_exists" in json.dumps(first_call["messages"][0]["content"])


def test_discover_escaping_doc_path_fails_closed(tmp_path):
    """Fail closed when a document path escapes the repository."""
    result = DiscoveryAgent(_ScriptedClient([])).discover(str(tmp_path), "../outside.md")
    assert result.claims == [] and result.coverage["status"] == "failed"


def test_discover_missing_doc_fails_closed(tmp_path):
    """Fail closed when the requested document is absent."""
    result = DiscoveryAgent(_ScriptedClient([])).discover(str(tmp_path), "gone.md")
    assert result.claims == [] and result.coverage["status"] == "failed"


def test_discover_coerces_class3_to_class1_when_the_claim_binds(tmp_path):
    """Classify a successfully bound claim as mechanically checkable."""
    (tmp_path / "CLAUDE.md").write_text("read architecture/ARCH.md")
    inventory = {
        "claims": [
            {
                "literal": "architecture/ARCH.md",
                "predicate": "path_exists",
                "spans": [[1, 1]],
                "claim_class": 3,
                "note": "model called it unbindable but it binds",
                "confidence": 0.9,
            },
        ]
    }
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="ok")]),
            _emit(inventory),
        ]
    )
    result = DiscoveryAgent(client).discover(str(tmp_path), "CLAUDE.md")
    [claim] = result.claims
    assert claim.check is not None
    assert claim.claim_class == 1


def test_prompt_is_shared_first_two_blocks_with_cache_breakpoint(tmp_path):
    """Keep the reusable prompt prefix separate from document-specific content."""
    (tmp_path / "CLAUDE.md").write_text("read architecture/ARCH.md")
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="ok")]),
            _emit({"claims": []}),
        ]
    )
    DiscoveryAgent(client).discover(str(tmp_path), "CLAUDE.md")
    blocks = client.calls[0]["messages"][0]["content"]
    assert isinstance(blocks, list) and len(blocks) == 2
    shared, specific = blocks
    assert shared["cache_control"] == {"type": "ephemeral"}
    assert "path_exists" in shared["text"] and "Repo map:" in shared["text"]
    # Document content and its path header must trail the reusable prefix.
    assert "read architecture/ARCH.md" not in shared["text"]
    assert "Document path:" not in shared["text"]
    assert "Document path: CLAUDE.md" in specific["text"]
    assert "read architecture/ARCH.md" in specific["text"]


def test_coverage_carries_usage(tmp_path):
    """Expose every token-usage counter in discovery coverage."""
    (tmp_path / "CLAUDE.md").write_text("hello")
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="ok")]),
            _emit({"claims": []}),
        ]
    )
    result = DiscoveryAgent(client).discover(str(tmp_path), "CLAUDE.md")
    assert set(result.coverage["usage"]) == {
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }


def test_coverage_carries_the_tool_trace(tmp_path):
    """Record each tool call, its arguments, and the extent of its result."""
    (tmp_path / "CLAUDE.md").write_text("hello")
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="read_file", input={"path": "CLAUDE.md"})]),
            _Resp([_Block("text", text="ok")]),
            _emit({"claims": []}),
        ]
    )
    result = DiscoveryAgent(client).discover(str(tmp_path), "CLAUDE.md")
    assert result.coverage["tool_trace"] == [
        {
            "tool": "read_file",
            "args": {"path": "CLAUDE.md"},
            "returned_chars": 5,
            "truncated": False,
            "total_chars": 5,
        }
    ]


def test_discover_keeps_the_usage_billed_before_a_mid_loop_crash(tmp_path):
    """Retain billed usage and tool coverage when the model loop fails mid-unit."""
    (tmp_path / "CLAUDE.md").write_text("read architecture/ARCH.md")
    first = _Resp([_Block("tool_use", id="t1", name="glob", input={"pattern": "*.md"})])
    first.usage = _Usage(
        input_tokens=1200,
        output_tokens=300,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )

    class _DiesAfterOneCall:
        """Fail after returning one billed tool-call response."""

        def __init__(self):
            """Seed the client with one successful response."""
            self._responses = [first]
            self.messages = self
            self.calls = []

        def create(self, **kwargs):
            """Return the first response and fail every later request."""
            self.calls.append(kwargs)
            if not self._responses:
                raise RuntimeError("connection reset by peer")
            return self._responses.pop(0)

    result = DiscoveryAgent(_DiesAfterOneCall()).discover(str(tmp_path), "CLAUDE.md")

    assert result.claims == []
    assert result.coverage["status"] == "error"
    assert "connection reset by peer" in result.coverage["detail"]
    assert result.coverage["usage"]["input_tokens"] == 1200
    assert result.coverage["usage"]["output_tokens"] == 300
    assert result.coverage["doc_hash"]
    # Failed units retain the same tool-result extent as completed units.
    assert result.coverage["tool_trace"] == [
        {
            "tool": "glob",
            "args": {"pattern": "*.md"},
            "returned_chars": len("CLAUDE.md"),
            "truncated": False,
            "total_chars": len("CLAUDE.md"),
        }
    ]


def test_coverage_states_the_size_of_the_doc_it_was_given(tmp_path):
    """Record the character count of the complete document injected into the prompt."""
    (tmp_path / "CLAUDE.md").write_text("héllo wörld")
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="ok")]),
            _emit({"claims": []}),
        ]
    )
    result = DiscoveryAgent(client).discover(str(tmp_path), "CLAUDE.md")
    assert result.coverage["doc_chars"] == len("héllo wörld")
