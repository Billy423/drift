"""Test docstring discovery merging, filtering, journaling, and failure isolation."""

from drift.graph.nodes.discover import make_discover_docstrings
from drift.kernels.models import Anchor, EvClaim, SSlot


def _claim(literal="timeout"):
    """Build a docstring claim for the requested literal."""
    return EvClaim(
        anchor=Anchor(doc_path="pkg/mod.py", spans=((12, 12),), literal=literal),
        check=None,
        claim_class=2,
        s_slot=SSlot(note="n", confidence=0.5),
        provenance={"agent_ver": "agent/x", "producer": "docstrings"},
    )


class _Writer:
    """Record journal writes in memory."""

    def __init__(self):
        """Initialize an empty row buffer."""
        self.rows = []

    def write(self, component, record_type, payload):
        """Append one journal record."""
        self.rows.append((component, record_type, payload))

    def flush(self):
        """No-op: these tests assert what was written, not when it reached disk."""


class _FakeProducer:
    """Return fixed claims and coverage while recording filters."""

    def __init__(self, claims, coverage):
        """Store the fixed producer output."""
        self._out = (claims, coverage)
        self.doc_filters = []

    def produce(self, doc_filter=None):
        """Record the filter and return the fixed output."""
        self.doc_filters.append(doc_filter)
        return self._out


def _state(**over):
    """Build graph state with optional overrides."""
    base = {"repo_root": "/r", "worklist": [], "claims": [], "coverages": [], "doc_filter": None}
    base.update(over)
    return base


def test_merges_claims_and_journals_coverage():
    """The node merges claims and journals coverage before inventory rows."""
    cov = {"unit": "docstring_corpus", "status": "complete"}
    claim = _claim()
    writer = _Writer()
    node = make_discover_docstrings(lambda root: _FakeProducer([claim], cov), writer)
    out = node(_state(claims=["c0"], coverages=[{"unit": "README.md"}]))
    assert out["claims"] == ["c0", claim]
    assert out["coverages"][-1] == cov
    assert [(c, rt) for c, rt, _p in writer.rows] == [
        ("docstrings", "agent_coverage"),
        ("docstrings", "claim_inventory"),
    ]
    assert writer.rows[0][2] == cov
    assert writer.rows[1][2]["anchor"]["literal"] == "timeout"


def test_doc_filter_is_threaded_into_the_producer_instead_of_skipping_the_node():
    """The node passes a document filter to the producer and journals its output."""
    cov = {"unit": "docstring_corpus", "status": "complete"}
    claim = _claim()
    producer = _FakeProducer([claim], cov)
    writer = _Writer()
    node = make_discover_docstrings(lambda root: producer, writer)

    out = node(_state(doc_filter="pkg/mod.py", claims=["c0"]))

    assert producer.doc_filters == ["pkg/mod.py"]
    assert out["claims"] == ["c0", claim]
    assert [(c, rt) for c, rt, _p in writer.rows] == [
        ("docstrings", "agent_coverage"),
        ("docstrings", "claim_inventory"),
    ]


def test_an_unfiltered_run_hands_the_producer_no_filter():
    """An unfiltered run passes `None` rather than omitting the producer argument."""
    producer = _FakeProducer([], {"unit": "docstring_corpus", "status": "complete"})
    node = make_discover_docstrings(lambda root: producer, _Writer())

    node(_state(claims=[]))

    assert producer.doc_filters == [None]


def test_producer_crash_is_fail_isolated():
    """A producer exception yields error coverage without discarding existing claims."""

    class _Boom:
        """Raise whenever production starts."""

        def produce(self, doc_filter=None):
            """Simulate a producer failure."""
            raise RuntimeError("griffe exploded")

    writer = _Writer()
    node = make_discover_docstrings(lambda root: _Boom(), writer)
    out = node(_state(claims=["c0"]))
    assert out["claims"] == ["c0"]
    assert out["coverages"][-1]["status"] == "error"
    assert writer.rows[0][1] == "agent_coverage"
