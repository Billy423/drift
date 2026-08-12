"""Exercise bounded, repository-contained file and glob tools with coverage ledgers."""

import os

from drift.agent.toolbelt import make_toolbelt
from tests.fixtures.deadline import deadline


def test_read_file_and_glob(tmp_path):
    """Read a repository file and enumerate files through the toolbelt."""
    (tmp_path / "a.md").write_text("hello")
    belt = {t.name: t for t in make_toolbelt(str(tmp_path))}
    assert belt["read_file"].fn(path="a.md") == "hello"
    assert "a.md" in belt["glob"].fn(pattern="*.md")


def test_read_file_rejects_escape(tmp_path):
    """Reject a relative path that escapes the repository."""
    belt = {t.name: t for t in make_toolbelt(str(tmp_path))}
    assert "error" in belt["read_file"].fn(path="../secrets.txt").lower()


def test_read_file_capped_per_call(tmp_path):
    """Cap the content returned by each file read."""
    from drift.agent.toolbelt import make_toolbelt

    (tmp_path / "big.txt").write_text("x" * 100_000)
    rf = {t.name: t for t in make_toolbelt(str(tmp_path), read_char_cap=1000)}["read_file"]
    out = rf.fn(path="big.txt")
    assert out.endswith("…[truncated]") and len(out) <= 1000 + len("\n…[truncated]")


def test_read_file_cumulative_budget_exhausts(tmp_path):
    """Share the unit content budget across successive reads."""
    from drift.agent.toolbelt import make_toolbelt

    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("y" * 1000)
    rf = {
        t.name: t for t in make_toolbelt(str(tmp_path), read_char_cap=1000, unit_char_budget=2500)
    }["read_file"]
    assert "y" in rf.fn(path="f0.txt")
    assert "y" in rf.fn(path="f1.txt")
    third = rf.fn(path="f2.txt")
    assert third.endswith("…[truncated]")
    assert "budget exhausted" in rf.fn(path="f3.txt")


def test_small_read_untruncated_and_counts_actual_bytes(tmp_path):
    """Return a small file without a truncation marker."""
    from drift.agent.toolbelt import make_toolbelt

    (tmp_path / "small.md").write_text("hello")
    rf = {t.name: t for t in make_toolbelt(str(tmp_path))}["read_file"]
    assert rf.fn(path="small.md") == "hello"


def test_read_file_ledgers_returned_chars_and_truncation(tmp_path):
    """Ledger returned characters, truncation, and total size for every read."""
    (tmp_path / "big.txt").write_text("x" * 100_000)
    (tmp_path / "small.md").write_text("hello")
    belt = {t.name: t for t in make_toolbelt(str(tmp_path), read_char_cap=1000)}
    rf = belt["read_file"]

    rf.fn(path="small.md")
    rf.fn(path="big.txt")

    # Total size distinguishes a nearly complete capped read from a small fraction of a file.
    assert rf.ledger == [
        {"returned_chars": 5, "truncated": False, "total_chars": 5},
        {"returned_chars": 1000, "truncated": True, "total_chars": 100_000},
    ]


def test_ledger_is_shared_across_the_belts_tools(tmp_path):
    """Share one ordered ledger across every tool in a unit's toolbelt."""
    (tmp_path / "a.md").write_text("hello")
    belt = {t.name: t for t in make_toolbelt(str(tmp_path))}
    belt["read_file"].fn(path="a.md")
    belt["glob"].fn(pattern="*.md")
    assert belt["read_file"].ledger is belt["glob"].ledger
    assert len(belt["glob"].ledger) == 2


def test_exhausted_budget_ledgers_a_zero_char_capped_read(tmp_path):
    """Ledger an exhausted-budget notice as a capped read with no returned content."""
    (tmp_path / "f.txt").write_text("y" * 1000)
    rf = {t.name: t for t in make_toolbelt(str(tmp_path), read_char_cap=1000, unit_char_budget=0)}[
        "read_file"
    ]
    rf.fn(path="f.txt")
    # The budget path never opens the file, so its total size remains unknown.
    assert rf.ledger == [{"returned_chars": 0, "truncated": True, "total_chars": None}]


