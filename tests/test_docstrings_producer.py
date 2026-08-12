"""The docstring producer: emission filters, claim assembly through the shared normalize, and
the single-document filter."""

import dataclasses

import griffe

from drift.docstrings import DocstringProducer
from drift.kernels.pysymbols import provider_for as _provider_for
from drift.kernels.registry import predicate_registry

_MOD = '''
def send(msg, error_msg=None, **kwargs):
    """Send.

    Parameters
    ----------
    msg : str
        The message.
    error_msg : str
        Shown on failure.
    ghost : str
        Documented but absent.
    * bullet-artifact : str
        Markup residue.
    **kwargs : dict
        Literal variadic doc.
    Distribution|None : type-expr
        Not a param name.
    cls : type
        Implicit receiver.
    """
'''


def _produce(tmp_path):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(_MOD)
    return DocstringProducer(str(tmp_path), agent_ver="agent/0.3").produce()


def test_filters_and_claims(tmp_path):
    claims, coverage = _produce(tmp_path)
    literals = {c.check.normalized_args[1] for c in claims if c.check}
    # emitted: real params + the genuinely-documented-but-absent one
    assert {"msg", "error_msg", "ghost"} <= literals
    # the four 07-14 parse-fix classes are never emitted as bound claims
    for banned in ("bullet-artifact", "kwargs", "cls"):
        assert banned not in literals
    assert all("|" not in lit for lit in literals)


def test_anchor_is_doc_line_not_bare_name(tmp_path):
    claims, _ = _produce(tmp_path)
    ghost = next(c for c in claims if c.check and c.check.normalized_args[1] == "ghost")
    assert ghost.anchor.literal == "ghost : str"
    assert ghost.anchor.doc_path == "mypkg/mod.py"
    assert ghost.provenance["producer"] == "docstrings"


def test_docstring_lane_records_bound(tmp_path):
    """Every claim this producer emits is bound, because its arguments derive from the
    docstring itself, so the bind block is uniformly 'bound' and a query across producers need
    not special-case it. The producer does decline — at the parse filter and at normalize — but
    a declined parameter doc produces no claim at all, and its accounting lives in the coverage
    record split below."""
    claims, _ = _produce(tmp_path)
    assert claims, "fixture produced no claims"
    for c in claims:
        assert c.provenance["bind"] == {"outcome": "bound"}


def test_docstring_lane_coverage_splits_its_decline_causes(tmp_path):
    """`skipped` was one aggregate over two different causes, which makes a change in the
    total unreadable. Split, a decline count that moves says which cause moved it."""
    _claims, coverage = _produce(tmp_path)
    assert set(coverage["skipped"]) == {"filtered", "normalize-declined"}
    assert coverage["skipped"]["filtered"] >= 1  # the fixture's unparseable-shape classes
    total = coverage["skipped"]["filtered"] + coverage["skipped"]["normalize-declined"]
    assert f"{total} param docs" in coverage["detail"]


_MOD_SPACED_TYPE_EXPR = '''
def send(msg):
    """Send.

    Parameters
    ----------
    msg : str
        The message.
    Distribution | None : type-expr
        Spaced pipe: griffe reports the name as just "Distribution" (a legal identifier).
    """
'''


def test_spaced_type_expr_not_emitted(tmp_path):
    # 07-14 type-expr class, spaced variant: the locator must require the param name to be
    # the WHOLE token before the separator, or "Distribution" leaks through as a bound claim.
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(_MOD_SPACED_TYPE_EXPR)
    claims, _ = DocstringProducer(str(tmp_path), agent_ver="agent/0.3").produce()
    literals = {c.check.normalized_args[1] for c in claims if c.check}
    assert "Distribution" not in literals
    assert "msg" in literals


