"""Getting a cell to a worker, and finding out whether it is still alive.

Liveness is not a timeout: the probe answers lost, alive or inconclusive, and only the
first ends a wait.
"""

from __future__ import annotations

import time
import uuid

from drift.graph import read_model, session_read
from drift.graph.cell import run_cell
from drift.graph.progress import progress

#: The fan-in's clock. The probe fires only after this many empty polls; that many
#: inconclusive probes in a row are announced in the report, and never fail the run.
POLL_INTERVAL_SECONDS = 5.0
PROBE_AFTER_EMPTY_POLLS = 12
PROBE_UNAVAILABLE_AFTER = 5


def _celery_dispatch(run_id: int, producer: str, unit_ref: str, repo_root: str, config: dict):
    """Publish one cell to the broker and return the task id it was published under.

    The id is generated here and passed in rather than read off the returned `AsyncResult`,
    which the frame must never touch: it polls for `cell_result` rows instead, and that is what
    keeps its own `finally` reachable when a cell dies.
    """
    from drift.tasks.cells import run_cell_task

    task_id = str(uuid.uuid4())
    run_cell_task.apply_async(args=[run_id, producer, unit_ref, repo_root, config], task_id=task_id)
    return task_id


class _NonClosingSession:
    """A view of the frame's session whose `close()` does nothing.

    `run_cell` closes the session it is handed, which is right for a cell that opened its own.
    Closing the frame's would detach the frame's ORM state mid-run.
    """

    def __init__(self, session) -> None:
        self._session = session

    def __getattr__(self, name):
        """Proxy every other attribute to the wrapped session."""
        return getattr(self.__dict__["_session"], name)

    def close(self) -> None:
        """Deliberately nothing: the frame opened this session and the frame closes it."""


def inline_liveness_probe():
    """A liveness oracle for cells that ran in this process, where no worker can answer.

    The fan-in treats silence as inconclusive and keeps waiting, which is a permanent hang when
    there is no broker at all. So this answers rather than declining: empty mappings let the
    fan-in declare the cell lost, where returning None or raising would reinstate the hang.
    """

    class _NoWorkers:
        """An inspect result that answers, definitively, that no worker holds anything."""

        def active(self):
            """No worker is running any task."""
            return {}

        def reserved(self):
            """No worker has any task queued."""
            return {}

    return _NoWorkers()


def in_process_dispatch(client=None, session_factory=None):
    """A dispatcher that runs each cell inline, in the calling process.

    A supported way to run a scan and not only a test seam: a single-document check uses it, so
    the whole pipeline is available with no broker and no worker to install first.

    Args:
        client: A model client for the cell. It cannot cross a broker message, so this is also
            the only way to put a scripted one inside a cell.
        session_factory: When given, cells share the frame's session instead of opening their
            own. Correct only in-process: a cell opening its own connection could not see a
            `ScanRun` row committed inside the caller's still-open transaction, so it would
            write nothing while the frame polled for a row that could never come.
    """

    def dispatch(run_id: int, producer: str, unit_ref: str, repo_root: str, config: dict):
        """Run one cell to completion here and now; the returned id names no broker task."""
        cell_factory = (
            None if session_factory is None else (lambda: _NonClosingSession(session_factory()))
        )
        run_cell(
            run_id,
            producer,
            unit_ref,
            repo_root,
            config,
            client=client,
            session_factory=cell_factory,
        )
        return f"in-process:{producer}:{unit_ref}"

    return dispatch


def _worker_membership(task_id: str, inspect_factory) -> bool | None:
    """Is `task_id` in some live worker's `active` or `reserved` set?

    Returns:
        True or False when a worker answered, and None when none did. The distinction is
        probe-error against probe-negative: a call that raises or goes unanswered says nothing
        about the cell, only about the monitor, and returning False there would fail a healthy
        run over a broker hiccup. A worker answering with an empty set is an answer, and is the
        only way a lost cell is ever declared.
    """
    try:
        inspect = inspect_factory()
        if inspect is None:
            return None
        active, reserved = inspect.active(), inspect.reserved()
    except Exception:
        return None
    if active is None or reserved is None:
        return None
    seen: set[str] = set()
    for mapping in (active or {}, reserved or {}):
        for tasks in mapping.values():
            for task in tasks or []:
                found = task.get("id") if isinstance(task, dict) else getattr(task, "id", None)
                if found:
                    seen.add(found)
    return task_id in seen


