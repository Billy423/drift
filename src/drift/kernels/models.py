"""The types a producer and a kernel meet on, and the closed vocabularies the gate enforces."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Anchor:
    """Where a claim lives in a document: path, line spans, and the exact literal."""

    doc_path: str
    spans: tuple[tuple[int, int], ...]
    literal: str


@dataclass(frozen=True)
class Check:
    """A claim's replayable mechanical leg.

    `normalization` and `normalized_args` are recorded, not re-derived: replay reads them back,
    so a claim resolves the same way however it was produced.
    """

    predicate: str
    raw: dict
    normalization: dict
    normalized_args: tuple[str, ...]


@dataclass(frozen=True)
class SSlot:
    """A producer's own read on whether a claim is live: journal data, never truth."""

    note: str
    confidence: float


@dataclass(frozen=True)
class EvClaim:
    """One assertion a document makes about the code, with its check if one could be built."""

    anchor: Anchor
    check: Check | None
    claim_class: int  # 1 existence · 2 property · 3 behavioral/unbindable
    s_slot: SSlot
    provenance: dict  # {"agent_ver": ...} — never part of identity


# Closed set: the gate rejects any reason not listed here. Each must be decidable by the
# kernel from the repository alone — a reason requiring judgement does not belong.
UNGATEABLE_REASONS = frozenset(
    {
        "external",  # not this repository's own API, so its truth is elsewhere
        "module-unreachable",  # generated/absent module; nothing to resolve against
        "variadic",  # **kwargs swallows the param — absence proves nothing
        "no-makefile",  # no root Makefile to adjudicate a target against
        "gitignored",  # absent but ignored: build output, not drift
        "makefile-includes",  # `include` directives — the target set is not knowable
        "base-ambiguous",  # bare path missing at its syntax base, present elsewhere in the tree
        "no-manifest",  # no package.json to adjudicate a script key against
        "manifest-unparseable",  # manifest present but not valid JSON
        "no-signature",  # symbol resolves but its signature is statically underivable
        "external-base",  # the MRO reaches an external base without a hit — unknowable
        "not-a-class",  # class_has_member declining a target it does not fit
    }
)


# Why a claim did or did not get a check, recorded into `provenance["bind"]`. Closure is
# held by a test rather than by the gate: only the claim assembler writes these.
BIND_OUTCOMES = frozenset(
    {
        "bound",  # a check exists; the proposal (if any) lives in check.raw
        "model-none",  # the model itself said no predicate fits
        "unregistered-predicate",  # the model named a predicate outside the registry
        "args-rejected",  # the anchoring guard refused the args
        "normalize-declined",  # the predicate's own normalize refused literal/args
    }
)


# The closed producer vocabulary. Closure is write-side only: `canonical_producer` folds what
# is already stored, because no journal row is ever migrated.
PRODUCERS: frozenset[str] = frozenset(
    {
        "agent",  # the discovery agent (agent/discovery.py)
        "docstrings",  # the deterministic docstring reader (docstrings.py)
    }
)

# Journaled per run, so a run says which vocabulary produced its rows. Bump with PRODUCERS.
PRODUCERS_VER = "1"


def require_admitted_producer(claim: EvClaim, where: str = "") -> str:
    """Raise unless the claim names an admitted producer; return that producer.

    Only the call on claims entering the pipeline is a guarantee. The journal write boundary
    calls this again as defence in depth, but there a raise lands inside a handler that rolls
    back and continues. An absent producer is not the harmless case it looks: the ranked tier
    separates the producers by complement, so a claim naming none renders as the docstring one's.

    Args:
        where: Free-text context for the message; it never affects the decision.
    """
    producer = claim.provenance.get("producer", "")
    if producer not in PRODUCERS:
        context = f" (at {where})" if where else ""
        raise ValueError(
            f"producer {producer!r}{context} is not in the closed vocabulary "
            f"{sorted(PRODUCERS)}. The old singular 'docstring' is accepted on read only "
            f"(canonical_producer)."
        )
    return producer


def canonical_producer(stored: str | None) -> str | None:
    """Read side: fold a stored producer string onto today's vocabulary.

    Lenient where the write boundary is strict, because no stored row is ever migrated: a reader
    that raised on an older spelling could not open its own corpus. Unrecognised values pass
    through unchanged — the closure says what may be written, not what exists.
    """
    return _PRODUCER_ALIASES.get(stored, stored)


# Read side only: writing "docstring" must fail rather than be silently corrected, or both
# spellings go on being produced.
_PRODUCER_ALIASES = {"docstring": "docstrings"}


class Ungateable(Exception):
    """A kernel's statement that a claim is unadjudicable — never that it is false.

    `reason` must be a member of `UNGATEABLE_REASONS`, which the gate enforces. Replay maps it
    to keep-open.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
