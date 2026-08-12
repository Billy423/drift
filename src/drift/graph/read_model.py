"""A run's report inputs, rebuilt from that run's own journal rows.

What a cell hands back crosses a broker and is advisory; the journal row is the durable record, so
findings, the ranked tier, coverage and kernel errors are all re-derived here instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from drift.domain.findings import Finding
from drift.graph.nodes.gate import (
    _COMMENT_REASONS,  # the skip reasons the report surfaces, not every skip
    _preview_annotation_text,
)
from drift.graph.nodes.judge import (
    _to_finding,  # reused, so a rebuilt `finding.check` is the one the run wrote
)
from drift.graph.ranked import RankedEntry
from drift.judge.semantic_judge import S_THRESHOLD  # imported, never restated as a second literal
from drift.kernels.models import Anchor, Check, EvClaim, SSlot
from drift.persistence.models import JournalRecord

__all__ = ["RunReadModel", "build_read_model", "cell_results_of", "claim_from_payload"]

#: Every stream the read model touches, read in one query and partitioned in Python: they are
#: all scoped by `run_id`, so a query per stream would only buy round trips.
_READ_STREAMS = (
    "claim_inventory",
    "gate_outcome",
    "gate_ungateable",
    "gate_kill",
    "preview_verdict",
    "s_verdict",
    "agent_coverage",
    "cell_result",
    "frame_plan",
)

#: The disposition row's two never-ran states, in banner order. Kept apart from `never_reported`
#: below: a cell that never ran is not a cell that ran and failed to report.
_NEVER_RAN_STATES = ("deferred_by_wallet", "never_dispatched")


def _join_key(doc_path, literal, predicate, producer, args) -> tuple:
    """The identity surface every claim-naming stream reduces to.

    Constructed on both sides rather than read: the side streams carry a claim reference and
    `claim_inventory` does not, but it does carry every component of one.
    """
    return (doc_path, literal, predicate, producer, tuple(args or ()))


def _key_of_side_stream(payload: dict) -> tuple:
    """The join key as a claim-naming side stream spells its components."""
    return _join_key(
        payload.get("doc_path"),
        payload.get("literal"),
        payload.get("predicate"),
        payload.get("producer"),
        payload.get("normalized_args"),
    )


def _key_of_inventory(payload: dict) -> tuple | None:
    """None for an unbound claim: no check, so no identity, and no gate row to join against."""
    anchor, check = payload.get("anchor") or {}, payload.get("check")
    if not check:
        return None
    return _join_key(
        anchor.get("doc_path"),
        anchor.get("literal"),
        check.get("predicate"),
        (payload.get("provenance") or {}).get("producer"),
        check.get("normalized_args"),
    )


def claim_from_payload(payload: dict) -> EvClaim:
    """Rebuild the `EvClaim` a `claim_inventory` row recorded — a decode, not a reconstruction.

    The row holds every field, so nothing here is inferred or defaulted. That is what lets a
    finding rebuilt from rows behave like one built during the run: resolving an issue later
    replays the check this carries, and a lossy rebuild would change what gets replayed.
    """
    anchor = payload["anchor"]
    check_payload = payload.get("check")
    check = (
        Check(
            predicate=check_payload["predicate"],
            raw=dict(check_payload.get("raw") or {}),
            normalization=dict(check_payload.get("normalization") or {}),
            normalized_args=tuple(check_payload.get("normalized_args") or ()),
        )
        if check_payload
        else None
    )
    s_slot = payload.get("s_slot") or {}
    return EvClaim(
        anchor=Anchor(
            doc_path=anchor["doc_path"],
            spans=tuple(tuple(span) for span in (anchor.get("spans") or ())),
            literal=anchor.get("literal", ""),
        ),
        check=check,
        claim_class=int(payload.get("claim_class", 1)),
        s_slot=SSlot(note=s_slot.get("note", ""), confidence=s_slot.get("confidence", 1.0)),
        provenance=dict(payload.get("provenance") or {}),
    )


@dataclass
class RunReadModel:
    """What the frame's tail needs, rebuilt from rows — reconcile input, then report input."""

    findings: list[Finding] = field(default_factory=list)
    ranked_entries: list[RankedEntry] = field(default_factory=list)
    coverages: list[dict] = field(default_factory=list)
    kernel_errors: list[dict] = field(default_factory=list)
    #: `cell_result` payloads, keyed by `(producer, unit_ref)` — the fan-in's own record.
    cell_results: dict[tuple[str, str], dict] = field(default_factory=dict)
    #: Planned cells with no result row of their own, `{unit, status}`. A deferred cell
    #: writes no row at all, so without this a short run never says what it skipped.
    cell_shortfalls: list[dict] = field(default_factory=list)

    def as_state(self, partial_notes: list[str]) -> dict:
        """The dict `render_report` consumes: the same channels a graph invocation fills."""
        return {
            "findings": self.findings,
            "ranked_entries": self.ranked_entries,
            "coverages": self.coverages,
            "kernel_errors": self.kernel_errors,
            "cell_shortfalls": self.cell_shortfalls,
            "partial_notes": partial_notes,
        }


