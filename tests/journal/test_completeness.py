"""Completeness and publishability tests for journaled runs.

Each declared mode has a focused example. One combined case confirms that four simultaneous signals
accumulate as non-empty reasons in stable order. Publishability is classified separately so designed
coverage shortfalls remain visible without being treated as malfunctions.
"""

from __future__ import annotations

from drift.journal import completeness
from drift.journal.completeness import (
    DEFEATING_MODES,
    FORGIVEN_MODES,
    MODES,
    Reason,
    is_publishable,
    run_incompleteness,
)
from drift.journal.writer import JournalWriter, Stamps
from drift.persistence.models import ScanRun

_STAMPS = Stamps("agent/x", "sjudge/x", "claude-sonnet-5")


def _run(db_session, status="done"):
    """Persist a scan-run fixture."""
    run = ScanRun(repo="r", commit_sha="abc", status=status)
    db_session.add(run)
    db_session.flush()
    return run


def _writer(db_session, run):
    """Build a journal writer for the fixture run."""
    return JournalWriter(db_session, run.id, "r", "abc", _STAMPS)


def _coverage(writer, unit, status, component="agent"):
    """Write a coverage row while preserving producer identity in `component`.

    A unit name cannot identify its producer: an agent document may also be named
    `docstring_corpus`.
    """
    writer.write(component, "agent_coverage", {"unit": unit, "status": status})


def _disposition(writer, funded=(), deferred=(), never=()):
    """Write the frame's final dispatch disposition."""
    writer.write(
        "run",
        "frame_plan",
        {
            "phase": "disposition",
            "funded": [list(k) for k in funded],
            "deferred_by_wallet": [list(k) for k in deferred],
            "never_dispatched": [list(k) for k in never],
        },
    )


def _cell_result(writer, cell_key, status="completed", claims_emitted=None):
    """Write a cell result, omitting `claims_emitted` when it is unspecified.

    Real cells always record the field; omission constructs the missing-field case tested below.
    """
    payload = {"cell_key": list(cell_key), "status": status}
    if claims_emitted is not None:
        payload["claims_emitted"] = claims_emitted
    writer.write("cell", "cell_result", payload)


def _wallet_rail(writer, cells_done=1, cells_total=3):
    """Write a frame-level wallet-exhaustion rail stop."""
    writer.write(
        "run",
        "rail_stop",
        {
            "lane": "frame",
            "reason": "wallet-exhausted",
            "cells_done": cells_done,
            "cells_total": cells_total,
            "items_done": 0,
            "items_total": cells_total - 1,
        },
    )


def _modes(reasons):
    """Extract reason modes in their emitted order."""
    return [r.mode for r in reasons]


def _reason(reasons, mode):
    """Select the reason for one mode."""
    return next(r for r in reasons if r.mode == mode)


def test_a_clean_run_is_publishable(db_session):
    """Accept a completed run whose coverage contains no incomplete work."""
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    _coverage(writer, "docstring_corpus", "complete")
    writer.write("gate", "gate_kill", {"kind": "binding_fail", "literal": "x", "doc_path": "A.md"})
    db_session.flush()

    assert run_incompleteness(db_session, run.id) == []


def test_a_rail_stopped_run_names_the_rail(db_session):
    """Combine rail firings into one reason that names both sources."""
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    writer.write("agent", "rail_stop", {"lane": "discover", "reason": "budget_cap:dollars"})
    writer.write(
        "semantic_judge",
        "rail_stop",
        {"lane": "semantic_judge", "reason": "budget_cap:max_s_candidates"},
    )
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["rail_stop"]
    detail = reasons[0].detail
    assert "2" in detail
    assert "discover/budget_cap:dollars" in detail
    assert "semantic_judge/budget_cap:max_s_candidates" in detail


def test_a_failed_run_is_not_publishable(db_session):
    """Report a failed scan run as not done."""
    run = _run(db_session, status="failed")
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["run_not_done"]
    assert "failed" in reasons[0].detail


def test_a_run_still_marked_running_is_not_publishable(db_session):
    """Treat every status other than `done` as incomplete.

    A process killed mid-scan can leave committed rows and a run permanently marked `running`.
    """
    run = _run(db_session, status="running")
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["run_not_done"]
    assert "running" in reasons[0].detail


