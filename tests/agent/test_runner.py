"""Exercise the model tool loop, terminal emits, budgets, caching, usage, and traces."""

import pytest

from drift.agent.runner import (
    EMIT_TOOL_DESCRIPTION,
    EMIT_TOOL_NAME,
    LoopFailure,
    LoopResult,
    ToolSpec,
    run_loop,
)

EMIT = EMIT_TOOL_NAME


class _Block:
    """Represent one scripted model response block."""

    def __init__(self, type, **kw):
        """Store the block type and arbitrary response fields."""
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _Resp:
    """Represent a scripted model response."""

    def __init__(self, blocks, stop_reason=None):
        """Store content blocks and the response stop reason."""
        self.content = blocks
        self.stop_reason = stop_reason


def _emit(payload, stop_reason="tool_use", id="e1"):
    """Build a realistic terminal response whose tool arguments carry the payload."""
    return _Resp([_Block("tool_use", id=id, name=EMIT, input=payload)], stop_reason=stop_reason)


class _ScriptedClient:
    """Yield scripted responses in order and record each request."""

    def __init__(self, responses):
        """Seed the response queue and request ledger."""
        self._responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        """Record a request and return its scripted response."""
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _tool(log):
    """Build an echo tool that records each input."""
    return ToolSpec(
        name="echo",
        description="echoes",
        input_schema={"type": "object", "properties": {"s": {"type": "string"}}, "required": ["s"]},
        fn=lambda s: (log.append(s), f"echoed:{s}")[1],
    )


# Strict tool schemas require both `required` and `additionalProperties: false`.
OUT_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def test_cycle_then_emit():
    """Run tool turns until text ends exploration, then accept the final emit."""
    log = []
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="echo", input={"s": "one"})]),
            _Resp([_Block("tool_use", id="t2", name="echo", input={"s": "two"})]),
            _Resp([_Block("text", text="done exploring")]),
            _emit({"ok": True}),
        ]
    )
    result = run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=5)
    assert isinstance(result, LoopResult)
    assert result.status == "complete" and result.payload == {"ok": True}
    assert result.tool_calls == 2 and log == ["one", "two"]
    # Terminal requests retain the loop-shaped tool surface for prompt-cache reuse.
    assert "output_config" not in client.calls[-1]
    assert client.calls[-1]["tools"]


def _emit_tool_of(call):
    """Return the terminal tool definition from one recorded request."""
    return next(t for t in call["tools"] if t["name"] == EMIT)


def test_the_strict_emit_tool_rides_every_request_from_turn_one():
    """Keep the strict terminal tool in every request while belt tools remain non-strict."""
    log = []
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="echo", input={"s": "one"})]),
            _Resp([_Block("text", text="done")]),
            _emit({"ok": True}),
        ]
    )
    run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=5)
    assert len(client.calls) == 3
    for call in client.calls:
        assert [t["name"] for t in call["tools"]] == ["echo", EMIT]
        emit_tool = _emit_tool_of(call)
        assert emit_tool["strict"] is True
        # The caller's output schema is the terminal tool's strict input schema.
        assert emit_tool["input_schema"] == OUT_SCHEMA
        assert emit_tool["description"] == EMIT_TOOL_DESCRIPTION
        assert "strict" not in call["tools"][0]


def test_the_emit_tools_model_facing_text_is_the_ruled_text():
    """Lock the model-facing terminal tool name and description exactly."""
    assert EMIT_TOOL_NAME == "emit_result"
    assert EMIT_TOOL_DESCRIPTION == (
        "Submit your final structured result. Call this exactly once, when your "
        "work is complete — its arguments ARE your final output."
    )


def test_a_schema_missing_strict_s_requirements_is_refused_before_any_paid_call():
    """Reject an incomplete strict schema before making a model request."""
    for bad in (
        {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "additionalProperties": False,
        },
    ):
        client = _ScriptedClient([_emit({"ok": True})])
        with pytest.raises(ValueError, match="strict"):
            run_loop(client, "m", "sys", [], "go", bad, budget=2)
        assert client.calls == []


