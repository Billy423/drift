"""What the commands do, as opposed to which options they expose.

Both surfaces call this, so each is only a signature. A surface importing a surface would be an
import cycle.
"""

from __future__ import annotations

import os
import sys

import typer
from sqlalchemy.exc import OperationalError

from drift.cli.addressing import candidates_for
from drift.cli.errors import EXIT_MEASUREMENT, EXIT_RUNTIME, fail

#: Above this pre-flight estimate a scan asks before spending, when there is somebody to ask.
CONFIRM_ABOVE_USD = 1.00


def _is_interactive() -> bool:
    """Is there a person at the other end of this invocation?"""
    return sys.stdin.isatty()


def _why(exc) -> list[str]:
    """The hint lines under an unresolvable document: why it failed, and what to run next."""
    if exc.hazard_reason:
        first = f"it exists, but enumeration skipped it: {exc.hazard_reason}"
    elif exc.near_misses:
        first = "did you mean: " + ", ".join(exc.near_misses)
    else:
        first = "no path of that name is in this repository"
    return [
        first,
        f"{exc.enumerated_count} doc unit(s) enumerated — run 'drift units <repo>' to list them",
    ]


def _plan_or_fail(abs_path: str, doc: str | None):
    """Resolve `doc` against the repository, or exit with a usage error explaining why not."""
    from drift.graph.planning import DocumentNotResolvable, plan_scan

    if doc is None:
        return plan_scan(abs_path)

    candidates = candidates_for(doc, abs_path)
    if not candidates:
        # An empty candidate list would plan a whole-repository scan, which is the worst
        # possible answer to a request for one document.
        fail(
            f"{doc!r} is outside this repository",
            hints=["a document must resolve inside the repository being scanned"],
        )
    try:
        return plan_scan(abs_path, candidates)
    except DocumentNotResolvable as exc:
        fail(f"{exc.spelling!r} is not a scannable unit of this repository", hints=_why(exc))


def _scan_here(
    abs_path: str,
    plan,
    *,
    budget: float,
    strict_measurement: bool = False,
    max_s_candidates: int | None = None,
    journal_export: str | None = None,
    confirm: bool = False,
) -> None:
    """Run a scan in this process and print its report."""
    from drift.graph.dispatch import in_process_dispatch, inline_liveness_probe
    from drift.graph.frame import run_scan
    from drift.graph.nodes.rails import StrictMeasurementAbort

    if confirm and plan.estimate_usd > CONFIRM_ABOVE_USD and _is_interactive():
        typer.echo(
            f"pre-flight estimate: ${plan.estimate_usd:.2f} over {len(plan.worklist)} doc unit(s)"
        )
        if not typer.confirm("continue?"):
            return

    try:
        run_id, report_text = run_scan(
            abs_path,
            budget=budget,
            strict_measurement=strict_measurement,
            max_s_candidates=max_s_candidates,
            journal_export=journal_export,
            plan=plan,
            # The frame and its cells both run here, so no broker or worker is needed.
            dispatch=in_process_dispatch(),
            inspect_factory=inline_liveness_probe,
        )
    except StrictMeasurementAbort as exc:
        # No report: under measurement conditions a partial artifact is the failure, not a
        # fallback. The distinct code lets a harness tell truncation from a crash.
        fail(str(exc), code=EXIT_MEASUREMENT)
    except RuntimeError as exc:
        # The pre-spend unit cap refuses through the frame, so without this a scan of a checkout
        # carrying a virtual environment answers with a stack trace.
        fail(str(exc), code=EXIT_RUNTIME)
    except OperationalError:
        # The likeliest first-run failure of all, and the one the driver cannot recover from.
        fail("cannot reach the database — run `make up` and `make migrate`", code=EXIT_RUNTIME)
    typer.echo(report_text)
    typer.echo(f"run_id: {run_id}", err=True)


def _require_directory(path: str) -> str:
    abs_path = os.path.abspath(path)
    if not os.path.isdir(abs_path):
        fail("not a directory", abs_path)
    return abs_path


def _require_writable(journal_export: str | None) -> None:
    """Probe the export path before the run; discovering it afterwards costs the whole artifact."""
    if journal_export is None:
        return
    try:
        with open(journal_export, "a", encoding="utf-8"):
            pass
    except OSError as exc:
        fail("journal export path not writable", str(exc))
