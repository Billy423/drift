"""Planning a scan: enumerate once, resolve the document, estimate — before anything is written.

Planning is separated from running so that four things become possible at once: refusing an
unresolvable document without creating a run, quoting an estimate before the user commits to
spending, printing an error built from the enumeration that produced it, and keeping a single walk
of the tree per run.
"""

import inspect
import subprocess

import pytest

from drift.graph.planning import DocumentNotResolvable, plan_scan


def _repo(tmp_path, files, git=True):
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    if git:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return str(tmp_path)


DOCS = {
    "README.md": "hello",
    "docs/guide.md": "hello there",
    "pkg/__init__.py": "",
    "pkg/thing.py": "def f(x):\n    pass\n",
    "loose/helper.py": "def g():\n    pass\n",
}


def test_a_plan_with_no_document_covers_the_whole_repo(tmp_path):
    plan = plan_scan(_repo(tmp_path, DOCS))

    assert plan.kind == "repo"
    assert plan.doc_filter is None
    assert plan.worklist == ["README.md", "docs/guide.md"]
    assert plan.enumerated_count == 2
    assert plan.estimate_usd > 0


def test_a_document_candidate_narrows_the_worklist_but_not_the_unit_count(tmp_path):
    """`enumerated_count` reports what the repository holds, so the error text can say so."""
    plan = plan_scan(_repo(tmp_path, DOCS), ["docs/guide.md"])

    assert plan.kind == "doc"
    assert plan.doc_filter == "docs/guide.md"
    assert plan.worklist == ["docs/guide.md"]
    assert plan.enumerated_count == 2


def test_the_first_candidate_that_is_a_real_unit_wins(tmp_path):
    plan = plan_scan(_repo(tmp_path, DOCS), ["nowhere/guide.md", "docs/guide.md"])
    assert plan.doc_filter == "docs/guide.md"


def test_a_candidate_enumeration_skipped_is_refused_with_its_reason(tmp_path):
    """The symlink is COMMITTED, which is both the faithful threat model and the only shape
    that reaches classification: an untracked file is not one of the repository's own."""
    import os

    for rel, body in DOCS.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    (tmp_path / "big.md").write_text("x" * 400_001)
    os.symlink("/etc/hosts", tmp_path / "escaped.md")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    repo = str(tmp_path)

    with pytest.raises(DocumentNotResolvable) as exc:
        plan_scan(repo, ["escaped.md"])

    assert exc.value.hazard_reason == "escapes-repo"
    assert exc.value.spelling == "escaped.md"


def test_an_unresolvable_document_carries_what_the_message_needs(tmp_path):
    with pytest.raises(DocumentNotResolvable) as exc:
        plan_scan(_repo(tmp_path, DOCS), ["docs/guid.md"])

    err = exc.value
    assert err.spelling == "docs/guid.md"
    assert err.candidates_tried == ["docs/guid.md"]
    assert err.hazard_reason is None
    assert err.enumerated_count == 2
    assert "docs/guide.md" in err.near_misses


def test_a_tracked_python_file_inside_a_package_resolves_to_the_docstring_lane(tmp_path):
    plan = plan_scan(_repo(tmp_path, DOCS), ["pkg/thing.py"])

    assert plan.kind == "py"
    assert plan.doc_filter == "pkg/thing.py"
    assert plan.worklist == []  # legal: this lane walks the symbol table, not the doc worklist


def test_a_python_file_outside_the_symbol_corpus_is_refused(tmp_path):
    """`loose/` has no package marker, so nothing would ever walk it — an empty run in disguise."""
    with pytest.raises(DocumentNotResolvable):
        plan_scan(_repo(tmp_path, DOCS), ["loose/helper.py"])


def test_an_untracked_python_file_is_refused(tmp_path):
    repo = _repo(tmp_path, DOCS)
    (tmp_path / "pkg" / "untracked.py").write_text("def h():\n    pass\n")

    with pytest.raises(DocumentNotResolvable):
        plan_scan(repo, ["pkg/untracked.py"])


def test_a_stub_file_is_accepted_like_a_module(tmp_path):
    files = dict(DOCS, **{"pkg/thing.pyi": "def f(x: int) -> None: ...\n"})
    plan = plan_scan(_repo(tmp_path, files), ["pkg/thing.pyi"])
    assert plan.kind == "py"


def test_a_python_candidate_on_a_non_git_tree_is_refused(tmp_path):
    """The tracked-file list is the case-exact address space; a directory walk is not."""
    with pytest.raises(DocumentNotResolvable):
        plan_scan(_repo(tmp_path, DOCS, git=False), ["pkg/thing.py"])


def test_planning_cannot_write_anything(tmp_path):
    """The guarantee that an unresolvable document leaves no trace is structural, not a promise.

    Planning takes no session and no writer, so there is nothing for it to write with.
    """
    params = set(inspect.signature(plan_scan).parameters)
    assert not params & {"session", "session_factory", "writer", "run_id"}