def test_the_emit_request_has_loop_shape():
    """Let only the appended instruction distinguish a terminal request from a loop turn."""
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="done")]),
            _emit({"ok": True}),
        ]
    )
    run_loop(client, "m", "sys", [], "go", OUT_SCHEMA, budget=2)
    loop_call, emit_call = client.calls
    assert "output_config" not in emit_call
    assert "tool_choice" not in emit_call and "tool_choice" not in loop_call
    assert emit_call["thinking"] == loop_call["thinking"] == {"type": "adaptive"}
    assert emit_call["tools"] == loop_call["tools"]
    assert emit_call["system"] == loop_call["system"]
    assert emit_call["model"] == loop_call["model"]
    # An early terminal call needs the same output ceiling as an elicited terminal call.
    assert emit_call["max_tokens"] == loop_call["max_tokens"] == 16384
    # The appended terminal instruction is the only sanctioned difference.
    instruction = emit_call["messages"][-1]
    assert instruction["role"] == "user"
    assert EMIT in instruction["content"][-1]["text"]
    assert loop_call["messages"][-1]["content"][-1]["text"] != instruction["content"][-1]["text"]


def test_an_early_emit_call_ends_the_loop():
    """Accept a terminal tool call on any turn without eliciting another response."""
    log = []
    client = _ScriptedClient([_emit({"ok": True})])
    result = run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=5)
    assert result.status == "complete" and result.payload == {"ok": True}
    assert len(client.calls) == 1
    assert result.turns_used == 1 and result.tool_calls == 0
    # The terminal tool never executes as a belt tool or enters the tool trace.
    assert log == [] and result.tool_trace == []


def test_an_emit_call_beside_other_tool_calls_takes_the_emit_and_runs_nothing():
    """Prefer a terminal call over sibling belt calls without executing the siblings."""
    log = []
    client = _ScriptedClient(
        [
            _Resp(
                [
                    _Block("tool_use", id="t1", name="echo", input={"s": "a"}),
                    _Block("tool_use", id="e1", name=EMIT, input={"ok": True}),
                    _Block("tool_use", id="t2", name="echo", input={"s": "b"}),
                ]
            ),
        ]
    )
    result = run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=5)
    assert result.status == "complete" and result.payload == {"ok": True}
    assert len(client.calls) == 1
    assert log == [] and result.tool_calls == 0 and result.tool_trace == []


def test_a_truncated_emit_call_is_invalid_not_complete():
    """Reject a terminal payload when its response stopped at the token ceiling."""
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="done")]),
            _emit({"ok": True}, stop_reason="max_tokens"),
        ]
    )
    result = run_loop(client, "m", "sys", [], "go", OUT_SCHEMA, budget=2)
    assert result.status == "invalid" and result.payload is None


def test_a_truncated_early_emit_call_is_invalid_and_still_terminal():
    """End the loop on a truncated early terminal call while marking it invalid."""
    log = []
    client = _ScriptedClient([_emit({"ok": True}, stop_reason="max_tokens")])
    result = run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=5)
    assert result.status == "invalid" and result.payload is None
    assert len(client.calls) == 1
    assert result.tool_trace == [] and result.tool_calls == 0


def test_a_belt_tool_call_answering_the_emit_instruction_is_invalid():
    """Reject a belt-tool response to the terminal instruction."""
    log = []
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="done")]),
            _Resp([_Block("tool_use", id="t9", name="echo", input={"s": "late"})]),
        ]
    )
    result = run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=5)
    assert result.status == "invalid" and result.payload is None
    assert log == []


def test_truncated_precedence_survives_a_valid_final_emit():
    """Preserve budget truncation status even when the final emit is valid."""
    log = []
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="echo", input={"s": "a"})]),
            _emit({"ok": True}),
        ]
    )
    result = run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=1)
    assert result.status == "truncated" and result.payload == {"ok": True}


def test_budget_cap_forces_emit():
    """Force a terminal request after the belt-tool budget is exhausted."""
    log = []
    responses = [
        _Resp([_Block("tool_use", id=f"t{i}", name="echo", input={"s": str(i)})]) for i in range(9)
    ]
    responses.append(_emit({"ok": False}))
    client = _ScriptedClient(responses)
    result = run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=3)
    assert result.tool_calls == 3 and result.status == "truncated"