def test_error_read_ledgers_zero_chars_untruncated(tmp_path):
    """Ledger an escape rejection as an uncapped read with no returned content."""
    rf = {t.name: t for t in make_toolbelt(str(tmp_path))}["read_file"]
    rf.fn(path="../secrets.txt")
    # An escape rejection never opens the file, so its total size remains unknown.
    assert rf.ledger == [{"returned_chars": 0, "truncated": False, "total_chars": None}]


def test_glob_ledgers_its_own_500_hit_cap(tmp_path):
    """Ledger a glob result as truncated when it reaches the listing cap."""
    for i in range(501):
        (tmp_path / f"f{i}.md").write_text("x")
    g = {t.name: t for t in make_toolbelt(str(tmp_path))}["glob"]
    out = g.fn(pattern="*.md")
    entry = g.ledger[0]
    assert entry["returned_chars"] == len(out) and entry["truncated"] is True
    # Total characters describe the uncapped listing, as they do for a file read.
    assert entry["total_chars"] > len(out)


def test_read_file_refuses_a_fifo_without_opening_it(tmp_path):
    """Refuse a FIFO before opening it, because opening one may block indefinitely."""
    os.mkfifo(os.path.join(str(tmp_path), "pipe.md"))
    rf = {t.name: t for t in make_toolbelt(str(tmp_path))}["read_file"]

    with deadline():
        out = rf.fn(path="pipe.md")

    assert out == "error: not a readable regular file (not-regular)"
    # Refusal precedes any open, leaving both returned content and total size unknown.
    assert rf.ledger == [{"returned_chars": 0, "truncated": False, "total_chars": None}]


def test_read_file_refuses_a_directory_and_a_dangling_symlink(tmp_path):
    """Refuse directories and dangling symlinks as non-regular inputs."""
    os.mkdir(os.path.join(str(tmp_path), "adirectory.md"))
    os.symlink(os.path.join(str(tmp_path), "gone.md"), os.path.join(str(tmp_path), "dangling.md"))
    rf = {t.name: t for t in make_toolbelt(str(tmp_path))}["read_file"]

    assert rf.fn(path="adirectory.md") == "error: not a readable regular file (not-regular)"
    assert rf.fn(path="dangling.md") == "error: not a readable regular file (unreadable)"


def test_read_file_survives_a_bound_that_cuts_a_multibyte_character(tmp_path):
    """Decode a bounded read even when its byte limit splits a multibyte character."""
    (tmp_path / "cjk.md").write_text("字" * 5_000, encoding="utf-8")
    rf = {t.name: t for t in make_toolbelt(str(tmp_path), read_char_cap=1_000)}["read_file"]

    out = rf.fn(path="cjk.md")

    assert out.endswith("…[truncated]")
    assert len(out) <= 1_000 + len("\n…[truncated]")
    entry = rf.ledger[0]
    assert entry["truncated"] is True
    assert entry["returned_chars"] <= 1_000
    assert entry["total_chars"] == 15_000


def test_read_file_refuses_symlink_escape(tmp_path):
    """Refuse an in-repository symlink that resolves outside the repository."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SECRET.txt").write_text("TOP-SECRET-HOST-FILE")
    repo = tmp_path / "repo"
    repo.mkdir()
    os.symlink(str(outside), str(repo / "vendor"))
    rf = {t.name: t for t in make_toolbelt(str(repo))}["read_file"]
    assert rf.fn(path="vendor/SECRET.txt") == "error: path escapes the repo"


def test_glob_with_no_matches_returns_no_content(tmp_path):
    """Count the no-match message as zero listing content."""
    g = {t.name: t for t in make_toolbelt(str(tmp_path))}["glob"]
    assert g.fn(pattern="*.md") == "(no matches)"
    assert g.ledger == [{"returned_chars": 0, "truncated": False, "total_chars": 0}]
