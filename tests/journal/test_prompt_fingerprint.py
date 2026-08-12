"""Tests content fingerprints for every model-facing prompt surface."""

import re

import drift.agent.discovery as discovery
import drift.agent.runner as runner
import drift.judge.semantic_judge as semantic_judge
from drift.kernels.registry import Predicate, predicate_registry, register_predicate

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def test_both_fingerprints_are_stable_full_sha256():
    """Each producer returns a distinct, stable, full SHA-256 fingerprint."""
    a1, a2 = discovery.prompt_fingerprint(), discovery.prompt_fingerprint()
    j1, j2 = semantic_judge.prompt_fingerprint(), semantic_judge.prompt_fingerprint()
    assert a1 == a2 and j1 == j2
    assert _HEX64.match(a1) and _HEX64.match(j1)
    assert a1 != j1


def test_a_system_prompt_edit_changes_the_fingerprint(monkeypatch):
    """Editing the judge system prompt changes its fingerprint."""
    before = semantic_judge.prompt_fingerprint()
    monkeypatch.setattr(semantic_judge, "_SYSTEM", semantic_judge._SYSTEM + " ")
    assert semantic_judge.prompt_fingerprint() != before


def test_a_render_source_edit_changes_both_fingerprints(monkeypatch):
    """Replacing either render function changes that producer's fingerprint."""

    def _other_render(*a, **kw):
        """Provide different render source while leaving prompt constants unchanged."""
        return []

    j_before = semantic_judge.prompt_fingerprint()
    monkeypatch.setattr(semantic_judge, "_render", _other_render)
    assert semantic_judge.prompt_fingerprint() != j_before

    a_before = discovery.prompt_fingerprint()
    monkeypatch.setattr(discovery.DiscoveryAgent, "_render_prompt", _other_render)
    assert discovery.prompt_fingerprint() != a_before


def test_an_agent_system_prompt_edit_changes_the_fingerprint(monkeypatch):
    """Editing the discovery system prompt changes its fingerprint."""
    before = discovery.prompt_fingerprint()
    monkeypatch.setattr(discovery, "_SYSTEM", discovery._SYSTEM + " ")
    assert discovery.prompt_fingerprint() != before


def test_an_agent_output_schema_edit_changes_the_fingerprint(monkeypatch):
    """Editing the discovery output schema changes its fingerprint."""
    before = discovery.prompt_fingerprint()
    patched = dict(discovery._OUTPUT_SCHEMA)
    patched["description"] = "edited"
    monkeypatch.setattr(discovery, "_OUTPUT_SCHEMA", patched)
    assert discovery.prompt_fingerprint() != before


def test_an_emit_tool_description_edit_changes_BOTH_fingerprints(monkeypatch):
    """Editing the shared emit-tool description changes both fingerprints."""
    a_before, j_before = discovery.prompt_fingerprint(), semantic_judge.prompt_fingerprint()
    monkeypatch.setattr(runner, "EMIT_TOOL_DESCRIPTION", runner.EMIT_TOOL_DESCRIPTION + " Now.")
    assert discovery.prompt_fingerprint() != a_before
    assert semantic_judge.prompt_fingerprint() != j_before
    monkeypatch.undo()
    assert discovery.prompt_fingerprint() == a_before
    assert semantic_judge.prompt_fingerprint() == j_before


def test_an_emit_tool_name_edit_changes_BOTH_fingerprints(monkeypatch):
    """Editing the shared emit-tool name changes both fingerprints."""
    a_before, j_before = discovery.prompt_fingerprint(), semantic_judge.prompt_fingerprint()
    monkeypatch.setattr(runner, "EMIT_TOOL_NAME", "emit_result_v2")
    assert discovery.prompt_fingerprint() != a_before
    assert semantic_judge.prompt_fingerprint() != j_before


def test_a_vocabulary_change_changes_the_agent_fingerprint():
    """Registering a predicate changes the discovery fingerprint until it is removed."""
    before = discovery.prompt_fingerprint()
    register_predicate(
        Predicate(
            name="_t_fp_probe",
            description="test",
            normalize=lambda *a: None,
            kernel=lambda *a: True,
        )
    )
    try:
        assert discovery.prompt_fingerprint() != before
    finally:
        predicate_registry.pop("_t_fp_probe")
    assert discovery.prompt_fingerprint() == before
