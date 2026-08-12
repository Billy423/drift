"""Exercise semantic adjudication, prompt isolation, usage, and judge version locks."""

import pytest

from drift.judge.semantic_judge import (
    S_THRESHOLD,
    JudgeEmitError,
    SemanticJudge,
    SVerdict,
    s_passed,
)
from drift.kernels.models import Anchor, Check, EvClaim, SSlot

# The judge and discovery producer share the strict terminal tool-call carrier.
from tests.agent.test_runner import _Block, _emit, _Resp, _ScriptedClient, _Usage

_MATERIALITY_WORDS = ["material", "worth", "important", "significant", "severity"]


def _claim(note: str = "looks fine to me", confidence: float = 0.9) -> EvClaim:
    """Build a mechanically refuted claim awaiting semantic adjudication."""
    anchor = Anchor(doc_path="README.md", spans=((3, 3), (10, 10)), literal="see `drift.old_fn`")
    check = Check(
        predicate="path_exists",
        raw={"doc_path": "README.md", "literal": "see `drift.old_fn`"},
        normalization={},
        normalized_args=("drift/old_fn.py",),
    )
    return EvClaim(
        anchor=anchor,
        check=check,
        claim_class=1,
        s_slot=SSlot(note=note, confidence=confidence),
        provenance={"agent_ver": "agent/0.1"},
    )


def test_adjudicate_parses_verdict_from_scripted_loop():
    """Parse a semantic verdict from the terminal tool call."""
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="let me think")]),
            _emit({"live": True, "reasoning": "still a live reference", "confidence": 0.8}),
        ]
    )
    judge = SemanticJudge(client=client, budget=3)
    verdict = judge.adjudicate(_claim(), "doc text here", "repo map here", "/repo")
    assert isinstance(verdict, SVerdict)
    assert verdict.live is True
    assert verdict.reasoning == "still a live reference"
    assert verdict.confidence == 0.8


def test_leash_caps_tool_calls_at_budget():
    """Force adjudication to terminate at its tool-call budget."""
    responses = [
        _Resp([_Block("tool_use", id=f"t{i}", name="glob", input={"pattern": "*.md"})])
        for i in range(5)
    ]
    client = _ScriptedClient(responses)
    judge = SemanticJudge(client=client, budget=3)
    # A belt-tool response to the forced terminal instruction carries no verdict payload.
    with pytest.raises(ValueError):
        judge.adjudicate(_claim(), "doc text", "repo map", "/repo")
    # Three loop turns plus one forced terminal turn leave one response unconsumed.
    assert len(client.calls) == 4
    assert len(client._responses) == 1


def _as_text(content) -> str:
    """Flatten either prompt strings or content-block lists into text."""
    if isinstance(content, str):
        return content
    return " ".join(
        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
    )


def test_prompt_hygiene_no_materiality_language_but_has_claim_and_doc():
    """Include claim context without asking the judge to assess materiality."""
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="let me think")]),
            _emit({"live": True, "reasoning": "ok", "confidence": 0.6}),
        ]
    )
    judge = SemanticJudge(client=client, budget=3)
    judge.adjudicate(_claim(), "THE-DOC-FULL-TEXT-MARKER", "THE-REPO-MAP-MARKER", "/repo")

    first_call = client.calls[0]
    system_text = _as_text(first_call["system"])
    user_text = _as_text(first_call["messages"][0]["content"])

    assert "see `drift.old_fn`" in user_text
    assert "THE-DOC-FULL-TEXT-MARKER" in user_text
    assert "THE-REPO-MAP-MARKER" in user_text

    combined = (system_text + " " + user_text).lower()
    for word in _MATERIALITY_WORDS:
        assert word not in combined


def test_s_slot_is_never_rendered_into_the_prompt():
    """Keep the discovery producer's semantic note out of the independent judge prompt."""
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="let me think")]),
            _emit({"live": True, "reasoning": "ok", "confidence": 0.6}),
        ]
    )
    judge = SemanticJudge(client=client, budget=3)
    judge.adjudicate(_claim(note="MY-OWN-DISCOVERER-NOTE-XYZ"), "doc", "map", "/repo")
    user_text = _as_text(client.calls[0]["messages"][0]["content"])
    assert user_text
    assert "MY-OWN-DISCOVERER-NOTE-XYZ" not in user_text


@pytest.mark.parametrize(
    ("live", "confidence", "threshold", "expected"),
    [
        (True, 0.9, S_THRESHOLD, True),
        (True, 0.4, S_THRESHOLD, False),
        (False, 0.9, S_THRESHOLD, False),
        (True, 0.6, 0.7, False),
        (True, 0.7, 0.7, True),
    ],
)
def test_s_passed(live, confidence, threshold, expected):
    """Require both a live verdict and confidence at the configured threshold."""
    verdict = SVerdict(live=live, reasoning="r", confidence=confidence)
    assert s_passed(verdict, threshold) is expected


def _billed(response, **usage):
    """Attach token usage to a scripted response."""
    response.usage = _Usage(cache_read_input_tokens=0, cache_creation_input_tokens=0, **usage)
    return response


