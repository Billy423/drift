"""One error format and one exit-code scheme for every command.

Every expected failure goes through `fail`, so a traceback always means an unexpected one, and
`ERROR_BRANCHES` enumerates those paths so that the claim can be checked.
"""

from __future__ import annotations

from typing import NamedTuple, NoReturn, Sequence

import typer

#: Ran to completion and produced a report, including a run stopped by its budget.
EXIT_OK = 0
#: Runtime failure — no report could be produced.
EXIT_RUNTIME = 1
#: Usage error — nothing ran.
EXIT_USAGE = 2
#: Measurement abort. Belongs to the `drift dev` surface alone.
EXIT_MEASUREMENT = 3


class ErrorBranch(NamedTuple):
    """One enumerated failure path: where it lives, what triggers it, how it exits.

    Indexed by the function that raises, not by the command: several commands share one helper.
    """

    where: str
    condition: str
    exit_code: int


ERROR_BRANCHES: tuple[ErrorBranch, ...] = (
    ErrorBranch("_run._require_directory", "the repository path is not a directory", EXIT_USAGE),
    ErrorBranch("_run._plan_or_fail", "the document resolves outside the repository", EXIT_USAGE),
    ErrorBranch("_run._plan_or_fail", "the document names nothing scannable", EXIT_USAGE),
    ErrorBranch("_run._require_writable", "the journal export path is not writable", EXIT_USAGE),
    ErrorBranch("_run._scan_here", "a measurement run was truncated", EXIT_MEASUREMENT),
    ErrorBranch("_run._scan_here", "the pre-spend unit cap refused the run", EXIT_RUNTIME),
    ErrorBranch("_run._scan_here", "the database is unreachable", EXIT_RUNTIME),
    ErrorBranch("main.units", "the repository path is not a directory", EXIT_USAGE),
    ErrorBranch("main.scan", "the broker is unreachable", EXIT_RUNTIME),
)


def fail(
    what: str,
    detail: str | None = None,
    *,
    hints: Sequence[str] = (),
    code: int = EXIT_USAGE,
) -> NoReturn:
    """Print `error: <what>[: <detail>]` with optional indented hints, then exit with `code`.

    Hints carry no prefix of their own: they are continuations of the one error, not further
    errors, so a reader can tell at a glance how many things went wrong.
    """
    typer.echo(f"error: {what}" + (f": {detail}" if detail else ""), err=True)
    for hint in hints:
        typer.echo(f"  {hint}", err=True)
    raise typer.Exit(code=code)
