"""The ranked tier: banded, sectioned by producer, labelled unverified.

The tier replaced a descending sort on the producer's own confidence score. A sort presents
every position as meaningful, which that score cannot support: it is one model's summary
judgement, not a calibrated probability, so neighbouring positions differ by nothing a reader
could act on. The banded form says only what the score can carry — two groups, suspected
(<= 0.2) first, then unexamined — and states that order within a group is arbitrary, so a
nine-way tie cannot render as a precision the number does not have.

The band cut is a chosen threshold, not a validated one, and nothing here should be read as
evidence that it separates well.

The two rules that carry over unchanged:

  · **Never interleave the lanes.** The docstring producer stamps `confidence = 1.0` on every
    claim it emits — a synthetic placeholder, not a judgment.
  · **Say it is unverified.** This tier is the candidate surface; a reader who mistakes it for
    the HIGH tier has been handed unverified output as a verified number.
"""

from drift.domain.findings import Confidence, Evidence, Finding, Location
from drift.graph.ranked import RankedEntry
from drift.kernels.models import Anchor, EvClaim, SSlot
from drift.report.render import to_markdown


def _entry(literal, confidence, producer="agent", note="a note", annotation=None):
    return RankedEntry(
        claim=EvClaim(
            anchor=Anchor(doc_path="README.md", spans=((1, 1),), literal=literal),
            check=None,
            claim_class=3,
            s_slot=SSlot(note=note, confidence=confidence),
            provenance={"producer": producer},
        ),
        annotation=annotation,
    )


def _literals_in_order(text, section):
    """The literals rendered under one `###` section, in render order."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"### {section}"))
    out = []
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        if line.startswith("- `README.md`: "):
            out.append(line.split(": ", 1)[1])
    return out


def test_the_suspected_band_renders_first_and_at_the_measured_cut():
    """Suspected before unexamined, and the cut is inclusive. The band is defined as
    `confidence <= 0.2`; an exclusive cut would silently redefine it, and entries sit close
    enough to the boundary for that to change what a reader sees first."""
    entries = [
        _entry("high", 0.95),
        _entry("edge", 0.20),
        _entry("mid", 0.50),
        _entry("low", 0.05),
    ]
    text = to_markdown([], entries, [])
    assert _literals_in_order(text, "From the agent · SUSPECTED") == ["edge", "low"]
    assert _literals_in_order(text, "From the agent · unexamined") == ["high", "mid"]
    assert text.index("SUSPECTED") < text.index("unexamined")


def test_within_a_band_order_is_arrival_order_and_says_so():
    """A nine-way tie must not render as rank. Within a band the order is stable arrival
    order, and the section heading says the order is arbitrary."""
    entries = [_entry("first", 0.15), _entry("second", 0.05), _entry("third", 0.15)]
    text = to_markdown([], entries, [])
    assert _literals_in_order(text, "From the agent · SUSPECTED") == ["first", "second", "third"]
    assert "not ranked within the band" in text


def test_the_deterministic_producer_is_a_separate_section_and_is_never_interleaved():
    entries = [
        _entry("docstring-claim", 1.0, producer="docstring"),
        _entry("agent-low", 0.1),
        _entry("agent-high", 0.9),
    ]
    text = to_markdown([], entries, [])

    assert _literals_in_order(text, "From the agent · SUSPECTED") == ["agent-low"]
    assert _literals_in_order(text, "From the agent · unexamined") == ["agent-high"]
    assert _literals_in_order(text, "From the deterministic producer") == ["docstring-claim"]
    # and the synthetic 1.0 did not land in a band
    assert text.index("### From the agent") < text.index("### From the deterministic")


def test_an_empty_band_is_omitted_not_rendered_empty():
    entries = [_entry("high", 0.9)]
    text = to_markdown([], entries, [])
    assert "SUSPECTED" not in text
    assert _literals_in_order(text, "From the agent · unexamined") == ["high"]


def test_a_nan_confidence_never_vanishes_from_the_report(review="F10"):
    """Two independent band predicates are both False for NaN; the second band must be the
    COMPLEMENT of the first, so a malformed confidence still renders (in unexamined — it is
    not evidence of drift, and it must not wear the suspected band's measured label)."""
    entries = [_entry("weird", float("nan")), _entry("low", 0.1)]
    text = to_markdown([], entries, [])
    assert _literals_in_order(text, "From the agent · SUSPECTED") == ["low"]
    assert _literals_in_order(text, "From the agent · unexamined") == ["weird"]


def test_the_deterministic_producer_keeps_gate_order_not_confidence_order():
    """Its confidence is a placeholder, so sorting by it would be sorting by nothing."""
    entries = [
        _entry("b", 1.0, producer="docstring"),
        _entry("a", 1.0, producer="docstring"),
    ]
    text = to_markdown([], entries, [])
    assert _literals_in_order(text, "From the deterministic producer") == ["b", "a"]


def test_the_tier_is_explicitly_labelled_unverified():
    text = to_markdown([], [_entry("x", 0.5)], [])
    heading = next(ln for ln in text.splitlines() if ln.startswith("## Ranked tier"))
    assert "UNVERIFIED" in heading


def test_a_preview_annotation_renders_with_its_claim():
    entries = [_entry("npm run build", 0.6, annotation="preview `manifest_key_exists`: absent")]
    text = to_markdown([], entries, [])
    assert "preview `manifest_key_exists`: absent" in text
    assert "a note" in text  # the s_slot note is not replaced by the annotation


def test_an_empty_tier_still_renders_its_heading():
    text = to_markdown([], [], [])
    assert "## Ranked tier" in text
    assert "_none_" in text


def test_the_high_tier_is_untouched():
    finding = Finding(
        check_id="path_exists",
        identity=("README.md", "docs/gone.md"),
        doc_location=Location("README.md", 3, 3),
        code_anchor=None,
        summary="stale path_exists claim 'docs/gone.md' in README.md",
        evidence=Evidence(
            doc_claim="docs/gone.md", code_truth="path not found in the scanned tree"
        ),
        confidence=Confidence.HIGH,
    )
    text = to_markdown([finding], [], [])
    assert "## Verified findings — 1" in text
    assert "docs/gone.md" in text
