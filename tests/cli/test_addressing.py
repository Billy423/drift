"""Document addressing, candidate half: turning what a user typed into repo-relative spellings.

This module decides SHAPE only. Which candidate is a real scannable unit is the frame's question,
because only the frame owns the address spaces — and keeping those two jobs apart is what lets the
contract's three spellings resolve to one unit without a second walk of the tree.
"""

import os

from drift.cli.addressing import candidates_for


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "README.md").write_text("hello")
    (repo / "docs" / "guide.md").write_text("hello")
    return repo


def test_an_absolute_path_inside_the_repo_gives_one_repo_relative_candidate(tmp_path):
    repo = _repo(tmp_path)
    assert candidates_for(str(repo / "docs" / "guide.md"), str(repo)) == ["docs/guide.md"]


def test_a_cwd_relative_spelling_resolves_when_cwd_is_the_repo(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    assert candidates_for("docs/guide.md", str(repo)) == ["docs/guide.md"]


def test_the_repo_relative_spelling_survives_from_a_cwd_outside_the_repo(tmp_path, monkeypatch):
    """The hard case, and the reason two bases are offered rather than one.

    From an unrelated directory, `docs/guide.md` names nothing under cwd but names a real unit
    under the repo. Dropping the cwd candidate for escaping containment and keeping the
    repo-relative one is what makes all three spellings reach the same unit.
    """
    repo = _repo(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.chdir(outside)

    assert candidates_for("docs/guide.md", str(repo)) == ["docs/guide.md"]


def test_both_bases_are_offered_in_order_when_they_differ(tmp_path, monkeypatch):
    """cwd first, then the repo — a bare name typed in a shell means the file the user can see."""
    repo = _repo(tmp_path)
    nested = repo / "docs"
    monkeypatch.chdir(nested)
    # `README.md` is both `docs/README.md` (cwd, absent) and `README.md` (repo, present).
    assert candidates_for("README.md", str(repo)) == ["docs/README.md", "README.md"]


def test_identical_candidates_are_offered_once(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    assert candidates_for("README.md", str(repo)) == ["README.md"]


def test_a_spelling_outside_the_repo_yields_no_candidates(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    assert candidates_for("../elsewhere/secret.md", str(repo)) == []
    assert candidates_for("/etc/hosts", str(repo)) == []


def test_a_symlinked_route_to_the_repo_resolves_to_the_same_address(tmp_path):
    """A repo reached through a symlinked path is ordinary on macOS, where /tmp is /private/tmp."""
    repo = _repo(tmp_path)
    link = tmp_path / "link-to-repo"
    os.symlink(repo, link)

    assert candidates_for(str(link / "docs" / "guide.md"), str(link)) == ["docs/guide.md"]
    assert candidates_for(str(link / "docs" / "guide.md"), str(repo)) == ["docs/guide.md"]


def test_a_candidate_need_not_exist_because_existence_is_not_this_layer_s_question(tmp_path):
    """Existence is decided against the address spaces, never against the filesystem.

    `os.path.exists('readme.md')` is true on a case-insensitive filesystem when only `README.md`
    is on disk, so an existence branch here would be unreliable on the author's own machine.
    """
    repo = _repo(tmp_path)
    assert candidates_for(str(repo / "docs" / "nope.md"), str(repo)) == ["docs/nope.md"]


def test_a_trailing_slash_or_dot_segments_normalise(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    assert candidates_for("./docs/../README.md", str(repo)) == ["README.md"]
