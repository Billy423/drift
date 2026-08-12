"""Tests the closed producer vocabulary's strict writes and lenient reads."""

import pytest

from drift.docstrings import DocstringProducer
from drift.graph.ranked import RankedEntry
from drift.journal.serialize import claim_payload, claim_ref
from drift.kernels.models import (
    PRODUCERS,
    PRODUCERS_VER,
    Anchor,
    Check,
    EvClaim,
    SSlot,
    canonical_producer,
)
from drift.report.render import to_markdown


def _claim(producer=None, literal="docs/x.md"):
    """Build a checked claim with an optional producer spelling."""
    provenance = {"agent_ver": "agent/x"}
    if producer is not None:
        provenance["producer"] = producer
    return EvClaim(
        anchor=Anchor(doc_path="README.md", spans=((3, 3),), literal=literal),
        check=Check(
            predicate="link_resolves",
            raw={"literal": literal, "doc_path": "README.md"},
            normalization={"base": "doc-relative"},
            normalized_args=(literal,),
        ),
        claim_class=1,
        s_slot=SSlot(note="n", confidence=0.5),
        provenance=provenance,
    )


def test_the_vocabulary_is_exactly_the_two_producers():
    """The writable producer vocabulary contains exactly the two product producers."""
    assert PRODUCERS == {"agent", "docstrings"}


def test_the_docstring_lane_emits_the_plural_spelling(tmp_path):
    """The docstring producer stamps claims with the canonical plural spelling."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "m.py").write_text(
        '''
def f(timeout):
    """Do a thing.

    Args:
        timeout: how long to wait.
    """
'''
    )
    claims, _coverage = DocstringProducer(str(tmp_path), agent_ver="agent/x").produce()
    assert claims, "fixture must emit at least one docstring-lane claim"
    assert {c.provenance["producer"] for c in claims} == {"docstrings"}


def test_the_lane_string_and_the_producer_string_now_agree(tmp_path):
    """A payload's lane and producer fields use the same canonical spelling."""
    payload = claim_payload(_claim("docstrings"), doc_hash="", lane="docstrings")
    assert payload["lane"] == payload["provenance"]["producer"]


@pytest.mark.parametrize(
    ("stored", "expected"),
    [("docstring", "docstrings"), ("docstrings", "docstrings"), ("agent", "agent")],
)
def test_a_reader_folds_the_old_spelling_onto_the_new(stored, expected):
    """The read boundary canonicalizes known stored producer spellings."""
    assert canonical_producer(stored) == expected


def test_a_reader_passes_an_unrecognised_stored_value_through_unchanged():
    """The read boundary preserves unknown historical producer spellings."""
    assert canonical_producer("golden-set") == "golden-set"
    assert canonical_producer(None) is None


def test_the_ranked_tier_reads_a_stored_old_spelling_as_the_deterministic_lane():
    """The ranked tier treats the old docstring spelling as the deterministic producer."""
    entries = [
        RankedEntry(claim=_claim("agent", literal="a.md")),
        RankedEntry(claim=_claim("docstring", literal="b.md")),
    ]
    md = to_markdown([], entries, [])
    agent_at = md.index("a.md")
    det_at = md.index("b.md")
    # The deterministic section follows the discovery section in the rendered report.
    assert agent_at < det_at


def test_an_unknown_producer_raises_when_a_claim_naming_row_is_built():
    """Building a claim reference rejects an unknown producer."""
    with pytest.raises(ValueError, match="docstrings"):
        claim_ref(_claim("agnet"))


def test_an_unknown_producer_raises_when_the_inventory_row_is_built():
    """Building an inventory payload rejects an unknown producer."""
    with pytest.raises(ValueError, match="agnet"):
        claim_payload(_claim("agnet"), doc_hash="", lane="agent")


def test_a_claim_with_no_producer_at_all_raises_too():
    """Building a claim reference rejects a missing producer."""
    with pytest.raises(ValueError):
        claim_ref(_claim(None))


