"""`tasks/scan.py` — the task the service seam publishes.

`scan_repo`, whose body is now the FRAME — argument marshalling, the knob resolution both ends of
the service seam share, `max_retries=0`, the explicit task name.

Offline: the frame is stubbed.
"""

import pytest

from drift.runconfig import RUN_CONFIG_DEFAULTS
from drift.tasks.scan import scan_repo

# --- the re-pointed task -----------------------------------------------------------------------


@pytest.fixture
def frame_calls(monkeypatch):
    """Capture what the task hands the frame, without running one."""
    import drift.tasks.scan as scan_mod

    calls: list[tuple] = []

    def _fake_frame(path, **kwargs):
        calls.append((path, kwargs))
        return 4242, "report text"

    monkeypatch.setattr(scan_mod, "run_scan", _fake_frame)
    return calls


def test_the_task_runs_the_frame_and_returns_its_run_id(frame_calls):
    """The re-point itself: the task's body is `run_scan`, and it returns the `run_id`."""
    returned = scan_repo({"path": "/repo", "commit_sha": None})

    assert returned == 4242  # not the report: the id is the key to it (result-backend size)
    (path, kwargs) = frame_calls[0]
    assert path == "/repo"


def test_the_task_threads_every_knob_it_is_given(frame_calls):
    scan_repo(
        {"path": "/repo", "commit_sha": None},
        {
            "budget": 1.5,
            "strict_measurement": True,
            "max_s_candidates": 7,
            "doc_filter": "docs/README.md",
        },
    )

    (_path, kwargs) = frame_calls[0]
    assert kwargs == {
        "budget": 1.5,
        "strict_measurement": True,
        "max_s_candidates": 7,
        "doc_filter": "docs/README.md",
    }


def test_a_message_without_a_config_runs_on_the_shared_defaults(frame_calls):
    """One implementation, two invocation modes, and never two budget semantics."""
    scan_repo({"path": "/repo", "commit_sha": None})

    (_path, kwargs) = frame_calls[0]
    assert kwargs == RUN_CONFIG_DEFAULTS


def test_an_unknown_knob_fails_at_the_boundary_not_inside_a_paid_run(frame_calls):
    with pytest.raises(ValueError, match="unknown run-config key"):
        scan_repo({"path": "/repo", "commit_sha": None}, {"concurrency": 4})
    assert frame_calls == []  # refused before the frame — before any spend


def test_a_pinned_commit_is_refused_before_any_work(frame_calls):
    """The frame resolves HEAD and checks nothing out, so a pin cannot be honoured. Refusing
    is the only honest answer: accepting one would scan a different commit than the caller
    named."""
    with pytest.raises(ValueError, match="commit_sha"):
        scan_repo({"path": "/repo", "commit_sha": "deadbeef"})
    assert frame_calls == []


def test_the_task_never_retries():
    """Celery's default would re-run paid work outside the wallet's knowledge."""
    assert scan_repo.max_retries == 0


def test_the_task_carries_its_explicit_name():
    """Explicit, because the name is what the queue routing (and the Makefile) is pinned to."""
    assert scan_repo.name == "drift.scan.scan_repo"
