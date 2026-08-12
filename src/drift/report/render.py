"""The markdown report: an incompleteness banner, the verified findings, then the candidates.

A verified finding passed the replay gate and the judge; a candidate carries no high-grade
binding. Claims the judge found not live belong to neither section and stay in the journal.
"""

from __future__ import annotations

from drift.domain.findings import Finding
from drift.graph.ranked import RankedEntry
from drift.kernels.models import canonical_producer

# The one producer whose confidence is a real read. The other stamps a constant on everything
# it emits, so banding its claims would rank them against nothing.
_AGENT_PRODUCER = "agent"


def to_markdown(
    findings: list[Finding],
    ranked_entries: list[RankedEntry],
    incomplete_units: list[dict],
    partial_notes: list[str] | None = None,
) -> str:
    """Render the whole report as Markdown.

    Args:
        incomplete_units: Units that did not scan cleanly, each with the status naming why.
        partial_notes: Reasons the scan stopped early, banner-rendered so that a reader cannot
            mistake a scan that ran out of money for one that found nothing.
    """
    lines: list[str] = ["# drift report", ""]
    if incomplete_units:
        units = ", ".join(_incomplete_label(u) for u in incomplete_units)
        lines += [
            f"> ⚠ INCOMPLETE — {len(incomplete_units)} unit(s) not fully scanned ({units}).",
            "",
        ]
    for note in partial_notes or []:
        lines += [f"> ⚠ PARTIAL — {note}", ""]
    lines += _high_section(findings)
    lines += _ranked_section(ranked_entries)
    return "\n".join(lines)


def _incomplete_label(unit: dict) -> str:
    """`unit (status)`, so that a hole and a deliberate deferral do not read identically."""
    name = unit.get("unit", "?")
    status = unit.get("status")
    return f"{name} ({status})" if status else name


def _high_section(findings: list[Finding]) -> list[str]:
    """The verified section: one entry per finding, with the claim and the code truth beside it."""
    out = [f"## Verified findings — {len(findings)}", ""]
    if not findings:
        return out + ["_none_", ""]
    for f in findings:
        out += [
            f"- **{f.check_id}** · `{f.doc_location.file}:{f.doc_location.start_line}`",
            f"  - doc claims: {f.evidence.doc_claim}",
            f"  - code truth: {f.evidence.code_truth}",
            f"  - {f.summary}",
        ]
    return out + [""]


# The suspected band's upper bound, inclusive: claims at or below it concentrated known drift.
# Calibrated on one version of the discovery prompt; re-measure before adding a finer cut.
SUSPECTED_BAND_MAX = 0.2


def _ranked_section(entries: list[RankedEntry]) -> list[str]:
    """The candidate tier: suspected first, split by producer, honest about order within a band.

    Sorting by descending confidence is the obvious move and is wrong — confidence here means
    "sure the claim still holds", so it would sink suspected drift to the bottom. Within a band
    the order is arrival order, and the heading says so.
    """
    out = [
        f"## Ranked tier (candidates — UNVERIFIED) — {len(entries)}",
        "",
        "_Not certified by the replay gate. These are candidates the agent surfaced, banded by "
        "its own confidence that each claim still holds — read them as leads, never as "
        "findings._",
        "",
    ]
    if not entries:
        return out + ["_none_", ""]

    # Read through `canonical_producer`: a report may be rendered from rows written under an
    # older spelling, and reading one must not crash on a name that was legal when written.
    def _from_agent(e: RankedEntry) -> bool:
        return canonical_producer(e.claim.provenance.get("producer")) == _AGENT_PRODUCER

    agent = [e for e in entries if _from_agent(e)]
    other = [e for e in entries if not _from_agent(e)]
    # One partition, not two comparisons: both would be False for a NaN confidence, and a
    # claim must never silently vanish from the report.
    suspected: list[RankedEntry] = []
    unexamined: list[RankedEntry] = []
    for e in agent:
        (suspected if e.claim.s_slot.confidence <= SUSPECTED_BAND_MAX else unexamined).append(e)
    if suspected:
        out += _section(
            f"From the agent · SUSPECTED (confidence <= {SUSPECTED_BAND_MAX}; "
            "not ranked within the band)",
            suspected,
        )
    if unexamined:
        out += _section(
            f"From the agent · unexamined (confidence > {SUSPECTED_BAND_MAX}; "
            "not ranked within the band)",
            unexamined,
        )
    if other:
        out += _section(
            "From the deterministic producer — NOT ranked "
            "(its confidence is a synthetic placeholder)",
            other,
        )
    return out


def _section(title: str, entries: list[RankedEntry]) -> list[str]:
    """One titled band: its entries in the order given, each with any mechanical annotation."""
    out = [f"### {title} — {len(entries)}", ""]
    for e in entries:
        out += [f"- `{e.claim.anchor.doc_path}`: {e.claim.anchor.literal}"]
        if e.annotation:
            out += [f"  - {e.annotation}"]
        out += [f"  - {e.claim.s_slot.note}"]
    return out + [""]
