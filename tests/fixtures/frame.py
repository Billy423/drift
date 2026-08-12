"""Fixtures that isolate frame planning, cell dispatch, and frame reconciliation."""

from __future__ import annotations

import subprocess

from drift.graph import cell as cell_module
from drift.graph.frame import reconcile_run, render_report
from drift.graph.nodes.enumerate_units import enumerate_docs
from drift.journal.writer import JournalWriter, Stamps

__all__ = [
    "cell_result_payload",
    "cell_state",
    "finish",
    "frame_repo",
    "frame_run",
    "planned",
    "stub_dispatch",
    "write_cell_result",
]

_STAMPS = Stamps(
    agent_ver=cell_module.AGENT_VER, judge_ver=cell_module.JUDGE_VER, model=cell_module.MODEL
)


def cell_result_payload(cell_key, **overrides) -> dict:
    """Build a valid terminal cell payload through the production payload builder.

    Using the production builder prevents a stub from writing a status or producer that a real
    cell could not emit.
    """
    payload = cell_module._terminal_payload(
        tuple(cell_key),
        overrides.pop("status", "completed"),
        overrides.pop("claims_emitted", 0),
        overrides.pop("error", None),
        overrides.pop("partial_notes", []),
        overrides.pop("counts", {}),
    )
    payload.update(overrides)
    return payload


def write_cell_result(session, run_id, cell_key, **overrides) -> dict:
    """Journal the `cell_result` row a cell would have written."""
    payload = cell_result_payload(cell_key, **overrides)
    writer = JournalWriter(session, run_id, "stub", "sha", _STAMPS)
    writer.write("cell", "cell_result", payload)
    writer.flush()
    return payload


def stub_dispatch(session, *, report=True, hook=None):
    """Return a dispatcher that can write, alter, or omit each terminal cell row.

    `hook(run_id, producer, unit_ref, repo_root, config)` runs first and may return a dict of
    `cell_result` overrides (`status`, `partial_notes`, `counts`, …) or the string `"no-row"` to
    model a cell that was dispatched and never reported. `report=False` does that for every cell.
    """

    def dispatch(run_id, producer, unit_ref, repo_root, config):
        """Dispatch one scripted cell and optionally journal its terminal row."""
        overrides = hook(run_id, producer, unit_ref, repo_root, config) if hook else None
        if report and overrides != "no-row":
            write_cell_result(session, run_id, (producer, unit_ref), **(overrides or {}))
        return f"stub-task:{producer}:{unit_ref}"

    return dispatch


def frame_repo(tmp_path, name: str = "repo", files: dict[str, str] | None = None):
    """Create a committed repository suitable for a frame-level scan.

    `files` defaults to a single `README.md`; pass a mapping to shape the worklist a frame-level
    test needs (keys are repo-relative paths, parents created).
    """
    repo = tmp_path / name
    repo.mkdir(parents=True)
    for rel, text in (files or {"README.md": "x"}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
        cwd=repo,
        check=True,
    )
    return repo


def frame_run(repo, db_session, monkeypatch, *, dispatch=None, **kwargs):
    """Run the real frame with a supplied dispatcher or a journal-writing default.

    Planning, fan-in, disposition, reconciliation, and rendering remain production behaviour.
    The default reports each cell synchronously; callers may supply any dispatcher, including one
    that reports no terminal row. Polling is immediate for deterministic tests.
    """
    return _run(repo, db_session, dispatch=dispatch or stub_dispatch(db_session), **kwargs)


def _run(repo, db_session, *, dispatch, **kwargs):
    """Call the production frame with deterministic client and session dependencies."""
    from drift.graph import frame

    return frame.run_scan(
        str(repo),
        client=object(),
        session_factory=lambda: db_session,
        dispatch=dispatch,
        poll_interval=0,
        **kwargs,
    )


def planned(repo_root: str) -> dict:
    """Return the worklist and hazards the production enumerator plans."""
    worklist, hazards = enumerate_docs(repo_root)
    return {"planned_worklist": worklist, "planned_hazards": hazards}


def finish(session, repo_root: str, run_id: int, out: dict) -> dict:
    """Apply production reconciliation and rendering to a directly invoked cell graph.

    Direct graph invocation has no frame to add `result` and `report_text`; this fixture calls the
    same functions in the same order as the frame instead of duplicating that tail.
    """
    out = dict(out)
    out["result"] = reconcile_run(session, repo_root, run_id, out["findings"])
    out["report_text"] = render_report(out)
    return out


def cell_state(**overrides) -> dict:
    """Return the channels a cell graph owns before frame reconciliation.

    A stub must model cell-owned channels, not a rendered report; rendering belongs to the frame.
    """
    state = {
        "findings": [],
        "ranked_entries": [],
        "coverages": [],
        "kernel_errors": [],
        "partial_notes": [],
        "spend": 0.0,
    }
    state.update(overrides)
    return state
