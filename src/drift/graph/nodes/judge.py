"""The semantic judge, over claims the gate refuted and nothing else."""

from __future__ import annotations

from drift.agent.repo_map import build_repo_map
from drift.cost import DEFAULT_MODEL, usage_cost_usd
from drift.domain.findings import Confidence, Evidence, Finding, Location
from drift.fsguard import read_doc_bytes
from drift.gate.replay import GateOutcome
from drift.graph.nodes.rails import _budget_of, _cap_of, _rail_stop
from drift.graph.progress import progress
from drift.graph.state import ScanState
from drift.journal.serialize import claim_ref
from drift.judge.semantic_judge import s_passed
from drift.kernels.models import EvClaim

_CODE_TRUTH = {
    "path_exists": "path not found in the scanned tree",
    "link_resolves": "link target not found in the scanned tree",
    "symbol_resolves": "symbol not resolvable in the repo's static tree",
    "signature_has_param": "parameter absent from the symbol's real signature",
    "make_target_exists": "target not defined in the root Makefile",
}


def _read_doc_text(repo_root: str, doc_path: str) -> str:
    """One document's text for the judge, contained and bounded; '' on any refusal.

    Decoded with `errors="replace"`, as every other reader of these files does, so a document
    that is not valid UTF-8 never ends a scan. This is a second whole read of a file, straight
    into a prompt, which is why it goes through the bounded reader rather than a path join.
    """
    read = read_doc_bytes(repo_root, doc_path)
    if read is None:
        return ""
    return read.data.decode("utf-8", errors="replace")


def _to_finding(claim: EvClaim) -> Finding:
    """A mechanically-refuted claim the judge found still live, as a high-confidence `Finding`."""
    check = claim.check
    doc_path = claim.anchor.doc_path
    literal = claim.anchor.literal
    spans = claim.anchor.spans
    first_span_start = spans[0][0] if spans else 0
    summary = f"stale {check.predicate} claim {literal!r} in {doc_path}"
    code_truth = _CODE_TRUTH.get(
        check.predicate, "mechanical check failed against the scanned tree"
    )
    return Finding(
        check_id=check.predicate,
        identity=(doc_path, *check.normalized_args),
        doc_location=Location(doc_path, first_span_start, first_span_start),
        code_anchor=None,
        summary=summary,
        evidence=Evidence(doc_claim=literal, code_truth=code_truth),
        confidence=Confidence.HIGH,
        check={
            "predicate": check.predicate,
            "raw": dict(check.raw),
            "normalization": dict(check.normalization),
            "normalized_args": list(check.normalized_args),
        },
    )


def _by_producer(claims) -> str:
    """Claim counts by producer, as "4 agent, 6 docstrings" — for the cap-skip banner.

    One cell runs one producer, so this is usually a single entry. It stays a breakdown for the
    single-process graph, where both producers' claims meet the same cap together.
    """
    counts: dict[str, int] = {}
    for claim in claims:
        # Every claim here passed `_admit_producers` at ingress, so the producer is one of a
        # closed set of non-empty names. The default is what keeps the display from raising.
        name = claim.provenance.get("producer", "")
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{counts[name]} {name}" for name in sorted(counts))


