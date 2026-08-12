"""The measurement surface: the options that reproduce a published number.

Behaviour is identical to the ordinary commands; only the exposed options differ.
"""

from __future__ import annotations

import typer

from drift.cli._run import _plan_or_fail, _require_directory, _require_writable, _scan_here
from drift.runconfig import (
    DEFAULT_BUDGET_USD,
    DEFAULT_CHECK_BUDGET_USD,
    DEFAULT_MAX_S_CANDIDATES,
    DEFAULT_STRICT_MEASUREMENT,
)

dev_app = typer.Typer(help="Reproduce a published measurement.")

_STRICT_HELP = "abort loudly on a soft rail or a journal failure instead of producing a partial run"
_EXPORT_HELP = "write the run's journal rows to this file; an aborted run leaves one too"
_CAP_HELP = "per-run cap on paid adjudications; defaults to the built-in rail"


@dev_app.command()
def scan(
    path: str,
    budget: float = typer.Option(DEFAULT_BUDGET_USD, "--budget"),
    strict_measurement: bool = typer.Option(
        DEFAULT_STRICT_MEASUREMENT, "--strict-measurement", help=_STRICT_HELP
    ),
    journal_export: str | None = typer.Option(None, "--journal-export", help=_EXPORT_HELP),
    max_s_candidates: int | None = typer.Option(
        DEFAULT_MAX_S_CANDIDATES, "--max-s-candidates", help=_CAP_HELP
    ),
) -> None:
    """Scan a whole repository under measurement conditions."""
    abs_path = _require_directory(path)
    _require_writable(journal_export)
    _scan_here(
        abs_path,
        _plan_or_fail(abs_path, None),
        budget=budget,
        strict_measurement=strict_measurement,
        max_s_candidates=max_s_candidates,
        journal_export=journal_export,
    )


@dev_app.command()
def check(
    path: str,
    doc: str,
    budget: float = typer.Option(DEFAULT_CHECK_BUDGET_USD, "--budget"),
    strict_measurement: bool = typer.Option(
        DEFAULT_STRICT_MEASUREMENT, "--strict-measurement", help=_STRICT_HELP
    ),
    journal_export: str | None = typer.Option(None, "--journal-export", help=_EXPORT_HELP),
    max_s_candidates: int | None = typer.Option(
        DEFAULT_MAX_S_CANDIDATES, "--max-s-candidates", help=_CAP_HELP
    ),
) -> None:
    """Scan one document under measurement conditions — the form every published figure used."""
    abs_path = _require_directory(path)
    _require_writable(journal_export)
    _scan_here(
        abs_path,
        _plan_or_fail(abs_path, doc),
        budget=budget,
        strict_measurement=strict_measurement,
        max_s_candidates=max_s_candidates,
        journal_export=journal_export,
    )