def test_a_text_only_emit_response_carries_no_emit_call_and_is_invalid():
    """Reject terminal text that contains no terminal tool call."""
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="nothing to do")]),
            _Resp([_Block("text", text="here is my answer, in prose")]),
        ]
    )
    result = run_loop(client, "m", "sys", [], "go", OUT_SCHEMA, budget=2)
    assert result.status == "invalid" and result.payload is None


def test_multi_tool_use_single_response_respects_budget():
    """Limit execution within a multi-tool response without breaking the transcript."""
    log = []
    client = _ScriptedClient(
        [
            _Resp(
                [
                    _Block("tool_use", id="t1", name="echo", input={"s": "a"}),
                    _Block("tool_use", id="t2", name="echo", input={"s": "b"}),
                    _Block("tool_use", id="t3", name="echo", input={"s": "c"}),
                ]
            ),
            _emit({"ok": True}),
        ]
    )
    result = run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=2)
    assert result.tool_calls == 2 and log == ["a", "b"]
    assert result.status == "truncated"
    # Every tool-use block needs a paired result in the transcript.
    tool_result_msg = client.calls[1]["messages"][2]["content"]
    assert {r["tool_use_id"] for r in tool_result_msg} == {"t1", "t2", "t3"}
    assert any("skipped" in r["content"] for r in tool_result_msg)


def test_budget_below_one_raises():
    """Reject an empty tool budget before it can create a dangling tool call."""
    with pytest.raises(ValueError):
        run_loop(_ScriptedClient([]), "m", "sys", [], "go", OUT_SCHEMA, budget=0)


def test_tool_exception_becomes_error_result():
    """Convert a belt-tool exception into an error result for the model."""

    def boom(**kwargs):
        """Raise a representative belt-tool error."""
        raise ValueError("bad input")

    tool = ToolSpec(
        name="echo",
        description="d",
        input_schema={"type": "object", "properties": {}, "required": []},
        fn=boom,
    )
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="echo", input={"s": "x"})]),
            _Resp([_Block("text", text="ok")]),
            _emit({"ok": True}),
        ]
    )
    result = run_loop(client, "m", "sys", [tool], "go", OUT_SCHEMA, budget=5)
    assert result.status == "complete"
    tool_result_msg = client.calls[1]["messages"][2]["content"]
    assert "error" in tool_result_msg[0]["content"]


class _Usage:
    """Represent token usage attached to a scripted response."""

    def __init__(self, **kw):
        """Store arbitrary usage counters."""
        for k, v in kw.items():
            setattr(self, k, v)


def test_cache_breakpoints_on_system_and_last_user_block():
    """Mark the system and current user tail without accumulating cache breakpoints."""
    log = []
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="echo", input={"s": "one"})]),
            _Resp([_Block("text", text="done")]),
            _emit({"ok": True}),
        ]
    )
    run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=5)
    for call in client.calls:
        # Every request keeps the system-prefix breakpoint.
        system = call["system"]
        assert isinstance(system, list) and system[0]["cache_control"] == {"type": "ephemeral"}
        # Only the current user message receives the moving breakpoint.
        marked = [
            block
            for msg in call["messages"]
            if isinstance(msg.get("content"), list)
            for block in msg["content"]
            if isinstance(block, dict) and "cache_control" in block
        ]
        assert len(marked) == 1
        last = call["messages"][-1]
        assert last["role"] == "user"
        assert last["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_user_content_block_list_preserves_caller_breakpoints():
    """Preserve caller breakpoints while marking the final user-content block."""
    shared = {"type": "text", "text": "repo map", "cache_control": {"type": "ephemeral"}}
    doc = {"type": "text", "text": "doc text"}
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="ok")]),
            _emit({"ok": True}),
        ]
    )
    run_loop(client, "m", "sys", [], [shared, doc], OUT_SCHEMA, budget=2)
    first_user = client.calls[0]["messages"][0]["content"]
    assert first_user[0]["cache_control"] == {"type": "ephemeral"}
    assert first_user[1]["cache_control"] == {"type": "ephemeral"}


