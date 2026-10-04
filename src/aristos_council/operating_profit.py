"""PROFIT GUARD (SMALLCAP-BAND-GAP-1) - a lens whose measures need operating profit does not
apply to a company that has none.

Magic Formula RAW, Value + Momentum, Earnings Power Value, Quality and Cyclical Income all
divide by, or capitalise, operating profit (earnings yield = EBIT / EV, return on capital =
NOPAT / capital, EPV = normalised EBIT / cost of capital, debt / EBIT). For a company whose
latest operating profit is zero or negative those numbers are meaningless (a negative yield
ranked against positive ones), so the lens says "does not apply: no operating profit" instead
of ranking it on negative numbers.

null != false (rule 3): a company whose operating profit is simply NOT KNOWN is not gated; only a
CONFIRMED zero-or-negative latest figure is. The published strategy files are immutable
(rule 7), so the five existing lenses are named here; a NEW lens file declares
``requires_operating_profit: true`` itself.
"""
from __future__ import annotations

from typing import Optional

NO_OPERATING_PROFIT_REASON = "no operating profit"

OPERATING_PROFIT_LENS_IDS = frozenset({
    "magic_formula_raw_v1",
    "magic_formula_momentum_v1",
    "epv_v1",
    "quality_v1",
    "cyclical_income_v1",
})


def lens_requires_operating_profit(rank_strategy) -> bool:
    if rank_strategy is None:
        return False
    return bool(getattr(rank_strategy, "requires_operating_profit", False)
                or getattr(rank_strategy, "id", "") in OPERATING_PROFIT_LENS_IDS)


def latest_operating_profit(fundamentals) -> Optional[float]:
    """The newest annual operating profit (EBIT) on file, or None when there is none."""
    if fundamentals is None:
        return None
    for series in (getattr(fundamentals, "operating_income", None),
                   getattr(fundamentals, "ebit", None)):
        if series:
            latest = series[0]
            if latest is not None:
                return float(latest)
    return None


def has_no_operating_profit(fundamentals) -> bool:
    """True only when the latest operating profit is KNOWN and is zero or negative."""
    latest = latest_operating_profit(fundamentals)
    return latest is not None and latest <= 0
