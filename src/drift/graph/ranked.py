"""The ranked tier's carrier type: one candidate the report shows as a lead, never as a finding.

A pipeline type rather than a rendering one — the graph constructs these and carries them as a
state channel, so the renderer imports this module and not the reverse.
"""

from __future__ import annotations

from dataclasses import dataclass

from drift.kernels.models import EvClaim

__all__ = ["RankedEntry"]


@dataclass(frozen=True)
class RankedEntry:
    """One ranked-tier candidate: the claim, plus a mechanical note where one applies.

    An entry carries no score. The report bands entries by the claim's own confidence, and within
    a band the order is arrival order rather than a ranking.

    Attributes:
        annotation: A preview-grade predicate's result. Informational, never a verdict — it stays
            out of the frozen claim and out of the verified findings.
    """

    claim: EvClaim
    annotation: str | None = None