def test_a_lost_unit_and_a_clipped_unit_are_separate_modes(db_session):
    """Distinguish a failed unit from one that returned a truncated inventory.

    Failure loses the unit; truncation reduces coverage without changing the precision of emitted
    findings. Separate modes let consumers select either condition without parsing details.
    """
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    _coverage(writer, "B.md", "error")
    _coverage(writer, "C.md", "truncated")
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["unit_error", "unit_truncated"]
    lost, clipped = reasons
    assert "1 of 3" in lost.detail and "error" in lost.detail
    assert "1 of 3" in clipped.detail


def test_a_kernel_error_is_reported(db_session):
    """Report kernel errors without treating binding failures as incompleteness."""
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    writer.write("gate", "gate_kill", {"kind": "binding_fail", "literal": "x", "doc_path": "A.md"})
    writer.write("gate", "gate_kill", {"kind": "kernel_error", "literal": "y", "doc_path": "A.md"})
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["kernel_error"]
    assert "1" in reasons[0].detail
    # A binding failure is the gate rejecting a stale anchor, not an incomplete run.
    assert "binding_fail" not in reasons[0].detail


def test_a_failed_adjudication_is_not_silently_publishable(db_session):
    """Count fail-isolated judge exceptions as incompleteness.

    These exceptions appear only as error verdicts. Ignoring them would make the run look clean and
    compute judge accuracy over an incomplete denominator.
    """
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    writer.write("semantic_judge", "s_verdict", {"literal": "x", "doc_path": "A.md", "live": False})
    writer.write(
        "semantic_judge",
        "s_verdict",
        {"literal": "y", "doc_path": "A.md", "live": False, "error": True},
    )
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["s_judge_error"]
    assert "1 of 2" in reasons[0].detail  # the denominator is the actionable part


def test_a_run_over_zero_units_is_not_vacuously_publishable(db_session):
    """Reject a completed run with no coverage rows instead of publishing from zero data."""
    run = _run(db_session)
    _writer(db_session, run)
    db_session.flush()

    assert _modes(run_incompleteness(db_session, run.id)) == ["no_units"]


def test_every_firing_mode_contributes_its_own_reason(db_session):
    """Accumulate four simultaneous modes as non-empty reasons in stable order."""
    run = _run(db_session, status="failed")
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "error")
    writer.write("agent", "rail_stop", {"lane": "discover", "reason": "budget_cap:dollars"})
    writer.write("gate", "gate_kill", {"kind": "kernel_error", "literal": "y", "doc_path": "A.md"})
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    # Stable order lets callers diff run verdicts without sorting.
    assert _modes(reasons) == ["rail_stop", "run_not_done", "unit_error", "kernel_error"]
    assert all(isinstance(r, Reason) and r.detail for r in reasons)


def test_an_unknown_run_id_is_not_silently_publishable(db_session):
    """Return `run_missing` when no run row exists; an empty list would falsely mean clean."""
    reasons = run_incompleteness(db_session, -1)
    assert _modes(reasons) == ["run_missing"]


def test_the_forgiven_modes_are_the_only_ones_that_do_not_defeat_fitness():
    """List both sides of the fitness split exhaustively.

    Defining the defeating side as all modes minus forgiven modes would be tautological and could
    silently publish a newly omitted failure mode.
    """
    assert FORGIVEN_MODES == frozenset({"coverage_shortfall", "unit_zero_yield"})
    assert DEFEATING_MODES == frozenset(
        {
            "run_missing",
            "rail_stop",
            "run_not_done",
            "no_units",
            "unit_error",
            "unit_truncated",
            "kernel_error",
            "s_judge_error",
            "cells_unreported",
        }
    )
    assert is_publishable([]) is True
    assert is_publishable([Reason("coverage_shortfall", "2 cells deferred")]) is True
    assert is_publishable([Reason("unit_error", "1 unit died")]) is False


def test_no_mode_is_classified_as_both_defeating_and_forgiven():
    """Keep the defeating and forgiven sets disjoint.

    Publishability consults only `DEFEATING_MODES`, while a union-based partition check cannot
    detect overlap.
    """
    assert DEFEATING_MODES & FORGIVEN_MODES == frozenset()
    assert is_publishable([Reason(next(iter(DEFEATING_MODES)), "x")]) is False


def test_every_mode_the_predicate_can_emit_is_classified_by_the_split():
    """Require the fitness sets to partition the declared mode vocabulary.

    This compares constants only. Each mode's behavior test must separately ensure that
    `run_incompleteness` emits only declared modes.
    """
    assert MODES == DEFEATING_MODES | FORGIVEN_MODES


def test_the_partition_lock_is_falsifiable_by_a_real_mutation():
    """Show that omitting a real mode from both fitness sets breaks the partition."""
    forgotten = "unit_zero_yield"
    assert forgotten in MODES
    assert MODES != (DEFEATING_MODES - {forgotten}) | (FORGIVEN_MODES - {forgotten})


