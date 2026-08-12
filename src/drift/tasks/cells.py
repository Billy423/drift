"""Celery task wrapper for the cell graph."""

from __future__ import annotations

from drift.graph.cell import run_cell
from drift.tasks.celery_app import celery_app

__all__ = ["run_cell_task"]


@celery_app.task(name="drift.cells.run_cell", max_retries=0)
def run_cell_task(run_id: int, producer: str, unit_ref: str, repo_root: str, config: dict) -> dict:
    """Run one cell of a scan.

    Retries are disabled because a retry re-runs paid model calls. Broker redelivery is
    absorbed by the terminal-status row keyed on the run and cell.

    Args:
        producer: One of `kernels.models.PRODUCERS`.
        unit_ref: Repository-relative path of the file to scan.
        config: Run knobs, including this cell's `max_s_candidates` allowance.

    Returns:
        An advisory outcome; the `cell_result` journal row is the authoritative record.
    """
    return run_cell(run_id, producer, unit_ref, repo_root, config)
