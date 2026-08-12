"""The `drift` command line: `units`, `check` and `scan`, plus the `dev` subcommands."""

from __future__ import annotations

import os
from collections import Counter

import typer

from drift.app.service import enqueue_scan
from drift.cli._run import (
    _plan_or_fail,
    _require_directory,
    _scan_here,
)
from drift.cli.errors import EXIT_RUNTIME, fail
from drift.runconfig import DEFAULT_BUDGET_USD, DEFAULT_CHECK_BUDGET_USD

app = typer.Typer(help="Check whether a repository's documentation still matches its code.")

_BUDGET_HELP = "USD ceiling. The scan stops after the document that reaches it."


@app.callback()
def main() -> None:
    """Check whether a repository's documentation still matches its code."""


@app.command()
def units(path: str) -> None:
    """List the documents a scan would read."""
    abs_path = os.path.abspath(path)
    if not os.path.isdir(abs_path):
        fail("not a directory", abs_path)

    # Lazy: this command needs no database, and the enumeration module imports SQLAlchemy.
    from drift.graph.nodes.enumerate_units import enumerate_docs

    worklist, hazards = enumerate_docs(abs_path)
    for unit in worklist:
        typer.echo(unit)

    # A truncated unit stays in the worklist, is listed above, and is not counted here.
    skipped = [h for h in hazards if h["disposition"] == "skipped"]
    summary = f"{len(worklist)} doc unit(s)"
    if skipped:
        by_reason = Counter(h["reason"] for h in skipped)
        detail = ", ".join(f"{reason} ×{n}" for reason, n in sorted(by_reason.items()))
        summary += f"; {len(skipped)} skipped ({detail})"
    typer.echo(summary, err=True)


@app.command()
def check(
    path: str,
    doc: str,
    budget: float = typer.Option(
        DEFAULT_CHECK_BUDGET_USD,
        "--budget",
        help=_BUDGET_HELP,
    ),
) -> None:
    """Check one document against the code. Needs Postgres and an API key.

    Both producers run over whatever you name. A prose file is read by
    the model, which is what you pay for, and contributes no
    docstring claims. A Python file is the reverse: its whole symbol
    table is checked against its docstrings for $0, no model call.
    """
    abs_path = _require_directory(path)
    _scan_here(abs_path, _plan_or_fail(abs_path, doc), budget=budget)


@app.command()
def scan(
    path: str,
    budget: float = typer.Option(
        DEFAULT_BUDGET_USD,
        "--budget",
        help=_BUDGET_HELP,
    ),
    run_async: bool = typer.Option(
        False,
        "--async",
        help="run the scan on a worker instead of here. Needs Redis and two workers",
    ),
    yes: bool = typer.Option(False, "--yes", help="skip the cost confirmation"),
) -> None:
    """Check every document in a repository. Needs Postgres and an API key."""
    abs_path = _require_directory(path)
    if run_async:
        # Submission spends nothing here, so there is nothing to estimate or ask about.
        try:
            task_id = enqueue_scan({"path": abs_path, "commit_sha": None}, budget=budget)
        except RuntimeError as exc:
            # Celery reports an unreachable broker as a bare RuntimeError from its result
            # backend, so the type says nothing; the message is collapsed to one line because
            # its own is a paragraph.
            fail(
                "the broker is unreachable",
                " ".join(str(exc).split()),
                hints=("start it with `make up`, or drop --async to run the scan here",),
                code=EXIT_RUNTIME,
            )
        typer.echo(task_id)
        return
    _scan_here(abs_path, _plan_or_fail(abs_path, None), budget=budget, confirm=not yes)


# Imported here rather than at the top: it is built on this module's app.
from drift.cli.dev import dev_app  # noqa: E402

app.add_typer(dev_app, name="dev")

if __name__ == "__main__":
    app()
