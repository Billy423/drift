"""Tests that claim references carry a recomputable identity surface."""

from drift.journal.serialize import claim_ref
from drift.kernels.models import Anchor, Check, EvClaim, SSlot


def _claim(check):
    """Build a claim with an optional mechanical check."""
    return EvClaim(
        anchor=Anchor(doc_path="README.md", spans=((3, 3),), literal="docs/x.md"),
        check=check,
        claim_class=1 if check else 3,
        s_slot=SSlot(note="", confidence=0.5),
        provenance={"producer": "agent", "agent_ver": "agent/0.7"},
    )


def test_a_checked_claim_yields_the_full_identity_surface():
    """A checked claim names its predicate, producer, document, literal, and arguments."""
    check = Check(
        predicate="link_resolves",
        raw={"literal": "docs/x.md", "doc_path": "README.md"},
        normalization={"base": "doc-relative"},
        normalized_args=("docs/x.md",),
    )
    assert claim_ref(_claim(check)) == {
        "literal": "docs/x.md",
        "doc_path": "README.md",
        "predicate": "link_resolves",
        "producer": "agent",
        "normalized_args": ["docs/x.md"],
    }


def test_an_unchecked_claim_still_names_its_producer():
    """An unchecked claim uses empty check fields while retaining its producer."""
    ref = claim_ref(_claim(None))
    assert ref["predicate"] == ""
    assert ref["normalized_args"] == []
    assert ref["producer"] == "agent"
