"""Minimal pure .gitignore matcher: root file only, pure repo@sha."""

from drift.kernels.gitignore import is_ignored


def _repo(tmp_path, gitignore: str):
    (tmp_path / ".gitignore").write_text(gitignore)
    return str(tmp_path)


def test_no_gitignore_matches_nothing(tmp_path):
    assert is_ignored(str(tmp_path), "build/out.bin") is False


def test_basename_pattern(tmp_path):
    root = _repo(tmp_path, "*.log\n")
    assert is_ignored(root, "deep/dir/x.log") is True
    assert is_ignored(root, "deep/dir/x.txt") is False


def test_dir_pattern_matches_contents(tmp_path):
    root = _repo(tmp_path, "doc/html/\n")
    assert is_ignored(root, "doc/html/index.html") is True
    assert is_ignored(root, "doc/html") is True
    assert is_ignored(root, "doc/htmlx") is False


def test_anchored_pattern(tmp_path):
    root = _repo(tmp_path, "/build\n")
    assert is_ignored(root, "build") is True
    assert is_ignored(root, "build/a.o") is True
    assert is_ignored(root, "src/build") is False


def test_comments_and_blanks_skipped(tmp_path):
    root = _repo(tmp_path, "# comment\n\n*.tmp\n")
    assert is_ignored(root, "a.tmp") is True


def test_negation_carves_out_a_file(tmp_path):
    root = _repo(tmp_path, "build/\n!build/keep.md\n")
    assert is_ignored(root, "build/keep.md") is False
    assert is_ignored(root, "build/other.md") is True


def test_negation_then_reignore_last_match_wins(tmp_path):
    root = _repo(tmp_path, "!x.log\n*.log\n")
    assert is_ignored(root, "x.log") is True
