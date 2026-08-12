import os
import subprocess

from drift.agent.repo_map import build_repo_map


def test_git_repo_lists_tracked_files_sorted(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "b.py").write_text("b")
    (tmp_path / "a.md").write_text("a")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)

    result = build_repo_map(str(tmp_path))

    assert result == "a.md\nb.py"


def test_non_git_dir_falls_back_to_walk(tmp_path):
    (tmp_path / "x.md").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "y.md").write_text("y")

    result = build_repo_map(str(tmp_path))
    lines = result.split("\n")

    assert "x.md" in lines
    assert os.path.join("sub", "y.md") in lines


def test_truncates_and_appends_marker(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.md").write_text("x")

    result = build_repo_map(str(tmp_path), limit=2)
    lines = result.split("\n")

    assert len(lines) == 3
    assert lines[-1] == "… (truncated)"
