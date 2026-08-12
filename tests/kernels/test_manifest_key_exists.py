"""`manifest_key_exists`, the first preview-grade predicate.

Deliberately narrow semantics: root-only `package.json`,
`normalized_args = ("package.json", "scripts.<key>")`, absent manifest ->
Ungateable("no-manifest"), unparseable -> Ungateable("manifest-unparseable").

Whole-tree and monorepo lookup, and a `pyproject` surface, are deliberately absent. Measuring the
narrow kernel before widening it is the entire point of the preview grade, and building the wide
version now would pre-empt the measurement that promotion is supposed to rest on.
"""

import json

import pytest

from drift.kernels.manifest_key_exists import MANIFEST_KEY_EXISTS
from drift.kernels.models import Ungateable
from drift.kernels.registry import predicate_registry


def _norm(literal, doc_path="README.md"):
    return MANIFEST_KEY_EXISTS.normalize(literal, doc_path, None)


# --- grade ---


def test_the_predicate_is_registered_at_preview_grade():
    assert predicate_registry["manifest_key_exists"].grade == "preview"


def test_every_other_shipped_predicate_is_high_grade():
    """The default must stay `high`, so that a predicate which forgot to declare a grade
    cannot silently acquire the one that is unable to mint. `class_has_member` is the second
    deliberate preview entry."""
    assert {
        name: p.grade for name, p in predicate_registry.items() if name != "manifest_key_exists"
    } == {
        "class_has_member": "preview",
        "path_exists": "high",
        "link_resolves": "high",
        "symbol_resolves": "high",
        "signature_has_param": "high",
        "make_target_exists": "high",
    }


# --- normalize: X's trigger, verbatim ---


def test_npm_run_binds_to_the_scripts_key():
    norm, args = _norm("npm run build")
    assert args == ("package.json", "scripts.build")
    assert norm["manifest"] == "package.json"


def test_the_four_lifecycle_verbs_bind_without_run():
    for verb in ("start", "test", "stop", "restart"):
        _norm_result = _norm(f"npm {verb}")
        assert _norm_result is not None, verb
        assert _norm_result[1] == ("package.json", f"scripts.{verb}")


def test_builtin_npm_commands_do_not_bind():
    """`npm install` is npm's own verb, not a claim about this repo's manifest."""
    for literal in ("npm install", "npm ci", "npm publish", "npm audit"):
        assert _norm(literal) is None


def test_non_npm_literals_do_not_bind():
    for literal in ("make build", "npm", "npm run build && npm test", "yarn build", ""):
        assert _norm(literal) is None


def test_backticks_and_whitespace_are_tolerated():
    assert _norm("  `npm run lint`  ")[1] == ("package.json", "scripts.lint")


def test_scoped_script_names_survive():
    assert _norm("npm run build:prod")[1] == ("package.json", "scripts.build:prod")


def test_identity_carries_the_manifest_path_not_the_doc_location():
    """The path is IN the args, so two docs claiming the same script share one identity and no
    path can leak in from the doc's own location."""
    a = _norm("npm run build", "README.md")[1]
    b = _norm("npm run build", "docs/deep/nested/guide.md")[1]
    assert a == b == ("package.json", "scripts.build")


# --- kernel: pure repo@sha ---


def _write_manifest(tmp_path, obj):
    (tmp_path / "package.json").write_text(json.dumps(obj))


def test_present_key_is_true(tmp_path):
    _write_manifest(tmp_path, {"scripts": {"build": "tsc"}})
    assert MANIFEST_KEY_EXISTS.kernel(str(tmp_path), "package.json", "scripts.build") is True


def test_absent_key_is_false(tmp_path):
    """False is the drift condition — the doc documents a script the manifest does not define."""
    _write_manifest(tmp_path, {"scripts": {"build": "tsc"}})
    assert MANIFEST_KEY_EXISTS.kernel(str(tmp_path), "package.json", "scripts.start") is False


def test_absent_scripts_block_is_false(tmp_path):
    _write_manifest(tmp_path, {"name": "x"})
    assert MANIFEST_KEY_EXISTS.kernel(str(tmp_path), "package.json", "scripts.start") is False