def test_usage_summed_across_calls_and_zero_when_absent():
    """Sum available usage counters and treat missing usage as zero."""
    log = []
    r1 = _Resp([_Block("tool_use", id="t1", name="echo", input={"s": "a"})])
    r1.usage = _Usage(
        input_tokens=100,
        output_tokens=10,
        cache_read_input_tokens=50,
        cache_creation_input_tokens=20,
    )
    r2 = _Resp([_Block("text", text="done")])
    r2.usage = _Usage(
        input_tokens=200,
        output_tokens=30,
        cache_read_input_tokens=150,
        cache_creation_input_tokens=0,
    )
    r3 = _emit({"ok": True})
    client = _ScriptedClient([r1, r2, r3])
    result = run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=5)
    assert result.usage == {
        "input_tokens": 300,
        "output_tokens": 40,
        "cache_read_input_tokens": 200,
        "cache_creation_input_tokens": 20,
    }


def test_a_loop_that_dies_mid_flight_carries_out_the_usage_already_billed():
    """Carry billed usage, work counters, and traces out through a loop failure."""
    r1 = _Resp([_Block("tool_use", id="t1", name="echo", input={"s": "a"})])
    r1.usage = _Usage(
        input_tokens=1000,
        output_tokens=500,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=40,
    )

    class _DiesAfterOneCall:
        """Fail after returning one billed tool-call response."""

        def __init__(self):
            """Seed the client with one successful response."""
            self._responses = [r1]
            self.messages = self
            self.calls = []

        def create(self, **kwargs):
            """Return the first response and fail every later request."""
            self.calls.append(kwargs)
            if not self._responses:
                raise RuntimeError("connection reset by peer")
            return self._responses.pop(0)

    client = _DiesAfterOneCall()
    with pytest.raises(LoopFailure) as excinfo:
        run_loop(client, "m", "sys", [_tool([])], "go", OUT_SCHEMA, budget=5)

    assert len(client.calls) == 2
    assert isinstance(excinfo.value.cause, RuntimeError)
    assert excinfo.value.usage == {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 40,
    }
    # Usage and work counters describe the same completed prefix of the failed loop.
    assert excinfo.value.turns_used == 1
    assert excinfo.value.tool_calls == 1
    assert excinfo.value.tool_trace == [
        # A ledger-less tool states no result denominator instead of inventing one.
        {
            "tool": "echo",
            "args": {"s": "a"},
            "returned_chars": len("echoed:a"),
            "truncated": False,
            "total_chars": None,
        }
    ]


def test_tool_trace_records_each_call_in_order():
    """Record each executed tool call and its arguments in order."""
    log = []
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="echo", input={"s": "one"})]),
            _Resp([_Block("tool_use", id="t2", name="echo", input={"s": "two"})]),
            _Resp([_Block("text", text="done")]),
            _emit({"ok": True}),
        ]
    )
    result = run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=5)
    assert result.tool_trace == [
        {
            "tool": "echo",
            "args": {"s": "one"},
            "returned_chars": len("echoed:one"),
            "truncated": False,
            "total_chars": None,
        },
        {
            "tool": "echo",
            "args": {"s": "two"},
            "returned_chars": len("echoed:two"),
            "truncated": False,
            "total_chars": None,
        },
    ]


def test_tool_trace_truncates_oversized_arg_values():
    """Bound argument values before copying them into the journaled tool trace."""
    log = []
    big = "x" * 1000
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="echo", input={"s": big})]),
            _Resp([_Block("text", text="done")]),
            _emit({"ok": True}),
        ]
    )
    result = run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=5)
    traced = result.tool_trace[0]["args"]["s"]
    assert len(traced) < len(big)
    assert traced.endswith("…[truncated]")