def test_a_budget_stopped_run_is_fit_and_publishes(db_session):
    """Keep a wallet-only boundary stop publishable while reporting rail and shortfall."""
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "docstring_corpus", "complete")
    _coverage(writer, "A.md", "complete")
    _wallet_rail(writer, cells_done=2, cells_total=4)
    _disposition(
        writer,
        funded=[("docstrings", "docstring_corpus"), ("agent", "A.md")],
        deferred=[("agent", "B.md"), ("agent", "C.md")],
    )
    _cell_result(writer, ("docstrings", "docstring_corpus"))
    _cell_result(writer, ("agent", "A.md"))
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["rail_stop", "coverage_shortfall"]
    assert is_publishable(reasons) is True
    shortfall = _reason(reasons, "coverage_shortfall")
    assert shortfall.fact("cells_funded") == 2
    assert shortfall.fact("cells_deferred") == 2
    assert "2 of 4 cell(s)" in shortfall.detail


def test_a_second_rail_riding_along_defeats_the_wallets_forgiveness(db_session):
    """Forgive rail stops only when every firing is wallet exhaustion.

    Rail firings share one detail string, so classification must inspect each structured payload
    rather than search the collapsed detail for `wallet-exhausted`.
    """
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    _wallet_rail(writer)
    writer.write(
        "semantic_judge",
        "rail_stop",
        {"lane": "semantic_judge", "reason": "budget_cap:max_s_candidates"},
    )
    _disposition(writer, funded=[("agent", "A.md")], deferred=[("agent", "B.md")])
    _cell_result(writer, ("agent", "A.md"))
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    rail = _reason(reasons, "rail_stop")
    assert "wallet-exhausted" in rail.detail  # Detail alone would misclassify this run.
    assert is_publishable(reasons) is False


def test_a_rail_reason_that_merely_contains_the_wallets_is_not_the_wallets(db_session):
    """Compare rail reasons by equality; `not-wallet-exhausted` is a different reason."""
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    writer.write("run", "rail_stop", {"lane": "frame", "reason": "not-wallet-exhausted"})
    db_session.flush()

    assert is_publishable(run_incompleteness(db_session, run.id)) is False


def test_a_rail_stop_reason_carrying_no_payload_facts_is_never_forgiven():
    """Fail closed when a rail-stop reason lacks the payload facts needed for forgiveness."""
    hand_built = Reason("rail_stop", "1 rail firing(s): frame/wallet-exhausted")
    assert is_publishable([hand_built]) is False


def test_an_enumeration_safety_skip_is_coverage_shortfall_not_unit_error(db_session):
    """Classify a skipped coverage row as a forgiven shortfall rather than a unit failure.

    Adding an error row still emits `unit_error` and defeats publishability.
    """
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    _coverage(writer, "hazard_fifo.md", "skipped")
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["coverage_shortfall"]
    assert is_publishable(reasons) is True
    assert _reason(reasons, "coverage_shortfall").fact("units_skipped") == 1

    _coverage(writer, "B.md", "error")
    db_session.flush()
    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["unit_error", "coverage_shortfall"]
    assert is_publishable(reasons) is False


def test_a_run_whose_whole_doc_corpus_was_skipped_publishes_as_fit_today(db_session):
    """Characterize the unbounded forgiveness of designed coverage shortfalls.

    Complete docstring coverage prevents `no_units`, so a run that skips every agent document has
    only `coverage_shortfall` and remains publishable.
    """
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "docstring_corpus", "complete")
    _coverage(writer, "README.md", "skipped")
    _coverage(writer, "GUIDE.md", "skipped")
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["coverage_shortfall"]
    assert _reason(reasons, "coverage_shortfall").fact("units_skipped") == 2
    assert is_publishable(reasons) is True


def test_a_funded_cell_that_never_reported_is_an_unfit_hole(db_session):
    """Treat a funded cell with no result as an unfit accounting hole.

    The hole is derived from the disposition and result rows so no separately stored count can
    disagree with them.
    """
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    _disposition(writer, funded=[("agent", "A.md"), ("docstrings", "docstring_corpus")])
    _cell_result(writer, ("agent", "A.md"))
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["cells_unreported"]
    assert is_publishable(reasons) is False
    hole = _reason(reasons, "cells_unreported")
    assert "docstrings:docstring_corpus" in hole.detail
    assert hole.fact("cells_unreported") == (("docstrings", "docstring_corpus"),)


