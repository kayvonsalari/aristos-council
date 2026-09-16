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

**One exception, and it is an ABSTENTION, not a clamp (BAND-3).** Past ``GAP_SANITY``
(±150%) the value is not rendered at all. A three-fold implied move is not a reading of
anything — it is what a thin EBIT year, a share-count mismatch or a currency basis error
looks like coming out the far end. Left in the table it sits beside the sane rows with
exactly their authority, which is the one thing an honest number must never do. So the
value abstains and says why, rather than being quietly shrunk into plausibility — a clamp
would keep a wrong number and hide its wrongness. The band row keeps its percentile: the
percentile is computed from the distribution, not from this arithmetic, and is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .price_context import currency_note, format_money
from .valuation_band import ValuationBand

_EV_EBIT = "ev_ebit"
_PE = "pe"

# BAND-3 — the sanity bound on the implied move, either way. Beyond ±150% the inputs are
# not believable as a reading: Kinetik (KNTK) rendered +322% off an EV/EBIT of 16.4x
# against its own median of 23.2x on 2026-09-15, on the same page as Cheniere's sane +24%.
# A bound, never a clamp: past it the value ABSTAINS with its reason (see the module
# docstring). Basis-agnostic on purpose — it catches a thin EBIT year, a share-count
# mismatch and a currency error alike, and does NOT overlap or second-guess VALBAND-FX.
#
# Written on the MAGNITUDE (±150%), but only the upward side is reachable: the gap is
# ``implied_price / last_close - 1`` and an implied price is never negative (a
# non-positive implied equity value abstains earlier), so the gap floors at -100% and
# cannot pass -150%. A collapse is therefore always stated, however severe; only an
# implausible spike is withheld. Left symmetric rather than written as ``gap >
# GAP_SANITY``, because the symmetric form stays correct if the arithmetic ever changes
# shape. Pinned by test_reversion_sanity.py so the dead half is not mistaken for a bug.
GAP_SANITY = 1.5
# REV-BOUND-1 — the DOWNWARD bound, which BAND-3 left unreachable.
#
# BAND-3 wrote its bound on the magnitude (+/-150%) and noted that only the upward side
# could ever fire, because an implied price is never negative so the gap floors at -100%.
# That note was right about the arithmetic and wrong about the consequence: Murphy Oil came
# back at -98% and BP at -96%, both comfortably inside the bound and both absurd. A name
# that must fall by more than three quarters to reach its OWN median is telling you the
# median is wrong, not that the shares are worth a fifth of their price.
#
# -75% rather than -50%: a cyclical at a genuine peak can carry a halving to its own
# through-cycle median, and that IS a reading. Three quarters is not.
GAP_SANITY_DOWN = -0.75

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
    # VALBAND-FX-1 — the conversion this value passed through, when it passed through
    # one. Empty for a same-currency name. Rendered so every figure is traceable back to
    # a labelled rate and the month it belongs to.
    fx_note: str = ""

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
                f"{('; ' + self.fx_note) if self.fx_note else ''}"
                f"{currency_note(self.currency)}")


def _abstain(note: str, band: Optional[ValuationBand] = None,
             currency: Optional[str] = None) -> ReversionValue:
    return ReversionValue(
        note=note, currency=currency,
        window_years=band.window_years if band is not None else 0,
        months_covered=band.months_covered if band is not None else 0,
        months_total=band.months_total if band is not None else 0)


def _fx_note(band: ValuationBand) -> str:
    """The labelled rate this value was converted at — pair, rate and the month it
    belongs to, e.g. "DKK->USD @ 0.1550 (2026-08-31)".

    Empty for a same-currency name, so a domestic row gains nothing to read past. A
    converted row without this line would be a number a reader cannot retrace."""
    pair = getattr(band, "fx_pair", "")
    rate = getattr(band, "fx_rate_current", None)
    if not pair or rate is None:
        return ""
    base = pair.replace("=X", "")
    lead = f"{base[:3]}->{base[3:6]}" if len(base) >= 6 else base
    asof = getattr(band, "fx_rate_asof", None)
    when = f" ({asof.isoformat()})" if asof is not None else ""
    return f"converted {lead} @ {rate:.4f}{when}"


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
        # VALBAND-FX-1 — TWO conversions, both of which this used to skip.
        #
        # 1. CURRENCY. `current_earnings` and `current_net_debt` are recorded in the
        #    ACCOUNTS currency. The band's percentile loop converts each month at that
        #    month's rate, but these two were carried out raw — so the implied price came
        #    out in the home currency and was rendered under the quote currency's symbol.
        #    Live on 2026-09-01: NVO showed "$874.75 (+1830%)" from a 10.4x -> 29.6x
        #    reversion, which cannot produce a 19x price gap. 874.75 was DKK.
        #
        # 2. SHARE CLASS. Dividing by `shares_outstanding` gives a price per ORDINARY
        #    share, but the quote is per QUOTED UNIT, and a depositary receipt can bundle
        #    several ordinaries. GSK showed "$20.10 (-60%)" against a near-flat 11.0x vs
        #    11.8x — that is GBP per ordinary, and one GSK ADR is two ordinaries.
        #
        # Both are fixed with recorded data, not assumptions: the band's own rate for the
        # current month, and the quoted-unit count implied by market cap / price. No ADR
        # ratio is guessed anywhere — the unit count comes from the same two vendor
        # figures the band already builds its current point from.
        rate = band.fx_rate_current
        if rate is None or rate <= 0:
            return _abstain(
                "the accounts currency could not be converted to the quote currency for "
                "the current month, so a reversion price cannot be stated in the same "
                "currency as the price", band, currency)
        implied_equity = (ebit * median - net_debt) * rate
        if implied_equity <= 0:
            return _abstain("implied equity value is not positive at the median "
                            "multiple (net debt exceeds the implied enterprise value)",
                            band, currency)
        if band.fx_pair:
            # A cross-currency name is quoted in units that need not be ordinary shares.
            units = band.quoted_units
            if units is None or units <= 0:
                return _abstain(
                    "not evaluated — share-class mismatch: the quoted unit could not be "
                    "reconciled with reported shares outstanding, so a per-share price "
                    "cannot be stated against this quote", band, currency)
        else:
            # A domestic listing is quoted in its own ordinary shares by construction,
            # so the reported share count IS the quoted-unit count. Kept explicitly so
            # same-currency names are byte-identical to their pre-VALBAND-FX-1 values.
            units = band.current_shares
            if units is None or units <= 0:
                return _abstain("shares outstanding unavailable", band, currency)
        price = implied_equity / units

    gap = price / last_close - 1.0
    if gap > GAP_SANITY or gap < GAP_SANITY_DOWN:
        # BAND-3: past the bound this is an artefact, not a reading. Abstain through the
        # SAME path every other unstateable case takes, so the row keeps its shape and the
        # reason travels with it. The band's percentile is untouched.
        return _abstain(
            f"implied move {gap:+.0%} exceeds the sanity bound "
            f"(±{GAP_SANITY:.0%}) — inputs suspect (thin EBIT year, share-count or "
            "currency mismatch); not stated", band, currency)

    return ReversionValue(
        price=price, gap=gap, median_multiple=median,
        basis=band.basis, window_years=band.window_years,
        months_covered=band.months_covered, months_total=band.months_total,
        currency=currency,
        fx_note=_fx_note(band),
        note=f"{_BASIS_PHRASE.get(band.basis or '', 'multiple')} median "
             f"{median:.4g} over {band.months_covered} of {band.months_total} months")
