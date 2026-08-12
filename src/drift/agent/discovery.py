"""The discovery producer: a model inventories one document's claims about its repository."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass

from drift.agent import runner  # the module, so the shared emit text is read at call time
from drift.agent.repo_map import build_repo_map
from drift.agent.runner import LoopFailure, run_loop
from drift.agent.toolbelt import make_toolbelt
from drift.fsguard import DocRead, read_doc_bytes
from drift.kernels.models import Anchor, Check, EvClaim, SSlot
from drift.kernels.registry import predicate_registry, vocabulary

# Must name a member of the closed set in `kernels.models.PRODUCERS`.
_PRODUCER = "agent"

__all__ = ["DiscoveryAgent", "DiscoveryResult", "prompt_fingerprint"]


def prompt_fingerprint() -> str:
    """A hash of this producer's own model-facing surfaces, beside the run's hand-written stamp.

    Hashed: the system text, the live vocabulary, the emit tool's name and description, the
    output schema, and `_render_prompt`'s source, since what the model sees can change in
    assembly rather than in a string. Not hashed, though it is sent on every turn: the
    description each toolbelt tool carries into the request.
    """
    import json

    surfaces = [
        _SYSTEM,
        inspect.getsource(DiscoveryAgent._render_prompt),
        vocabulary(),
        runner.EMIT_TOOL_NAME,
        runner.EMIT_TOOL_DESCRIPTION,
        # The schema's field descriptions reach the model, so editing one is a prompt change.
        json.dumps(_OUTPUT_SCHEMA, sort_keys=True),
    ]
    return hashlib.sha256("\x00".join(surfaces).encode()).hexdigest()


_SYSTEM = (
    "You are a cartographer of one documentation unit's claims about the repository it lives in. "
    "Inventory EVERY claim the doc makes that could go stale, INCLUDING claims that are currently "
    "true — a passing claim today is tomorrow's drift detector; do not skip one because it "
    "currently checks out. A claim is any repo-referencing assertion: a file/dir path, an import, "
    "a command to run, a fact about repo structure or behavior. For each claim, try to bind it to "
    "one predicate from the vocabulary below by giving its exact `predicate` name and the exact "
    "`literal` text as it appears in the doc. When a predicate binds, the `literal` MUST be the "
    "minimal reference substring only — for a path claim, exactly the path token (e.g. "
    "`docs/guide.md` or `../fairy/CLAUDE.md`), NEVER a whole markdown link, heading, YAML line, "
    "or sentence containing it (the mechanical checker treats the literal as the path verbatim; "
    "anything else cannot bind). If no predicate in the vocabulary fits, set "
    'predicate to "none" rather than forcing a bad match. You may call read_file and glob to '
    "inspect the repo before deciding whether a mention is a live reference. For every claim also "
    "give your own liveness read: a short `note` plus a `confidence` in [0, 1] reflecting how sure "
    "you are that the claim is still true. This is your own judgment call, auxiliary evidence — "
    "the mechanical check (when a predicate binds) is the actual authority, not your note. "
    "When a predicate requires arguments beyond the literal (see each predicate's description), "
    "propose them in `args`; leave `args` empty otherwise."
)

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "literal": {
                        "type": "string",
                        "description": "Exact claim text as it appears in the doc.",
                    },
                    "predicate": {
                        "type": "string",
                        "description": 'A predicate name from the vocabulary, or "none".',
                    },
                    "spans": {
                        "type": "array",
                        # No minItems/maxItems: the schema compiler rejects array-length
                        # constraints outright, so `_parse_spans` drops malformed pairs instead.
                        "items": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                        "description": "1-indexed [start_line, end_line] mentions of the claim.",
                    },
                    "claim_class": {
                        "type": "integer",
                        "description": "1 existence, 2 property, 3 behavioral/unbindable.",
                    },
                    "note": {
                        "type": "string",
                        "description": "Your own liveness read, one sentence.",
                    },
                    "confidence": {
                        "type": "number",
                        # Must match the system prompt's wording: the report bands on this field.
                        "description": ("How sure you are that the claim is still true, 0 to 1."),
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Proposed predicate arguments when the predicate needs them "
                            "(signature_has_param: [dotted_symbol, param]; make_target_exists: "
                            "[target]; symbol_resolves: [dotted_symbol] optional). Empty if the "
                            "literal itself is the argument. Every arg must be visible in or "
                            "directly implied by the literal."
                        ),
                    },
                },
                "required": [
                    "literal",
                    "predicate",
                    "spans",
                    "claim_class",
                    "note",
                    "confidence",
                    "args",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class DiscoveryResult:
    """One `discover()` run: the claims the harness assembled, plus that run's coverage record."""

    claims: list[EvClaim]
    coverage: dict


