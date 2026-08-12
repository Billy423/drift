"""The domain types a finding is made of, and the identity rule that deduplicates it."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum


class Confidence(StrEnum):
    """How a finding was decided.

    One member, because a claim no mechanical check can decide does not become a finding at
    all: it stays a candidate in the ranked tier.
    """

    HIGH = "HIGH"  # decided mechanically, without a model call


class IssueStatus(StrEnum):
    """Lifecycle state of a persisted issue. The legal transitions live in the issue store."""

    DISCOVERED = "DISCOVERED"
    REVIEWING = "REVIEWING"
    SUBMITTED = "SUBMITTED"
    MERGED = "MERGED"
    REJECTED = "REJECTED"
    RESOLVED = "RESOLVED"  # reconcile closes an issue whose drift is gone; never set by hand


@dataclass(frozen=True)
class Location:
    """A file span, lines counted from one — either a document site or a code site."""

    file: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Evidence:
    """The contradiction made concrete: what the document claims against what the code is."""

    doc_claim: str  # what the document asserts
    code_truth: str  # what the code is. Code is the truth here; the document is what went stale


@dataclass(frozen=True)
class Finding:
    """One immutable, language-agnostic fact a check emits. It carries no lifecycle and no state."""

    check_id: str
    identity: tuple[str, ...]  # owned by the check that emits it; the only input to dedup_key
    doc_location: Location
    code_anchor: Location | None
    summary: str  # a human one-liner, and not part of identity
    evidence: Evidence
    confidence: Confidence
    check: dict | None = None  # the replayable stored check; excluded from dedup_key

    @property
    def dedup_key(self) -> str:
        """A hash over the check id and the identity tuple, and nothing else.

        Line numbers, evidence and summary are all excluded because each moves as the code and
        the document move. Including any of them would churn the key and break the run-to-run
        match the issue lifecycle rests on.
        """
        core = "|".join([self.check_id, *self.identity])
        return hashlib.sha256(core.encode("utf-8")).hexdigest()[:32]
