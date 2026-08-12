"""The price table, and the dollar cost of the paid model calls a scan makes.

The budget gate spends dollars rather than counting tokens, so it self-adjusts as rates and
caching change. `PRICE_TABLE_VER` is journaled per run, so a reported figure names its table.
"""

from __future__ import annotations


class UnknownModelPriceError(RuntimeError):
    """A model the price table does not price.

    Never priced by guess: another model's rates mis-price every call in one direction or the
    other, and pricing at zero disables the budget gate outright.
    """


# Bump whenever a price row changes, a rate change for an already-listed model included.
PRICE_TABLE_VER = "1"

# Anthropic prices cache reads and writes as multiples of the input rate. Kept as multiples, so
# a rate change cannot leave the table internally inconsistent while still looking maintained.
_CACHE_READ_MULTIPLE = 0.1
_CACHE_WRITE_MULTIPLE = 1.25


def _model_prices(input_usd_per_mtok: float, output_usd_per_mtok: float) -> dict[str, float]:
    """One model's per-token price row, with the two cache rates derived from input."""
    per_input_token = input_usd_per_mtok / 1_000_000
    return {
        "input_tokens": per_input_token,
        "output_tokens": output_usd_per_mtok / 1_000_000,
        "cache_read_input_tokens": per_input_token * _CACHE_READ_MULTIPLE,
        "cache_creation_input_tokens": per_input_token * _CACHE_WRITE_MULTIPLE,
    }


# Anthropic list pricing, in USD per million input and output tokens. List rates rather than any
# promotional rate: the gate stops early, and a figure from this table is an upper bound.
_PER_TOKEN_USD: dict[str, dict[str, float]] = {
    "claude-sonnet-5": _model_prices(3.00, 15.00),
}

# The model the discovery producer and the judge run on. Not a fallback:
# an unpriced model raises rather than being priced as this one.
DEFAULT_MODEL = "claude-sonnet-5"


def require_priced_model(model: str) -> None:
    """Check that the price table can price `model`. Call once, before any paid work.

    `usage_cost_usd` raises too, but only after money has been spent; checking up front turns an
    unpriceable run into a setup error instead.

    Raises:
        UnknownModelPriceError: If the table has no row for `model`.
    """
    if model not in _PER_TOKEN_USD:
        raise UnknownModelPriceError(
            f"no price row for model {model!r} (priced: {sorted(_PER_TOKEN_USD)}). "
            f"Add its rates to drift.cost._PER_TOKEN_USD and bump PRICE_TABLE_VER — the "
            f"budget gate cannot be trusted on a model it cannot price."
        )


def usage_cost_usd(usage: dict, model: str = DEFAULT_MODEL) -> float:
    """Dollar cost of one journaled `usage` record.

    A missing or non-numeric count is zero: an incomplete usage record must not crash the budget
    accountant. An unpriced model is different in kind and raises.

    Args:
        usage: The four token counts the model runner journals, cache reads and writes included.

    Raises:
        UnknownModelPriceError: If the table has no row for `model`.
    """
    require_priced_model(model)
    prices = _PER_TOKEN_USD[model]
    total = 0.0
    for field, price in prices.items():
        total += _as_int(usage.get(field, 0)) * price
    return total


def _as_int(value) -> int:
    """Coerce a usage field to a non-negative int; anything unparseable is zero."""
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0
