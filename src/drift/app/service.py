"""The application-service seam: the one entry point a front end uses to submit a scan."""

from __future__ import annotations

from drift.runconfig import (
    DEFAULT_BUDGET_USD,
    DEFAULT_DOC_FILTER,
    DEFAULT_MAX_S_CANDIDATES,
    DEFAULT_STRICT_MEASUREMENT,
    run_config,
)
from drift.tasks.scan import scan_repo


def enqueue_scan(
    repo_ref: dict,
    *,
    budget: float = DEFAULT_BUDGET_USD,
    strict_measurement: bool = DEFAULT_STRICT_MEASUREMENT,
    max_s_candidates: int | None = DEFAULT_MAX_S_CANDIDATES,
    doc_filter: str | None = DEFAULT_DOC_FILTER,
) -> str:
    """Submit a repository for scanning on a worker; the only entry point a front end calls.

    The submitted unit is the frame, the same one a synchronous `drift scan` runs, and the knobs
    take their defaults from the same constants the CLI does: a service path with defaults of its
    own would be a second budget semantics.

    Returns:
        The submitted task's id, which a caller can hold before any run row exists. It is not a
        run id; the run id is the task's own return value.
    """
    return scan_repo.delay(
        repo_ref,
        run_config(
            budget=budget,
            strict_measurement=strict_measurement,
            max_s_candidates=max_s_candidates,
            doc_filter=doc_filter,
        ),
    ).id