def _streams(session, run_id: int) -> dict[str, list[dict]]:
    """This run's journal payloads, partitioned by stream and kept in write order."""
    rows = (
        session.query(JournalRecord)
        .filter(JournalRecord.run_id == run_id)
        .filter(JournalRecord.record_type.in_(_READ_STREAMS))
        .order_by(JournalRecord.id)
        .all()
    )
    out: dict[str, list[dict]] = {name: [] for name in _READ_STREAMS}
    for row in rows:
        out[row.record_type].append(row.payload or {})
    return out


def cell_results_of(session, run_id: int) -> dict[tuple[str, str], dict]:
    """This run's `cell_result` payloads keyed by cell key — the fan-in's completion predicate.

    Its own query rather than a slice of `build_read_model`: the fan-in polls many times per run,
    and must not drag the bulkiest stream along on every poll.
    """
    rows = (
        session.query(JournalRecord)
        .filter_by(run_id=run_id, record_type="cell_result")
        .order_by(JournalRecord.id)
        .all()
    )
    results: dict[tuple[str, str], dict] = {}
    for row in rows:
        payload = row.payload or {}
        key = payload.get("cell_key") or []
        if len(key) == 2:
            # First writer wins: a redelivered cell writes no second row, but a corpus read
            # back from an export must not depend on that.
            results.setdefault((key[0], key[1]), payload)
    return results


def build_read_model(session, run_id: int) -> RunReadModel:
    """Rebuild `run_id`'s findings, ranked tier, coverage and kernel errors from its rows."""
    streams = _streams(session, run_id)
    inventory = streams["claim_inventory"]

    gate_by_key = {_key_of_side_stream(p): p for p in streams["gate_outcome"]}
    verdict_by_key = {_key_of_side_stream(p): p for p in streams["s_verdict"]}
    preview_by_key = {_key_of_side_stream(p): p for p in streams["preview_verdict"]}
    journal_only_ungateable = {
        _key_of_side_stream(p)
        for p in streams["gate_ungateable"]
        if p.get("reason") not in _COMMENT_REASONS
    }
    killed = {_key_of_side_stream(p) for p in streams["gate_kill"]}

    # A finding needs the gate to have certified the claim, and a confident live verdict.
    # Confidence is not a refinement of `live`: an uncertain judge must suppress, never mint.
    high_keys = {
        key
        for key, gate in gate_by_key.items()
        if gate.get("outcome") == "M_CERTIFIED"
        and (verdict := verdict_by_key.get(key)) is not None
        and verdict.get("live") is True
        and verdict.get("error") is not True
        # an absent `confidence` is not a confident verdict
        and (verdict.get("confidence") or 0.0) >= S_THRESHOLD
    }

    # A claim leaves the ranked tier once the gate has answered it: certified, passing, killed, or
    # declined for an unsurfaced reason. A kill is an answer, not a lead.
    excluded = set(gate_by_key) | journal_only_ungateable | killed

    findings: list[Finding] = []
    ranked: list[RankedEntry] = []
    seen_high: set[tuple] = set()
    for payload in inventory:
        claim = claim_from_payload(payload)
        key = _key_of_inventory(payload)
        if key is not None and key in high_keys:
            if key not in seen_high:
                seen_high.add(key)
                findings.append(_to_finding(claim))
            continue
        if key is not None and key in excluded:
            continue
        preview = preview_by_key.get(key) if key is not None else None
        annotation = (
            _preview_annotation_text(
                claim.check.predicate, preview.get("outcome"), preview.get("detail")
            )
            if preview is not None and claim.check is not None
            else None
        )
        ranked.append(RankedEntry(claim, annotation))

    # The incompleteness banner reads these together with the kernel errors below.
    coverages = list(streams["agent_coverage"])
    kernel_errors = [
        {
            "unit": p.get("doc_path", ""),
            "status": "kernel_error",
            "detail": p.get("detail", ""),
        }
        for p in streams["gate_kill"]
        if p.get("kind") == "kernel_error"
    ]

    results: dict[tuple[str, str], dict] = {}
    for payload in streams["cell_result"]:
        key = payload.get("cell_key") or []
        if len(key) == 2:
            results.setdefault((key[0], key[1]), payload)

    return RunReadModel(
        findings=findings,
        ranked_entries=ranked,
        coverages=coverages,
        kernel_errors=kernel_errors,
        cell_results=results,
        cell_shortfalls=_cell_shortfalls(streams["frame_plan"], results),
    )


def _cell_shortfalls(frame_plan: list[dict], results: dict[tuple[str, str], dict]) -> list[dict]:
    """Planned cells that produced no result of their own — the banner's cell-side input.

    Derived from the disposition row and the `cell_result` rows rather than stored, as the fitness
    check derives its own unreported set, so the two cannot disagree about which cells reported.
    No disposition row means no entries: inventing them from the plan row would describe an
    abandoned run as a partly-deferred one.
    """
    rows = [p for p in frame_plan if p.get("phase") == "disposition"]
    if not rows:
        return []
    disposition = rows[-1]
    out: list[dict] = []
    for producer, unit_ref in (tuple(k) for k in (disposition.get("funded") or [])):
        if (producer, unit_ref) not in results:
            out.append({"unit": f"{producer}:{unit_ref}", "status": "never_reported"})
    for state in _NEVER_RAN_STATES:
        for producer, unit_ref in (tuple(k) for k in (disposition.get(state) or [])):
            out.append({"unit": f"{producer}:{unit_ref}", "status": state})
    return out