def _default_inspect():
    """The broker's worker-inspection handle, imported late so importing this module needs none."""
    from drift.tasks.celery_app import celery_app

    return celery_app.control.inspect()


def _revoke(task_ids) -> None:
    """Ask the broker to drop cells that have not started; a started cell is never killed.

    `terminate=False` is the point: a running cell has already been paid for, and killing it
    mid-flight destroys evidence the run owes its artifact.
    """
    ids = [tid for tid in task_ids if tid]
    if not ids:
        return
    try:
        from drift.tasks.celery_app import celery_app

        celery_app.control.revoke(ids, terminate=False)
        progress(f"revoked {len(ids)} un-started cell(s)")
    except Exception as exc:  # noqa: BLE001 - a failed revoke must not replace the real error
        progress(f"revoke failed ({exc!r}); un-started cells may still run")


def _await_cell_result(
    session_factory,
    run_id: int,
    cell_key: tuple[str, str],
    task_id,
    notes: list[str],
    poll_interval: float,
    inspect_factory,
) -> tuple[dict | None, bool]:
    """Poll for one cell's `cell_result` row.

    Liveness, not a timeout: nothing here is a clock on the cell's work, so a cell that
    legitimately takes hours is never touched. The first Ctrl-C stops further dispatch but keeps
    waiting for the cell in flight, whose work is paid for; a second abandons that wait.

    Returns:
        The row and whether an interrupt was seen. A row of None means the cell was declared
        lost: dispatched, no row, and in no live worker's queue.
    """
    interrupted = False
    while True:
        try:
            return _poll_until_reported(
                session_factory, run_id, cell_key, task_id, notes, poll_interval, inspect_factory
            ), interrupted
        except KeyboardInterrupt:
            if interrupted:
                progress(f"interrupt (2nd): abandoning the wait for {cell_key}")
                raise
            interrupted = True
            progress(
                f"interrupt: no further cells will be dispatched; still waiting for {cell_key} "
                f"(Ctrl-C again to abandon it)"
            )


def _poll_until_reported(
    session_factory, run_id, cell_key, task_id, notes, poll_interval, inspect_factory
) -> dict | None:
    """One cell's poll loop: its row, or None once the liveness probe says nothing holds it."""
    empty_polls = 0
    inconclusive = 0
    announced_unavailable = False
    while True:
        row = session_read.fresh_read(
            session_factory, lambda s: read_model.cell_results_of(s, run_id)
        ).get(cell_key)
        if row is not None:
            return row
        empty_polls += 1
        if empty_polls % PROBE_AFTER_EMPTY_POLLS == 0:
            verdict = _worker_membership(task_id, inspect_factory)
            if verdict is None:
                inconclusive += 1
                if inconclusive >= PROBE_UNAVAILABLE_AFTER and not announced_unavailable:
                    announced_unavailable = True
                    notes.append(
                        f"probe_unavailable: the liveness probe for cell {cell_key} has been "
                        f"inconclusive {inconclusive} times running; the frame is STILL WAITING. "
                        f"A run is never failed on the monitor's own failure."
                    )
                    progress(f"{cell_key}: probe_unavailable — still waiting")
            elif verdict is False:
                # One last look before declaring a hole: the row may have landed between the
                # poll above and the worker's reply.
                row = session_read.fresh_read(
                    session_factory, lambda s: read_model.cell_results_of(s, run_id)
                ).get(cell_key)
                if row is not None:
                    return row
                progress(f"{cell_key}: LOST — in no live worker, and no cell_result row")
                return None
            else:
                inconclusive = 0
        time.sleep(poll_interval)
