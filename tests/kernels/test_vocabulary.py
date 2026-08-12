"""The binding vocabulary states which predicates can mint, high-grade first.

Why the discovery producer is told at all: its vocabulary now contains a predicate whose bind
cannot lead to a certifiable finding. Leaving that implicit would make the two grades
interchangeable from the model's side, and predicate competition — a preview bind displacing a
would-be high-grade one — would become an invisible prompt accident instead of something the
claim record can be read for. This only makes the distinction sayable.
"""

from drift.kernels.registry import Predicate, predicate_registry, register_predicate, vocabulary


def test_high_grade_predicates_are_listed_before_preview_ones():
    text = vocabulary()
    assert text.index("path_exists") < text.index("manifest_key_exists")


def test_both_grades_are_labelled():
    text = vocabulary()
    assert "HIGH-grade" in text
    assert "preview" in text.lower()


def test_every_registered_predicate_appears():
    """A registration that never reaches the prompt is a predicate the agent cannot bind — the
    failure mode is silent, so pin it."""
    text = vocabulary()
    for name in predicate_registry:
        assert f"- {name}:" in text, name


def test_the_preview_block_is_omitted_when_nothing_is_preview_grade(monkeypatch):
    """No empty section headers: with only HIGH-grade entries the prompt says nothing about a
    grade distinction that does not exist for this vocabulary."""
    high_only = {n: p for n, p in predicate_registry.items() if p.grade == "high"}
    monkeypatch.setattr("drift.kernels.registry.predicate_registry", high_only)
    text = vocabulary()
    assert "preview" not in text.lower()
    assert "path_exists" in text


def test_a_predicate_declaring_no_grade_lists_as_high():
    p = Predicate(
        name="_t_grade_default",
        description="test",
        normalize=lambda *a: None,
        kernel=lambda *a: True,
    )
    register_predicate(p)
    try:
        text = vocabulary()
        head = text.split("preview")[0]
        assert "_t_grade_default" in head
    finally:
        predicate_registry.pop("_t_grade_default")


def test_the_agent_stamp_is_declared_in_one_place():
    """`DiscoveryAgent`'s default and the scan's declared stamp must not drift apart: the
    journal would then label rows with a version the prompt never ran under."""
    from drift.agent.discovery import DiscoveryAgent
    from drift.graph.cell import AGENT_VER

    assert DiscoveryAgent(client=None)._agent_ver == AGENT_VER


def test_the_vocabulary_change_carries_a_rebaseline():
    """Any vocabulary edit is a producer-version re-baseline shipping in the same commit.
    Without the bump, discovery inventories recorded under the old vocabulary would be read as
    if the new one had produced them."""
    from drift.graph.cell import AGENT_VER

    assert AGENT_VER != "agent/0.5"  # the stamp before the two grades existed
    # bumped again by signature_has_param's description edit and class_has_member's entry
    assert AGENT_VER != "agent/0.6"
