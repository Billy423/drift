"""Regression over the false-positive classes an earlier deterministic engine produced.

Two acceptance shapes, and the second is what keeps the first honest. Classes that were retired
mechanically must never reach a certified finding again. Classes that belong to the judge — a
reference that looks cross-repo, a write target, aspirational prose — must still reach one,
because suppressing them in the kernel is exactly the move that made the earlier engine wrong
about most of what it reported.

Each test class below is a minimal synthetic fixture rather than a repository clone, built
directly against `replay()` and the normalize surface it exercises, so nothing here depends on
external repository state.
"""

from __future__ import annotations

from drift.docstrings import DocstringProducer
from drift.gate.replay import GateOutcome, replay
from drift.kernels.link_resolves import LINK_RESOLVES
from drift.kernels.models import Anchor, Check, EvClaim, SSlot
from drift.kernels.path_exists import PATH_EXISTS
from drift.kernels.symbol_resolves import SYMBOL_RESOLVES


def _claim(predicate, literal, doc_path, normalized_args, normalization=None, producer="agent"):
    return EvClaim(
        anchor=Anchor(doc_path=doc_path, spans=((1, 1),), literal=literal),
        check=Check(
            predicate=predicate,
            raw={"literal": literal, "doc_path": doc_path, "proposed_args": []},
            normalization=normalization or {},
            normalized_args=normalized_args,
        ),
        claim_class=1,
        s_slot=SSlot(note="", confidence=0.5),
        provenance={"producer": producer, "agent_ver": "agent/0.3"},
    )


# --- 1. mechanically-retired: docstring parse artifacts (Task 8's fixture shape) ---

_PARSE_ARTIFACT_MOD = '''
def op(msg, **kwargs):
    """Op.

    Parameters
    ----------
    msg : str
        The real, present param — binds normally.
    self : object
        Implicit receiver, filtered.
    cls : type
        Implicit receiver, filtered.
    * artifact : str
        Bullet residue (star-prefixed) — never a real param.
    Something|None : type-expr
        Not a param name (spaced pipe type expression).
    """
'''


class TestDocstringParseArtifactsNeverCertify:
    """star-prefixed / type-expr / self / cls docstring "params": the producer emits NOTHING
    for them, so replay() never even sees a claim to certify."""

    def test_producer_emits_only_the_real_param(self, tmp_path):
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "mod.py").write_text(_PARSE_ARTIFACT_MOD)
        claims, _ = DocstringProducer(str(tmp_path), agent_ver="agent/0.3").produce()
        literals = {c.check.normalized_args[1] for c in claims if c.check}
        assert literals == {"msg"}
        for banned in ("self", "cls", "artifact", "Something"):
            assert banned not in literals


# --- 2. mechanically-retired: generated-module symbol -> Ungateable(module-unreachable) ---


class TestGeneratedModuleSymbolNeverCertifies:
    """A symbol claim into an intermediate module griffe can't statically load (generated
    bindings, e.g. pypdfium2.raw) is Ungateable(module-unreachable) — never a guessed
    M_CERTIFIED finding."""

    def test_symbol_into_unreachable_submodule_is_ungateable(self, tmp_path):
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        literal = "mypkg.generated.WIDGET_ID"
        (tmp_path / "DOC.md").write_text(f"See {literal} for the generated constant.\n")
        norm, args = SYMBOL_RESOLVES.normalize(literal, "DOC.md", None)
        claim = _claim("symbol_resolves", literal, "DOC.md", args, normalization=norm)
        [result] = replay(str(tmp_path), [claim])
        assert result.outcome == GateOutcome.UNGATEABLE
        assert result.detail == "module-unreachable"


# --- 3. mechanically-retired: scheme / MyST-style links -> normalize() None (never binds) ---


