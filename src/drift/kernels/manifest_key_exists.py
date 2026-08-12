"""An npm script a document tells the reader to run is defined in the root package.json.

Scoped to the root manifest deliberately. A monorepo lookup or a `pyproject.toml` surface would
change which claims fire, and this predicate's grade is decided on the ones it fires today.
"""

from __future__ import annotations

import json
import os
import re

from drift.kernels.models import Ungateable
from drift.kernels.registry import Predicate, register_predicate

# npm's lifecycle verbs resolve to `scripts.<verb>` without an explicit `run`. Anything else
# after a bare `npm` is a built-in command — a claim about npm, not this repository.
_NPM_LIFECYCLE = frozenset({"start", "test", "stop", "restart"})
_NPM = re.compile(r"^npm\s+(run\s+)?([A-Za-z0-9:_-]+)\s*$")

_MANIFEST = "package.json"
# Bounds the read of a file an untrusted repository controls; not a tuning knob.
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def _normalize(
    literal: str, doc_path: str, proposed_args: tuple[str, ...] | None = None
) -> tuple[dict, tuple[str, ...]] | None:
    """`npm run <script>` or a lifecycle verb -> ("package.json", "scripts.<script>").

    `proposed_args` is ignored: the args derive fully from the literal. The manifest path is an
    argument rather than an implication of the document's location, so two documents naming the
    same script share one identity.
    """
    stripped = literal.strip().strip("`").strip()
    match = _NPM.match(stripped)
    if match is None:
        return None
    is_run, verb = match.group(1), match.group(2)
    if not is_run and verb not in _NPM_LIFECYCLE:
        return None
    return {"manifest": _MANIFEST}, (_MANIFEST, f"scripts.{verb}")


def _lookup(obj: dict, keypath: str) -> bool:
    """Does the dot-separated `keypath` resolve in the parsed manifest?

    Presence, not truthiness: `"start": ""` is a defined script.
    """
    cursor = obj
    for key in keypath.split("."):
        if not isinstance(cursor, dict) or key not in cursor:
            return False
        cursor = cursor[key]
    return True


def _kernel(repo_root: str, manifest_path: str, keypath: str) -> bool:
    """Pure check against the repository at one revision: is the key defined in the manifest?

    Both read failures decline jurisdiction rather than refute. Reading a missing manifest as a
    missing key would manufacture drift in every repository that is not an npm project.
    """
    target = os.path.join(repo_root, manifest_path)
    # This is the only kernel that reads bytes, and a scanned repository controls its own
    # tree: `package.json` can be a symlink out of it, which a lexical check cannot see.
    try:
        real_target = os.path.realpath(target)
        real_root = os.path.realpath(repo_root)
        if not (real_target == real_root or real_target.startswith(real_root + os.sep)):
            raise Ungateable("manifest-unparseable")
        if os.path.getsize(real_target) > _MAX_MANIFEST_BYTES:
            raise Ungateable("manifest-unparseable")
        with open(real_target, "rb") as fh:
            raw = fh.read(_MAX_MANIFEST_BYTES)
    except OSError as exc:
        raise Ungateable("no-manifest") from exc
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise Ungateable("manifest-unparseable") from exc
    if not isinstance(parsed, dict):  # valid JSON, wrong shape — still not an adjudicable input
        raise Ungateable("manifest-unparseable")
    return _lookup(parsed, keypath)


MANIFEST_KEY_EXISTS = Predicate(
    name="manifest_key_exists",
    description=(
        "asserts that an npm script a doc tells the reader to run (`npm run <script>`, or "
        '`npm start`/`test`/`stop`/`restart`) is defined under "scripts" in the repo\'s root '
        "package.json; bind the command literal itself"
    ),
    normalize=_normalize,
    kernel=_kernel,
    grade="preview",
)
register_predicate(MANIFEST_KEY_EXISTS)