def test_a_null_valued_key_still_counts_as_present(tmp_path):
    """Presence, not truthiness: `"start": ""` is a defined script."""
    _write_manifest(tmp_path, {"scripts": {"start": ""}})
    assert MANIFEST_KEY_EXISTS.kernel(str(tmp_path), "package.json", "scripts.start") is True


def test_missing_manifest_is_ungateable(tmp_path):
    with pytest.raises(Ungateable) as exc:
        MANIFEST_KEY_EXISTS.kernel(str(tmp_path), "package.json", "scripts.start")
    assert exc.value.reason == "no-manifest"


def test_unparseable_manifest_is_ungateable(tmp_path):
    (tmp_path / "package.json").write_text("{not json,,,")
    with pytest.raises(Ungateable) as exc:
        MANIFEST_KEY_EXISTS.kernel(str(tmp_path), "package.json", "scripts.start")
    assert exc.value.reason == "manifest-unparseable"


def test_a_non_object_manifest_is_unparseable_not_a_crash(tmp_path):
    """Valid JSON, wrong shape — still an input the kernel cannot adjudicate against."""
    (tmp_path / "package.json").write_text("[1, 2, 3]")
    with pytest.raises(Ungateable) as exc:
        MANIFEST_KEY_EXISTS.kernel(str(tmp_path), "package.json", "scripts.start")
    assert exc.value.reason == "manifest-unparseable"


def test_a_nested_subdirectory_manifest_is_not_consulted(tmp_path):
    """Root-only, verbatim from X. A monorepo's `packages/*/package.json` is exactly the
    generalization the preview phase exists to measure before it is built."""
    (tmp_path / "packages").mkdir()
    (tmp_path / "packages" / "package.json").write_text(json.dumps({"scripts": {"start": "x"}}))
    with pytest.raises(Ungateable) as exc:
        MANIFEST_KEY_EXISTS.kernel(str(tmp_path), "package.json", "scripts.start")
    assert exc.value.reason == "no-manifest"


def test_both_ungateable_reasons_are_in_the_closed_set():
    from drift.kernels.models import UNGATEABLE_REASONS

    assert {"no-manifest", "manifest-unparseable"} <= UNGATEABLE_REASONS


# --- untrusted-repo containment ---


def test_a_manifest_symlinked_out_of_the_repo_is_not_read(tmp_path):
    """The first kernel that reads BYTES, pointed at third-party repos by any corpus scan.
    A repo controls its own tree, and `open()` follows symlinks — so containment must be by
    realpath, which a lexical check cannot do. Unreadable input is `manifest-unparseable`,
    already in the closed set: the adjudication input could not be obtained, which is exactly
    what that reason means."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.json").write_text(json.dumps({"scripts": {"start": "x"}}))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").symlink_to(outside / "secret.json")

    with pytest.raises(Ungateable) as exc:
        MANIFEST_KEY_EXISTS.kernel(str(repo), "package.json", "scripts.start")
    assert exc.value.reason == "manifest-unparseable"


def test_an_oversized_manifest_is_declined_rather_than_read(tmp_path):
    """A multi-GB `package.json` (or a symlink to an endless device inside the tree) would hang
    or OOM the scan process — killing the graph before the report node, mid-way through a paid
    run. That is the invariant Unit A and Delta 10 exist to protect."""
    from drift.kernels.manifest_key_exists import _MAX_MANIFEST_BYTES

    (tmp_path / "package.json").write_bytes(b"x" * (_MAX_MANIFEST_BYTES + 1))
    with pytest.raises(Ungateable) as exc:
        MANIFEST_KEY_EXISTS.kernel(str(tmp_path), "package.json", "scripts.start")
    assert exc.value.reason == "manifest-unparseable"


def test_a_symlink_that_stays_inside_the_repo_is_still_read(tmp_path):
    """Containment, not symlink-phobia: a repo that legitimately symlinks its own manifest is a
    normal repo, and declining it would cost recall for nothing."""
    (tmp_path / "real.json").write_text(json.dumps({"scripts": {"start": "x"}}))
    (tmp_path / "package.json").symlink_to(tmp_path / "real.json")

    assert MANIFEST_KEY_EXISTS.kernel(str(tmp_path), "package.json", "scripts.start") is True
