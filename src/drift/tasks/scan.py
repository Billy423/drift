"""The Celery task `enqueue_scan` publishes: the frame, run on a worker.

Its body is `graph.frame.run_scan`, the same frame a plain `drift scan` runs in the calling
process, so the two invocation modes cannot acquire different budget semantics.
"""

from __future__ import annotations

from drift.graph.frame import run_scan
from drift.runconfig import run_config
from drift.tasks.celery_app import celery_app


# Unrouted by name, so the frame lands on the default queue: a frame consumed by the
# cells worker would occupy the pool it then waits on.
@celery_app.task(name="drift.scan.scan_repo", max_retries=0)
def scan_repo(repo_ref: dict, config: dict | None = None) -> int:
    """Run one repository scan as the frame, and return the new run's id.

    The frame holds its worker slot for as long as it waits on its cells, so this path needs a
    cells worker running beside the default one. Retries are disabled because a retried scan
    re-runs paid model calls the frame's wallet never learned were spent.

    Args:
        repo_ref: `{"path": ...}`. A `commit_sha` is refused rather than honoured: the frame
            resolves HEAD at enumeration and checks nothing out.
        config: The run's knobs, or `None` for all defaults. A dict rather than parameters
            because widening the signature is a broker-compatibility event.

    Returns:
        The run id, not the report. Every return value is kept by the result backend, and the
        report is reconstructible from the run's own rows.
    """
    pinned = repo_ref.get("commit_sha")
    if pinned is not None:
        raise ValueError(
            f"repo_ref pins commit_sha={pinned!r}, which this path cannot honour: the frame "
            f"resolves HEAD at enumeration and checks nothing out. Check the repo out at that "
            f"commit and enqueue it with commit_sha=None."
        )
    resolved = run_config(config)
    run_id, _report_text = run_scan(
        repo_ref["path"],
        doc_filter=resolved["doc_filter"],
        budget=resolved["budget"],
        strict_measurement=resolved["strict_measurement"],
        max_s_candidates=resolved["max_s_candidates"],
    )
    return run_id