class DiscoveryAgent:
    """Inventories one document's claims about its repository, through a budgeted tool loop.

    The model never mints a `Check`: it proposes a predicate name and arguments, and the harness
    alone calls `normalize`, so no model output can move a claim's mechanical identity.
    """

    def __init__(
        self,
        client,
        model: str = "claude-sonnet-5",
        # Bump on any edit to the system prompt, the vocabulary or the output schema: it stamps
        # every journalled row, and a stale stamp makes two unlike runs look comparable.
        agent_ver: str = "agent/0.9",  # also set in `graph.cell`; change both together
        budget: int = 25,
    ) -> None:
        self._client = client
        self._model = model
        self._agent_ver = agent_ver
        self._budget = budget

    def discover(self, repo_root: str, doc_path: str) -> DiscoveryResult:
        """Inventory one document, returning its claims and a coverage record of the attempt."""
        read = self._read_doc(repo_root, doc_path)
        if read is None:
            coverage = {
                "unit": doc_path,
                "doc_hash": "",
                "turns_used": 0,
                "tool_calls": 0,
                "tool_trace": [],
                "status": "failed",
            }
            return DiscoveryResult([], coverage)
        doc_bytes = read.data
        # Over the bytes the model saw: a truncated read hashes its prefix, so two runs
        # that showed the same input get the same hash.
        doc_hash = hashlib.sha256(doc_bytes).hexdigest()
        coverage_base = {
            "unit": doc_path,
            "doc_hash": doc_hash,
            "doc_chars": len(doc_bytes.decode("utf-8", errors="replace")),
            **(
                {"doc_truncated": True, "doc_bytes_total": read.size_bytes}
                if read.truncated
                else {}
            ),
        }

        user_content = self._render_prompt(repo_root, doc_path, doc_bytes)
        tools = make_toolbelt(repo_root)
        try:
            loop_result = run_loop(
                self._client,
                self._model,
                _SYSTEM,
                tools,
                user_content,
                _OUTPUT_SCHEMA,
                self._budget,
            )
        except LoopFailure as exc:
            # Returned rather than raised: the earlier turns were already billed, and a
            # crashed unit is the expensive kind to lose from the cost record.
            return DiscoveryResult(
                [],
                {
                    **coverage_base,
                    "turns_used": exc.turns_used,
                    "tool_calls": exc.tool_calls,
                    "tool_trace": exc.tool_trace,
                    "status": "error",
                    "detail": repr(exc.cause),
                    "usage": exc.usage,
                },
            )
        coverage = {
            **coverage_base,
            "turns_used": loop_result.turns_used,
            "tool_calls": loop_result.tool_calls,
            "tool_trace": loop_result.tool_trace,
            "status": loop_result.status,
            "usage": loop_result.usage,
        }

        raw_claims = loop_result.payload.get("claims") if loop_result.payload else None
        if not isinstance(raw_claims, list):
            status = coverage["status"] if loop_result.payload is None else "invalid"
            return DiscoveryResult([], {**coverage, "status": status})

        claims = [self._assemble(item, doc_path) for item in raw_claims if isinstance(item, dict)]
        return DiscoveryResult([c for c in claims if c is not None], coverage)

    def _render_prompt(self, repo_root: str, doc_path: str, doc_bytes: bytes) -> list[dict]:
        """The model's whole input: the vocabulary, the repository map, the budget and the document.

        Two blocks, the shared one first. The leading block is byte-identical for every unit of
        a scan and carries the cache breakpoint, so later units read it from cache. Reordering
        the blocks, or letting anything unit-specific into the leading one, removes that saving.
        """
        doc_text = doc_bytes.decode("utf-8", errors="replace")
        repo_map = build_repo_map(repo_root)
        shared = (
            f"Predicate vocabulary (bind claims to one of these when they fit):\n{vocabulary()}\n\n"
            f"Repo map:\n{repo_map}\n\n"
            f"You have a budget of {self._budget} tool calls to explore the repo before you must "
            "emit your final inventory."
        )
        specific = f"Document path: {doc_path}\n\nDocument text:\n{doc_text}"
        return [
            {"type": "text", "text": shared, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": specific},
        ]

    @staticmethod
    def _read_doc(repo_root: str, doc_path: str) -> DocRead | None:
        """Read the document from inside the repository sandbox; None on any refusal.

        Fail-closed, because `discover` must not raise on a bad path, and hang-closed:
        `read_doc_bytes` lstats before opening, so a named pipe in the scanned tree is refused
        rather than blocking this call forever.
        """
        return read_doc_bytes(repo_root, doc_path)

    @staticmethod
    def _validate_args(literal: str, predicate_name: str, raw_args) -> tuple[str, ...] | None:
        """Reject proposed arguments the literal does not contain; validation, not normalization.

        Every argument must be textually anchored in the literal — a dotted name by its final
        component, anything else as a substring — which stops one claim from being checked with
        another claim's arguments.
        """
        if not isinstance(raw_args, list) or not all(isinstance(a, str) for a in raw_args):
            return None
        for arg in raw_args:
            probe = arg.rsplit(".", 1)[-1] if "." in arg else arg
            if probe and probe not in literal:
                return None
        return tuple(raw_args)

    def _assemble(self, item: dict, doc_path: str) -> EvClaim | None:
        """One inventory item as an `EvClaim`. The only place `normalize()` is ever called."""
        literal = item.get("literal")
        if not isinstance(literal, str):
            return None
        predicate_name = item.get("predicate")
        note = str(item.get("note", ""))
        try:
            # Clamped here because nothing upstream bounds it: the output schema states the range
            # in its description only, and the report bands on this value.
            confidence = min(1.0, max(0.0, float(item.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0

        check = None
        # Every outcome assigned below must be a member of `kernels.models.BIND_OUTCOMES`; a
        # test scans this method's source, so a new spelling fails there rather than widening it.
        bind_outcome = "model-none"
        if isinstance(predicate_name, str) and predicate_name != "none":
            if predicate_name not in predicate_registry:
                bind_outcome = "unregistered-predicate"
            else:
                raw_args = item.get("args", [])
                proposed: tuple[str, ...] | None
                if raw_args:
                    proposed = self._validate_args(literal, predicate_name, raw_args)
                    rejected = proposed is None
                else:
                    proposed, rejected = None, False
                if rejected:
                    bind_outcome = "args-rejected"
                else:
                    bind_outcome = "normalize-declined"
                    normalized = predicate_registry[predicate_name].normalize(
                        literal, doc_path, proposed
                    )
                    if normalized is not None:
                        normalization, normalized_args = normalized
                        bind_outcome = "bound"
                        check = Check(
                            predicate=predicate_name,
                            raw={
                                "literal": literal,
                                "doc_path": doc_path,
                                "proposed_args": list(proposed) if proposed else [],
                            },
                            normalization=normalization,
                            normalized_args=normalized_args,
                        )

        if check is not None:
            try:
                claim_class = int(item.get("claim_class", 1))
            except (TypeError, ValueError):
                claim_class = 1
            if claim_class not in (1, 2):
                # A high-grade bind asserts the claim is decidable, so class 3 cannot stand.
                # A preview bind asserts nothing of the kind, and coercing would suppress it.
                if predicate_registry[predicate_name].grade == "high":
                    claim_class = 1
                elif claim_class != 3:  # preview: keep the model's read, clamped to the schema
                    claim_class = 3
        else:
            claim_class = 3  # unknown predicate / "none" / normalize-rejected -> unbindable

        anchor = Anchor(
            doc_path=doc_path, spans=self._parse_spans(item.get("spans")), literal=literal
        )
        return EvClaim(
            anchor=anchor,
            check=check,
            claim_class=claim_class,
            s_slot=SSlot(note=note, confidence=confidence),
            provenance={
                "producer": _PRODUCER,
                "agent_ver": self._agent_ver,
                # A bound claim's proposal already lives in `check.raw`; no duplicate here.
                "bind": {"outcome": "bound"}
                if bind_outcome == "bound"
                else {
                    "outcome": bind_outcome,
                    "proposed_predicate": predicate_name
                    if isinstance(predicate_name, str)
                    else None,
                    # Stringified rather than dropped: a malformed proposal is worth keeping.
                    "proposed_args": [str(a) for a in item.get("args", [])]
                    if isinstance(item.get("args"), list)
                    else [str(item.get("args"))]
                    if item.get("args") is not None
                    else [],
                },
            },
        )

    @staticmethod
    def _parse_spans(raw) -> tuple[tuple[int, int], ...]:
        """The line spans the model reported, with anything not a pair of integers dropped."""
        if not isinstance(raw, list):
            return ()
        spans = []
        for pair in raw:
            try:
                start, end = pair
                spans.append((int(start), int(end)))
            except (TypeError, ValueError):
                continue
        return tuple(spans)
