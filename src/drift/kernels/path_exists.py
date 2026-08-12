"""A path a document mentions exists in the repository."""

from __future__ import annotations

import os
import posixpath
import re

from drift.kernels.gitignore import is_ignored
from drift.kernels.models import Ungateable
from drift.kernels.registry import Predicate, register_predicate

# Skip rather than guess: a literal that is not a plain path token — whitespace, markdown
# syntax, YAML punctuation — is unbindable, and stat-ing one mints pure artefacts.
_PATHLIKE = re.compile(r"^[A-Za-z0-9._@+\-/]+$")


def _normalize(
    literal: str, doc_path: str, proposed_args: tuple[str, ...] | None = None
) -> tuple[dict, tuple[str, ...]] | None:
    """Resolve a path literal against the base its own syntax implies.

    `./` and `../` are document-relative, a bare path repo-root-relative; non-pathlike, absolute
    and repo-escaping literals decline. Backtick stripping is recorded rather than re-derived,
    because replay reads the record. `proposed_args` is ignored: a proposed base would make a
    claim's identity depend on what the model said that run, and `base-ambiguous` covers it.
    """
    norm: dict = {}
    stripped = literal.strip()
    if len(stripped) >= 2 and stripped.startswith("`") and stripped.endswith("`"):
        stripped = stripped[1:-1]
        norm["stripped"] = "backticks"
    if not _PATHLIKE.match(stripped):
        return None
    if posixpath.isabs(stripped):
        return None
    if stripped.startswith(("./", "../")):
        base = posixpath.dirname(doc_path)
        norm["base"] = "doc-relative"
    else:
        base = ""
        norm["base"] = "repo-root"
    joined = posixpath.normpath(posixpath.join(base, stripped))
    if joined.startswith(".."):  # escapes the repository: rejected before any stat
        return None
    return norm, (joined,)


def _suffix_matches_in_tree(repo_root: str, rel_path: str) -> bool:
    """Does any file or directory end with `rel_path`'s whole trailing component sequence?

    The detector behind `base-ambiguous`: a bare reference that resolves nowhere at the root but
    matches under some parent. Whole components only — `reactor.rs` at the root does not match
    `actor/reactor.rs`. Ignored subtrees are pruned, because a namesake vendored under `.venv`
    is not the document's implied base and treating it as one would swallow real drift.
    """
    rel = rel_path.replace(os.sep, "/")
    suffix = "/" + rel
    for dirpath, dirnames, filenames in os.walk(repo_root):
        rel_dir = os.path.relpath(dirpath, repo_root).replace(os.sep, "/")
        prefix = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = [d for d in dirnames if d != ".git" and not is_ignored(repo_root, prefix + d)]
        for name in list(filenames) + list(dirnames):
            full = prefix + name
            if full == rel or full.endswith(suffix):
                return True
    return False


def _kernel(repo_root: str, rel_path: str) -> bool:
    """Pure check against the repository at one revision: does the path exist?

    Absent but ignored by git is build output, not drift. Absent at the implied base while the
    whole component sequence exists elsewhere is `base-ambiguous`: the document named a real path
    relative to a directory it did not spell out. A path matching nowhere stays False, so a
    genuinely stale one survives to be judged.
    """
    target = os.path.normpath(os.path.join(repo_root, rel_path))
    repo_root_norm = os.path.normpath(repo_root)
    if not target.startswith(repo_root_norm + os.sep) and target != repo_root_norm:
        return False
    if os.path.exists(target):
        return True
    if is_ignored(repo_root, rel_path):
        raise Ungateable("gitignored")
    if _suffix_matches_in_tree(repo_root, rel_path):
        raise Ungateable("base-ambiguous")
    return False


PATH_EXISTS = Predicate(
    name="path_exists",
    description=(
        "asserts that a repo-relative file or directory path mentioned in the doc exists "
        "in the repo (./ or ../ prefixes resolve relative to the doc; bare paths from repo root)"
    ),
    normalize=_normalize,
    kernel=_kernel,
)
register_predicate(PATH_EXISTS)
