"""Ungateable waveform: kernel skip -> UNGATEABLE outcome; replay_check keeps issues open."""

import pytest

from drift.gate.replay import GateOutcome, replay, replay_check
from drift.kernels.models import UNGATEABLE_REASONS, Anchor, Check, EvClaim, SSlot, Ungateable
from drift.kernels.registry import Predicate, predicate_registry, register_predicate


def _claim(predicate: str, literal: str, doc_path: str = "README.md") -> EvClaim:
    return EvClaim(
        anchor=Anchor(doc_path=doc_path, spans=((1, 1),), literal=literal),
        check=Check(
            predicate=predicate,
            raw={"literal": literal, "doc_path": doc_path},
            normalization={},
            normalized_args=(literal,),
        ),
        claim_class=1,
        s_slot=SSlot(note="", confidence=0.5),
        provenance={"agent_ver": "agent/0.3"},
    )


def _register_raising(name: str, exc: Exception) -> None:
    def _kernel(repo_root, *args):
        raise exc

    register_predicate(
        Predicate(name=name, description="test", normalize=lambda *a: ({}, ("x",)), kernel=_kernel)
    )


def test_ungateable_reason_carried():
    exc = Ungateable("variadic")
    assert exc.reason == "variadic"


def test_replay_maps_ungateable_to_outcome(tmp_path):
    (tmp_path / "README.md").write_text("see tok\n")
    _register_raising("_t1_ungateable", Ungateable("variadic"))
    try:
        results = replay(str(tmp_path), [_claim("_t1_ungateable", "tok")])
        assert results[0].outcome == GateOutcome.UNGATEABLE
        assert results[0].detail == "variadic"
    finally:
        predicate_registry.pop("_t1_ungateable")


def test_replay_still_maps_other_exceptions_to_kernel_error(tmp_path):
    (tmp_path / "README.md").write_text("see tok\n")
    _register_raising("_t1_boom", ValueError("boom"))
    try:
        results = replay(str(tmp_path), [_claim("_t1_boom", "tok")])
        assert results[0].outcome == GateOutcome.KERNEL_ERROR
    finally:
        predicate_registry.pop("_t1_boom")


def test_an_unknown_ungateable_reason_fails_loudly_at_the_gate(tmp_path):
    """The closed reason set is enforced, not merely documented.

    The failure this prevents: an unadmitted reason used to be routed purely by string
    comparison against `_COMMENT_REASONS`, so it became journal-only in silence, and a new
    kernel could quietly acquire a jurisdiction nobody had decided on. The gate now refuses
    the run instead.
    """
    (tmp_path / "README.md").write_text("see tok\n")
    _register_raising("_t1_unknown_reason", Ungateable("i-made-this-up"))
    try:
        with pytest.raises(ValueError) as exc:
            replay(str(tmp_path), [_claim("_t1_unknown_reason", "tok")])
        assert "i-made-this-up" in str(exc.value)
    finally:
        predicate_registry.pop("_t1_unknown_reason")


def test_every_reason_the_shipped_kernels_raise_is_admitted():
    """The closed set is the whole set: twelve reasons, no more, no fewer.

    The three most recent describe skips the kernels could not previously express:
    `no-signature` (a symbol resolves but its signature is not statically derivable),
    `external-base` (an MRO reaches an external base without a hit, so the member's truth is
    unknowable from the repository alone) and `not-a-class` (`class_has_member` declining a
    target it does not fit). Each was added deliberately rather than by entailment from the
    other two — entailment is how the first additions slipped in unnoticed.
    """
    assert UNGATEABLE_REASONS == {
        "external",
        "module-unreachable",
        "variadic",
        "no-makefile",
        "gitignored",
        "makefile-includes",
        "base-ambiguous",
        "no-manifest",
        "manifest-unparseable",
        "no-signature",
        "external-base",
        "not-a-class",
    }


def test_replay_check_ungateable_keeps_issue_open(tmp_path):
    """Regression lock: Ungateable during stored-check replay -> True (drift still present)."""
    (tmp_path / "README.md").write_text("see tok\n")
    _register_raising("_t1_open", Ungateable("variadic"))
    try:
        check = {
            "predicate": "_t1_open",
            "raw": {"literal": "tok", "doc_path": "README.md"},
            "normalization": {},
            "normalized_args": ["tok"],
        }
        assert replay_check(str(tmp_path), check) is True
    finally:
        predicate_registry.pop("_t1_open")
