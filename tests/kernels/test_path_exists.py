import pytest

from drift.kernels.models import Ungateable
from drift.kernels.path_exists import PATH_EXISTS
from drift.kernels.registry import predicate_registry, vocabulary


def test_absent_and_gitignored_is_ungateable(tmp_path):
    (tmp_path / ".gitignore").write_text("doc/html/\n")
    with pytest.raises(Ungateable) as exc_info:
        PATH_EXISTS.kernel(str(tmp_path), "doc/html/index.html")
    assert exc_info.value.reason == "gitignored"


def test_absent_and_not_ignored_is_false(tmp_path):
    (tmp_path / ".gitignore").write_text("doc/html/\n")
    assert PATH_EXISTS.kernel(str(tmp_path), "src/gone.py") is False


def test_present_path_is_true_even_if_ignored(tmp_path):
    (tmp_path / ".gitignore").write_text("out/\n")
    (tmp_path / "out").mkdir()
    assert PATH_EXISTS.kernel(str(tmp_path), "out") is True


def test_registered():
    assert "path_exists" in predicate_registry
    assert "path_exists" in vocabulary()


def test_normalize_bare_is_repo_root():
    norm, args = PATH_EXISTS.normalize("architecture/ARCH.md", "docs/guide.md")
    assert norm == {"base": "repo-root"}
    assert args == ("architecture/ARCH.md",)


def test_normalize_dot_relative_is_doc_relative():
    norm, args = PATH_EXISTS.normalize("./sub/x.md", "docs/guide.md")
    assert norm == {"base": "doc-relative"}
    assert args == ("docs/sub/x.md",)


def test_normalize_escape_is_unbindable():
    assert PATH_EXISTS.normalize("../../etc/passwd", "docs/guide.md") is None


def test_normalize_absolute_is_unbindable():
    assert PATH_EXISTS.normalize("/etc/passwd", "docs/guide.md") is None


def test_kernel_true_false(tmp_path):
    (tmp_path / "real.md").write_text("x")
    assert PATH_EXISTS.kernel(str(tmp_path), "real.md") is True
    assert PATH_EXISTS.kernel(str(tmp_path), "gone.md") is False


def test_kernel_never_stats_outside_root(tmp_path):
    # escaping args can only arrive via a bug (normalize rejects them); kernel re-guards anyway
    assert PATH_EXISTS.kernel(str(tmp_path), "../real.md") is False


def test_normalize_non_pathlike_literals_are_unbindable():
    # shapes observed in real documents: markdown link, heading, YAML fragment, prose
    for bad in (
        "[`docs/GUIDE.md`](../docs/GUIDE.md)",
        "### 2a. STATE — `.config/STATE.md` (dedicated)",
        "- { path: log/,                kind: log }",
        "the sibling project's `state/build-status.md`",
        "a b",
    ):
        assert PATH_EXISTS.normalize(bad, "docs/guide.md") is None


def test_normalize_strips_surrounding_backticks_and_records_it():
    norm, args = PATH_EXISTS.normalize("`config/registry.yaml`", "docs/guide.md")
    assert args == ("config/registry.yaml",)
    assert norm["stripped"] == "backticks"
    assert norm["base"] == "repo-root"


# --- base resolution: what the literal says, and nothing a producer proposes ---


def test_normalize_ignores_proposed_args_option1_withdrawn():
    """Letting a producer propose the base was built and then withdrawn. It could refute a
    literal that resolves at its own base, by relocating it somewhere it does not; it never
    reached the dotless-directory cluster it was built for; and it made `normalized_args` depend
    on which producer ran, breaking the identity that keeps a claim stable across runs."""
    norm, args = PATH_EXISTS.normalize("actor/reactor.rs", "agents.md", ("src/actor/reactor.rs",))
    assert norm["base"] == "repo-root" and args == ("actor/reactor.rs",)


def test_a_literal_that_resolves_at_its_own_base_is_never_certified(tmp_path):
    """The guard that withdrawal restores: a proposal can no longer move the check off a path
    that actually exists."""
    (tmp_path / "actor").mkdir()
    (tmp_path / "actor" / "reactor.rs").write_text("x")
    (tmp_path / "src").mkdir()
    _, args = PATH_EXISTS.normalize("actor/reactor.rs", "DOC.md", ("src/actor/reactor.rs",))
    assert PATH_EXISTS.kernel(str(tmp_path), *args) is True


def test_absent_at_base_but_suffix_matches_is_base_ambiguous(tmp_path):
    """Option 2: bare path misses at repo-root but the same component sequence exists under a
    parent dir → mechanically unadjudicable (implied base), journal-only, never a HIGH."""
    (tmp_path / "src" / "actor").mkdir(parents=True)
    (tmp_path / "src" / "actor" / "reactor.rs").write_text("x")
    with pytest.raises(Ungateable) as exc:
        PATH_EXISTS.kernel(str(tmp_path), "actor/reactor.rs")
    assert exc.value.reason == "base-ambiguous"


def test_base_ambiguous_matches_directory_targets(tmp_path):
    (tmp_path / "site" / "public").mkdir(parents=True)
    with pytest.raises(Ungateable) as exc:
        PATH_EXISTS.kernel(str(tmp_path), "public")
    assert exc.value.reason == "base-ambiguous"


def test_absent_and_no_suffix_match_stays_false_true_positive_preserved(tmp_path):
    """The true-positive path: genuinely stale, nothing suffix-matches → False → M_CERTIFIED →
    S-judge. base-ambiguous must NOT swallow real drift."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "unrelated.rs").write_text("x")
    assert PATH_EXISTS.kernel(str(tmp_path), "actor/reactor.rs") is False


def test_suffix_match_requires_full_component_boundary(tmp_path):
    """`reactor.rs` existing must NOT make `actor/reactor.rs` base-ambiguous — the match is on
    the whole trailing component sequence, not a substring."""
    (tmp_path / "reactor.rs").write_text("x")
    assert PATH_EXISTS.kernel(str(tmp_path), "actor/reactor.rs") is False


def test_gitignored_takes_precedence_over_base_ambiguous(tmp_path):
    (tmp_path / ".gitignore").write_text("build/\n")
    (tmp_path / "src" / "build").mkdir(parents=True)
    (tmp_path / "src" / "build" / "out.o").write_text("x")
    with pytest.raises(Ungateable) as exc:
        PATH_EXISTS.kernel(str(tmp_path), "build/out.o")
    assert exc.value.reason == "gitignored"


def test_base_ambiguous_ignores_gitignored_namesakes(tmp_path):
    """A match found only inside an ignored tree (.venv, node_modules) is a vendored namesake
    rather than the document's implied base, and suppressing on it would swallow real drift."""
    (tmp_path / ".gitignore").write_text(".venv/\n")
    (tmp_path / ".venv" / "lib" / "actor").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "actor" / "reactor.rs").write_text("x")
    assert PATH_EXISTS.kernel(str(tmp_path), "actor/reactor.rs") is False
