import pytest

from drift.kernels.doc_contains import doc_contains


def test_doc_contains(tmp_path):
    (tmp_path / "d.md").write_text("see architecture/ARCH.md here")
    assert doc_contains(str(tmp_path), "d.md", "architecture/ARCH.md") is True
    assert doc_contains(str(tmp_path), "d.md", "not-there.md") is False
    assert doc_contains(str(tmp_path), "missing.md", "x") is False


def test_doc_contains_tolerates_invalid_utf8_bytes(tmp_path):
    """FIX 5a: decodes with errors="replace" (matches the scout's own tolerant read) — a doc
    with stray non-UTF-8 bytes must never raise here, only degrade the unreadable part."""
    (tmp_path / "d.md").write_bytes(b"\xff\xfe see docs/x.md")
    assert doc_contains(str(tmp_path), "d.md", "docs/x.md") is True


def test_doc_contains_separates_an_absent_document_from_an_unreadable_one(tmp_path, monkeypatch):
    """A missing file is an absent anchor; anything else that stops the read is not an answer."""
    (tmp_path / "d.md").write_text("architecture/ARCH.md\n", encoding="utf-8")

    def _denied(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("builtins.open", _denied)
    with pytest.raises(OSError):
        doc_contains(str(tmp_path), "d.md", "architecture/ARCH.md")
