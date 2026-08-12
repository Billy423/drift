"""The model tool loop: one request cycle with a tool budget and a structured final answer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from drift.agent.toolbelt import ToolSpec

__all__ = [
    "EMIT_TOOL_DESCRIPTION",
    "EMIT_TOOL_NAME",
    "LoopFailure",
    "LoopResult",
    "ToolSpec",
    "run_loop",
]

_EXTENT_KEYS = ("returned_chars", "truncated", "total_chars")

_CACHE_MARK = {"type": "ephemeral"}

_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


@dataclass(frozen=True)
class LoopResult:
    """What one loop run produced: its terminal payload, coverage counters and exit status."""

    payload: dict | None
    turns_used: int
    tool_calls: int
    status: str  # "complete" | "truncated" | "invalid"
    usage: dict = field(default_factory=dict)  # summed across every model call in the loop
    # One entry per executed tool call, in order: tool name and truncated arguments.
    tool_trace: list = field(default_factory=list)


class LoopFailure(RuntimeError):
    """A loop that died mid-flight, carrying the token usage already billed before the crash.

    `usage`, `turns_used` and `tool_calls` ride out with the exception so that a caller can bill
    a failed loop and record what it actually did before it died.
    """

    def __init__(
        self,
        cause: BaseException,
        usage: dict,
        turns_used: int = 0,
        tool_calls: int = 0,
        tool_trace: list | None = None,
    ) -> None:
        super().__init__(f"agent loop failed after billed usage: {cause!r}")
        self.cause = cause
        self.usage = usage
        self.turns_used = turns_used
        self.tool_calls = tool_calls
        self.tool_trace = list(tool_trace or [])


class _LoopState(TypedDict):
    """The loop's channel state.

    Routers must decide from these values: LangGraph does not write back in-place changes to
    the dict it hands a conditional-edge function.
    """

    messages: list
    turns: int
    tool_calls: int
    wants_tool_use: bool
    pending_tool_uses: list
    # Terminal signal. Not `payload is not None`: a truncated emit is payload-less and terminal.
    emit_taken: bool
    payload: dict | None
    status: str
    usage: dict


def _usage_of(response) -> dict:
    """Token usage of one response as a plain dict; zeros when the client reports none."""
    usage = getattr(response, "usage", None)
    return {f: int(getattr(usage, f, 0) or 0) for f in _USAGE_FIELDS}


_TRACE_VALUE_MAX = 200
_TRACE_MAX_ENTRIES = 32


def _trace_cap(s: str) -> str:
    return s if len(s) <= _TRACE_VALUE_MAX else s[:_TRACE_VALUE_MAX] + "…[truncated]"


def _trace_args(tool_input) -> dict:
    """A tool call's arguments as journal-safe strings.

    Keys and the entry count are capped as well as values: the model's input is untrusted
    repository text, and an unbounded argument must not reach the journal payload.
    """
    if not isinstance(tool_input, dict):
        return {}
    out = {}
    for k, v in list(tool_input.items())[:_TRACE_MAX_ENTRIES]:
        s = v if isinstance(v, str) else repr(v)
        out[_trace_cap(str(k))] = _trace_cap(s)
    return out


def _add_usage(total: dict, delta: dict) -> dict:
    return {f: total.get(f, 0) + delta.get(f, 0) for f in _USAGE_FIELDS}


def _bill(running: dict, delta: dict) -> dict:
    """Add `delta` into the crash-surviving `running` mirror in place, and return `delta`."""
    for f in _USAGE_FIELDS:
        running[f] += delta.get(f, 0)
    return delta


def _mark_last_user_block(messages: list) -> list:
    """A copy of `messages` with a cache breakpoint on the last user block, for one request.

    The stored history is never marked: breakpoints would accumulate, and the API allows only
    four per request. Each call thus reads the previous turn's cached prefix and extends it by one.
    """
    if not messages or messages[-1].get("role") != "user":
        return messages
    last = messages[-1]
    content = last["content"]
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content, "cache_control": dict(_CACHE_MARK)}]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        blocks = list(content[:-1]) + [{**content[-1], "cache_control": dict(_CACHE_MARK)}]
    else:
        return messages
    return messages[:-1] + [{**last, "content": blocks}]


def _tool_uses(response) -> list:
    return [b for b in response.content if getattr(b, "type", None) == "tool_use"]


#: Model-facing text, hashed into the discovery producer's fingerprint and the judge's:
#: editing either value re-baselines both. Exported for that, unlike `_EMIT_INSTRUCTION` below.
EMIT_TOOL_NAME = "emit_result"
EMIT_TOOL_DESCRIPTION = (
    "Submit your final structured result. Call this exactly once, when your "
    "work is complete — its arguments ARE your final output."
)
# `tool_choice` is deliberately unset: changing it between requests invalidates the
# message-tier prompt cache.
_EMIT_INSTRUCTION = f"Call the `{EMIT_TOOL_NAME}` tool now with your final structured output."


def _emit_tool(output_schema: dict) -> dict:
    """The caller's output schema, made callable as a strict tool.

    `strict: true` guarantees that `tool_use.input` validates against the schema exactly. The
    tool has no local implementation and is never added to the belt the loop executes from.

    Raises:
        ValueError: If the schema's top level lacks `required` or `additionalProperties: false`.
            Strict compilation needs both at every object level; checking only the top level
            catches the whole-schema mistake without duplicating the API's own compiler, and
            checking at construction time costs an error rather than a rejected first request.
    """
    if output_schema.get("additionalProperties") is not False or "required" not in output_schema:
        raise ValueError(
            "output_schema is not usable as a strict tool schema: it must carry "
            "`required` and `additionalProperties: false` at every object level "
            "(the top level is missing one of them)"
        )
    return {
        "name": EMIT_TOOL_NAME,
        "description": EMIT_TOOL_DESCRIPTION,
        "input_schema": output_schema,
        "strict": True,
    }


def _emit_call(response):
    """The response's emit `tool_use` block, or None."""
    for tu in _tool_uses(response):
        if getattr(tu, "name", None) == EMIT_TOOL_NAME:
            return tu
    return None


