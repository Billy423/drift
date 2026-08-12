"""`link_resolves` binds only inside its declared jurisdiction.

Two properties, and the second is what makes the first mean anything:

1. **Decline** — every one of the seventeen Sphinx and RST literals below, plus nested-toctree
   shapes, is refused by `_normalize` (returns None), so the claim falls to the ranked tier
   instead of being answered mechanically.
2. **Non-regression twin** — a literal that binds today still binds, with byte-identical
   `normalized_args`. Without this, property 1 is satisfiable by declining everything, and the
   identity contract that lets stored claims survive a jurisdiction change would break in
   silence.

The seventeen literals are transcribed from a real Sphinx project, not invented for this test.
Each one was a link the kernel bound and answered wrongly before the jurisdiction rule existed,
so the rule is a claim about exactly these strings: substituting plausible-looking ones would
quietly stop testing it.
"""

from drift.kernels.link_resolves import LINK_RESOLVES

# --- the measured corpus (verbatim; doc paths are the reports' own) ---

_DOTTED_11 = [  # docs/source/ecdsa.rst — autodoc module pages, `.util`/`.der`/… are not extensions
    "ecdsa.curves",
    "ecdsa.der",
    "ecdsa.ecdh",
    "ecdsa.ecdsa",
    "ecdsa.eddsa",
    "ecdsa.ellipticcurve",
    "ecdsa.errors",
    "ecdsa.keys",
    "ecdsa.numbertheory",
    "ecdsa.rfc6979",
    "ecdsa.util",
]

_BARE_6 = [  # suffix-elided toctree children: 5 in index.rst + 1 in modules.rst
    ("quickstart", "docs/source/index.rst"),
    ("basics", "docs/source/index.rst"),
    ("ec_arithmetic", "docs/source/index.rst"),
    ("glossary", "docs/source/index.rst"),
    ("modules", "docs/source/index.rst"),
    ("ecdsa", "docs/source/modules.rst"),
]


def test_the_seventeen_measured_rst_literals_all_decline():
    """The decline rule, tested over the observed strings themselves rather than samples."""
    declined = []
    for literal in _DOTTED_11:
        if LINK_RESOLVES.normalize(literal, "docs/source/ecdsa.rst", None) is None:
            declined.append(literal)
    for literal, doc in _BARE_6:
        if LINK_RESOLVES.normalize(literal, doc, None) is None:
            declined.append(literal)
    assert len(declined) == 17, f"only {len(declined)}/17 declined: missed {declined}"


def test_nested_toctree_shapes_decline_too():
    """The draft rule that keyed on a separator killed 6/17 and missed these entirely: a nested
    toctree entry carries a `/` and still has no extension."""
    for literal in ("api/reference", "guides/getting-started", "sub/dir/page"):
        assert LINK_RESOLVES.normalize(literal, "docs/source/index.rst", None) is None


def test_a_directory_reference_declines():
    """Extensionless directory refs demote to the ranked tier. This is the accepted recall
    cost: `docs/` cannot be told from a suffix-elided toctree entry without knowing the
    documentation dialect, and a predicate is not allowed to need that."""
    assert LINK_RESOLVES.normalize("docs", "README.md", None) is None
    assert LINK_RESOLVES.normalize("docs/", "README.md", None) is None


def test_the_section_shaped_allowlist_entries_are_narrowed_out():
    """The extensionless-filename allowlist re-admitted the exact toctree-child shape the
    jurisdiction rule exists to decline. The first two rows are the specimens that showed it:
    both were false positives — `contributing.rst` sits in the same directory, and
    `changelog.md` is generated at build time and gitignored — and they were the allowlist's
    entire contribution. `notice` and `authors` are the same doc-section shape and leave with
    them."""
    for literal, doc in (
        ("contributing", "docs/source/contributors/index.rst"),
        ("changelog", "docs/source/other/index.rst"),
        ("notice", "README.md"),
        ("authors", "docs/index.rst"),
        ("../CONTRIBUTING", "docs/a.md"),  # moved out of _STILL_BINDS by the narrowing
    ):
        assert LINK_RESOLVES.normalize(literal, doc, None) is None, literal


def test_the_jurisdiction_version_carries_the_narrowing():
    """The lists are versioned precisely so a change is a declared event, not a drift."""
    from drift.kernels.link_resolves import LINK_JURISDICTION_VERSION

    assert LINK_JURISDICTION_VERSION != "1"


# --- property 2: the non-regression twin ---

# (literal, doc_path, expected normalized_args[0]) — every row's expectation is the output from
# before the jurisdiction rule existed, which is the point: this list is the control.
_STILL_BINDS = [
    ("guide.md", "docs/a.md", "docs/guide.md"),
    ("sub/b.md", "docs/a.md", "docs/sub/b.md"),
    ("../README.md", "docs/a.md", "README.md"),
    ("guide.md#setup", "docs/a.md", "docs/guide.md"),
    ("`notes.txt`", "README.md", "notes.txt"),
    ("./conf.py", "docs/source/index.rst", "docs/source/conf.py"),
    ("CHANGELOG.rst", "README.md", "CHANGELOG.rst"),
    ("docs/img/logo.png", "README.md", "docs/img/logo.png"),
    (".github/workflows/ci.yml", "README.md", ".github/workflows/ci.yml"),
    ("pyproject.toml", "README.md", "pyproject.toml"),
    ("Makefile", "README.md", "Makefile"),  # on the narrowed extensionless-filename allowlist
    ("LICENSE", "docs/a.md", "docs/LICENSE"),
    ("README", "docs/a.md", "docs/README"),
    ("Dockerfile", "README.md", "Dockerfile"),
]


def test_literals_that_bind_today_still_bind_with_identical_args():
    """No identity migration (invariant #4): the accepted set's normalization output is
    unchanged, so no stored check's `normalized_args` moves under it."""
    for literal, doc_path, expected in _STILL_BINDS:
        result = LINK_RESOLVES.normalize(literal, doc_path, None)
        assert result is not None, f"{literal!r} in {doc_path} stopped binding"
        _norm, args = result
        assert args == (expected,), f"{literal!r} normalized to {args}, expected {(expected,)}"


def test_the_pre_existing_rejections_are_untouched():
    """Δ7 adds a jurisdiction test; it must not disturb the admission gates already in place."""
    assert LINK_RESOLVES.normalize("https://x.io/a.md", "docs/a.md", None) is None  # scheme
    assert LINK_RESOLVES.normalize("#anchor", "docs/a.md", None) is None  # in-page
    assert LINK_RESOLVES.normalize("/etc/passwd.md", "docs/a.md", None) is None  # absolute
    assert LINK_RESOLVES.normalize("../../x.md", "docs/a.md", None) is None  # repo escape
    assert LINK_RESOLVES.normalize("a b.md", "docs/a.md", None) is None  # not pathlike
