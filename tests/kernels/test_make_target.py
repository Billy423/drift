"""make_target_exists: root Makefile parse; no Makefile is skip-don't-guess."""

import pytest

from drift.kernels.make_target import MAKE_TARGET_EXISTS
from drift.kernels.models import Ungateable

_MAKEFILE = """\
.PHONY: clean lint
VAR := x

build: src/main.o
\tcc -o build src/main.o

clean:
\trm -rf build

lint:
\truff check .
"""


def test_plain_rule_and_phony_targets(tmp_path):
    (tmp_path / "Makefile").write_text(_MAKEFILE)
    for target in ("build", "clean", "lint"):
        assert MAKE_TARGET_EXISTS.kernel(str(tmp_path), target) is True


def test_absent_target_false(tmp_path):
    (tmp_path / "Makefile").write_text(_MAKEFILE)
    assert MAKE_TARGET_EXISTS.kernel(str(tmp_path), "deploy") is False


def test_variable_assignment_is_not_a_target(tmp_path):
    (tmp_path / "Makefile").write_text(_MAKEFILE)
    assert MAKE_TARGET_EXISTS.kernel(str(tmp_path), "VAR") is False


def test_no_makefile_ungateable(tmp_path):
    with pytest.raises(Ungateable) as e:
        MAKE_TARGET_EXISTS.kernel(str(tmp_path), "clean")
    assert e.value.reason == "no-makefile"


_MAKEFILE_WITH_INCLUDE = """\
include extra.mk

clean:
\trm -rf build
"""


def test_include_directive_absent_target_ungateable(tmp_path):
    (tmp_path / "Makefile").write_text(_MAKEFILE_WITH_INCLUDE)
    with pytest.raises(Ungateable) as e:
        MAKE_TARGET_EXISTS.kernel(str(tmp_path), "deploy")
    assert e.value.reason == "makefile-includes"


def test_include_directive_found_target_still_true(tmp_path):
    (tmp_path / "Makefile").write_text(_MAKEFILE_WITH_INCLUDE)
    assert MAKE_TARGET_EXISTS.kernel(str(tmp_path), "clean") is True


_MAKEFILE_WITH_VAR_RULE = """\
BIN := app

$(BIN): src
\tcc -o $(BIN) src

clean:
\trm -rf $(BIN)
"""


def test_dollar_paren_rule_absent_target_ungateable(tmp_path):
    (tmp_path / "Makefile").write_text(_MAKEFILE_WITH_VAR_RULE)
    with pytest.raises(Ungateable) as e:
        MAKE_TARGET_EXISTS.kernel(str(tmp_path), "deploy")
    assert e.value.reason == "makefile-includes"


def test_normalize_target_shape():
    norm, args = MAKE_TARGET_EXISTS.normalize("make clean", "README.md", ("clean",))
    assert args == ("clean",)
    assert MAKE_TARGET_EXISTS.normalize("make $(VAR)", "README.md", ("$(VAR)",)) is None
