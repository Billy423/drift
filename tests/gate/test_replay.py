from drift.gate.replay import GateOutcome, replay, replay_check
from drift.kernels.models import Anchor, Check, EvClaim, SSlot
from drift.kernels.registry import Predicate, predicate_registry


def _claim(check):
    return EvClaim(
        anchor=Anchor(doc_path="d.md", spans=((1, 1),), literal="file.md"),
        check=check,
        claim_class=1,
        s_slot=SSlot(note="reads live", confidence=0.9),
        provenance={"agent_ver": "agent/0.1"},
    )


def _check(doc_path="d.md", literal="file.md", normalized_args=("file.md",)):
    return Check(
        predicate="path_exists",
        raw={"doc_path": doc_path, "literal": literal},
        normalization={"base": "repo-root"},
        normalized_args=normalized_args,
    )


# --- replay(): the truth table ---


def test_doc_mentions_target_target_absent_is_m_certified(tmp_path):
    (tmp_path / "d.md").write_text("see file.md here")
    claim = _claim(_check())
    [result] = replay(str(tmp_path), [claim])
    assert result.outcome == GateOutcome.M_CERTIFIED
    assert result.claim is claim


def test_doc_mentions_target_target_present_is_passing(tmp_path):
    (tmp_path / "d.md").write_text("see file.md here")
    (tmp_path / "file.md").write_text("x")
    claim = _claim(_check())
    [result] = replay(str(tmp_path), [claim])
    assert result.outcome == GateOutcome.PASSING


def test_literal_absent_from_doc_is_binding_fail(tmp_path):
    (tmp_path / "d.md").write_text("no mention of the target here")
    claim = _claim(_check())
    [result] = replay(str(tmp_path), [claim])
    assert result.outcome == GateOutcome.BINDING_FAIL


def test_unbound_claim_is_unbound(tmp_path):
    claim = _claim(None)
    [result] = replay(str(tmp_path), [claim])
    assert result.outcome == GateOutcome.UNBOUND


def test_replay_returns_one_result_per_claim_in_order(tmp_path):
    (tmp_path / "d.md").write_text("see file.md here")
    (tmp_path / "file.md").write_text("x")
    unbound = _claim(None)
    passing = _claim(_check())
    results = replay(str(tmp_path), [unbound, passing])
    assert [r.outcome for r in results] == [GateOutcome.UNBOUND, GateOutcome.PASSING]


# --- replay(): kernel raises ---


def test_kernel_raise_is_kernel_error_and_does_not_escape(tmp_path, monkeypatch):
    (tmp_path / "d.md").write_text("see file.md here")

    def _raiser(repo_root, *args):
        raise RuntimeError("boom")

    original = predicate_registry["path_exists"]
    raising_predicate = Predicate(
        name=original.name,
        description=original.description,
        normalize=original.normalize,
        kernel=_raiser,
    )
    monkeypatch.setitem(predicate_registry, "path_exists", raising_predicate)

    claim = _claim(_check())
    [result] = replay(str(tmp_path), [claim])
    assert result.outcome == GateOutcome.KERNEL_ERROR
    assert "boom" in result.detail


# --- replay(): leg order (doc leg first — BINDING_FAIL wins over KERNEL_ERROR) ---


def test_binding_fail_wins_over_kernel_error_when_both_would_fire(tmp_path, monkeypatch):
    (tmp_path / "d.md").write_text("no mention of the target here")

    def _raiser(repo_root, *args):
        raise RuntimeError("should never be called")

    original = predicate_registry["path_exists"]
    monkeypatch.setitem(
        predicate_registry,
        "path_exists",
        Predicate(
            name=original.name,
            description=original.description,
            normalize=original.normalize,
            kernel=_raiser,
        ),
    )
    claim = _claim(_check())
    [result] = replay(str(tmp_path), [claim])
    assert result.outcome == GateOutcome.BINDING_FAIL


# --- replay_check(): the stored-dict form used by reconcile ---


def _stored_check(doc_path, literal, normalized_args):
    return {
        "predicate": "path_exists",
        "raw": {"doc_path": doc_path, "literal": literal},
        "normalization": {"base": "repo-root"},
        "normalized_args": list(normalized_args),
    }


