"""`drift.fsguard` — the three pre-open guards, each against the case it exists for.

The integration tests (`tests/graph/test_step2_enumeration_safety.py`) prove the pipeline
survives a hazardous repo. These prove the guards themselves, including the branches that repo
does not contain: a dangling symlink, a directory named `*.md`, and a multibyte file at the
bound (where "400 000 characters" and "400 000 bytes" stop agreeing).
"""

from __future__ import annotations

import os

from drift.fsguard import B_DOC, SKIP_REASONS, classify_unit, read_doc_bytes, safe_join


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    return str(root)


# --- containment ------------------------------------------------------------------------------


def test_safe_join_contains_traversal_and_escaping_symlinks(tmp_path):
    root = _repo(tmp_path)
    (tmp_path / "outside.md").write_text("secret\n", encoding="utf-8")
    os.symlink(tmp_path / "outside.md", os.path.join(root, "link.md"))

    assert safe_join(root, "README.md") is not None
    assert safe_join(root, "../outside.md") is None
    assert safe_join(root, "link.md") is None
    assert safe_join(root, ".") is not None  # the root itself is contained, not an escape


def test_an_in_repo_symlink_is_not_an_escape(tmp_path):
    """Containment is about where it lands, not about being a link."""
    root = _repo(tmp_path)
    os.symlink(os.path.join(root, "README.md"), os.path.join(root, "alias.md"))

    shape = classify_unit(root, "alias.md")
    assert shape.skip_reason is None
    assert read_doc_bytes(root, "alias.md").data == b"hello\n"


# --- shape ------------------------------------------------------------------------------------


def test_classify_unit_names_one_closed_reason_per_cause(tmp_path):
    root = _repo(tmp_path)
    (tmp_path / "outside.md").write_text("secret\n", encoding="utf-8")
    os.symlink(tmp_path / "outside.md", os.path.join(root, "escape.md"))
    os.mkfifo(os.path.join(root, "pipe.md"))
    os.symlink(os.path.join(root, "gone.md"), os.path.join(root, "dangling.md"))
    os.mkdir(os.path.join(root, "adirectory.md"))

    reasons = {
        rel: classify_unit(root, rel).skip_reason
        for rel in ("README.md", "escape.md", "pipe.md", "dangling.md", "adirectory.md")
    }
    assert reasons == {
        "README.md": None,
        "escape.md": "escapes-repo",
        "pipe.md": "not-regular",
        "dangling.md": "unreadable",
        "adirectory.md": "not-regular",
    }
    assert set(reasons.values()) - {None} <= SKIP_REASONS


def test_a_fifo_is_refused_without_being_opened(tmp_path):
    """The whole point of the ordering: `open()` on this would never return."""
    root = _repo(tmp_path)
    os.mkfifo(os.path.join(root, "pipe.md"))

    assert classify_unit(root, "pipe.md").skip_reason == "not-regular"
    assert read_doc_bytes(root, "pipe.md") is None


# --- the input bound --------------------------------------------------------------------------


def test_a_unit_at_or_below_the_bound_is_read_whole_and_not_flagged(tmp_path):
    root = _repo(tmp_path)
    exact = "y" * B_DOC
    with open(os.path.join(root, "exact.md"), "w", encoding="utf-8") as fh:
        fh.write(exact)

    read = read_doc_bytes(root, "exact.md")
    assert read.truncated is False
    assert read.data.decode() == exact
    assert classify_unit(root, "exact.md").oversize is False


def test_an_oversize_unit_is_cut_at_the_bound_and_says_so(tmp_path):
    root = _repo(tmp_path)
    with open(os.path.join(root, "big.md"), "w", encoding="utf-8") as fh:
        fh.write("z" * (B_DOC + 5_000))

    read = read_doc_bytes(root, "big.md")
    assert read.truncated is True
    assert len(read.data) == B_DOC
    assert read.size_bytes == B_DOC + 5_000
    assert classify_unit(root, "big.md").oversize is True


def test_the_byte_bound_never_yields_more_characters_than_the_stated_bound(tmp_path):
    """`B_doc` is stated in characters and enforced in bytes; UTF-8 makes that sound.

    Every character costs at least one byte, so a read stopped at `bound` bytes decodes to at
    most `bound` characters — exactly for ASCII, and conservatively (early) for multibyte text.
    A cut through a multibyte sequence is absorbed by the pipeline's standard `errors="replace"`.
    """
    root = _repo(tmp_path)
    bound = 1_000
    with open(os.path.join(root, "cjk.md"), "w", encoding="utf-8") as fh:
        fh.write("字" * bound)  # 3 bytes each

    read = read_doc_bytes(root, "cjk.md", bound=bound)
    text = read.data.decode("utf-8", errors="replace")
    assert read.truncated is True
    assert len(read.data) == bound
    assert len(text) <= bound
    assert len(text) < bound  # multibyte: clipped early, which is the conservative direction


def test_read_doc_bytes_refuses_what_classify_unit_refuses(tmp_path):
    root = _repo(tmp_path)
    (tmp_path / "outside.md").write_text("secret\n", encoding="utf-8")
    os.symlink(tmp_path / "outside.md", os.path.join(root, "escape.md"))

    assert read_doc_bytes(root, "escape.md") is None
    assert read_doc_bytes(root, "../outside.md") is None
    assert read_doc_bytes(root, "absent.md") is None
