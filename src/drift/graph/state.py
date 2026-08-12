"""The typed channels every node of a scan graph reads and writes."""

from __future__ import annotations

from typing import TypedDict

from drift.domain.findings import Finding
from drift.gate.replay import GateResult
from drift.graph.ranked import RankedEntry
from drift.judge.semantic_judge import SVerdict
from drift.kernels.models import EvClaim
from drift.persistence.store import ReconcileResult


class ScanState(TypedDict):
    """Typed channel schema for a scan graph: a repository goes in, a two-tier report comes out."""

    repo_root: str
    run_id: int
    doc_filter: str | None
    worklist: list[str]  # adopt_worklist fills, from planned_worklist alone
    # What the frame enumerated. Nothing below the frame walks the tree, so absent or None
    # yields an empty worklist rather than a second enumeration that could disagree.
    planned_worklist: list[str] | None
    planned_hazards: list[dict] | None  # the enumeration hazards that came with it
    claims: list[EvClaim]  # discover fills
    coverages: list[dict]  # discover fills (one per unit)
    # gate_replay fills: the claims eligible for high-grade adjudication, not every claim
    # replayed. Preview-bound claims are routed out before this channel is built.
    gate_results: list[GateResult]
    verdicts: list[tuple[EvClaim, SVerdict]]  # semantic_judge fills (adjudicated only)
    findings: list[Finding]  # semantic_judge fills (judge-passed candidates only)
    # gate_replay fills: claims with no mechanical check, behavioural claims, comment-routed
    # skips, declined binds and preview-annotated claims.
    ranked_entries: list[RankedEntry]
    kernel_errors: list[dict]  # gate_replay fills (KERNEL_ERROR outcomes -> report completeness)
    result: ReconcileResult | None  # reconcile fills
    report_text: str  # report fills
    budget: float  # USD ceiling, checked between document units; None or absent means unlimited
    max_s_candidates: int | None  # candidates this invocation may judge; None = module default
    spend: float  # cumulative USD; discover + semantic_judge accumulate
    units_discovered: int  # doc units actually scanned (discover fills; for the partial banner)
    partial_notes: list[str]  # banner lines for the incompleteness banner (cap-skip / budget-stop)
    # Measurement mode: the soft rails and a journal failure become loud aborts, so a published
    # number can never come from a silently truncated run. Absent or False is an ordinary scan.
    strict_measurement: bool