def test_coverage_record_shape(tmp_path):
    _, coverage = _produce(tmp_path)
    assert coverage["unit"] == "docstring_corpus"
    assert coverage["status"] == "complete"
    assert coverage["symbols_walked"] >= 1
    assert coverage["claims_emitted"] >= 3
    # unconstrained: the two `--doc` fields say so rather than being absent (a reader that
    # divides claims by symbols must be able to see whether a filter was in force)
    assert coverage["doc_filter"] is None
    assert coverage["param_docs_filtered_out"] == 0


# --- the `--doc` filter, applied during iteration rather than to the output ---

_KEPT_PY = '''
def kept(alpha, beta):
    """Kept.

    Parameters
    ----------
    alpha : str
        First.
    beta : str
        Second.
    """
'''

_OTHER_PY = '''
def other(gamma):
    """Other.

    Parameters
    ----------
    gamma : str
        Third.
    """
'''


def _two_file_repo(tmp_path):
    """A corpus with BOTH a matching and a non-matching `.py` file.

    Degenerate-corpus discipline: a one-file corpus cannot falsify a filter — every claim it
    produces is in-filter whether the filter runs or not.
    """
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "kept.py").write_text(_KEPT_PY)
    (pkg / "other.py").write_text(_OTHER_PY)
    (tmp_path / "README.md").write_text("# readme\n")
    return DocstringProducer(str(tmp_path), agent_ver="agent/0.3")


def _counting_normalize(monkeypatch):
    """Record the `(doc_line, file, args)` of every `signature_has_param.normalize` call.

    The predicate's own `normalize` is the producer's per-parameter work, which makes it the
    observable the filter is testable through: a filter applied to `produce()`'s output would
    normalize every parameter doc in the corpus first.

    The guarantee is narrower than it first looks, and the narrow form is the true one. A `--doc`
    run pays no per-parameter emission work for parameter docs outside the filter, and a `--doc`
    run naming a non-Python file loads no symbol provider at all. It does not skip the corpus
    walk: parsing happens inside the provider's generator before each parameter doc is yielded,
    so the iteration runs whole and only the emission work is skipped.
    """
    calls: list[str] = []
    pred = predicate_registry["signature_has_param"]

    def normalize(doc_line, file, args):
        calls.append(file)
        return pred.normalize(doc_line, file, args)

    monkeypatch.setitem(
        predicate_registry, "signature_has_param", dataclasses.replace(pred, normalize=normalize)
    )
    return calls


def test_doc_filter_contributes_only_that_files_claims(tmp_path):
    """`--doc foo.py` produces that file's claims and no others."""
    claims, coverage = _two_file_repo(tmp_path).produce(doc_filter="mypkg/kept.py")

    assert {c.anchor.doc_path for c in claims} == {"mypkg/kept.py"}
    assert {c.check.normalized_args[1] for c in claims if c.check} == {"alpha", "beta"}
    assert coverage["claims_emitted"] == len(claims) == 2


def test_doc_filter_naming_a_markdown_doc_contributes_nothing(tmp_path):
    """`--doc README.md` makes this producer contribute nothing, because no Python file matches.

    The coverage record still says `complete`. Contributing nothing under a filter that names no
    Python file is the intended outcome, not a failure, and the count of filtered-out parameter
    docs is zero rather than three: the corpus is never walked, so nothing is walked and then
    dropped. The `detail` field is where the reason lives.
    """
    claims, coverage = _two_file_repo(tmp_path).produce(doc_filter="README.md")

    assert claims == []
    assert coverage["status"] == "complete"
    assert coverage["claims_emitted"] == 0
    assert coverage["symbols_walked"] == 0
    assert coverage["param_docs_filtered_out"] == 0  # nothing was walked, so nothing was cut
    assert "no Python corpus walked" in coverage["detail"]
    assert "README.md" in coverage["detail"]


