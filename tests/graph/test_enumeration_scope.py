"""Enumeration covers the repository's own files, not everything under its directory.

A checkout carries other people's trees — installed packages, vendored dependencies, build output.
Those are not documents this repository is responsible for, and a tool that offers them as its
first answer is offering noise. The repository already declares which files are its own, so that
declaration is what enumeration reads.

This is a different question from which documents are LIVE. That one is a judgement about content
this repository owns, and it is not decided here.
"""

import subprocess

from drift.graph.nodes.enumerate_units import enumerate_docs


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path, files, init=True):
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    if init:
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "add", "-A")
    return str(tmp_path)


def test_a_vendored_tree_is_not_this_repositorys_documents(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "README.md": "ours",
            "docs/guide.md": "ours",
            ".gitignore": "node_modules/\n.venv/\n",
        },
    )
    for vendored in ("node_modules/pkg/README.md", ".venv/lib/site-packages/thing/HISTORY.rst"):
        p = tmp_path / vendored
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("theirs")

    worklist, _hazards = enumerate_docs(repo)

    assert worklist == ["README.md", "docs/guide.md"]


def test_the_declaration_is_read_from_the_repository_not_from_a_list_of_names(tmp_path):
    """A name list would be whack-a-mole across ecosystems; this asks the repository instead.

    `third_party/` is on nobody's denylist, and it is excluded here for the only reason that
    generalises: this repository says it is not one of its own files.
    """
    repo = _repo(tmp_path, {"README.md": "ours", ".gitignore": "third_party/\n"})
    p = tmp_path / "third_party" / "vendor" / "NOTES.md"
    p.parent.mkdir(parents=True)
    p.write_text("theirs")

    worklist, _hazards = enumerate_docs(repo)

    assert worklist == ["README.md"]


def test_an_ignore_file_at_a_higher_level_still_takes_effect(tmp_path):
    """The case a pattern matcher gets wrong: the rules live above the directory being scanned.

    Asking the repository is what makes this work — a matcher reading `<repo>/.gitignore` finds
    no file and excludes nothing.
    """
    root = tmp_path / "outer"
    (root / "inner").mkdir(parents=True)
    (root / ".gitignore").write_text("inner/.venv/\n")
    (root / "inner" / "README.md").write_text("ours")
    vendored = root / "inner" / ".venv" / "pkg.md"
    vendored.parent.mkdir(parents=True)
    vendored.write_text("theirs")
    _git(root, "init", "-q")
    _git(root, "add", "-A")

    worklist, _hazards = enumerate_docs(str(root / "inner"))

    assert worklist == ["README.md"]


def test_a_tree_that_is_not_a_repository_is_walked_as_before(tmp_path):
    """No declaration to read, so nothing is excluded and the whole tree is walked."""
    repo = _repo(tmp_path, {"README.md": "x", "docs/guide.md": "y"}, init=False)

    worklist, _hazards = enumerate_docs(repo)

    assert worklist == ["README.md", "docs/guide.md"]


def test_a_document_written_but_not_yet_added_is_not_scanned(tmp_path):
    """The stated cost of asking the repository, pinned so it is a decision and not a surprise.

    A file the repository has not been told about is not yet one of its own. `git add` is the fix,
    and for a tool that reports on committed content it is arguably the right answer anyway.
    """
    repo = _repo(tmp_path, {"README.md": "ours"})
    (tmp_path / "DRAFT.md").write_text("written a moment ago")

    worklist, _hazards = enumerate_docs(repo)

    assert worklist == ["README.md"]


def test_the_same_tree_is_one_tree_to_enumeration_and_to_the_path_predicate(tmp_path):
    """The asymmetry this closes: the predicate honoured the repository's declaration and
    enumeration did not, so one part of the system saw files the other refused to."""
    from drift.kernels.gitignore import is_ignored

    repo = _repo(tmp_path, {"README.md": "ours", ".gitignore": "build/\n"})
    p = tmp_path / "build" / "out.md"
    p.parent.mkdir()
    p.write_text("generated")

    worklist, _hazards = enumerate_docs(repo)

    assert "build/out.md" not in worklist
    assert is_ignored(repo, "build/out.md")
