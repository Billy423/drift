"""Progress lines for a human watching a scan.

Its own module because every graph module writes them, and importing the frame to get one
would point the dependency backwards.
"""

from __future__ import annotations

import sys


def progress(msg: str) -> None:
    """Print a progress line to stderr; stdout carries the report and nothing else."""
    print(f"[drift] {msg}", file=sys.stderr, flush=True)
