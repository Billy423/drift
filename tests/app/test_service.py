"""`enqueue_scan` — the sole front-end seam, and what it publishes.

The sole-entry-point property has two halves and this file pins both. The seam still returns an
`AsyncResult` id string, which is its documented contract: a caller holds that id before a run
row exists. And what it publishes is the frame's full knob set, resolved from the shared
defaults, so the service path cannot acquire a second budget semantics of its own.

Offline: `.delay` is stubbed, so nothing reaches a broker.
"""

from __future__ import annotations

import pytest

from drift.app.service import enqueue_scan
from drift.runconfig import RUN_CONFIG_DEFAULTS


class _FakeAsyncResult:
    """Stand-in for what `.delay` returns — only its `id` is part of the seam's contract."""

    def __init__(self, task_id: str) -> None:
        self.id = task_id


@pytest.fixture
def published(monkeypatch):
    """Capture the message `enqueue_scan` would publish, without a broker."""
    import drift.app.service as service_mod

    calls: list[tuple] = []

    def _fake_delay(*args):
        calls.append(args)
        return _FakeAsyncResult("task-abc-123")

    monkeypatch.setattr(service_mod.scan_repo, "delay", _fake_delay)
    return calls


def test_it_returns_the_async_result_id_not_a_run_id(published):
    assert enqueue_scan({"path": "/repo", "commit_sha": None}) == "task-abc-123"


def test_it_publishes_the_repo_ref_and_a_fully_resolved_config(published):
    enqueue_scan({"path": "/repo", "commit_sha": None})

    (repo_ref, config) = published[0]
    assert repo_ref == {"path": "/repo", "commit_sha": None}
    assert config == RUN_CONFIG_DEFAULTS  # every knob explicit on the wire, none left implied


def test_it_carries_the_knobs_it_is_given(published):
    enqueue_scan(
        {"path": "/repo", "commit_sha": None},
        budget=2.5,
        strict_measurement=True,
        max_s_candidates=3,
        doc_filter="README.md",
    )

    (_repo_ref, config) = published[0]
    assert config == {
        "budget": 2.5,
        "strict_measurement": True,
        "max_s_candidates": 3,
        "doc_filter": "README.md",
    }


def test_the_knobs_are_keyword_only(published):
    """Positional knobs would make the seam's shape a compatibility hazard for every front-end."""
    with pytest.raises(TypeError):
        enqueue_scan({"path": "/repo", "commit_sha": None}, 2.5)