def test_an_emit_turn_that_takes_no_emit_call_raises_with_the_usage_it_billed():
    """Raise with all billed usage when the terminal turn contains no verdict call."""
    client = _ScriptedClient(
        [
            _billed(
                _Resp([_Block("text", text="nothing to do")]), input_tokens=900, output_tokens=40
            ),
            _billed(
                _Resp([_Block("text", text="here is my answer in prose")]),
                input_tokens=1000,
                output_tokens=60,
            ),
        ]
    )
    judge = SemanticJudge(client=client, budget=2)
    with pytest.raises(JudgeEmitError) as excinfo:
        judge.adjudicate(_claim(), "doc", "map", "/repo")
    assert excinfo.value.usage["input_tokens"] == 1900
    assert excinfo.value.usage["output_tokens"] == 100


def test_a_truncated_emit_is_an_invalid_emit_not_a_short_verdict():
    """Reject a truncated terminal call as an invalid verdict while retaining its bill."""
    emit = _billed(
        _emit({"reasoning": "I was cut off mid-"}, stop_reason="max_tokens"),
        input_tokens=1000,
        output_tokens=16384,
    )
    client = _ScriptedClient(
        [
            _billed(_Resp([_Block("text", text="thinking")]), input_tokens=800, output_tokens=20),
            emit,
        ]
    )
    judge = SemanticJudge(client=client, budget=2)
    with pytest.raises(JudgeEmitError) as excinfo:
        judge.adjudicate(_claim(), "doc", "map", "/repo")
    assert excinfo.value.usage["output_tokens"] == 16404


def test_a_completed_emit_call_is_a_verdict_even_at_the_same_token_count():
    """Accept a terminal tool call whose response completed with stop_reason="tool_use"."""
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="thinking")]),
            _emit({"live": False, "reasoning": "changelog entry", "confidence": 0.9}),
        ]
    )
    verdict = SemanticJudge(client=client, budget=2).adjudicate(_claim(), "doc", "map", "/repo")
    assert verdict.live is False and verdict.confidence == 0.9


def test_client_is_lazily_constructed_when_none():
    """Delay live client construction until adjudication needs it."""
    judge = SemanticJudge(client=None)
    assert judge._client is None


def test_confidence_above_one_is_clamped_to_one():
    """Clamp confidence values above the valid range."""
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="let me think")]),
            _emit({"live": True, "reasoning": "ok", "confidence": 1.7}),
        ]
    )
    judge = SemanticJudge(client=client, budget=3)
    verdict = judge.adjudicate(_claim(), "doc", "map", "/repo")
    assert verdict.confidence == 1.0


def test_confidence_below_zero_is_clamped_to_zero():
    """Clamp confidence values below the valid range."""
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="let me think")]),
            _emit({"live": True, "reasoning": "ok", "confidence": -0.3}),
        ]
    )
    judge = SemanticJudge(client=client, budget=3)
    verdict = judge.adjudicate(_claim(), "doc", "map", "/repo")
    assert verdict.confidence == 0.0


def test_non_numeric_confidence_defaults_to_zero():
    """Default a non-numeric confidence value to zero."""
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="let me think")]),
            _emit({"live": True, "reasoning": "ok", "confidence": "high"}),
        ]
    )
    judge = SemanticJudge(client=client, budget=3)
    verdict = judge.adjudicate(_claim(), "doc", "map", "/repo")
    assert verdict.confidence == 0.0


def test_non_bool_live_is_coerced_via_truthiness():
    """Coerce a non-boolean live field through truthiness."""
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="let me think")]),
            _emit({"live": "yes", "reasoning": "ok", "confidence": 0.6}),
        ]
    )
    judge = SemanticJudge(client=client, budget=3)
    verdict = judge.adjudicate(_claim(), "doc", "map", "/repo")
    assert verdict.live is True


def test_prompt_is_shared_first_two_blocks_with_cache_breakpoint():
    """Keep reusable judge context separate from claim-specific prompt content."""
    client = _ScriptedClient(
        [
            _Resp([_Block("text", text="let me think")]),
            _emit({"live": True, "reasoning": "ok", "confidence": 0.6}),
        ]
    )
    judge = SemanticJudge(client=client, budget=3)
    judge.adjudicate(_claim(), "THE-DOC-TEXT", "THE-REPO-MAP", "/repo")
    blocks = client.calls[0]["messages"][0]["content"]
    assert isinstance(blocks, list) and len(blocks) == 2
    shared, specific = blocks
    assert shared["cache_control"] == {"type": "ephemeral"}
    assert "THE-REPO-MAP" in shared["text"] and "THE-DOC-TEXT" in shared["text"]
    assert "Claim literal:" not in shared["text"]
    assert "Claim literal: see `drift.old_fn`" in specific["text"]


def test_system_prompt_never_leaks_the_mechanical_verdict():
    """Keep the mechanical verdict out of the semantic judge's system prompt."""
    from drift.judge.semantic_judge import _SYSTEM

    assert "mechanical check" not in _SYSTEM.lower()
    assert "target is absent" not in _SYSTEM.lower()


def test_system_prompt_carries_the_criterion_separation():
    """Tell the judge to assess liveness independently from mechanical accuracy."""
    from drift.judge.semantic_judge import _SYSTEM

    low = _SYSTEM.lower()
    assert "not its accuracy" in low
    assert "do not mark a claim not-live because the repository" in low


def test_the_judge_stamp_is_declared_in_one_place():
    """Keep the judge's runtime stamp aligned with the scan's declared stamp."""
    from drift.graph.cell import JUDGE_VER

    assert SemanticJudge()._judge_ver == JUDGE_VER
