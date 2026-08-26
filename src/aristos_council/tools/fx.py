"""VALBAND-2 — MONTHLY FX, for bands whose accounts and price are in different currencies.

WHY MONTHLY AND NOT TODAY'S RATE
The valuation band is a five-year history of a company's own multiple. Its statement side
(EBIT, net income, debt, cash) is reported in the ACCOUNTS currency; its price side
(market cap, closes) is in the PRICE currency. For an ADR — ASML, NVO, TSM, VALE — those
differ, and v1 abstained rather than convert, which was right then and darkens most of a
real portfolio now.

Converting at TODAY's rate would be worse than abstaining. An EV/EBIT from 2023 valued at
2026 FX does not distort one point, it distorts the SHAPE of the band: every historical
multiple is rescaled by a factor that has nothing to do with the company, so the
percentile a reader is shown is partly a currency chart. Each month is therefore valued
at ITS OWN month-end rate, and a month with no rate is DROPPED and counted — never
guessed, never back-filled from a neighbour, never silently given today's rate.

DIRECT FIRST, THEN THE REVERSE PAIR INVERTED
yfinance publishes FX as a pair ticker. Coverage differs by pair, so:

  1. ``<FROM><TO>=X`` (DKKUSD=X) is queried first and used wherever it has a month;
  2. for months it lacks, ``<TO><FROM>=X`` (USDDKK=X) is queried and INVERTED (1/rate);
  3. a month absent from both is missing, and stays missing.

Which source a month used is recorded, because the provenance line names it per name.

Rates are fetched through ``adapter.get_price_history`` — the same path every other input
takes — so they are cached, frozen into the run record and replayed offline exactly like
a price series. Nothing here fetches directly.

Pure apart from that one adapter call: same bars in, same rates out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

# A month-end rate is matched to a month, not to a day: the statement side is annual and
# the price side is a month-end close, so day-level precision would be false precision.
_MonthKey = tuple[int, int]

DIRECT = "direct"
INVERTED = "inverted"


@dataclass(frozen=True)
class MonthlyFx:
    """Month-end rates in units of ``to_ccy`` per 1 ``from_ccy``, plus their provenance.

    ``rates`` is keyed by (year, month) so a caller can ask for the month a valuation
    point belongs to without matching exact days. ``sources`` records DIRECT or INVERTED
    per month — the report names it, and a band that mixed the two says so."""

    from_ccy: str
    to_ccy: str
    rates: dict[_MonthKey, float] = field(default_factory=dict)
    sources: dict[_MonthKey, str] = field(default_factory=dict)
    direct_pair: str = ""
    reverse_pair: str = ""
    # Pairs that returned nothing at all, for an abstention that NAMES what was missing.
    missing_pairs: tuple[str, ...] = ()

    def rate_for(self, day: date) -> Optional[float]:
        """The rate for ``day``'s month, or None. Never falls back to another month."""
        return self.rates.get((day.year, day.month))

    def source_for(self, day: date) -> str:
        return self.sources.get((day.year, day.month), "")

    @property
    def available(self) -> bool:
        return bool(self.rates)

    @property
    def used_direct(self) -> int:
        return sum(1 for s in self.sources.values() if s == DIRECT)

    @property
    def used_inverted(self) -> int:
        return sum(1 for s in self.sources.values() if s == INVERTED)

    def provenance(self) -> str:
        """The clause the band's display carries, naming the pair(s) actually used.

        Per name, because coverage differs by pair: two names in the same cohort can
        legitimately have taken different routes to the same conversion."""
        if not self.available:
            return ""
        lead = (f"{self.from_ccy} accounts converted to {self.to_ccy}, monthly FX, "
                f"source yfinance ")
        if self.used_inverted and not self.used_direct:
            return lead + f"{self.reverse_pair} inverted"
        if self.used_inverted:
            return (lead + f"{self.direct_pair} ({self.used_inverted} month"
                    f"{'s' if self.used_inverted != 1 else ''} from "
                    f"{self.reverse_pair} inverted)")
        return lead + self.direct_pair


def _month_end_rates(bars) -> dict[_MonthKey, tuple[date, float]]:
    """Last close of each calendar month in a rate series, positive only."""
    out: dict[_MonthKey, tuple[date, float]] = {}
    for b in bars or ():
        d = getattr(b, "day", None)
        c = getattr(b, "close", None)
        if d is None or c is None or c <= 0:
            continue
        key = (d.year, d.month)
        prev = out.get(key)
        if prev is None or d > prev[0]:
            out[key] = (d, float(c))
    return out


def _fetch(adapter, pair: str, *, start: date, end: date):
    """One rate series through the adapter's price path, or None on any failure.

    A transient error is allowed to propagate — the run's existing retry discipline owns
    that decision; anything else degrades to "this pair is unavailable", which the caller
    turns into an honest abstention rather than a guess."""
    from ..data.adapter import TransientFetchError

    try:
        ph = adapter.get_price_history(pair, start=start, end=end)
    except TransientFetchError:
        raise
    except Exception:
        return None
    return getattr(ph, "bars", None)


def monthly_fx_series(adapter, from_ccy: str, to_ccy: str, *,
                      start: date, end: date) -> MonthlyFx:
    """Month-end ``from_ccy`` -> ``to_ccy`` rates across the window.

    Queries the direct pair, then fills only the months it lacks from the inverted
    reverse pair. The reverse pair is not fetched at all when the direct one is complete,
    so the common case costs one call."""
    direct_pair = f"{from_ccy}{to_ccy}=X"
    reverse_pair = f"{to_ccy}{from_ccy}=X"
    # A month-end close needs the days around it; widen slightly so the first month of
    # the window is not lost to a weekend.
    lo = start - timedelta(days=7)

    rates: dict[_MonthKey, float] = {}
    sources: dict[_MonthKey, str] = {}
    missing: list[str] = []

    direct = _month_end_rates(_fetch(adapter, direct_pair, start=lo, end=end))
    if not direct:
        missing.append(direct_pair)
    for key, (_, value) in direct.items():
        rates[key] = value
        sources[key] = DIRECT

    # Only the months the direct pair could not supply are worth a second call.
    wanted = _months_between(start, end)
    if any(k not in rates for k in wanted):
        reverse = _month_end_rates(_fetch(adapter, reverse_pair, start=lo, end=end))
        if not reverse:
            missing.append(reverse_pair)
        for key, (_, value) in reverse.items():
            if key in rates or value <= 0:
                continue
            rates[key] = 1.0 / value
            sources[key] = INVERTED

    return MonthlyFx(from_ccy=from_ccy, to_ccy=to_ccy, rates=rates, sources=sources,
                     direct_pair=direct_pair, reverse_pair=reverse_pair,
                     missing_pairs=tuple(missing))


def _months_between(start: date, end: date) -> list[_MonthKey]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out
