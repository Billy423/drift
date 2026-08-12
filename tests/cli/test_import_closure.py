"""Importing the command surface must not drag in anything the public package leaves behind.

Asserted as an import CLOSURE rather than as a source-level check, because the reachable set is
transitive: a module can import nothing on the list itself and still pull in three of them through
a neighbour. A source check passes in that case and the package still fails to import once those
modules are gone.
"""

import subprocess
import sys

#: Modules the public package does not carry. Kept here rather than derived, because the point is
#: to fail when something starts reaching one of them, and a list derived from what is currently
#: reachable could never fail.
NOT_SHIPPED = (
    "drift.engine",
    "drift.verifiers",
    "drift.claims",
    "drift.docs",
    "drift.diffscope",
    "drift.retrieval",
    "drift.report.reporter",
    "drift.report.models",
    "drift.judge.signature_judge",
    "drift.judge.base",
    "drift.judge.models",
    # Three `drift.graph.*` names are deliberately absent: the modules they named are gone, and
    # `graph/nodes/` and `graph/state.py` are now live under those exact names. A
    # tripwire that names something shipped is not a tripwire.
)


def _modules_after_importing(target: str) -> set[str]:
    """Every `drift` module loaded by importing `target`, in a fresh interpreter.

    A subprocess because the test session has already imported most of the package; asking this
    one in-process would answer a question about the test run rather than about the import.
    """
    code = (
        f"import {target}, sys;print('\\n'.join(m for m in sys.modules if m.startswith('drift')))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return set(out.stdout.split())


def _dropped(loaded: set[str]) -> set[str]:
    return {m for m in loaded if any(m == n or m.startswith(n + ".") for n in NOT_SHIPPED)}


def test_the_command_surface_reaches_nothing_that_is_left_behind():
    """No channel remains — including through the task module, which the broker imports by name."""
    assert _dropped(_modules_after_importing("drift.cli.main")) == set()
    assert _dropped(_modules_after_importing("drift.cli._run")) == set()
    assert _dropped(_modules_after_importing("drift.tasks.scan")) == set()


def test_the_measurement_surface_reaches_nothing_that_is_left_behind():
    assert _dropped(_modules_after_importing("drift.cli.dev")) == set()


def test_the_check_and_scan_paths_reach_nothing_that_is_left_behind():
    """The frame is imported lazily inside the commands, so it is asked about separately."""
    assert _dropped(_modules_after_importing("drift.graph.frame")) == set()
