"""symbol_resolves admission LAW: external-skip, module-unreachable, attr-missing refutes."""

import pytest

from drift.kernels.models import Ungateable
from drift.kernels.symbol_resolves import SYMBOL_RESOLVES


def _mini_repo(tmp_path):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text("def real_fn(a, b=1):\n    return a\n")
    return str(tmp_path)


def test_resolves_true(tmp_path):
    root = _mini_repo(tmp_path)
    assert SYMBOL_RESOLVES.kernel(root, "mypkg.mod.real_fn") is True


def test_attr_missing_refutes(tmp_path):
    root = _mini_repo(tmp_path)
    assert SYMBOL_RESOLVES.kernel(root, "mypkg.mod.gone_fn") is False


def _inheritance_repo(tmp_path, mod_source: str):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(mod_source)
    return str(tmp_path)


def test_member_inherited_from_in_package_base_resolves(tmp_path):
    """`.members` is own-only, so an inherited member read as absent became an unhedged
    refutation — a class was reported as missing methods it genuinely carries from its base."""
    root = _inheritance_repo(
        tmp_path,
        "class Base:\n    def hook(self):\n        pass\n\nclass Sub(Base):\n    pass\n",
    )
    assert SYMBOL_RESOLVES.kernel(root, "mypkg.mod.Sub.hook") is True


def test_member_inherited_transitively_resolves(tmp_path):
    """The closure is transitive: a grandparent's member is still the class's member."""
    root = _inheritance_repo(
        tmp_path,
        "class A:\n    def hook(self):\n        pass\n\n"
        "class B(A):\n    pass\n\nclass C(B):\n    pass\n",
    )
    assert SYMBOL_RESOLVES.kernel(root, "mypkg.mod.C.hook") is True


def test_member_absent_under_external_base_ungateable(tmp_path):
    """An MRO reaching an external base without a hit is a skip: the member's truth lives in
    a package this kernel does not load. Refuting would repeat the mistake above, and loading
    the package would break the rule that a predicate decides a claim from the repository at
    that commit and nothing else."""
    root = _inheritance_repo(tmp_path, "from external import Base\n\nclass Sub(Base):\n    pass\n")
    with pytest.raises(Ungateable) as e:
        SYMBOL_RESOLVES.kernel(root, "mypkg.mod.Sub.gone")
    assert e.value.reason == "external-base"


def test_member_absent_with_all_bases_resolvable_refutes(tmp_path):
    """Refutation stays earned: every base inspected in-package and none carries the member."""
    root = _inheritance_repo(tmp_path, "class Base:\n    x = 1\n\nclass Sub(Base):\n    pass\n")
    assert SYMBOL_RESOLVES.kernel(root, "mypkg.mod.Sub.gone") is False


def test_external_package_ungateable(tmp_path):
    root = _mini_repo(tmp_path)
    with pytest.raises(Ungateable) as e:
        SYMBOL_RESOLVES.kernel(root, "requests.get")
    assert e.value.reason == "external"


def test_missing_intermediate_module_ungateable(tmp_path):
    """The pypdfium2.raw generated-bindings class: intermediate module not statically loadable."""
    root = _mini_repo(tmp_path)
    with pytest.raises(Ungateable) as e:
        SYMBOL_RESOLVES.kernel(root, "mypkg.rawgen.FPDF_thing")
    assert e.value.reason == "module-unreachable"


def test_normalize_shape_and_backticks():
    norm, args = SYMBOL_RESOLVES.normalize("`mypkg.mod.real_fn`", "README.md", None)
    assert args == ("mypkg.mod.real_fn",)
    assert norm.get("stripped") == "backticks"
    assert SYMBOL_RESOLVES.normalize("not a symbol!", "README.md", None) is None


def test_normalize_strips_call_parens():
    _, args = SYMBOL_RESOLVES.normalize("mypkg.mod.real_fn()", "README.md", None)
    assert args == ("mypkg.mod.real_fn",)