def test_a_complete_unit_that_emitted_zero_claims_is_signalled(db_session):
    """Report complete agent units that emitted no claims.

    The detail uses complete agent units as its denominator, and structured facts name the empty
    units without requiring consumers to parse prose.
    """
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "Changelog.rst", "complete")
    _coverage(writer, "SECURITY.md", "complete")
    _coverage(writer, "README.md", "complete")
    _cell_result(writer, ("agent", "Changelog.rst"), claims_emitted=0)
    _cell_result(writer, ("agent", "SECURITY.md"), claims_emitted=0)
    _cell_result(writer, ("agent", "README.md"), claims_emitted=7)
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["unit_zero_yield"]
    empty = _reason(reasons, "unit_zero_yield")
    assert "2 of 3 complete unit(s) emitted zero claims" in empty.detail
    assert "Changelog.rst" in empty.detail and "SECURITY.md" in empty.detail
    assert empty.fact("units") == ("Changelog.rst", "SECURITY.md")


def test_a_zero_yield_reason_is_hashable(db_session):
    """Keep zero-yield facts hashable by storing unit names in tuples."""
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    _cell_result(writer, ("agent", "A.md"), claims_emitted=0)
    db_session.flush()

    assert len({*run_incompleteness(db_session, run.id)}) == 1


def test_only_a_complete_unit_can_be_zero_yield(db_session):
    """Reserve zero-yield for complete units so failures are not reported twice.

    Truncated, failed, and skipped units already have distinct modes and may legitimately record
    zero emitted claims. Their status, not the count, excludes them here.
    """
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "clipped.md", "truncated")
    _coverage(writer, "died.md", "error")
    _coverage(writer, "hazard.md", "skipped")
    _cell_result(writer, ("agent", "clipped.md"), claims_emitted=0)
    _cell_result(writer, ("agent", "died.md"), claims_emitted=0)
    _cell_result(writer, ("agent", "hazard.md"), claims_emitted=0)
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert "unit_zero_yield" not in _modes(reasons)
    assert _modes(reasons) == ["unit_error", "unit_truncated", "coverage_shortfall"]


def test_a_cell_that_crashed_after_a_complete_coverage_row_is_not_zero_yield(db_session):
    """Require both complete coverage and a completed cell result for zero-yield.

    A cell can crash after recording complete coverage. Classifying its zero count as zero-yield
    would turn a malfunction into a forgiven reason.

    The current predicate has a gap: because the cell reported, coverage is complete, and the cell
    is excluded from zero-yield, no reason fires and the run remains publishable.
    """
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    _disposition(writer, funded=[("agent", "A.md")])
    _cell_result(writer, ("agent", "A.md"), status="unit_error", claims_emitted=0)
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == []
    assert is_publishable(reasons) is True


def test_lane_b_is_excluded_by_the_component_column_not_by_the_unit_name(db_session):
    """Exclude the docstring producer by component rather than by unit name.

    Zero-yield covers paid agent work. Both producers can use the unit name `docstring_corpus`, so
    the component column must select both the signal and its denominator.
    """
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "docstring_corpus", "complete", component="docstrings")
    _coverage(writer, "docstring_corpus", "complete", component="agent")
    _cell_result(writer, ("docstrings", "docstring_corpus"), claims_emitted=0)
    _cell_result(writer, ("agent", "docstring_corpus"), claims_emitted=0)
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["unit_zero_yield"]
    empty = _reason(reasons, "unit_zero_yield")
    assert empty.fact("units") == ("docstring_corpus",)
    assert "1 of 1 complete unit(s)" in empty.detail


def test_a_complete_unit_with_no_cell_result_row_is_cells_unreported_not_zero_yield(db_session):
    """Classify a missing cell result as unreported, not zero-yield.

    Without a result the run cannot know whether the cell produced nothing, and the existing hole
    reason already captures the failure.
    """
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    _disposition(writer, funded=[("agent", "A.md"), ("agent", "B.md")])
    _cell_result(writer, ("agent", "A.md"), claims_emitted=4)
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["cells_unreported"]
    assert "B.md" in _reason(reasons, "cells_unreported").detail


def test_a_cell_result_row_that_does_not_carry_a_count_is_not_read_as_zero(db_session):
    """Do not interpret a missing `claims_emitted` field as zero.

    Real cells record the field; a row without it provides no evidence for a zero-yield signal.
    """
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    _cell_result(writer, ("agent", "A.md"))
    db_session.flush()

    assert run_incompleteness(db_session, run.id) == []


