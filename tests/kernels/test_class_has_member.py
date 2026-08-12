"""class_has_member — the fitting predicate for class-member claims.

Preview grade: cannot mint a HIGH, cannot suppress. Member present (own or via an in-package
base) -> True; absent with every base resolvable -> False; absent with an unresolvable base ->
Ungateable("external-base"); a non-class target -> Ungateable("not-a-class") — the predicate
declines what it does not fit, with a reason naming why.

The tests are derived from the predicate's description rather than from its branches, one
fixture per target kind the description claims, each using that kind's hard case.
"""

import pytest

from drift.kernels.class_has_member import CLASS_HAS_MEMBER
from drift.kernels.models import Ungateable


def _repo(tmp_path, mod_source: str):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(mod_source)
    return str(tmp_path)


def test_own_member_present_true(tmp_path):
    root = _repo(tmp_path, "class Cfg:\n    port = 8888\n")
    assert CLASS_HAS_MEMBER.kernel(root, "mypkg.mod.Cfg", "port") is True


def test_method_member_present_true(tmp_path):
    root = _repo(tmp_path, "class Cfg:\n    def hook(self):\n        pass\n")
    assert CLASS_HAS_MEMBER.kernel(root, "mypkg.mod.Cfg", "hook") is True


def test_member_inherited_from_in_package_base_true(tmp_path):
    """The hard case for 'member': inherited, not own — `.members` is own-only, so a naive
    lookup refutes what the class genuinely carries."""
    root = _repo(
        tmp_path,
        "class Base:\n    pre_save_hook = None\n\nclass Sub(Base):\n    pass\n",
    )
    assert CLASS_HAS_MEMBER.kernel(root, "mypkg.mod.Sub", "pre_save_hook") is True


def test_member_absent_with_all_bases_resolvable_false(tmp_path):
    """Refutation is earned: every base was inspected and none carries the member."""
    root = _repo(tmp_path, "class Base:\n    x = 1\n\nclass Sub(Base):\n    pass\n")
    assert CLASS_HAS_MEMBER.kernel(root, "mypkg.mod.Sub", "gone") is False


def test_member_absent_under_external_base_is_ungateable(tmp_path):
    """The member's truth lives in a package this predicate does not load, so absence here is
    unknown rather than false — skip, never refute. This is the exact distinction
    signature_has_param once collapsed, and refuting instead would re-commit it."""
    root = _repo(tmp_path, "from external import Base\n\nclass Sub(Base):\n    pass\n")
    with pytest.raises(Ungateable) as e:
        CLASS_HAS_MEMBER.kernel(root, "mypkg.mod.Sub", "logging_config")
    assert e.value.reason == "external-base"


def test_function_target_declined_not_a_class(tmp_path):
    """The predicate declines every target it does not fit. The hard case is a real function,
    which signature_has_param does fit — so declining is a choice about jurisdiction, not an
    inability to answer."""
    root = _repo(tmp_path, "def plain(a, b=1):\n    return a\n")
    with pytest.raises(Ungateable) as e:
        CLASS_HAS_MEMBER.kernel(root, "mypkg.mod.plain", "b")
    assert e.value.reason == "not-a-class"


def test_module_target_declined_not_a_class(tmp_path):
    root = _repo(tmp_path, "x = 1\n")
    with pytest.raises(Ungateable) as e:
        CLASS_HAS_MEMBER.kernel(root, "mypkg.mod", "x")
    assert e.value.reason == "not-a-class"


def test_external_symbol_ungateable(tmp_path):
    root = _repo(tmp_path, "class Cfg:\n    pass\n")
    with pytest.raises(Ungateable) as e:
        CLASS_HAS_MEMBER.kernel(root, "traitlets.config.Application", "log_level")
    assert e.value.reason == "external"


def test_missing_class_is_module_unreachable(tmp_path):
    """An absent leaf is a skip here: whether the class exists at all is symbol_resolves'
    question, not this predicate's."""
    root = _repo(tmp_path, "class Cfg:\n    pass\n")
    with pytest.raises(Ungateable) as e:
        CLASS_HAS_MEMBER.kernel(root, "mypkg.mod.Gone", "x")
    assert e.value.reason == "module-unreachable"


def test_grade_is_preview():
    """A preview predicate enters the registry unable to mint or suppress. Promotion is a
    separate decision, taken on measured precision over its own fires."""
    assert CLASS_HAS_MEMBER.grade == "preview"


def test_description_claims_the_class_member_shape():
    """Binding follows the description, so the class-member and config-option claim shape must
    have exactly one advertised home — and it is this predicate."""
    d = CLASS_HAS_MEMBER.description
    assert "class" in d
    assert "member" in d


def test_normalize_requires_proposed_class_and_member():
    assert CLASS_HAS_MEMBER.normalize("ServerApp.port", "doc.md", None) is None
    assert CLASS_HAS_MEMBER.normalize("x", "doc.md", ("notdotted", "port")) is None
    assert CLASS_HAS_MEMBER.normalize("x", "doc.md", ("mypkg.mod.Cfg", "not an ident")) is None
    norm, args = CLASS_HAS_MEMBER.normalize("x", "doc.md", ("mypkg.mod.Cfg", "port"))
    assert args == ("mypkg.mod.Cfg", "port")
    assert norm == {"source": "proposed"}
