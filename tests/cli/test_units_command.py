"""`drift units` — tier 0: enumeration with no database, no network and no API key.

The command exists so a reader can answer "what can I scan, and what will it cost?" before
running anything paid, and so every unresolved-document error has a referent to point at.
"""

import os

from typer.testing import CliRunner

from drift.cli.main import app

runner = CliRunner()


def _repo(tmp_path, files: dict[str, str]):
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return str(tmp_path)


def test_units_lists_doc_units_one_per_line_in_enumeration_order(tmp_path):
    repo = _repo(
        tmp_path,
        {
            "README.md": "hello",
            "docs/guide.md": "hello",
            "docs/api.rst": "hello",
            "notes.txt": "hello",
            "src/thing.py": "x = 1",  # not a doc extension
        },
    )
    result = runner.invoke(app, ["units", repo])

    assert result.exit_code == 0
    printed = result.stdout.strip().splitlines()
    assert printed == ["README.md", "docs/api.rst", "docs/guide.md", "notes.txt"]


def test_units_summary_counts_units_and_only_skipped_hazards(tmp_path):
    """A truncated unit stays in the worklist, so counting it as skipped would contradict the
    listing this command just printed."""
    repo = _repo(tmp_path, {"README.md": "hello", "big.md": "x" * 400_001})
    os.symlink("/etc/hosts", os.path.join(repo, "escaped.md"))

    result = runner.invoke(app, ["units", repo])

    assert result.exit_code == 0
    printed = result.stdout.strip().splitlines()
    # the oversize unit is still scannable; the escaping symlink is not
    assert printed == ["README.md", "big.md"]
    assert "2 doc unit(s)" in result.stderr
    assert "1 skipped" in result.stderr
    assert "escapes-repo" in result.stderr
    assert "truncated" not in result.stderr


def test_units_says_nothing_about_hazards_when_there_are_none(tmp_path):
    repo = _repo(tmp_path, {"README.md": "hello"})
    result = runner.invoke(app, ["units", repo])
    assert result.exit_code == 0
    assert "1 doc unit(s)" in result.stderr
    assert "skipped" not in result.stderr


def test_units_on_a_non_directory_is_a_usage_error(tmp_path):
    missing = str(tmp_path / "nope")
    result = runner.invoke(app, ["units", missing])

    assert result.exit_code == 2
    assert result.stderr.startswith("error: ")
    assert "not a directory" in result.stderr
    assert "Traceback" not in result.stderr


def test_units_needs_no_database(tmp_path, monkeypatch):
    """Tier 0's dependency promise, pinned rather than assumed.

    The engine is built lazily at import, so nothing connects — but that is a property of a
    module this command does not own, and it is exactly the kind of thing that decays silently.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody:nobody@127.0.0.1:1/nothing")
    repo = _repo(tmp_path, {"README.md": "hello"})

    result = runner.invoke(app, ["units", repo])

    assert result.exit_code == 0
    assert "README.md" in result.stdout


def test_units_output_is_accepted_by_the_document_addressing_rule(tmp_path):
    """Every path this command prints must be a legal document argument.

    Pinned here as a shape assertion — repo-relative, no leading `./`, forward slashes — because
    the addressing rule resolves against exactly this string space.
    """
    repo = _repo(tmp_path, {"docs/nested/deep.md": "hello"})
    result = runner.invoke(app, ["units", repo])

    (printed,) = result.stdout.strip().splitlines()
    assert printed == "docs/nested/deep.md"
    assert not printed.startswith("./")
    assert not os.path.isabs(printed)


def test_the_error_branch_table_covers_every_failure_path_in_the_cli():
    """The claim "every failure path prints one error line" quantifies over a set, so the set
    has to be kept honest mechanically.

    Counting `fail(...)` call sites and comparing them to the declared table is what stops the
    table from silently falling behind the code it claims to enumerate.
    """
    import ast
    import pathlib

    from drift.cli.errors import ERROR_BRANCHES

    cli_dir = pathlib.Path(__import__("drift.cli", fromlist=["cli"]).__file__).parent
    call_sites = 0
    for module in sorted(cli_dir.glob("*.py")):
        tree = ast.parse(module.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "fail":
                call_sites += 1

    assert call_sites == len(ERROR_BRANCHES), (
        f"{call_sites} `fail(...)` call site(s) in drift/cli/ but "
        f"{len(ERROR_BRANCHES)} declared branch(es) — update ERROR_BRANCHES"
    )
