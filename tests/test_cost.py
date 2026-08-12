"""Cost-table integrity — the price table is part of every published cost number.

Published costs are only re-derivable if the table they were computed with is identified in the
run's own record. That makes the price table an artifact rather than an internal convenience: a
later change to a rate must be visible as a version change, not silently rewrite what past runs
appear to have cost.

These tests pin the two properties that make it so: a model with no price can never be scanned
silently, and the table carries a version the run journals.
"""

from __future__ import annotations

import pytest

from drift.cost import (
    DEFAULT_MODEL,
    PRICE_TABLE_VER,
    UnknownModelPriceError,
    require_priced_model,
    usage_cost_usd,
)

_USAGE = {
    "input_tokens": 1_000_000,
    "output_tokens": 1_000_000,
    "cache_read_input_tokens": 1_000_000,
    "cache_creation_input_tokens": 1_000_000,
}


def test_the_default_model_is_priced():
    # $3 + $15 + $0.30 + $3.75 per 1M of each
    assert usage_cost_usd(_USAGE, DEFAULT_MODEL) == pytest.approx(22.05)


def test_an_unpriced_model_raises_rather_than_falling_back():
    """The failure this closes is silent and directional. The table used to fall back to the
    default model's prices for anything it did not know, so changing the scan model without
    adding its row kept working and mis-priced every call: a cheaper model over-counts and stops
    the budget early, a pricier one under-counts and blows through the ceiling — the exact
    `--budget 5` spends $25 hazard. Pricing at zero would be worse still (it disables the gate),
    but "refuse" satisfies that concern too, and refusing is this project's rule everywhere else:
    skip-don't-guess."""
    with pytest.raises(UnknownModelPriceError) as exc:
        usage_cost_usd(_USAGE, "claude-haiku-4-5-20251001")
    assert "claude-haiku-4-5-20251001" in str(exc.value)


def test_require_priced_model_is_the_pre_spend_guard():
    """`usage_cost_usd` raising is a backstop against a code bug, not the guard: it fires after
    money is spent, and a scan that has spent money must still emit a report (Unit A). The real
    check runs once, before the client exists."""
    require_priced_model(DEFAULT_MODEL)  # does not raise
    with pytest.raises(UnknownModelPriceError):
        require_priced_model("some-model-nobody-priced")


def test_cache_prices_are_derived_from_the_input_price():
    """Anthropic prices cache reads and writes as multiples of the input rate. Written as
    absolute constants they silently stop tracking it — a later table edit that changes input
    but not the two derived numbers produces a table that is internally inconsistent and still
    looks maintained."""
    from drift.cost import _CACHE_READ_MULTIPLE, _CACHE_WRITE_MULTIPLE, _PER_TOKEN_USD

    prices = _PER_TOKEN_USD[DEFAULT_MODEL]
    assert prices["cache_read_input_tokens"] == pytest.approx(
        prices["input_tokens"] * _CACHE_READ_MULTIPLE
    )
    assert prices["cache_creation_input_tokens"] == pytest.approx(
        prices["input_tokens"] * _CACHE_WRITE_MULTIPLE
    )


def test_the_table_carries_a_version():
    assert isinstance(PRICE_TABLE_VER, str) and PRICE_TABLE_VER
