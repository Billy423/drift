"""link_resolves admission: doc-relative base, fragment strip, scheme reject, gitignore layer."""

import pytest

from drift.kernels.link_resolves import LINK_RESOLVES
from drift.kernels.models import Ungateable


def test_scheme_urls_unbindable():
    assert LINK_RESOLVES.normalize("https://x.io/a.md", "docs/a.md", None) is None
    assert LINK_RESOLVES.normalize("mailto:x@y.z", "docs/a.md", None) is None


def test_fragment_stripped_and_recorded():
    norm, args = LINK_RESOLVES.normalize("guide.md#setup", "docs/a.md", None)
    assert args == ("docs/guide.md",)
    assert norm["fragment"] == "#setup"
    assert norm["base"] == "doc-relative"


def test_base_is_doc_relative_always():
    _, args = LINK_RESOLVES.normalize("sub/b.md", "docs/a.md", None)
    assert args == ("docs/sub/b.md",)


def test_parent_traversal_within_repo():
    _, args = LINK_RESOLVES.normalize("../README.md", "docs/a.md", None)
    assert args == ("README.md",)


def test_repo_escape_rejected():
    assert LINK_RESOLVES.normalize("../../x.md", "docs/a.md", None) is None


def test_kernel_true_false_ungateable(tmp_path):
    (tmp_path / "README.md").write_text("x")
    (tmp_path / ".gitignore").write_text("site/\n")
    assert LINK_RESOLVES.kernel(str(tmp_path), "README.md") is True
    assert LINK_RESOLVES.kernel(str(tmp_path), "gone.md") is False
    with pytest.raises(Ungateable) as e:
        LINK_RESOLVES.kernel(str(tmp_path), "site/index.html")
    assert e.value.reason == "gitignored"
