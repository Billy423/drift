"""A link a document makes resolves to a file in the repository."""

from __future__ import annotations

import posixpath
import re

from drift.kernels.path_exists import _PATHLIKE
from drift.kernels.path_exists import _kernel as _path_kernel
from drift.kernels.registry import Predicate, register_predicate

_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

# A link binds only when the literal names a file — a known extension, or an
# extensionless name below. After a join, an elided doc page and a broken link are identical.
LINK_JURISDICTION_VERSION = "2"  # bump on any change to either list below

# Never admit an extension that is also a plausible identifier tail: `.der`, `.keys`, `.util`.
_LINK_EXTENSIONS = frozenset(
    {
        # prose / rendered docs
        ".md",
        ".markdown",
        ".rst",
        ".txt",
        ".adoc",
        ".html",
        ".htm",
        ".pdf",
        ".ipynb",
        # source
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".java",
        ".rb",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".sh",
        ".bash",
        ".sql",
        # config / data
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".xml",
        ".csv",
        ".proto",
        ".lock",
        # assets
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".ico",
    }
)

# Build and legal conventions only, matched case-insensitively. A name that is also a
# document section heading — `contributing`, `changelog` — reopens what the rule above closes.
_EXTENSIONLESS_DOC_FILENAMES = frozenset({"readme", "license", "makefile", "dockerfile"})


def _in_jurisdiction(href: str) -> bool:
    """Does the pre-join literal name a file, by the two lists alone?

    Applied to `href` and never to the joined path, which no longer carries the distinction.
    """
    final = href.rsplit("/", 1)[-1]
    ext = posixpath.splitext(final)[1].lower()
    if ext:
        return ext in _LINK_EXTENSIONS
    return final.lower() in _EXTENSIONLESS_DOC_FILENAMES


def _normalize(
    literal: str, doc_path: str, proposed_args: tuple[str, ...] | None = None
) -> tuple[dict, tuple[str, ...]] | None:
    """Turn a link literal into the repo-relative path to check, or None to decline.

    `proposed_args` is ignored: a link's argument derives entirely from its own literal.
    """
    norm: dict = {"base": "doc-relative"}
    href = literal.strip()
    if len(href) >= 2 and href.startswith("`") and href.endswith("`"):
        href = href[1:-1]
        norm["stripped"] = "backticks"
    if _SCHEME.match(href):  # a scheme puts the target off the filesystem
        return None
    if "#" in href:
        href, frag = href.split("#", 1)
        norm["fragment"] = f"#{frag}"
        if not href:  # an in-page anchor names no file
            return None
    if not _PATHLIKE.match(href):  # markdown role syntax, prose residue
        return None
    if posixpath.isabs(href) or not href:
        return None
    if not _in_jurisdiction(href):  # declining leaves the claim in the ranked tier, unverified
        return None
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(doc_path), href))
    if joined.startswith(".."):
        return None
    return norm, (joined,)


def _kernel(repo_root: str, rel_path: str) -> bool:
    """Pure check against the repository at one revision: does the link target exist?

    Delegates to `path_exists`, so a link and a bare path share one existence rule and one set
    of reasons for declining.
    """
    return _path_kernel(repo_root, rel_path)


LINK_RESOLVES = Predicate(
    name="link_resolves",
    description=(
        "asserts that an intra-repo doc link target (markdown href, no scheme) exists; "
        "resolved relative to the linking doc's directory"
    ),
    normalize=_normalize,
    kernel=_kernel,
)
register_predicate(LINK_RESOLVES)