class TestSchemeAndMystLinksNeverBind:
    """Scheme URLs (network, impure) and MyST-style cross-reference roles never normalize into
    a Check, so there is nothing for replay() to certify. The MyST gate is a fix landed by this
    task: `link_resolves` lacked the pathlikeness admission gate `path_exists` already had,
    which let role syntax like `{doc}`guide`` bind as a spurious path — the exact 07-14 "8 MyST
    cross-refs mis-read as file paths" FP class (see task-11-report.md)."""

    def test_scheme_url_is_unbindable(self):
        assert LINK_RESOLVES.normalize("https://example.com/guide.md", "DOC.md", None) is None

    def test_myst_doc_role_is_unbindable(self):
        assert LINK_RESOLVES.normalize("{doc}`guide`", "DOC.md", None) is None

    def test_myst_colon_role_is_unbindable(self):
        assert LINK_RESOLVES.normalize(":doc:`guide`", "DOC.md", None) is None

    def test_unbound_claim_is_never_m_certified(self, tmp_path):
        # normalize rejected it -> check=None -> replay() reports UNBOUND, never M_CERTIFIED
        literal = "{doc}`guide`"
        (tmp_path / "DOC.md").write_text(f"See {literal} for the guide.\n")
        claim = EvClaim(
            anchor=Anchor(doc_path="DOC.md", spans=((1, 1),), literal=literal),
            check=None,
            claim_class=3,
            s_slot=SSlot(note="", confidence=0.5),
            provenance={"producer": "agent", "agent_ver": "agent/0.3"},
        )
        [result] = replay(str(tmp_path), [claim])
        assert result.outcome == GateOutcome.UNBOUND


# --- 4. mechanically-retired: variadic-absent param -> Ungateable(variadic) ---

_VARIADIC_MOD = '''
def send_extra(msg, **kwargs):
    """Send with pass-through extras.

    Parameters
    ----------
    msg : str
        The message.
    extra : str
        Documented but absorbed by **kwargs — the kernel can't tell.
    """
'''


class TestVariadicAbsentParamNeverCertifies:
    """A documented-but-absent param on a **kwargs-accepting callable is mechanically
    unadjudicable — Ungateable(variadic), never a guessed M_CERTIFIED finding."""

    def test_producer_claim_into_variadic_signature_is_ungateable(self, tmp_path):
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "mod.py").write_text(_VARIADIC_MOD)
        claims, _ = DocstringProducer(str(tmp_path), agent_ver="agent/0.3").produce()
        [extra_claim] = [c for c in claims if c.check and c.check.normalized_args[1] == "extra"]
        [result] = replay(str(tmp_path), [extra_claim])
        assert result.outcome == GateOutcome.UNGATEABLE
        assert result.detail == "variadic"


# --- 5. mechanically-retired: gitignored path -> Ungateable(gitignored) ---


class TestGitignoredPathNeverCertifies:
    """An absent path that's gitignored (build output) is mechanically unadjudicable —
    Ungateable(gitignored), never a guessed M_CERTIFIED finding."""

    def test_absent_gitignored_path_is_ungateable(self, tmp_path):
        (tmp_path / ".gitignore").write_text("dist/\n")
        literal = "dist/bundle.js"
        (tmp_path / "DOC.md").write_text(f"Built output lands at {literal}.\n")
        norm, args = PATH_EXISTS.normalize(literal, "DOC.md")
        claim = _claim("path_exists", literal, "DOC.md", args, normalization=norm)
        [result] = replay(str(tmp_path), [claim])
        assert result.outcome == GateOutcome.UNGATEABLE
        assert result.detail == "gitignored"


# --- 6. mechanically-retired: implied-base path -> Ungateable(base-ambiguous) ---


class TestImpliedBasePathNeverCertifies:
    """A bare path that misses at the repository root, but whose whole component sequence
    exists under some parent directory, cannot be adjudicated mechanically: the document
    referenced a real path relative to a directory it implied but never spelled out. This was
    the single largest false-positive class the kernel produced, and every target was real.
    The answer is Ungateable("base-ambiguous"), never a guessed finding.

    A single-component bare filename that also exists deep in the tree goes the same way. That
    is the conservative direction — where there is doubt, do not flag."""

    def test_implied_src_base_path_is_ungateable(self, tmp_path):
        (tmp_path / "src" / "actor").mkdir(parents=True)
        (tmp_path / "src" / "actor" / "reactor.rs").write_text("x")
        literal = "actor/reactor.rs"
        (tmp_path / "DOC.md").write_text(f"Modules live under src: {literal}\n")
        norm, args = PATH_EXISTS.normalize(literal, "DOC.md")
        claim = _claim("path_exists", literal, "DOC.md", args, normalization=norm)
        [result] = replay(str(tmp_path), [claim])
        assert result.outcome == GateOutcome.UNGATEABLE
        assert result.detail == "base-ambiguous"

    def test_s_jurisdiction_path_still_certifies_in_a_POPULATED_tree(self, tmp_path):
        """The judge-jurisdiction guard below uses empty fixtures, where the suffix walk can
        never fire, which makes it a paper guard. This is the real one: in a populated repository
        where nothing matches the literal's component sequence, the claim must still certify.
        The ambiguity skip suppresses genuine implied-base namesakes and nothing else."""
        (tmp_path / "src" / "actor").mkdir(parents=True)
        (tmp_path / "src" / "actor" / "reactor.rs").write_text("x")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("x")
        literal = "planned/module.py"
        (tmp_path / "DOC.md").write_text(f"See {literal} for details.\n")
        norm, args = PATH_EXISTS.normalize(literal, "DOC.md")
        claim = _claim("path_exists", literal, "DOC.md", args, normalization=norm)
        [result] = replay(str(tmp_path), [claim])
        assert result.outcome == GateOutcome.M_CERTIFIED

    def test_single_component_widening_is_pinned(self, tmp_path):
        """The accepted recall cost, pinned so it is a decision rather than a surprise: a bare
        single-component literal that also exists deeper in the tree skips instead of
        certifying."""
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "conftest.py").write_text("x")
        literal = "conftest.py"
        (tmp_path / "DOC.md").write_text(f"Fixtures live in {literal}.\n")
        norm, args = PATH_EXISTS.normalize(literal, "DOC.md")
        claim = _claim("path_exists", literal, "DOC.md", args, normalization=norm)
        [result] = replay(str(tmp_path), [claim])
        assert result.outcome == GateOutcome.UNGATEABLE
        assert result.detail == "base-ambiguous"


