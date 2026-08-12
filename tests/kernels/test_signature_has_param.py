"""signature_has_param: presence provable even on variadic; absence-under-variadic ungateable."""

import pytest

from drift.kernels.models import Ungateable
from drift.kernels.signature_has_param import SIGNATURE_HAS_PARAM


def _repo(tmp_path):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(
        "def plain(a, b=1):\n    return a\n\n"
        "def flex(a, **kwargs):\n    return a\n\n"
        "class Thing:\n    def __init__(self, size):\n        self.size = size\n"
    )
    return str(tmp_path)


def test_param_present_true(tmp_path):
    assert SIGNATURE_HAS_PARAM.kernel(_repo(tmp_path), "mypkg.mod.plain", "b") is True


def test_param_absent_nonvariadic_false(tmp_path):
    assert SIGNATURE_HAS_PARAM.kernel(_repo(tmp_path), "mypkg.mod.plain", "zz") is False


def test_param_present_on_variadic_true(tmp_path):
    assert SIGNATURE_HAS_PARAM.kernel(_repo(tmp_path), "mypkg.mod.flex", "a") is True


def test_param_absent_on_variadic_ungateable(tmp_path):
    with pytest.raises(Ungateable) as e:
        SIGNATURE_HAS_PARAM.kernel(_repo(tmp_path), "mypkg.mod.flex", "zz")
    assert e.value.reason == "variadic"


def test_class_resolves_init_signature(tmp_path):
    root = _repo(tmp_path)
    assert SIGNATURE_HAS_PARAM.kernel(root, "mypkg.mod.Thing", "size") is True
    assert SIGNATURE_HAS_PARAM.kernel(root, "mypkg.mod.Thing", "zz") is False


def test_class_with_inherited_init_is_ungateable(tmp_path):
    """A class whose `__init__` is inherited from an unloadable base has no statically-known
    signature, and absent information is a skip rather than a refutation. This is the hard
    case rather than an exotic one: most public classes do not declare their own
    `__init__`."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text("from external import Base\n\nclass Sub(Base):\n    pass\n")
    with pytest.raises(Ungateable) as e:
        SIGNATURE_HAS_PARAM.kernel(str(tmp_path), "mypkg.mod.Sub", "anything")
    assert e.value.reason == "no-signature"


def test_description_no_longer_advertises_class_constructors():
    """A predicate's description text drives what the discovery producer binds to it, so an
    advertisement the kernel cannot honour is a defect in itself: it cannot evaluate most class
    constructors. The lock is on the constructor claim, not on the bare substring "class", so a
    future legitimate wording such as "class method" does not trip it."""
    assert "class-constructor" not in SIGNATURE_HAS_PARAM.description
    assert "constructor" not in SIGNATURE_HAS_PARAM.description


def test_external_symbol_ungateable(tmp_path):
    with pytest.raises(Ungateable) as e:
        SIGNATURE_HAS_PARAM.kernel(_repo(tmp_path), "requests.get", "auth")
    assert e.value.reason == "external"


def test_missing_symbol_refutes(tmp_path):
    assert SIGNATURE_HAS_PARAM.kernel(_repo(tmp_path), "mypkg.mod.gone", "x") is False


def test_normalize_requires_two_args():
    assert SIGNATURE_HAS_PARAM.normalize("plain(b=2)", "README.md", None) is None
    norm, args = SIGNATURE_HAS_PARAM.normalize("plain(b=2)", "README.md", ("mypkg.mod.plain", "b"))
    assert args == ("mypkg.mod.plain", "b")