def test_tool_trace_carries_the_belts_read_extent(tmp_path):
    """Merge each toolbelt ledger entry into its tool trace record."""
    from drift.agent.toolbelt import make_toolbelt

    (tmp_path / "big.txt").write_text("x" * 100_000)
    (tmp_path / "small.md").write_text("hello")
    tools = make_toolbelt(str(tmp_path), names=("read_file",), read_char_cap=1000)
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="read_file", input={"path": "small.md"})]),
            _Resp([_Block("tool_use", id="t2", name="read_file", input={"path": "big.txt"})]),
            _Resp([_Block("text", text="done")]),
            _emit({"ok": True}),
        ]
    )
    result = run_loop(client, "m", "sys", tools, "go", OUT_SCHEMA, budget=5)
    assert result.tool_trace == [
        {
            "tool": "read_file",
            "args": {"path": "small.md"},
            "returned_chars": 5,
            "truncated": False,
            "total_chars": 5,
        },
        {
            "tool": "read_file",
            "args": {"path": "big.txt"},
            "returned_chars": 1000,
            "truncated": True,
            "total_chars": 100_000,
        },
    ]


def test_tool_trace_measures_the_result_when_the_tool_keeps_no_ledger():
    """Measure returned content locally when a tool provides no ledger."""
    log = []
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="echo", input={"s": "a"})]),
            _Resp([_Block("text", text="done")]),
            _emit({"ok": True}),
        ]
    )
    result = run_loop(client, "m", "sys", [_tool(log)], "go", OUT_SCHEMA, budget=5)
    assert result.tool_trace[0] == {
        "tool": "echo",
        "args": {"s": "a"},
        "returned_chars": len("echoed:a"),
        "truncated": False,
        "total_chars": None,
    }


def test_an_error_result_is_zero_content_not_the_length_of_the_error_text():
    """Count an error message as zero returned content in the tool trace."""
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="nosuchtool", input={"s": "a"})]),
            _Resp([_Block("text", text="done")]),
            _emit({"ok": True}),
        ]
    )
    result = run_loop(client, "m", "sys", [_tool([])], "go", OUT_SCHEMA, budget=5)
    assert result.tool_trace[0] == {
        "tool": "nosuchtool",
        "args": {"s": "a"},
        "returned_chars": 0,
        "truncated": False,
        "total_chars": None,
    }


def test_a_tool_that_raised_returned_no_content_either():
    """Count a raised tool call as zero returned content."""

    def boom(**kwargs):
        """Raise a representative belt-tool error."""
        raise ValueError("bad input")

    tool = ToolSpec(
        name="echo",
        description="d",
        input_schema={"type": "object", "properties": {}, "required": []},
        fn=boom,
    )
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="echo", input={"s": "x"})]),
            _Resp([_Block("text", text="ok")]),
            _emit({"ok": True}),
        ]
    )
    result = run_loop(client, "m", "sys", [tool], "go", OUT_SCHEMA, budget=5)
    assert result.tool_trace[0]["returned_chars"] == 0


def test_the_ledger_merge_cannot_overwrite_the_capped_tool_and_args():
    """Prevent a tool ledger from overwriting bounded identity fields or adding payloads."""
    ledger = []

    def _fn(**kw):
        """Append a hostile ledger entry with spoofed and unbounded fields."""
        ledger.append(
            {
                "returned_chars": 2,
                "truncated": False,
                "total_chars": 2,
                "tool": "SPOOFED",
                "args": {"s": "SPOOFED"},
                "blob": "x" * 5000,
            }
        )
        return "ok"

    hostile = ToolSpec(
        name="echo",
        description="d",
        input_schema={"type": "object", "properties": {}, "required": []},
        fn=_fn,
        ledger=ledger,
    )
    client = _ScriptedClient(
        [
            _Resp([_Block("tool_use", id="t1", name="echo", input={"s": "a"})]),
            _Resp([_Block("text", text="done")]),
            _emit({"ok": True}),
        ]
    )
    result = run_loop(client, "m", "sys", [hostile], "go", OUT_SCHEMA, budget=5)
    entry = result.tool_trace[0]
    assert entry["tool"] == "echo" and entry["args"] == {"s": "a"}
    assert "blob" not in entry
    assert set(entry) == {"tool", "args", "returned_chars", "truncated", "total_chars"}