def make_semantic_judge(semantic_judge, writer, model: str = DEFAULT_MODEL):
    """Return a node that adjudicates mechanically-refuted candidates and maps the survivors.

    Args:
        model: Prices this node's spend, as in `make_discover`.
    """

    def semantic_judge_node(state: ScanState) -> dict:
        """Node: adjudicate each mechanically-refuted candidate for liveness.

        The candidate cap is fail-soft and counted per run: the frame recounts what the run has
        already adjudicated and threads the remainder in. Skipped candidates are journaled, and
        an issue resolves by replaying its own stored check rather than by reading absence.
        """
        repo_root = state["repo_root"]
        budget = _budget_of(state)
        spend = state.get("spend", 0.0)
        partial_notes = list(state.get("partial_notes", []))
        doc_hashes = {c["unit"]: c.get("doc_hash", "") for c in state["coverages"]}

        def _journal_skip(a_claim, reason: str) -> None:
            """Journal one candidate the cap left unadjudicated — a record, never a finding."""
            writer.write(
                "semantic_judge",
                "s_judge_skipped",
                {
                    **claim_ref(a_claim),
                    "doc_hash": doc_hashes.get(a_claim.anchor.doc_path, ""),
                    "reason": reason,
                },
            )

        # Certified only: an unadjudicable outcome is not a refutation, and never reaches here.
        candidates = [
            gr.claim for gr in state["gate_results"] if gr.outcome == GateOutcome.M_CERTIFIED
        ]
        n_candidates = len(candidates)
        cap = _cap_of(state)
        to_adjudicate = candidates[:cap]
        cap_skipped = candidates[cap:]
        for claim in cap_skipped:
            _journal_skip(claim, "budget_cap:max_s_candidates")
        if cap_skipped:
            partial_notes.append(
                f"{len(cap_skipped)} of {n_candidates} M-certified candidate(s) were not "
                f"adjudicated (S-candidate cap {cap}); "
                f"{len(cap_skipped)} skipped: {_by_producer(cap_skipped)}."
            )
            _rail_stop(
                writer,
                state,
                "semantic_judge",
                "semantic_judge",
                "budget_cap:max_s_candidates",
                f"s-judge: {len(cap_skipped)} candidate(s) over cap {cap}",
                len(to_adjudicate),
                n_candidates,
                budget,
                spend,
            )

        if not to_adjudicate:
            progress("s-judge: 0 candidates")
            return {
                "verdicts": [],
                "findings": [],
                "spend": spend,
                "partial_notes": partial_notes,
            }
        progress(f"s-judge: {len(to_adjudicate)} candidate(s) …")
        repo_map = build_repo_map(repo_root)
        doc_cache: dict[str, str] = {}
        verdicts: list[tuple] = []
        findings: list[Finding] = []
        adjudicated = 0

        # No dollar check inside this loop — such a gate cuts a cell mid-flight,
        # and the cut cell still reports itself complete. Funding is decided between cells.
        for claim in to_adjudicate:
            doc_path = claim.anchor.doc_path
            literal = claim.anchor.literal
            doc_hash = doc_hashes.get(doc_path, "")
            if doc_path not in doc_cache:
                doc_cache[doc_path] = _read_doc_text(repo_root, doc_path)
            doc_text = doc_cache[doc_path]
            try:
                verdict = semantic_judge.adjudicate(claim, doc_text, repo_map, repo_root)
            except Exception as exc:  # one candidate's error must not end the scan
                # An adjudication can fail after calls were billed; those carry their spend on
                # the exception. Anything else has no usage to declare and cost nothing.
                lost_usage = getattr(exc, "usage", None) or {}
                writer.write(
                    "semantic_judge",
                    "s_verdict",
                    {
                        **claim_ref(claim),
                        "doc_hash": doc_hash,
                        "doc_chars": len(doc_text),
                        "live": False,
                        "reasoning": f"judge error: {exc!r}",
                        "confidence": 0.0,
                        "error": True,
                        "usage": lost_usage,
                        "tool_trace": list(getattr(exc, "tool_trace", None) or []),
                    },
                )
                spend += usage_cost_usd(lost_usage, model=model)
                progress(f"s-judge {literal!r}: ERROR {exc!r}")
                continue
            writer.write(
                "semantic_judge",
                "s_verdict",
                {
                    **claim_ref(claim),
                    "doc_hash": doc_hash,
                    "doc_chars": len(doc_text),
                    "live": verdict.live,
                    "reasoning": verdict.reasoning,
                    "confidence": verdict.confidence,
                    "error": False,
                    "usage": verdict.usage,
                    "tool_trace": verdict.tool_trace,
                },
            )
            verdicts.append((claim, verdict))
            spend += usage_cost_usd(verdict.usage, model=model)
            adjudicated += 1
            progress(f"s-judge {literal!r}: live={verdict.live} conf={verdict.confidence:.2f}")
            if s_passed(verdict):
                findings.append(_to_finding(claim))

        progress(f"s-judge: {adjudicated} adjudicated, {len(findings)} HIGH (M ∧ S)")
        return {
            "verdicts": verdicts,
            "findings": findings,
            "spend": spend,
            "partial_notes": partial_notes,
        }

    return semantic_judge_node
