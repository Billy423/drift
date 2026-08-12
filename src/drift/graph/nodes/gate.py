"""The replay gate — the only authority on whether a claim is mechanically refuted."""

from __future__ import annotations

from drift.gate.replay import GateOutcome, GateResult, replay
from drift.graph.nodes.rails import _safe_journal
from drift.graph.progress import progress
from drift.graph.ranked import RankedEntry
from drift.graph.state import ScanState
from drift.journal.serialize import claim_ref
from drift.kernels.models import EvClaim
from drift.kernels.registry import predicate_registry

# Skip reasons a reader should see; every other admitted reason stays journal-only. Membership is
# judged per reason: the document's own problem is worth surfacing, the repository's is not.
_COMMENT_REASONS = ("variadic", "no-makefile", "no-manifest")


def _is_preview(claim: EvClaim) -> bool:
    """Did this claim bind to a preview-grade predicate? A claim with no check binds to nothing."""
    if claim.check is None:
        return False
    predicate = predicate_registry.get(claim.check.predicate)
    return predicate is not None and predicate.grade == "preview"


def _preview_annotation_text(predicate: str, outcome_value: str, detail: str | None) -> str:
    """The ranked-tier note for a preview predicate's result, keyed on the journaled outcome.

    Keyed on the value rather than on a `GateResult` so that the read model can rebuild this
    exact string from a stored row, instead of growing a second renderer that drifts from this.
    """
    body = {
        GateOutcome.M_CERTIFIED.value: "mechanical check: absent",
        GateOutcome.PASSING.value: "mechanical check: present",
        GateOutcome.UNGATEABLE.value: f"mechanical check: unadjudicable ({detail})",
        GateOutcome.BINDING_FAIL.value: "mechanical check: not run (anchor no longer in the doc)",
        GateOutcome.KERNEL_ERROR.value: f"mechanical check: errored ({detail})",
        GateOutcome.UNBOUND.value: "mechanical check: not bound",
    }[outcome_value]
    return f"preview `{predicate}` — {body} (preview predicates cannot certify a finding)"


def _preview_annotation(gr) -> str:
    """The same note, for a `GateResult` in hand.

    Passes render as well as refutations: a tier showing only the fires is a high-grade tier with
    a different label, and the decision to promote a predicate needs both.
    """
    return _preview_annotation_text(gr.claim.check.predicate, gr.outcome.value, gr.detail)


def make_gate_replay(writer):
    """Return a node that replays every claim's two-leg check and routes the outcome."""

    def gate_replay(state: ScanState) -> dict:
        """Node: replay every claim's two-leg check and route the outcome.

        Grade routing runs before outcome routing. A preview-bound claim is annotated into the
        ranked tier and left out of `gate_results`, the only channel the judge reads, so a
        preview predicate cannot mint a finding — and, every outcome still rendering downstream,
        cannot suppress one either.
        """
        gate_results = replay(state["repo_root"], state["claims"])
        ranked_entries: list[RankedEntry] = []
        kernel_errors: list[dict] = []
        kept_results: list[GateResult] = []
        for gr in gate_results:
            if _is_preview(gr.claim):
                writer.write(
                    "gate",
                    "preview_verdict",
                    {
                        **claim_ref(gr.claim),
                        "outcome": gr.outcome.value,
                        "detail": gr.detail,
                    },
                )
                ranked_entries.append(RankedEntry(gr.claim, _preview_annotation(gr)))
                continue
            kept_results.append(gr)
            if gr.outcome == GateOutcome.UNGATEABLE:
                # Not a kill: the kernel declined to adjudicate. Mixing these into `gate_kill`
                # would cost that stream its meaning — "the check could not be run".
                writer.write(
                    "gate",
                    "gate_ungateable",
                    {
                        "reason": gr.detail,
                        **claim_ref(gr.claim),
                    },
                )
                if gr.detail in _COMMENT_REASONS:
                    ranked_entries.append(RankedEntry(gr.claim))
                continue
            if gr.outcome in (GateOutcome.BINDING_FAIL, GateOutcome.KERNEL_ERROR):
                writer.write(
                    "gate",
                    "gate_kill",
                    {
                        "kind": gr.outcome.value.lower(),
                        **claim_ref(gr.claim),
                        "detail": gr.detail,
                    },
                )
                if gr.outcome == GateOutcome.KERNEL_ERROR:
                    # A predicate bug, on a claim that bound, must not read as a clean scan.
                    kernel_errors.append(
                        {
                            "unit": gr.claim.anchor.doc_path,
                            "status": "kernel_error",
                            "detail": gr.detail,
                        }
                    )
            elif gr.outcome in (GateOutcome.PASSING, GateOutcome.M_CERTIFIED):
                writer.write(
                    "gate",
                    "gate_outcome",
                    {
                        # Verbatim, matching `preview_verdict`: one outcome spelled two ways
                        # makes a query return zero rows and look like an answer.
                        "outcome": gr.outcome.value,
                        **claim_ref(gr.claim),
                        "detail": gr.detail,
                    },
                )
            elif gr.outcome == GateOutcome.UNBOUND or gr.claim.claim_class == 3:
                ranked_entries.append(RankedEntry(gr.claim))
        # Flushed here rather than at the terminal commit: a hard kill after the gate would
        # otherwise keep the claim inventory and lose the whole funnel.
        partial_notes = list(state.get("partial_notes", []))
        _safe_journal(writer, state, partial_notes, "gate", lambda: None)
        by_outcome: dict[str, int] = {}
        for gr in gate_results:
            by_outcome[gr.outcome.value] = by_outcome.get(gr.outcome.value, 0) + 1
        progress(f"gate: {len(gate_results)} claim(s) → {by_outcome or 'none'}")
        return {
            "gate_results": kept_results,
            "ranked_entries": ranked_entries,
            "kernel_errors": kernel_errors,
            "partial_notes": partial_notes,
        }

    return gate_replay
