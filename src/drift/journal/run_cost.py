"""A run's own cost total, computed from the rows that run journaled.

Derived from the rows rather than from in-process state, which dies with an exception: an
aborted run — the one that burned money without finishing — would report a spend of zero.
"""

from __future__ import annotations

from sqlalchemy import select

from drift.cost import PRICE_TABLE_VER, UnknownModelPriceError, usage_cost_usd
from drift.persistence.models import JournalRecord

__all__ = ["summarize_run_cost"]

_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def summarize_run_cost(session, run_id: int, model: str, completed: bool = True) -> dict:
    """The `run_cost` payload for `run_id`: token totals, dollar spend, and its basis.

    Args:
        model: The price to use for a row carrying no model stamp of its own.
        completed: Whether the pipeline reached its end. An aborted run's artifact is otherwise
            indistinguishable from a finished one's.

    Returns:
        A payload whose `spend_usd` is a floor, never a bound. A unit whose journal write was
        rolled back leaves no row to count, and a row stamped with a model the price table
        cannot price is counted in `tokens` and named in `unpriced` instead of raising — this
        runs in a `finally`, where refusing to state a cost is worse than stating a floor.
    """
    totals = dict.fromkeys(_USAGE_FIELDS, 0)
    sources: dict[str, int] = {}
    unpriced: dict[str, int] = {}
    models: set[str] = set()
    spend = 0.0
    rows = session.scalars(
        select(JournalRecord).where(JournalRecord.run_id == run_id).order_by(JournalRecord.id)
    )
    for row in rows:
        # Any row carrying usage counts, whatever its type: an allowlist of stream names would
        # drop the next component to journal usage out of the total, with no test failing.
        usage = (row.payload or {}).get("usage")
        if not isinstance(usage, dict) or not usage:
            continue
        sources[row.record_type] = sources.get(row.record_type, 0) + 1
        for field in _USAGE_FIELDS:
            totals[field] += int(usage.get(field, 0) or 0)
        row_model = row.model or model
        try:
            spend += usage_cost_usd(usage, model=row_model)
        except UnknownModelPriceError:
            unpriced[row_model] = unpriced.get(row_model, 0) + 1
        else:
            models.add(row_model)
    return {
        "spend_usd": round(spend, 6),
        "tokens": totals,
        "sources": sources,
        "models": sorted(models),
        "unpriced": unpriced,
        "price_table_ver": PRICE_TABLE_VER,
        "graph_completed": completed,
    }
