"""The run knobs have one source, and the three surfaces that declare them are pinned to it.

Routing `enqueue_scan` to the frame makes the service path a second invocation mode of one
implementation, and two invocation modes must never become two budget semantics. The way that
happens in practice is a default value written down twice and changed once. These
tests are the mechanical link between `cli/main.py`'s Typer option declarations,
`run_scan`'s signature and `enqueue_scan`'s signature.

**Two assertions per knob, because one is not enough.** Value equality alone is vacuous — three
independent `5.0` literals satisfy it on the day they are written and stop satisfying it the day
one of them changes, which is the failure this file exists to prevent. So each knob is also
checked at the AST: its default must be *spelled* as the shared constant's name, never as a
literal. That is what makes the equality check a lock rather than a coincidence.

Offline, no DB, no broker.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from drift import runconfig
from drift.app.service import enqueue_scan
from drift.cli.dev import check as dev_check
from drift.cli.dev import scan as dev_scan
from drift.cli.main import check as cli_check
from drift.cli.main import scan as cli_scan
from drift.graph.frame import run_scan

_KNOBS = [
    ("budget", "DEFAULT_BUDGET_USD", runconfig.DEFAULT_BUDGET_USD),
    ("strict_measurement", "DEFAULT_STRICT_MEASUREMENT", runconfig.DEFAULT_STRICT_MEASUREMENT),
    ("max_s_candidates", "DEFAULT_MAX_S_CANDIDATES", runconfig.DEFAULT_MAX_S_CANDIDATES),
    ("doc_filter", "DEFAULT_DOC_FILTER", runconfig.DEFAULT_DOC_FILTER),
]

#: Command surfaces, one row per (command, option): the table is indexed by SURFACE because the
#: commands do not share every default. What must never diverge is the meaning of a budget — a
#: dollar ceiling checked at cell boundaries — and what must never be written twice is any one of
#: these numbers. A narrower command carrying a narrower default is not a second semantics; a
#: literal re-introduced beside the constant would be.
_CLI_SURFACES = [
    (cli_scan, "budget", "DEFAULT_BUDGET_USD", runconfig.DEFAULT_BUDGET_USD),
    (cli_check, "budget", "DEFAULT_CHECK_BUDGET_USD", runconfig.DEFAULT_CHECK_BUDGET_USD),
    (dev_scan, "budget", "DEFAULT_BUDGET_USD", runconfig.DEFAULT_BUDGET_USD),
    (
        dev_scan,
        "strict_measurement",
        "DEFAULT_STRICT_MEASUREMENT",
        runconfig.DEFAULT_STRICT_MEASUREMENT,
    ),
    (dev_scan, "max_s_candidates", "DEFAULT_MAX_S_CANDIDATES", runconfig.DEFAULT_MAX_S_CANDIDATES),
    (dev_check, "budget", "DEFAULT_CHECK_BUDGET_USD", runconfig.DEFAULT_CHECK_BUDGET_USD),
    (
        dev_check,
        "max_s_candidates",
        "DEFAULT_MAX_S_CANDIDATES",
        runconfig.DEFAULT_MAX_S_CANDIDATES,
    ),
    (
        dev_check,
        "strict_measurement",
        "DEFAULT_STRICT_MEASUREMENT",
        runconfig.DEFAULT_STRICT_MEASUREMENT,
    ),
]


def _cli_default(fn, param: str):
    """The value a Typer option declares as its default (unwrapping the `OptionInfo`)."""
    declared = inspect.signature(fn).parameters[param].default
    return getattr(declared, "default", declared)


def _sig_default(fn, param: str):
    return inspect.signature(fn).parameters[param].default


def _default_node(fn, param: str) -> ast.expr:
    """The AST node a function's source uses as `param`'s default.

    For a Typer option the declaration is `typer.Option(<default>, "--flag", ...)`, so the node
    that matters is the call's first positional argument, not the call itself.
    """
    module = ast.parse(Path(inspect.getsourcefile(fn)).read_text(encoding="utf-8"))
    target = fn.__name__
    func = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == target
    )
    args = func.args
    positional = args.posonlyargs + args.args
    names = [a.arg for a in positional] + [a.arg for a in args.kwonlyargs]
    if param in [a.arg for a in positional]:
        # Positional defaults are right-aligned against the parameter list.
        index = [a.arg for a in positional].index(param) - (len(positional) - len(args.defaults))
        assert index >= 0, f"{target}.{param} has no default"
        node = args.defaults[index]
    else:
        node = args.kw_defaults[[a.arg for a in args.kwonlyargs].index(param)]
    assert node is not None, f"{target}.{param} has no default ({names})"
    if isinstance(node, ast.Call):  # typer.Option(<default>, "--flag", …)
        node = node.args[0]
    return node


def _assert_spelled_as_constant(fn, param: str, constant_name: str) -> None:
    node = _default_node(fn, param)
    assert isinstance(node, ast.Name) and node.id == constant_name, (
        f"{fn.__name__}'s `{param}` default is spelled "
        f"{ast.unparse(node)!r}, not the shared constant `{constant_name}` — a re-introduced "
        f"literal is exactly how two invocation modes acquire two budget semantics"
    )


# --- the CLI -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("command", "param", "constant_name", "value"), _CLI_SURFACES)
def test_every_command_declares_its_default_as_a_named_constant(
    command, param, constant_name, value
):
    assert _cli_default(command, param) == value
    _assert_spelled_as_constant(command, param, constant_name)


def test_the_narrower_command_carries_the_narrower_ceiling():
    """Stated as a relation, so the two numbers cannot drift into the wrong order."""
    assert runconfig.DEFAULT_CHECK_BUDGET_USD < runconfig.DEFAULT_BUDGET_USD


def test_the_check_budget_is_not_a_knob_that_crosses_the_seam():
    """It is a command's default, not a run knob. Admitting it to the shared set would hand the
    service seam a parameter it does not have."""
    assert "DEFAULT_CHECK_BUDGET_USD" not in {
        name for name in dir(runconfig) if name in runconfig.RUN_CONFIG_DEFAULTS
    }
    assert runconfig.DEFAULT_CHECK_BUDGET_USD not in [
        v for k, v in runconfig.RUN_CONFIG_DEFAULTS.items() if k == "budget"
    ]


# --- the frame ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("knob", "constant_name", "value"), _KNOBS)
def test_the_frame_declares_the_shared_constants(knob, constant_name, value):
    assert _sig_default(run_scan, knob) == value
    _assert_spelled_as_constant(run_scan, knob, constant_name)


# --- the service seam --------------------------------------------------------------------------


@pytest.mark.parametrize(("knob", "constant_name", "value"), _KNOBS)
def test_the_service_seam_declares_the_shared_constants(knob, constant_name, value):
    """A service path with different defaults is a second budget semantics."""
    assert _sig_default(enqueue_scan, knob) == value
    _assert_spelled_as_constant(enqueue_scan, knob, constant_name)


def test_the_knob_set_is_exactly_the_frame_parameters_it_names():
    """Every knob in the closed set is a real `run_scan` parameter — no dead keys."""
    frame_params = set(inspect.signature(run_scan).parameters)
    assert set(runconfig.RUN_CONFIG_DEFAULTS) <= frame_params


# --- run_config: the resolver both ends of the seam use ----------------------------------------


def test_run_config_fills_every_default():
    assert runconfig.run_config() == runconfig.RUN_CONFIG_DEFAULTS
    assert runconfig.run_config() is not runconfig.RUN_CONFIG_DEFAULTS  # never the shared dict


def test_the_shared_defaults_cannot_be_mutated_in_place():
    """Process-global and read by every run: a stray write would move a later scan's budget."""
    with pytest.raises(TypeError):
        runconfig.RUN_CONFIG_DEFAULTS["budget"] = 999.0


def test_run_config_fills_the_gaps_of_a_partial_message():
    """A message that predates a knob runs with that knob's default, not a KeyError."""
    resolved = runconfig.run_config({"budget": 1.25})
    assert resolved["budget"] == 1.25
    assert resolved["max_s_candidates"] == runconfig.DEFAULT_MAX_S_CANDIDATES
    assert resolved["doc_filter"] == runconfig.DEFAULT_DOC_FILTER


def test_run_config_overrides_beat_the_dict():
    assert runconfig.run_config({"budget": 1.0}, budget=2.0)["budget"] == 2.0


def test_run_config_refuses_an_unknown_key():
    """Version skew fails at the boundary, never silently inside a paid run."""
    with pytest.raises(ValueError, match="unknown run-config key"):
        runconfig.run_config({"journal_export": "/tmp/x.jsonl"})