def test_replay_check_true_when_doc_leg_and_kernel_absent(tmp_path):
    (tmp_path / "d.md").write_text("see file.md here")
    check = _stored_check("d.md", "file.md", ("file.md",))
    assert replay_check(str(tmp_path), check) is True


def test_replay_check_false_when_target_present(tmp_path):
    (tmp_path / "d.md").write_text("see file.md here")
    (tmp_path / "file.md").write_text("x")
    check = _stored_check("d.md", "file.md", ("file.md",))
    assert replay_check(str(tmp_path), check) is False


def test_replay_check_false_when_doc_leg_fails(tmp_path):
    (tmp_path / "d.md").write_text("nothing relevant here")
    check = _stored_check("d.md", "file.md", ("file.md",))
    assert replay_check(str(tmp_path), check) is False


def test_replay_check_unknown_predicate_is_true(tmp_path):
    (tmp_path / "d.md").write_text("see file.md here")
    check = _stored_check("d.md", "file.md", ("file.md",))
    check["predicate"] = "retired_predicate_xyz"
    assert replay_check(str(tmp_path), check) is True


def test_replay_check_kernel_raise_is_true(tmp_path, monkeypatch):
    (tmp_path / "d.md").write_text("see file.md here")

    def _raiser(repo_root, *args):
        raise RuntimeError("boom")

    original = predicate_registry["path_exists"]
    monkeypatch.setitem(
        predicate_registry,
        "path_exists",
        Predicate(
            name=original.name,
            description=original.description,
            normalize=original.normalize,
            kernel=_raiser,
        ),
    )
    check = _stored_check("d.md", "file.md", ("file.md",))
    assert replay_check(str(tmp_path), check) is True


def test_replay_check_uses_stored_normalized_args_verbatim_never_renormalizes(tmp_path):
    """THE regression lock: raw.literal would re-normalize to a MISSING path
    (docs/sub/x.md, doc-relative to docs/guide.md) if replay_check re-derived args via
    predicate.normalize — but the STORED normalized_args point at an existing repo-root file
    (real.md). Correct behavior uses the stored args verbatim -> target exists -> not drifting."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("see ./sub/x.md here")
    (tmp_path / "real.md").write_text("x")
    # deliberately do NOT create docs/sub/x.md — re-normalization would find it absent

    check = {
        "predicate": "path_exists",
        "raw": {"doc_path": "docs/guide.md", "literal": "./sub/x.md"},
        "normalization": {"base": "doc-relative"},
        "normalized_args": ["real.md"],
    }
    assert replay_check(str(tmp_path), check) is False


def test_replay_check_tolerates_invalid_utf8_doc_bytes(tmp_path):
    """FIX 5b: the doc-leg call sits inside its own try/except so ANY exception -> True (the
    conservative "cannot verify, keep the issue open" direction) — pinned here on a doc with a
    stray non-UTF-8 byte, which must not raise and must still resolve correctly when the
    surrounding ASCII text is intact (errors="replace" degrades the byte, not the whole read)."""
    (tmp_path / "d.md").write_bytes(b"\xff\xfe see file.md here")
    (tmp_path / "file.md").write_text("x")
    check = _stored_check("d.md", "file.md", ("file.md",))
    result = replay_check(str(tmp_path), check)
    assert isinstance(result, bool)
    assert result is False  # doc leg holds (decoded text still matches) and target is present


def test_an_unreadable_document_keeps_the_issue_open(tmp_path, monkeypatch):
    """A document that cannot be read is not a document whose anchor left.

    A deletion, a permission error and a checkout race all reach `doc_contains` the same way.
    Reading them alike closes an issue on evidence nobody has, which is the one thing the replay
    gate exists to refuse.
    """
    doc = tmp_path / "guide.md"
    doc.write_text("see src/thing.py\n", encoding="utf-8")

    def _denied(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("builtins.open", _denied)

    check = {
        "predicate": "path_exists",
        "raw": {"doc_path": "guide.md", "literal": "src/thing.py"},
        "normalization": {},
        "normalized_args": ["src/thing.py"],
    }
    assert replay_check(str(tmp_path), check) is True