# --- S-jurisdiction: MUST still M_CERTIFY (suppressing these = the 93%-FP re-drift) ---


class TestSJurisdictionClassesMustCertify:
    """The mechanism's job stops at "does this target exist in the repository at this commit".
    Whether a reference is cross-repo, a write target or aspirational prose is a liveness
    question, and it belongs to the SemanticJudge rather than the gate. A mechanical filter for
    these would be syntax attempting semantics, which is what made the earlier engine wrong
    about most of what it reported."""

    @staticmethod
    def _certify(tmp_path, literal):
        (tmp_path / "DOC.md").write_text(f"See {literal} for details.\n")
        norm, args = PATH_EXISTS.normalize(literal, "DOC.md")
        claim = _claim("path_exists", literal, "DOC.md", args, normalization=norm)
        [result] = replay(str(tmp_path), [claim])
        return result

    def test_cross_repo_looking_path_certifies(self, tmp_path):
        result = self._certify(tmp_path, "othersdk/tests/unit")
        assert result.outcome == GateOutcome.M_CERTIFIED

    def test_write_target_path_not_gitignored_certifies(self, tmp_path):
        result = self._certify(tmp_path, "token.txt")
        assert result.outcome == GateOutcome.M_CERTIFIED

    def test_aspirational_path_certifies(self, tmp_path):
        result = self._certify(tmp_path, "planned/module.py")
        assert result.outcome == GateOutcome.M_CERTIFIED


# --- recall mirrors: MUST still M_CERTIFY (determinism is not a find-ceiling) ---

_RENAMED_PARAM_MOD = '''
def process(data, form="numpy"):
    """Process a waveform.

    Parameters
    ----------
    data : array
        The waveform data.
    fmt : str
        Renamed to `form` in the real signature — the docstring still says `fmt`.
    """
'''


class TestRecallMirrorsMustCertify:
    """Renamed-target drift the mechanism must still catch. These are recall tests, not
    precision ones: a documented parameter that was renamed in the code, and a link whose target
    moved, are the shapes the kernel exists to find."""

    def test_renamed_docstring_param_certifies(self, tmp_path):
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "mod.py").write_text(_RENAMED_PARAM_MOD)
        claims, _ = DocstringProducer(str(tmp_path), agent_ver="agent/0.3").produce()
        [fmt_claim] = [c for c in claims if c.check and c.check.normalized_args[1] == "fmt"]
        [result] = replay(str(tmp_path), [fmt_claim])
        assert result.outcome == GateOutcome.M_CERTIFIED

    def test_renamed_link_target_certifies(self, tmp_path):
        # the doc still links to the pre-rename filename (cibw.yaml -> cibuildwheel.yaml)
        literal = "cibw.yaml"
        (tmp_path / "DOC.md").write_text(f"See [config]({literal}) for the build config.\n")
        (tmp_path / "cibuildwheel.yaml").write_text("build: {}\n")  # the renamed file survives
        norm, args = LINK_RESOLVES.normalize(literal, "DOC.md", None)
        claim = _claim("link_resolves", literal, "DOC.md", args, normalization=norm)
        [result] = replay(str(tmp_path), [claim])
        assert result.outcome == GateOutcome.M_CERTIFIED