def test_zero_yield_takes_its_place_between_cells_unreported_and_kernel_error(db_session):
    """Keep zero-yield between unreported cells and kernel errors in the stable reason order."""
    run = _run(db_session)
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    _coverage(writer, "B.md", "complete")
    _disposition(writer, funded=[("agent", "A.md"), ("agent", "B.md")])
    _cell_result(writer, ("agent", "A.md"), claims_emitted=0)
    writer.write("gate", "gate_kill", {"kind": "kernel_error", "literal": "y", "doc_path": "A.md"})
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["cells_unreported", "unit_zero_yield", "kernel_error"]
    # An unreported cell cannot also be counted as zero-yield.
    assert _reason(reasons, "unit_zero_yield").fact("units") == ("A.md",)


def test_the_zero_yield_disposition_is_one_string_move(monkeypatch):
    """Show that moving zero-yield between fitness sets changes publishability."""
    reasons = [
        Reason(
            "unit_zero_yield",
            "1 of 3 complete unit(s) emitted zero claims: Changelog.rst",
            (("units", ("Changelog.rst",)),),
        )
    ]
    assert is_publishable(reasons) is True

    monkeypatch.setattr(completeness, "DEFEATING_MODES", DEFEATING_MODES | {"unit_zero_yield"})
    monkeypatch.setattr(completeness, "FORGIVEN_MODES", FORGIVEN_MODES - {"unit_zero_yield"})
    assert is_publishable(reasons) is False
    assert MODES == completeness.DEFEATING_MODES | completeness.FORGIVEN_MODES
    assert completeness.DEFEATING_MODES & completeness.FORGIVEN_MODES == frozenset()


def test_never_dispatched_cells_always_arrive_with_run_not_done(db_session):
    """Treat failed and interrupted runs with undispatched cells as unfit.

    `never_dispatched` has no completeness mode of its own. This test verifies only the consequent:
    given a non-`done` status, `run_not_done` defeats publishability. Frame tests own the separate
    invariant that undispatched cells prevent a run from ending `done`.
    """
    for status in ("failed", "running"):
        run = _run(db_session, status=status)
        writer = _writer(db_session, run)
        _coverage(writer, "A.md", "complete")
        _disposition(
            writer, funded=[("agent", "A.md")], never=[("agent", "B.md"), ("agent", "C.md")]
        )
        _cell_result(writer, ("agent", "A.md"))
        db_session.flush()

        reasons = run_incompleteness(db_session, run.id)
        assert _modes(reasons) == ["run_not_done"], status
        assert is_publishable(reasons) is False, status


def test_a_frame_that_died_before_its_disposition_row_is_not_double_counted(db_session):
    """Do not infer unreported cells when a failed frame wrote no disposition.

    Without the disposition there is no funded list. `run_not_done` already captures the failure,
    while deriving holes from the plan would report it twice.
    """
    run = _run(db_session, status="running")
    writer = _writer(db_session, run)
    _coverage(writer, "A.md", "complete")
    writer.write(
        "run",
        "frame_plan",
        {"phase": "plan", "cells": [["agent", "A.md"]], "cell_count": 1, "unit_count": 1},
    )
    db_session.flush()

    reasons = run_incompleteness(db_session, run.id)
    assert _modes(reasons) == ["run_not_done"]
    assert is_publishable(reasons) is False


def test_a_reason_is_still_hashable_after_the_structured_field(db_session):
    """Keep structured reason facts hashable because `Reason` is a frozen dataclass."""
    run = _run(db_session)
    writer = _writer(db_session, run)
    _wallet_rail(writer)
    _coverage(writer, "A.md", "complete")
    _disposition(writer, funded=[("agent", "A.md")], deferred=[("agent", "B.md")])
    _cell_result(writer, ("agent", "A.md"))
    db_session.flush()

    assert len({*run_incompleteness(db_session, run.id)}) == 2


def test_another_run_s_incompleteness_does_not_contaminate_this_one(db_session):
    """Scope incompleteness queries to the requested run."""
    dirty = _run(db_session, status="failed")
    dirty_writer = _writer(db_session, dirty)
    _coverage(dirty_writer, "A.md", "error")
    dirty_writer.write("agent", "rail_stop", {"lane": "discover", "reason": "budget_cap:dollars"})

    clean = _run(db_session)
    _coverage(_writer(db_session, clean), "A.md", "complete")
    db_session.flush()

    assert run_incompleteness(db_session, clean.id) == []
    assert run_incompleteness(db_session, dirty.id) != []