def _emit_payload(response, tool_use) -> dict | None:
    """A taken emit call's arguments, or None when the response was cut at `max_tokens`.

    Truncation is the only available signal. Strict validation covers a call that completed, and
    the arguments arrive deserialized, so a partial object has no parse step left to fail on.
    """
    if getattr(response, "stop_reason", None) == "max_tokens":
        return None
    value = getattr(tool_use, "input", None)
    return value if isinstance(value, dict) else None


def run_loop(client, model, system, tools, user_content, output_schema, budget) -> LoopResult:
    """Run one tool loop against `client` and return its payload, counters and exit status.

    The model may take the emit tool on any turn, and doing so ends the loop, so a model that
    finishes early never pays for a separate emit turn. `budget` bounds tool calls, not turns.

    Args:
        system: Cached as one block together with `tools`, so every loop sending the same pair
            shares the cached prefix.
        user_content: A string, or a list of content blocks. A caller whose prefix is shared
            across loops puts it in a leading block carrying its own `cache_control`.
        output_schema: Becomes a strict emit tool, so it must carry `required` and
            `additionalProperties: false` at every object level.
        budget: The most tool calls the loop may execute.

    Returns:
        The emit call's arguments with `status="complete"`; `"truncated"` when the budget ran out
        with the model still asking for tools, and `"invalid"` when no usable payload arrived.
        `usage` is summed over every request the loop made.

    Raises:
        ValueError: If `budget < 1`. A turn could then ask for tools that are never executed,
            leaving a `tool_use` block with no matching `tool_result` — a conversation the API
            rejects, better caught here than as a request error.
        LoopFailure: If a request fails mid-loop, carrying the usage already billed.
    """
    if budget < 1:
        raise ValueError("budget must be >= 1")
    # LangGraph discards channel state when a node raises, so the in-state tally is lost exactly
    # when calls have already been billed. These three mirrors ride out on `LoopFailure`.
    billed = dict.fromkeys(_USAGE_FIELDS, 0)
    worked = {"turns": 0, "tool_calls": 0}
    trace: list[dict] = []
    by_name = {t.name: t for t in tools}
    # Appended to the request only, never to `by_name`: the emit tool must never execute.
    sdk_tools = [t.to_sdk() for t in tools] + [_emit_tool(output_schema)]
    # Tools render before system, so one breakpoint caches both. Every turn must send the
    # identical toolset, or the cache is invalidated at the prompt's very first block.
    system_blocks = [{"type": "text", "text": system, "cache_control": dict(_CACHE_MARK)}]

    def agent_model(state: _LoopState) -> dict:
        kwargs = dict(
            model=model,
            # Loop turns carry the emit turn's ceiling: any turn can be the one that emits,
            # and a lower cap here would cut a payload the emit node would have fitted.
            max_tokens=16384,
            thinking={"type": "adaptive"},
            system=system_blocks,
            messages=_mark_last_user_block(state["messages"]),
            tools=sdk_tools,
        )
        response = client.messages.create(**kwargs)
        delta = _bill(billed, _usage_of(response))
        worked["turns"] += 1
        tool_uses = _tool_uses(response)
        out = {
            "messages": state["messages"] + [{"role": "assistant", "content": response.content}],
            "turns": state["turns"] + 1,
            "wants_tool_use": bool(tool_uses),
            "pending_tool_uses": tool_uses,
            "usage": _add_usage(state["usage"], delta),
        }
        emit_use = _emit_call(response)
        if emit_use is not None:
            # Terminal. Sibling `tool_use` blocks are dropped unexecuted: no follow-up request
            # is sent, so they never reach the API unpaired with a `tool_result`.
            payload = _emit_payload(response, emit_use)
            out["emit_taken"] = True
            out["payload"] = payload
            out["status"] = "complete" if payload is not None else "invalid"
            out["wants_tool_use"] = False
            out["pending_tool_uses"] = []
        return out

    def agent_tools(state: _LoopState) -> dict:
        # One response can carry more `tool_use` blocks than the budget allows, so the rest get
        # a synthesized result: every `tool_use` id must be answered or the API rejects it.
        remaining = max(budget - state["tool_calls"], 0)
        pending = state["pending_tool_uses"]
        to_run, to_skip = pending[:remaining], pending[remaining:]
        results = []
        for tu in to_run:
            # Recorded before the lookup, so an unknown or hostile tool name still reaches
            # the trace — capped, but verbatim.
            entry = {
                "tool": _trace_cap(str(tu.name)),
                "args": _trace_args(getattr(tu, "input", None)),
            }
            trace.append(entry)
            spec = by_name.get(tu.name)
            # The toolbelt records its own extent, so read the entry it appended for this call
            # rather than parsing the truncation marker back out of the result string.
            ledger = spec.ledger if spec is not None else None
            before = len(ledger) if ledger is not None else 0
            failed = False
            if spec is None:
                failed = True
                out = f"error: unknown tool {tu.name}"
            else:
                try:
                    out = spec.fn(**tu.input)
                except Exception as exc:  # the arguments are model-chosen
                    failed = True
                    out = f"error: {exc!r}"
            recorded = ledger[before:] if ledger is not None else []
            if recorded:
                # Exactly these keys: a ledger entry must not overwrite the capped `tool` and
                # `args`, nor add an unbounded field to the journal payload.
                entry.update({k: recorded[-1].get(k) for k in _EXTENT_KEYS})
            else:
                # An error string counts as zero content, as the toolbelt counts it, so that
                # `returned_chars` means the same thing on every entry.
                entry.update(
                    {
                        "returned_chars": 0 if failed else len(out),
                        "truncated": False,
                        "total_chars": None,
                    }
                )
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})
        for tu in to_skip:
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": "skipped: budget exhausted",
                }
            )
        worked["tool_calls"] += len(to_run)
        return {
            "messages": state["messages"] + [{"role": "user", "content": results}],
            "tool_calls": state["tool_calls"] + len(to_run),
            "pending_tool_uses": [],
        }

    def route(state: _LoopState) -> str:
        if state["emit_taken"]:
            return END
        if state["wants_tool_use"] and state["tool_calls"] < budget:
            return "agent_tools"
        return "emit"

    def route_after_tools(state: _LoopState) -> str:
        # Defensive: `agent_tools` cannot set `emit_taken`. Kept so the loop has one exit rule.
        if state["emit_taken"]:
            return END
        # Budget already gone: skip the model call `route` would only send to emit.
        return "emit" if state["tool_calls"] >= budget else "agent_model"

    def emit(state: _LoopState) -> dict:
        # Truncated means the budget ran out with the model still asking for tools — a fact
        # about the loop, not about how this final turn went.
        truncated = state["wants_tool_use"] and state["tool_calls"] >= budget
        messages = state["messages"] + [{"role": "user", "content": _EMIT_INSTRUCTION}]
        response = client.messages.create(
            model=model,
            # Must match the loop turns' ceiling; an emit cut short here yields no payload.
            max_tokens=16384,
            # The loop turns' shape, held deliberately: switching thinking on or off, or
            # sending a different toolset, invalidates the message-tier cache.
            thinking={"type": "adaptive"},
            system=system_blocks,
            messages=_mark_last_user_block(messages),
            tools=sdk_tools,
        )
        delta = _bill(billed, _usage_of(response))
        emit_use = _emit_call(response)
        payload = _emit_payload(response, emit_use) if emit_use is not None else None
        status = "truncated" if truncated else ("invalid" if payload is None else "complete")
        return {
            "payload": payload,
            "status": status,
            "usage": _add_usage(state["usage"], delta),
        }

    graph = StateGraph(_LoopState)
    graph.add_node("agent_model", agent_model)
    graph.add_node("agent_tools", agent_tools)
    graph.add_node("emit", emit)
    graph.add_edge(START, "agent_model")
    graph.add_conditional_edges(
        "agent_model", route, {"agent_tools": "agent_tools", "emit": "emit", END: END}
    )
    graph.add_conditional_edges(
        "agent_tools", route_after_tools, {"agent_model": "agent_model", "emit": "emit", END: END}
    )
    graph.add_edge("emit", END)
    compiled = graph.compile()

    try:
        final = compiled.invoke(
            {
                "messages": [{"role": "user", "content": user_content}],
                "turns": 0,
                "tool_calls": 0,
                "wants_tool_use": False,
                "pending_tool_uses": [],
                "emit_taken": False,
                "payload": None,
                "status": "invalid",
                "usage": dict.fromkeys(_USAGE_FIELDS, 0),
            }
        )
    except Exception as exc:
        raise LoopFailure(exc, dict(billed), worked["turns"], worked["tool_calls"], trace) from exc
    return LoopResult(
        payload=final["payload"],
        turns_used=final["turns"],
        tool_calls=final["tool_calls"],
        status=final["status"],
        usage=final["usage"],
        tool_trace=trace,
    )
