"""A claim's journal payloads, built in one place so every stream describes it identically.

A journal row carries no claim id; it names the claim by its fields. One function writes those
shared fields everywhere, so two streams cannot drift apart through an edit to one of them.
"""

from __future__ import annotations

from drift.kernels.models import Anchor, Check, EvClaim, SSlot, require_admitted_producer

__all__ = [
    "anchor_ref",
    "anchor_payload",
    "check_payload",
    "claim_payload",
    "claim_ref",
    "producer_of",
    "s_slot_payload",
]


def producer_of(claim: EvClaim) -> str:
    """The `producer` field of a journal row, refusing any name outside the vocabulary.

    The write boundary is strict where readers are lenient, so nothing new is ever written under
    a name no reader was taught. This is defence in depth, not the enforcement point: a raise
    here lands inside the journal's fail-soft wrapper, which rolls back the unit and continues.

    Raises:
        ValueError: If the claim names no admitted producer. An empty name raises too — the
            report tells the producers apart by name, so a nameless claim would render as the
            other one's.
    """
    return require_admitted_producer(claim, where="journal payload")


def anchor_ref(anchor: Anchor) -> dict:
    """The key every claim-naming stream carries: which literal, in which document."""
    return {"literal": anchor.literal, "doc_path": anchor.doc_path}


def claim_ref(claim: EvClaim) -> dict:
    """`anchor_ref` plus the identity fields, for streams that adjudicate a claim.

    The anchor alone is not unique: the docstring producer anchors on a line of docstring text,
    so two functions documenting the same parameter share one. Rows are joined back to a single
    claim on the identity fields — predicate, document path, normalized arguments.
    """
    return {
        **anchor_ref(claim.anchor),
        "predicate": claim.check.predicate if claim.check else "",
        "producer": producer_of(claim),
        "normalized_args": list(claim.check.normalized_args) if claim.check else [],
    }


def anchor_payload(anchor: Anchor) -> dict:
    """The full anchor: the shared key plus the line spans, which only the inventory records."""
    return {**anchor_ref(anchor), "spans": [list(span) for span in anchor.spans]}


def check_payload(check: Check | None) -> dict | None:
    """The replayable mechanical leg, or None when no predicate bound the claim.

    None is a value, not a missing field: the null rows are the queue of assertions no predicate
    reaches yet, so the absence is recorded rather than the row omitted.
    """
    if check is None:
        return None
    return {
        "predicate": check.predicate,
        "raw": dict(check.raw),
        "normalization": dict(check.normalization),
        "normalized_args": list(check.normalized_args),
    }


def s_slot_payload(s_slot: SSlot) -> dict:
    """The producer's own confidence read, stored as data rather than as a verdict.

    `note` is stored whole: it is the text the ranked report renders.
    """
    return {"note": s_slot.note, "confidence": s_slot.confidence}


def claim_payload(claim: EvClaim, doc_hash: str, lane: str) -> dict:
    """One inventory row: the claim as its producer emitted it, plus its context.

    Args:
        doc_hash: The version of the document the claim was read from. Empty for the docstring
            producer, whose corpus is not keyed by a document file.
        lane: The journal `component` this row is written under, repeated inside the payload so
            that a row read on its own still says which producer spoke.
    """
    # Not dead code: re-checks the producer name that the block below copies verbatim.
    producer_of(claim)
    return {
        "anchor": anchor_payload(claim.anchor),
        "check": check_payload(claim.check),
        "claim_class": claim.claim_class,
        "s_slot": s_slot_payload(claim.s_slot),
        "provenance": dict(claim.provenance),
        "doc_hash": doc_hash,
        "lane": lane,
    }
