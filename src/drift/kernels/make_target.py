"""A `make <target>` a document mentions is defined in the root Makefile."""

from __future__ import annotations

import os
import re

from drift.kernels.models import Ungateable
from drift.kernels.registry import Predicate, register_predicate

_TARGET_SHAPE = re.compile(r"^[A-Za-z0-9._-]+$")
_RULE = re.compile(r"^([A-Za-z0-9._\- ]+):(?!=)")  # `target:` but not `VAR :=`
_INCLUDE = re.compile(r"^-?s?include\s")
_MAKEFILE_NAMES = ("Makefile", "makefile", "GNUmakefile")


def _normalize(
    literal: str, doc_path: str, proposed_args: tuple[str, ...] | None = None
) -> tuple[dict, tuple[str, ...]] | None:
    """Validate a proposed target name, or decline.

    A target cannot be derived from arbitrary document text, so it must be proposed; the
    literal is never read here.
    """
    if not proposed_args or len(proposed_args) != 1:
        return None
    target = proposed_args[0].strip()
    if not _TARGET_SHAPE.match(target):
        return None
    return {"source": "proposed"}, (target,)


def _targets(text: str) -> set[str]:
    """Every target a Makefile's text names in a rule, skipping recipe lines and comments.

    A name beginning with a dot is a directive rather than a target and is dropped — except
    `.PHONY`, whose prerequisites are real target names and are collected in its place.
    """
    out: set[str] = set()
    for line in text.splitlines():
        if line.startswith("\t") or line.lstrip().startswith("#"):
            continue
        m = _RULE.match(line)
        if not m:
            continue
        names = m.group(1).split()
        if names == [".PHONY"]:
            _, _, rest = line.partition(":")
            out |= set(rest.split())
        else:
            out |= {n for n in names if not n.startswith(".")}
    return out


def _has_unknowable_targets(text: str) -> bool:
    """True if `include`/`$(...)`-derived rules make the target set not statically knowable."""
    for line in text.splitlines():
        if line.startswith("\t"):
            continue
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if _INCLUDE.match(stripped):
            return True
        colon_idx = line.find(":")
        if colon_idx != -1 and "$(" in line[:colon_idx]:
            return True
    return False


def _kernel(repo_root: str, target: str) -> bool:
    """Pure check against the repository at one revision: is the target defined?

    A found target wins outright. Otherwise an `include` directive or a `$(var)`-expanded rule
    name makes the target set statically unknowable, and the kernel declines rather than
    guessing False. No Makefile at all declines too.
    """
    for name in _MAKEFILE_NAMES:
        path = os.path.join(repo_root, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if target in _targets(text):
                return True
            if _has_unknowable_targets(text):
                raise Ungateable("makefile-includes")
            return False
    raise Ungateable("no-makefile")


MAKE_TARGET_EXISTS = Predicate(
    name="make_target_exists",
    description=(
        "asserts that a `make <target>` command mentioned in the doc names a target defined "
        "in the repo's root Makefile; args must be [target_name]"
    ),
    normalize=_normalize,
    kernel=_kernel,
)
register_predicate(MAKE_TARGET_EXISTS)
