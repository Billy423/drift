"""The judge: an independent, leashed reader deciding whether one claim is still live.

Liveness is its only criterion; materiality and severity stay out of its prompt, and the harness
alone decides what to do with a verdict. Every adjudication is a fresh loop.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass, field

from drift.agent import runner  # the module, so the shared emit text is read at call time
from drift.agent.runner import run_loop
from drift.agent.toolbelt import make_toolbelt
from drift.kernels.models import EvClaim

__all__ = [
    "JudgeEmitError",
    "SVerdict",
    "SemanticJudge",
    "S_THRESHOLD",
    "prompt_fingerprint",
    "s_passed",
]


def prompt_fingerprint() -> str:
    """A hash of this role's own model-facing surfaces, beside the run's hand-written stamp.

    Hashed: the system text, the output schema, the emit tool's name and description (shared
    with the discovery producer, so editing either moves both), and `_render`'s source, because
    what separates two prompt variants can live in assembly while the system text stays
    identical. Not hashed: the description each toolbelt tool sends on every turn.
    """
    surfaces = [
        _SYSTEM,
        inspect.getsource(_render),
        json.dumps(_OUTPUT_SCHEMA, sort_keys=True),
        runner.EMIT_TOOL_NAME,
        runner.EMIT_TOOL_DESCRIPTION,
    ]
    return hashlib.sha256("\x00".join(surfaces).encode()).hexdigest()


S_THRESHOLD = 0.5  # harness-owned: the judge reports a confidence, the harness sets the bar

_SYSTEM = (
    # The clauses separating liveness from accuracy are load-bearing: without them the judge
    # re-derives the target's absence itself and reads a disagreeing repository as "not live".
    "You are an independent reader judging ONE documentation claim. Your only job is to "
    "decide whether the claim is a live, present-tense assertion about this repo right now — "
    "or whether it is instead a plan/roadmap item, a historical/changelog entry, a "
    "write-target (something the doc says WILL be created), an aspirational statement, or a "
    "note the document itself marks as superseded. Liveness is about the assertion's intent "
    "as written, NOT its accuracy: whether the thing the claim points at actually exists in "
    "the repo today is a separate mechanical question that is not yours — a live assertion "
    "may well be wrong about the current repo, and detecting that is the rest of the "
    "system's job, possible only if you keep genuinely-live claims alive. Do not mark a "
    "claim not-live because the repository's current state disagrees with it. Use "
    "read_file/glob to read the claim's surrounding document context for liveness cues "
    "(headings, tense, changelog framing); do not use them to check whether the claim's "
    "target exists. Give your own honest liveness read; when uncertain, prefer a lower "
    "confidence rather than guessing."
)

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "live": {
            "type": "boolean",
            "description": "True if the claim is a live, present-tense assertion about this repo.",
        },
        "reasoning": {
            "type": "string",
            "description": "One or two sentences justifying the liveness read.",
        },
        "confidence": {
            "type": "number",
            "description": "Confidence in the liveness read, 0 to 1.",
        },
    },
    "required": ["live", "reasoning", "confidence"],
    "additionalProperties": False,
}


class JudgeEmitError(ValueError):
    """An adjudication that reached no usable verdict, carrying the usage it already billed.

    The leash may have burned its whole budget before the emit turn arrived unparseable, and
    billing that as nothing would make the most expensive candidate the cheapest on the books.
    """

    def __init__(self, message: str, usage: dict) -> None:
        super().__init__(message)
        self.usage = usage


@dataclass(frozen=True)
class SVerdict:
    """One verdict on a claim's liveness: live or not, why, and how confident."""

    live: bool
    reasoning: str
    confidence: float
    usage: dict = field(default_factory=dict)
    # Which tools this adjudication ran, and with what arguments.
    tool_trace: list = field(default_factory=list)


def s_passed(v: SVerdict, threshold: float = S_THRESHOLD) -> bool:
    """Harness-owned gate: a verdict passes only if it is live and confident enough."""
    return v.live and v.confidence >= threshold


def _render(claim: EvClaim, doc_text: str, repo_map: str) -> list[dict]:
    """One adjudication's whole input: the repository map, the document, and the claim.

    The document arrives whole because liveness is read from context, and the mechanical outcome
    is stated as a fact rather than as an instruction. The discovering producer's own note is left
    out: independence from the discovery loop is the point. The shared block leads and carries the
    cache breakpoint, so later candidates on the same document read it from cache.
    """
    anchor = claim.anchor
    spans = ", ".join(f"[{start}-{end}]" for start, end in anchor.spans) or "(none recorded)"
    predicate = claim.check.predicate if claim.check is not None else "none"
    shared = f"Repo map:\n{repo_map}\n\nDocument full text:\n{doc_text}"
    specific = (
        f"Claim literal: {anchor.literal}\n"
        f"Doc path: {anchor.doc_path}\n"
        f"Anchor spans (1-indexed line ranges): {spans}\n"
        f"Predicate: {predicate}\n"
        f"Mechanical check: target absent at current revision."
    )
    return [
        {"type": "text", "text": shared, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": specific},
    ]


def _clamp01(value: float) -> float:
    """Fold a reported confidence into the 0..1 the schema asks for."""
    return max(0.0, min(1.0, value))


class SemanticJudge:
    """Adjudicates one mechanically-refuted claim's liveness through a leashed tool loop.

    Independent of the discovery loop — a fresh `run_loop`, no shared transcript — and blind to
    claim class: one criterion, liveness, for every claim there is.
    """

    def __init__(
        self,
        client=None,
        model: str = "claude-sonnet-5",
        # Bump on any edit to the system prompt, the output schema or this role's request
        # shape: it stamps every journalled row, and a stale stamp hides a real difference.
        judge_ver: str = "sjudge/0.5",  # also set in `graph.cell`; change both together
        budget: int = 3,
    ) -> None:
        self._client = client  # built on first use when None
        self._model = model
        self._judge_ver = judge_ver
        self._budget = budget

    def _ensure_client(self):
        """The client, constructed late so a scan that judges nothing never needs credentials."""
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def adjudicate(self, claim: EvClaim, doc_text: str, repo_map: str, repo_root: str) -> SVerdict:
        """Judge one claim's liveness, returning the verdict and what it cost.

        Raises:
            JudgeEmitError: If no usable verdict arrived, carrying the usage already billed.
        """
        client = self._ensure_client()
        tools = make_toolbelt(repo_root, ("read_file", "glob"))
        user_content = _render(claim, doc_text, repo_map)
        result = run_loop(
            client, self._model, _SYSTEM, tools, user_content, _OUTPUT_SCHEMA, self._budget
        )
        if result.status == "invalid" or result.payload is None:
            raise JudgeEmitError(
                f"SemanticJudge: invalid emit for claim {claim.anchor.literal!r} "
                f"in {claim.anchor.doc_path!r}",
                result.usage,
            )
        payload = result.payload
        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return SVerdict(
            live=bool(payload.get("live", False)),
            reasoning=str(payload.get("reasoning", "")),
            confidence=_clamp01(confidence),
            usage=result.usage,
            tool_trace=result.tool_trace,
        )