def test_a_non_python_doc_filter_never_even_constructs_the_provider(tmp_path, monkeypatch):
    """A `--doc` run naming a non-Python file pays no symbol-provider load at all.

    This is the falsifier for the claim above. `provider_for` is made to explode, so the only
    way this test passes is if the short-circuit happens before it is called.
    """

    def _boom(_repo_root):
        raise AssertionError("provider_for must not be called for a non-.py --doc filter")

    monkeypatch.setattr("drift.docstrings.provider_for", _boom)

    claims, coverage = _two_file_repo(tmp_path).produce(doc_filter="docs/GUIDE.md")

    assert claims == []
    assert coverage["status"] == "complete"


def test_the_short_circuit_admits_pyi_stubs(tmp_path, monkeypatch):
    """`.pyi` is in the suffix set: a stub file is Python and must reach the walk.

    Without this the short-circuit would silently answer "nothing" for a filter the corpus
    could legitimately match, which is a wrong answer rather than a skipped cost.
    """
    walked = []
    real = _two_file_repo(tmp_path)
    monkeypatch.setattr(
        "drift.docstrings.provider_for",
        lambda root: walked.append(root) or _provider_for(root),
    )

    real.produce(doc_filter="mypkg/stubs.pyi")

    assert walked, "a .pyi filter must still walk the corpus"


def test_doc_filter_runs_before_any_per_param_normalize_work(tmp_path, monkeypatch):
    """No `--doc` run pays the producer's per-parameter emission work for parameter docs
    outside the filter.

    This is the guarantee in its true, narrow form. The stronger-sounding version — that a
    `--doc` run pays no unfiltered corpus walk — is not what the mechanism delivers: the
    provider's iteration runs whole, parsing included.

    Fails if the filter is applied after the per-parameter work, or to `produce()`'s output:
    the non-matching file's parameter doc would be normalized on its way to being discarded.
    """
    calls = _counting_normalize(monkeypatch)

    _two_file_repo(tmp_path).produce(doc_filter="mypkg/kept.py")

    assert set(calls) == {"mypkg/kept.py"}
    assert len(calls) == 2  # alpha, beta — and nothing from mypkg/other.py


def test_the_griffe_walk_is_deliberately_not_narrowed_by_the_filter(tmp_path, monkeypatch):
    """The filter narrows this producer's per-parameter work, and deliberately not the symbol
    load or the provider's per-function docstring parse.

    The provider is memoized per repository root and shared with the gate's kernels in the same
    cell process, so narrowing its load would turn resolvable symbols into skips — a filter on
    one producer would silently change what the gate can answer. The docstring parse therefore
    still runs for every function, and if this ever stops holding, the provider was touched.

    Scope: this covers `.py`-filtered and unfiltered runs, the two that reach the walk. A filter
    naming a non-Python file never gets this far; it short-circuits earlier, which the test
    above proves.
    """
    parses: list[int] = []
    real = griffe.parse_auto
    monkeypatch.setattr(griffe, "parse_auto", lambda *a, **k: (parses.append(1), real(*a, **k))[1])

    producer = _two_file_repo(tmp_path)
    producer.produce()
    unfiltered = len(parses)
    parses.clear()
    producer.produce(doc_filter="mypkg/kept.py")

    assert unfiltered >= 2  # both files carry a Parameters section
    assert len(parses) == unfiltered


def test_coverage_keeps_symbols_walked_and_adds_the_contributed_fields(tmp_path):
    """`symbols_walked` is not re-pointed at the filtered corpus: it still counts what the
    iteration walked. What the filter changed is reported in new fields rather than by quietly
    redefining an existing one, which would make the two runs' numbers incomparable."""
    _claims, coverage = _two_file_repo(tmp_path).produce(doc_filter="mypkg/kept.py")

    assert coverage["symbols_walked"] == 2  # mypkg.kept.kept AND mypkg.other.other
    assert coverage["symbols_contributed"] == 1
    assert coverage["param_docs_filtered_out"] == 1  # gamma
    assert coverage["doc_filter"] == "mypkg/kept.py"
    assert set(coverage["skipped"]) == {"filtered", "normalize-declined"}  # causes unchanged
    assert "mypkg/kept.py" in coverage["detail"]
