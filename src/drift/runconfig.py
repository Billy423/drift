"""The run knobs and their defaults, read by every surface that declares one.

A default belongs here and nowhere else: a value written down twice is changed once. The module
stays dependency-free, because the CLI imports it while declaring its options.
"""

from __future__ import annotations

from types import MappingProxyType

#: USD ceiling for one whole-repository scan: dispatch the next cell only while spend is below
#: it. A cell already in flight is never cut.
DEFAULT_BUDGET_USD = 5.0

#: USD ceiling for a single-document check — a second default, not a second semantics.
#: Not a shared knob: it never crosses the service seam, so the set omits it.
DEFAULT_CHECK_BUDGET_USD = 0.50

#: Measurement mode. Off by default: a scan prefers a partial report, a measurement the opposite.
DEFAULT_STRICT_MEASUREMENT = False

#: `None` means the built-in per-run rail, not "no cap". The knob exists so a measurement is
#: reproducible from a released tree rather than a patched one.
DEFAULT_MAX_S_CANDIDATES: int | None = None

#: `None` means every document unit found. A path restricts the run to that one document,
#: scanned by both producers.
DEFAULT_DOC_FILTER: str | None = None

#: The knob set, in the order the declaring surfaces list them. Read-only: it is process-global,
#: so a stray mutation would change a later scan's budget with nothing to show it.
RUN_CONFIG_DEFAULTS = MappingProxyType(
    {
        "budget": DEFAULT_BUDGET_USD,
        "strict_measurement": DEFAULT_STRICT_MEASUREMENT,
        "max_s_candidates": DEFAULT_MAX_S_CANDIDATES,
        "doc_filter": DEFAULT_DOC_FILTER,
    }
)

__all__ = [
    "DEFAULT_BUDGET_USD",
    "DEFAULT_DOC_FILTER",
    "DEFAULT_MAX_S_CANDIDATES",
    "DEFAULT_STRICT_MEASUREMENT",
    "RUN_CONFIG_DEFAULTS",
    "run_config",
]


def run_config(config: dict | None = None, **overrides) -> dict:
    """The knob set with defaults filled in, refusing any key it does not know.

    Both ends of the service seam call this, so a message that predates a knob still runs with
    that knob's default.

    Raises:
        ValueError: If a key is not a knob. An unknown key means the sender and this version
            disagree about the set; dropping it silently would change a paid run's behaviour.
    """
    merged = {**(config or {}), **overrides}
    unknown = set(merged) - set(RUN_CONFIG_DEFAULTS)
    if unknown:
        raise ValueError(
            f"unknown run-config key(s) {sorted(unknown)}; the knob set is "
            f"{sorted(RUN_CONFIG_DEFAULTS)} (drift.runconfig)"
        )
    return {**RUN_CONFIG_DEFAULTS, **merged}
