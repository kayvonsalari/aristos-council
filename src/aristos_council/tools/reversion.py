"""Reversion value (PRICE-1) — what the price would be at this company's OWN historical
valuation.

Why this exists
---------------
The valuation band (``tools/valuation_band.py``) answers "is this dear or cheap against
its own past?" with a percentile. A percentile alone cannot answer the next question a
reader always asks: *and what would it cost at a normal valuation for this company?*
This module answers exactly that, and nothing more.

WHAT THIS IS NOT — read this before rendering it anywhere
---------------------------------------------------------
It is **arithmetic**, not analysis. It re-prices today's earnings and today's net debt
at the MEDIAN of the multiples this same company actually traded on over the band's
window. It is **not a forecast**, **not a target price**, **not a recommendation**, and
it carries no view on whether the median multiple is deserved — a business that has
permanently derated (a broken moat, a patent cliff, a regulated return cut) *should*
trade below its own past median, and this number will call that a discount because
arithmetic cannot tell decline from mispricing. Every render site states this in plain
English.

Doctrine (non-negotiable): **display only**. It feeds no factor, no screen, no gate, no
rank, no verdict and no LLM prompt, and it is not registered as a criterion.

The formula
-----------
``median_multiple`` is the median of the SAME monthly multiple series the band already
took its percentile over — recorded by the band, never rebuilt here, so the two numbers
are consistent by construction.

EV/EBIT basis::

    implied_EV      = ebit x median_multiple
    implied_equity  = implied_EV - net_debt        (net debt on the band's own basis,
                                                    point-in-time at the same month)
    reversion_price = implied_equity / shares_outstanding

P/E basis::

    reversion_price = eps_ttm x median_pe

and in both cases::

    gap = reversion_price / last_close - 1         (+0.17 -> the price would be 17% higher)

Abstains (with the reason, never a guess) when the band abstained, when earnings are
non-positive on the band's basis, when shares outstanding are missing or non-positive,
when net debt is not computable on the band's basis, when the last close is missing, or
when the implied equity value is not positive.

**No clamping, no capping, no smoothing.** A name whose median multiple is three times
today's shows a huge gap, and that number is rendered as-is beside the median multiple
that produced it — auditable, rather than tidied into plausibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .price_context import currency_note, format_money
from .valuation_band import ValuationBand

_EV_EBIT = "ev_ebit"
_PE = "pe"

# The phrase naming the multiple in the rendered line, per basis.
_BASIS_PHRASE = {_EV_EBIT: "EV/EBIT", _PE: "P/E"}


@dataclass(frozen=True)
class ReversionValue:
    """The price implied by this name's own median multiple. ``price`` is None when the
    calculation ABSTAINED — ``note`` then carries the reason."""

    price: Optional[float] = None
    gap: Optional[float] = None               # price / last_close - 1
    median_multiple: Optional[float] = None
    basis: Optional[str] = None               # "ev_ebit" | "pe" | None when abstained
    window_years: int = 0
    months_covered: int = 0
    months_total: int = 0
    currency: Optional[str] = None
    note: str = ""

    @property
    def available(self) -> bool:
        return self.price is not None

    @property
    def display(self) -> str:
        """The one string every surface renders:

        "reversion value $31.80 (+17%) if EV/EBIT returned to its own 5-year median of
        18.4x; 42 of 61 months usable" — or "reversion value not evaluated — <reason>".
        """
        if not self.available:
            return f"reversion value not evaluated — {self.note}" if self.note \
                else "reversion value not evaluated"
        phrase = _BASIS_PHRASE.get(self.basis or "", "the multiple")
        return (f"reversion value {format_money(self.price, self.currency)} "
                f"({self.gap:+.0%}) if {phrase} returned to its own "
                f"{self.window_years}-year median of {self.median_multiple:.1f}x; "
                f"{self.months_covered} of {self.months_total} months usable"
                f"{currency_note(self.currency)}")


def _abstain(note: str, band: Optional[ValuationBand] = None,
             currency: Optional[str] = None) -> ReversionValue:
    return ReversionValue(
        note=note, currency=currency,
        window_years=band.window_years if band is not None else 0,
        months_covered=band.months_covered if band is not None else 0,
        months_total=band.months_total if band is not None else 0)


def reversion_value(band: Optional[ValuationBand], fundamentals, *,
                    last_close: Optional[float],
                    currency: Optional[str] = None) -> ReversionValue:
    """Today's earnings and net debt re-priced at this name's own median multiple.

    ``band`` is the ValuationBand for this name (it carries the median and the
    point-in-time inputs the band's CURRENT point was built from); ``last_close`` is the
    SAME price the PRICE-1 price line displays, so the gap always reconciles with the
    number the reader can see above it. Never raises — it abstains with a reason."""
    if band is None:
        return _abstain("the valuation band was not computed on this run", None, currency)
    if not band.available:
        return _abstain("the valuation band abstained for this name", band, currency)
    median = band.median_multiple
    if median is None or median <= 0:
        return _abstain("no usable median multiple in the band's series", band, currency)
    if last_close is None or last_close <= 0:
        return _abstain("last close unavailable — the gap cannot be stated",
                        band, currency)

    if band.basis == _PE:
        eps = getattr(fundamentals, "eps", None)
        if eps is None or eps <= 0:
            return _abstain("EPS (TTM) is not positive, so a P/E reversion value has "
                            "no meaning", band, currency)
        price = eps * median
    else:
        ebit = band.current_earnings
        if ebit is None or ebit <= 0:
            return _abstain("EBIT is not positive, so an EV/EBIT reversion value has "
                            "no meaning", band, currency)
        net_debt = band.current_net_debt
        if net_debt is None:
            return _abstain("net debt is not computable on the band's basis",
                            band, currency)
        shares = band.current_shares
        if shares is None or shares <= 0:
            return _abstain("shares outstanding unavailable", band, currency)
        implied_equity = ebit * median - net_debt
        if implied_equity <= 0:
            return _abstain("implied equity value is not positive at the median "
                            "multiple (net debt exceeds the implied enterprise value)",
                            band, currency)
        price = implied_equity / shares

    return ReversionValue(
        price=price, gap=price / last_close - 1.0, median_multiple=median,
        basis=band.basis, window_years=band.window_years,
        months_covered=band.months_covered, months_total=band.months_total,
        currency=currency,
        note=f"{_BASIS_PHRASE.get(band.basis or '', 'multiple')} median "
             f"{median:.4g} over {band.months_covered} of {band.months_total} months")
