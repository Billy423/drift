"""The two producers: the agent reading a document, and the docstring corpus."""

from __future__ import annotations

from drift.cost import DEFAULT_MODEL, usage_cost_usd
from drift.graph.nodes.rails import (
    _admit_producers,
    _budget_of,
    _journal_claim_inventory,
    _rail_stop,
    _safe_journal,
)
from drift.graph.progress import progress
from drift.graph.state import ScanState
from drift.kernels.models import EvClaim


def make_discover(discovery_agent, writer, model: str = DEFAULT_MODEL):
    """Return a node that runs the discovery producer over each worklist unit in turn.

    Args:
        model: Prices this node's spend, so it must be the model the scan actually runs.
    """

    def discover(state: ScanState) -> dict:
        """Node: run the discovery producer once per worklist unit, journalling each attempt.

        Spend accumulates from each unit's journaled usage and is checked before the next paid
        unit starts, never during one, so overshoot is bounded by a single unit. Every unit's
        rows are committed before the next begins, so a hard death costs the unit in flight.
        """
        claims: list[EvClaim] = []
        # Seeded from state, not fresh: this channel has no reducer, so last write wins and
        # starting empty would drop the coverage rows `adopt_worklist` put here.
        coverages: list[dict] = list(state.get("coverages", []))
        budget = _budget_of(state)
        spend = state.get("spend", 0.0)
        partial_notes = list(state.get("partial_notes", []))
        total = len(state["worklist"])
        units_done = 0
        for i, doc_path in enumerate(state["worklist"], 1):
            # Unreachable on the cell path: a cell holds one unit and spend enters at zero.
            # Live in the single-process graph, whose one invocation carries the whole list.
            if spend >= budget:
                remaining = total - units_done
                partial_notes.append(
                    f"budget ${budget:.2f} reached during discovery after {units_done} unit(s); "
                    f"{remaining} of {total} doc unit(s) not scanned; scan is partial."
                )
                _rail_stop(
                    writer,
                    state,
                    "agent",
                    "discover",
                    "budget_cap:dollars",
                    f"discover: budget ${budget:.2f} hit; {remaining} unit(s) skipped",
                    units_done,
                    total,
                    budget,
                    spend,
                )
                break
            progress(f"discover {i}/{total}: {doc_path} …")
            try:
                result = discovery_agent.discover(state["repo_root"], doc_path)
            except Exception as exc:  # one document's failure must not end the run
                # The producer converts a billed mid-loop crash into a coverage record, so this
                # catches pre-loop failures — it still bills any usage the exception declares.
                lost_usage = getattr(exc, "usage", None) or {}
                coverage = {
                    "unit": doc_path,
                    "doc_hash": "",
                    "turns_used": 0,
                    "tool_calls": 0,
                    "status": "error",
                    "detail": repr(exc),
                    "usage": lost_usage,
                }
                coverages.append(coverage)
                _safe_journal(
                    writer,
                    state,
                    partial_notes,
                    doc_path,
                    lambda cov=coverage: writer.write("agent", "agent_coverage", cov),
                )
                spend += usage_cost_usd(lost_usage, model=model)
                units_done += 1  # an errored unit still consumed a paid attempt
                progress(f"discover {i}/{total}: {doc_path} ERROR {exc!r}")
                continue
            _admit_producers(result.claims, doc_path)
            claims.extend(result.claims)
            coverages.append(result.coverage)
            cov = result.coverage

            def _emit(res=result, cov=cov):
                writer.write("agent", "agent_coverage", cov)
                # No join needed: the unit's coverage and its claims are both in hand here.
                _journal_claim_inventory(writer, "agent", res.claims, cov.get("doc_hash", ""))

            _safe_journal(writer, state, partial_notes, doc_path, _emit)
            usage = cov.get("usage", {})
            spend += usage_cost_usd(usage, model=model)
            units_done += 1
            progress(
                f"discover {i}/{total}: {doc_path} → {len(result.claims)} claim(s), "
                f"turns={cov.get('turns_used')} tools={cov.get('tool_calls')} "
                f"status={cov.get('status')} "
                f"tok(in={usage.get('input_tokens', 0)} "
                f"cached={usage.get('cache_read_input_tokens', 0)} "
                f"out={usage.get('output_tokens', 0)})"
            )
        return {
            "claims": claims,
            "coverages": coverages,
            "spend": spend,
            "units_discovered": units_done,
            "partial_notes": partial_notes,
        }

    return discover


def make_discover_docstrings(producer_factory, writer):
    """Return a node that runs the docstring producer over the repository's own packages.

    Deterministic, free, and it enumerates its own units.
    """

    def discover_docstrings(state: ScanState) -> dict:
        """Node: run the docstring producer, with one coverage record for the whole corpus.

        A document filter is handed to `produce()` rather than applied to what it returns:
        filtering afterwards pays for the whole walk and then throws it away. It rides as a call
        argument so that the factory contract stays `factory(repo_root)`.
        """
        try:
            claims, coverage = producer_factory(state["repo_root"]).produce(
                doc_filter=state.get("doc_filter")
            )
        except Exception as exc:  # a producer crash must not end the run
            # Every key the producer's own coverage record carries, at zero: to a reader of this
            # stream a missing key and a zero key are different facts.
            coverage = {
                "unit": "docstring_corpus",
                "symbols_walked": 0,
                "symbols_contributed": 0,
                "claims_emitted": 0,
                "status": "error",
                "skipped": {"filtered": 0, "normalize-declined": 0},
                # What the run asked for survives the crash: it is configuration, not a count.
                "doc_filter": state.get("doc_filter"),
                "param_docs_filtered_out": 0,
                "detail": repr(exc),
            }
            claims = []
        _admit_producers(claims, "docstring_corpus")
        partial_notes = list(state.get("partial_notes", []))

        def _emit() -> None:
            writer.write("docstrings", "agent_coverage", coverage)
            # Empty `doc_hash`: this corpus is keyed by Python symbols, not by a document version.
            _journal_claim_inventory(writer, "docstrings", claims, "")

        _safe_journal(writer, state, partial_notes, "docstring_corpus", _emit)
        progress(
            f"docstrings: {coverage.get('claims_emitted', 0)} claim(s), "
            f"status={coverage.get('status')}"
        )
        return {
            "claims": state["claims"] + list(claims),
            "coverages": state["coverages"] + [coverage],
            "partial_notes": partial_notes,
        }

    return discover_docstrings
