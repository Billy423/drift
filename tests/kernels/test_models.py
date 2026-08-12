from drift.kernels.models import Anchor, Check, EvClaim, SSlot


def _claim(check=None):
    return EvClaim(
        anchor=Anchor(doc_path="CLAUDE.md", spans=((3, 3),), literal="architecture/ARCH.md"),
        check=check,
        claim_class=1,
        s_slot=SSlot(note="reads live", confidence=0.9),
        provenance={"agent_ver": "agent/0.1"},
    )


def test_evclaim_is_frozen_and_holds_two_legs():
    check = Check(
        predicate="path_exists",
        raw={"literal": "architecture/ARCH.md", "doc_path": "CLAUDE.md"},
        normalization={"base": "repo-root"},
        normalized_args=("architecture/ARCH.md",),
    )
    claim = _claim(check)
    assert claim.check.normalized_args == ("architecture/ARCH.md",)
    assert claim.anchor.literal in claim.check.raw["literal"]


def test_unbindable_claim_has_no_check():
    assert _claim(None).check is None