class _NeverFailingWriter:
    """Record journal calls without introducing writer failures."""

    def __init__(self):
        """Start with an empty row log."""
        self.rows: list[tuple[str, str, dict]] = []

    def write(self, component, record_type, payload):
        """Record a requested journal write."""
        self.rows.append((component, record_type, payload))

    def flush(self):
        """Accept a flush without side effects."""

    def rollback(self):
        """Accept a rollback without side effects."""


class _FakeAgent:
    """Return configured claims from one discovery unit."""

    def __init__(self, claims):
        """Store the claims to return."""
        self._claims = claims

    def discover(self, repo_root, doc_path):
        """Return configured claims with complete discovery coverage."""
        from drift.agent.discovery import DiscoveryResult

        return DiscoveryResult(
            claims=list(self._claims),
            coverage={
                "unit": doc_path,
                "doc_hash": "h",
                "turns_used": 1,
                "tool_calls": 0,
                "status": "complete",
                "usage": {},
            },
        )


class _FakeProducer:
    """Return configured claims from the docstring corpus."""

    def __init__(self, claims):
        """Store the claims to return."""
        self._claims = claims

    def produce(self, doc_filter=None):
        """Return configured claims with complete corpus coverage."""
        return list(self._claims), {
            "unit": "docstring_corpus",
            "symbols_walked": 1,
            "claims_emitted": len(self._claims),
            "status": "complete",
        }


def _discover_state(**over):
    """Build minimal discovery state with optional field overrides."""
    state = {
        "repo_root": "/irrelevant",
        "worklist": ["d.md"],
        "claims": [],
        "coverages": [],
        "partial_notes": [],
        "spend": 0.0,
        "budget": None,
        "doc_filter": None,
    }
    state.update(over)
    return state


def test_an_unadmitted_producer_fails_fast_at_agent_lane_ingress_in_default_mode():
    """Discovery ingress rejects an unknown producer before fail-soft journaling."""
    from drift.graph.nodes.discover import make_discover

    writer = _NeverFailingWriter()
    node = make_discover(_FakeAgent([_claim("docstring")]), writer)

    with pytest.raises(ValueError, match="docstring"):
        node(_discover_state())

    # Ingress validation must happen before fail-soft journaling can swallow the error.
    assert writer.rows == []


def test_an_unadmitted_producer_fails_fast_at_docstring_lane_ingress_in_default_mode():
    """Docstring ingress rejects an unknown producer after producer isolation."""
    from drift.graph.nodes.discover import make_discover_docstrings

    writer = _NeverFailingWriter()
    node = make_discover_docstrings(lambda root: _FakeProducer([_claim("docstring")]), writer)

    with pytest.raises(ValueError, match="docstring"):
        node(_discover_state())

    assert writer.rows == []


def test_ingress_raises_before_the_claim_can_reach_any_rendering_surface():
    """Ingress rejects an unknown producer before the report can render the claim."""
    from drift.graph.nodes.discover import make_discover

    bad = _claim("agnet")
    writer = _NeverFailingWriter()
    node = make_discover(_FakeAgent([bad]), writer)

    with pytest.raises(ValueError) as exc:
        node(_discover_state())

    assert "agnet" in str(exc.value)
    # Producer admission is independent of strict journal mode.
    assert type(exc.value) is ValueError
    # Historical reads remain lenient, so write-side ingress must carry the guarantee.
    assert "docs/x.md" in to_markdown([], [RankedEntry(claim=bad)], [])


def test_an_admitted_producer_passes_ingress_untouched():
    """Ingress preserves a claim carrying an admitted producer."""
    from drift.graph.nodes.discover import make_discover

    good = _claim("agent")
    out = make_discover(_FakeAgent([good]), _NeverFailingWriter())(_discover_state())

    assert out["claims"] == [good]


def test_the_report_boundary_still_renders_an_unknown_producer():
    """The report remains lenient when reading an unknown stored producer."""
    md = to_markdown([], [RankedEntry(claim=_claim("golden-set"))], [])
    assert "docs/x.md" in md


def test_the_vocabulary_carries_a_version_for_rail_config():
    """The producer vocabulary exposes a non-empty version for run configuration."""
    assert isinstance(PRODUCERS_VER, str) and PRODUCERS_VER
