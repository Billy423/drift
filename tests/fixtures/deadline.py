"""A signal-based deadline for tests that could otherwise block indefinitely."""

from __future__ import annotations

import signal
from contextlib import contextmanager

#: A generous ceiling for scripted clients and temporary repositories; reaching it indicates a hang.
DEADLINE_SECONDS = 20


class DeadlineExceeded(BaseException):
    """Escape a deadline through production code that catches ordinary exceptions."""


@contextmanager
def deadline(seconds: int = DEADLINE_SECONDS):
    """Fail if the guarded block does not finish before the deadline.

    A signal can interrupt a blocking FIFO read; a thread-based guard cannot. Raising a
    `BaseException` also prevents production isolation handlers from swallowing the timeout as an
    ordinary unit failure.

    This helper requires POSIX signals and the main thread.
    """

    def _fire(signum, frame):  # noqa: ARG001 - handler signature
        """Raise the deadline exception from the signal handler."""
        raise DeadlineExceeded(f"exceeded {seconds}s — a read blocked (FIFO?) or a loop hung")

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
