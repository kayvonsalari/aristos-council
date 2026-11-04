"""The three forms' selection, pure: from one round's readings of the industry to the names each form holds.

A ``NameReading`` is what the gate and the valuation said about one name on one round date, from accounts published by
then. Nothing here touches data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..absolute_value.engine import CLEARS_HURDLE
from ..gate.engine import QUALIFIES
from . import CHEAP_FRACTION


@dataclass(frozen=True)
class NameReading:
    ticker: str
    eligible: bool = False                  # priced, and its market value at the date clears the industry's size floor
    gate_state: str = ""                    # qualifies / excluded / not_enough_data / not_covered ("" = no accounts read)
    backtest_state: str = ""                # the backtest-form verdict state ("" = valuation not run)
    base_yield: Optional[float] = None      # base per share over the price
    size_basis: str = ""                    # "shares x price" | "scaled" | "price only" | ""
    no_hurdle: bool = False                 # the reporting currency has no bond series (or none on that date)
    no_fx: bool = False                     # price and accounts in different currencies, no rate
    split_withheld: bool = False            # a split after the visible accounts: per-share figures not comparable
    no_accounts: bool = False


def cheapest_third(passers: list[NameReading]) -> list[str]:
    """Within the gate-passers that have a base yield, the highest-yielding third (at least one name). Ranked inside
    the industry, never across industries."""
    ranked = sorted((r for r in passers if r.base_yield is not None), key=lambda r: (-r.base_yield, r.ticker))
    if not ranked:
        return []
    take = max(1, len(ranked) // CHEAP_FRACTION)
    return sorted(r.ticker for r in ranked[:take])


def select_forms(readings) -> dict[int, list[str]]:
    """{1: gate alone, 2: gate plus relative cheapness, 3: gate plus the absolute verdict}, sorted ticker lists."""
    pool = [r for r in readings if r.eligible]
    passers = [r for r in pool if r.gate_state == QUALIFIES]
    return {1: sorted(r.ticker for r in passers),
            2: cheapest_third(passers),
            3: sorted(r.ticker for r in passers if r.backtest_state == CLEARS_HURDLE)}
