"""Agent-lane args proposal: anchoring validation before the shared normalize chokepoint."""

from drift.agent.discovery import DiscoveryAgent


def _agent():
    """Build a discovery agent without a live model client."""
    return DiscoveryAgent(client=object())


def test_valid_proposal_binds_signature_claim():
    """Bind an anchored signature parameter proposal."""
    claim = _agent()._assemble(
        {
            "literal": "plain(b=2)",
            "predicate": "signature_has_param",
            "args": ["mypkg.mod.plain", "b"],
            "spans": [[3, 3]],
            "claim_class": 2,
            "note": "",
            "confidence": 0.5,
        },
        "README.md",
    )
    assert claim.check is not None
    assert claim.check.normalized_args == ("mypkg.mod.plain", "b")
    assert claim.check.raw["proposed_args"] == ["mypkg.mod.plain", "b"]


def test_transposed_symbol_rejected_to_class3():
    """Reject a symbol that is not anchored in the claim literal."""
    claim = _agent()._assemble(
        {
            "literal": "plain(b=2)",
            "predicate": "signature_has_param",
            "args": ["otherpkg.other.fn", "b"],
            "spans": [[3, 3]],
            "claim_class": 2,
            "note": "",
            "confidence": 0.5,
        },
        "README.md",
    )
    assert claim.check is None
    assert claim.claim_class == 3


def test_unanchored_param_rejected():
    """Reject a parameter name absent from the claim literal."""
    args = _agent()._validate_args("plain(b=2)", "signature_has_param", ["mypkg.mod.plain", "zz"])
    assert args is None


def test_no_args_path_still_binds_path_exists():
    """Derive path predicate arguments from the claim literal when none are proposed."""
    claim = _agent()._assemble(
        {
            "literal": "docs/guide.md",
            "predicate": "path_exists",
            "args": [],
            "spans": [[1, 1]],
            "claim_class": 1,
            "note": "",
            "confidence": 0.9,
        },
        "README.md",
    )
    assert claim.check is not None
    assert claim.check.normalized_args == ("docs/guide.md",)


def test_agent_ver_bumped():
    """Pin the discovery producer's default version stamp."""
    assert _agent()._agent_ver == "agent/0.9"


def test_the_sort_keys_two_descriptions_agree():
    """Describe confidence in both surfaces as whether the claim is still true."""
    from drift.agent.discovery import _OUTPUT_SCHEMA, _SYSTEM

    conf_desc = _OUTPUT_SCHEMA["properties"]["claims"]["items"]["properties"]["confidence"][
        "description"
    ]
    assert "still true" in conf_desc
    assert "liveness read" not in conf_desc
    assert "still true" in _SYSTEM
