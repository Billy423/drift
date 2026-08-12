"""Base-closure hardening: an unresolvable base must skip, never crash.

Griffe raises `AliasResolutionError` on any attribute access of an alias whose target is
unloadable, and `getattr(x, name, default)` swallows only `AttributeError`. The one-hop shape
(`from external import Base` in the defining module) worked by accident. The re-export chain
(`from ._compat import Base`, where `_compat` re-exports a third-party class) is the hard case,
and it must land on `Ungateable("external-base")` rather than on a kernel error, which would
void the whole run's fitness over one unreachable package.
"""

import pytest

from drift.kernels.class_has_member import CLASS_HAS_MEMBER
from drift.kernels.models import Ungateable
from drift.kernels.pysymbols import _resolve_inherited
from drift.kernels.symbol_resolves import SYMBOL_RESOLVES


def _reexport_chain_repo(tmp_path):
    """`mod.Base` is an alias into `other`, whose `Base` is an alias into an external package —
    touching `.bases`/`.members` of that alias raises AliasResolutionError."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "other.py").write_text("from external import Base\n")
    (pkg / "mod.py").write_text("from mypkg.other import Base\n\nclass S(Base):\n    pass\n")
    return str(tmp_path)


def test_reexported_external_base_is_ungateable_not_an_error(tmp_path):
    root = _reexport_chain_repo(tmp_path)
    with pytest.raises(Ungateable) as e:
        SYMBOL_RESOLVES.kernel(root, "mypkg.mod.S.gone")
    assert e.value.reason == "external-base"


def test_class_has_member_survives_the_reexport_chain(tmp_path):
    root = _reexport_chain_repo(tmp_path)
    with pytest.raises(Ungateable) as e:
        CLASS_HAS_MEMBER.kernel(root, "mypkg.mod.S", "anything")
    assert e.value.reason == "external-base"


class _FakeNode:
    def __init__(self, members=None, bases=None):
        import griffe

        self.members = members or {}
        self.bases = bases or []
        self.kind = griffe.Kind.CLASS


class _FakeProvider:
    """A provider whose resolve() cannot produce a Symbol for the member it can see."""

    def __init__(self, top):
        self._top = top

    def _loaded(self):
        return self._top

    def resolve(self, dotted):
        return None


def test_member_seen_on_a_base_but_unresolvable_is_never_a_refutation():
    """A member seen on a base whose target will not resolve is absent information, not an
    absent member. Falling through to `return None` here would reintroduce the unhedged
    refutation inside the very function written to remove it."""
    base = _FakeNode(members={"hook": object()})
    base.canonical_path = "mypkg.base.B"
    sub = _FakeNode(bases=[base])
    provider = _FakeProvider({"mypkg": _FakeNode(members={"base": _FakeNode(members={"B": base})})})
    with pytest.raises(Ungateable) as e:
        _resolve_inherited(provider, sub, "hook")
    assert e.value.reason == "external-base"
