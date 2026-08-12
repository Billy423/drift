"""Verify that claim binding records a closed set of outcomes and rejected proposals."""

from drift.agent.discovery import DiscoveryAgent
from drift.journal.serialize import claim_payload
from drift.kernels.models import BIND_OUTCOMES


def _agent():
    """Build a discovery agent without a live model client."""
    return DiscoveryAgent(client=object())


def _item(predicate, args, literal="plain(b=2)"):
    """Build a minimal emitted claim proposal."""
    return {
        "literal": literal,
        "predicate": predicate,
        "args": args,
        "spans": [[3, 3]],
        "claim_class": 2,
        "note": "",
        "confidence": 0.5,
    }


def test_the_outcome_vocabulary_is_closed():
    """Keep binding outcomes within the declared five-value vocabulary."""
    assert BIND_OUTCOMES == frozenset(
        {"bound", "model-none", "unregistered-predicate", "args-rejected", "normalize-declined"}
    )


def test_model_none_is_recorded():
    """Record an explicit model decline without a bound check."""
    claim = _agent()._assemble(_item("none", []), "README.md")
    assert claim.check is None
    assert claim.provenance["bind"] == {
        "outcome": "model-none",
        "proposed_predicate": "none",
        "proposed_args": [],
    }


def test_unregistered_predicate_is_recorded_with_the_models_own_words():
    """Preserve an unregistered predicate proposal for later analysis."""
    claim = _agent()._assemble(_item("has_config_option", ["plain", "b"]), "README.md")
    assert claim.check is None
    assert claim.provenance["bind"]["outcome"] == "unregistered-predicate"
    assert claim.provenance["bind"]["proposed_predicate"] == "has_config_option"
    assert claim.provenance["bind"]["proposed_args"] == ["plain", "b"]


def test_anchoring_rejection_is_recorded():
    """Distinguish rejected arguments from an explicit model decline."""
    claim = _agent()._assemble(
        _item("signature_has_param", ["otherpkg.other.fn", "b"]), "README.md"
    )
    assert claim.check is None
    assert claim.provenance["bind"]["outcome"] == "args-rejected"
    assert claim.provenance["bind"]["proposed_args"] == ["otherpkg.other.fn", "b"]


def test_normalize_decline_is_recorded():
    """Record a predicate normalization decline separately from argument rejection."""
    claim = _agent()._assemble(_item("signature_has_param", ["plain", "b"]), "README.md")
    assert claim.check is None
    assert claim.provenance["bind"]["outcome"] == "normalize-declined"
    assert claim.provenance["bind"]["proposed_predicate"] == "signature_has_param"


def test_a_bound_claim_records_bound_and_nothing_else():
    """Avoid duplicating proposal details already stored on a bound check."""
    claim = _agent()._assemble(_item("signature_has_param", ["mypkg.mod.plain", "b"]), "README.md")
    assert claim.check is not None
    assert claim.provenance["bind"] == {"outcome": "bound"}


def test_every_emitted_outcome_is_in_the_closed_set():
    """Require every binding path to emit a declared outcome."""
    cases = [
        _item("none", []),
        _item("nope_not_real", []),
        _item("signature_has_param", ["otherpkg.other.fn", "b"]),
        _item("signature_has_param", ["plain", "b"]),
        _item("signature_has_param", ["mypkg.mod.plain", "b"]),
    ]
    for item in cases:
        claim = _agent()._assemble(item, "README.md")
        assert claim.provenance["bind"]["outcome"] in BIND_OUTCOMES


def test_the_writer_can_only_assign_members_of_the_closed_set():
    """Keep direct double-quoted outcome assignments in the closed vocabulary."""
    import inspect
    import re

    import drift.agent.discovery as discovery

    src = inspect.getsource(discovery.DiscoveryAgent._assemble)
    assigned = set(re.findall(r'bind_outcome = "([^"]+)"', src))
    assert assigned, "the source scan found no assignments — the lock has gone stale"
    assert assigned <= BIND_OUTCOMES, assigned - BIND_OUTCOMES


def test_malformed_args_still_keep_the_models_words():
    """Preserve malformed model arguments when recording their rejection."""
    claim = _agent()._assemble(_item("signature_has_param", "plain, b"), "README.md")
    assert claim.check is None
    assert claim.provenance["bind"]["outcome"] == "args-rejected"
    assert claim.provenance["bind"]["proposed_args"] == ["plain, b"]


def test_claim_inventory_rows_carry_the_bind_block():
    """Carry binding provenance into serialized claim inventory rows."""
    claim = _agent()._assemble(_item("none", []), "README.md")
    row = claim_payload(claim, doc_hash="abc", lane="agent_coverage")
    assert row["provenance"]["bind"]["outcome"] == "model-none"


def test_confidence_is_bounded_to_the_range_the_schema_advertises():
    """The report bands on this value, and nothing upstream enforces the range.

    The output schema states 0 to 1 in its description only — it carries no minimum or maximum,
    so strict tool validation says nothing about it. The clamp is the one place the value is
    bounded before it decides which section of a report a claim lands in.
    """
    agent = _agent()

    high = dict(_item("path_exists", []), confidence=1.5)
    low = dict(_item("path_exists", []), confidence=-3.0)

    assert agent._assemble(high, "docs/g.md").s_slot.confidence == 1.0
    assert agent._assemble(low, "docs/g.md").s_slot.confidence == 0.0
