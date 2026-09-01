"""VALBAND-FX-1 — the reversion value must be in the currency AND share class of the
price it sits beside.

OBSERVED 2026-09-01, adhoc pharma cohort, three lenses:

  NVO   price $45.33, EV/EBIT 10.4x against its own 5-year median of 29.6x,
        reversion value shown as "$874.75 (+1830%)".
        A 10.4x -> 29.6x reversion is a ~2.8x move. It cannot produce a 19x price gap.
        874.75 DKK is about USD 135, which IS consistent — the number was right and the
        CURRENCY was wrong.

  GSK   reversion "$20.10 (-60%)" against 11.0x vs an 11.8x median — a near-flat gap that
        should have landed just ABOVE the price. 20.10 is GBP per ORDINARY share, and one
        GSK ADR is two ordinaries.

  MRK / ABBV / JNJ  USD throughout, all sane. The bug was confined to names whose
        statements are kept in another currency.

Two independent defects, so two independent guards below: the conversion, and the unit
the price is per. Both are computed from figures the band already records — no ADR ratio
is guessed anywhere, which is what keeps this inside the house rule on abstention.
"""

from __future__ import annotations

from datetime import date

import pytest

from aristos_council.tools.reversion import reversion_value
from aristos_council.tools.valuation_band import valuation_band

from tests.test_valuation_band import (
    TODAY, _bars, _fiscal_years, _flat_ev_ebit, _fundamentals)
from aristos_council.data.adapter import Fundamentals
from tests.test_valuation_band_fx import _all_months, _FxAdapter

from aristos_council.tools.fx import monthly_fx_series


def _fx(rate=0.15, ccy="DKK"):
    return monthly_fx_series(_FxAdapter({f"{ccy}USD=X": _all_months(rate)}),
                             ccy, "USD", start=TODAY - _DAYS, end=TODAY)


from datetime import timedelta  # noqa: E402
_DAYS = timedelta(days=2000)


def _fund(*, financial_currency, shares=25.0, years=8):
    """Like the shared fixture, plus a DATED shares_outstanding series — the band reads
    that to record `current_shares`, and the domestic reversion path divides by it.

    `shares` is deliberately NOT market_cap/price (1000/100 = 10 quoted units), so the
    per-ordinary and per-quoted answers differ and the test can tell which one was used.
    That difference is the GSK case in miniature."""
    ends = _fiscal_years(years)
    aligned = {"ebit": [100.0] * years, "total_debt": [200.0] * years,
               "cash": [50.0] * years, "shares_outstanding": [shares] * years}
    period_ends = {k: ends[:years] for k in aligned}
    return Fundamentals(ticker="X", market_cap=1_000.0, aligned_annual=aligned,
                        aligned_period_ends=period_ends, currency="USD",
                        financial_currency=financial_currency)


def _cross_currency_band(*, rate=0.15):
    """A DKK-reporting, USD-quoted name — the NVO shape."""
    bars = _bars([100.0] * 61)
    f = _fund(financial_currency="DKK")
    return bars, f, valuation_band(bars, f, asof=TODAY, fx=_fx(rate))


def _same_currency_band():
    bars = _bars([100.0] * 61)
    f = _fund(financial_currency="USD")
    return bars, f, valuation_band(bars, f, asof=TODAY)


# --------------------------------------------------------------------------- #
# 1. THE CURRENCY — the NVO case
# --------------------------------------------------------------------------- #
def test_a_cross_currency_reversion_is_stated_in_the_QUOTE_currency():
    """The band converts every month for its percentile; the reversion inputs were
    carried out raw. This asserts the correction is applied, by ORDER OF MAGNITUDE
    against the raw home-currency figure — the exact value is a fixture detail, the
    scale is the bug."""
    bars, f, band = _cross_currency_band(rate=0.15)
    assert band.available, band.note
    assert band.fx_rate_current == pytest.approx(0.15, rel=1e-9)

    rev = reversion_value(band, f, last_close=100.0, currency="USD")
    assert rev.available, rev.note

    # the home-currency answer this used to render
    raw = (band.current_earnings * band.median_multiple - band.current_net_debt) \
        / band.quoted_units
    assert rev.price == pytest.approx(raw * 0.15, rel=1e-6)
    assert rev.price < raw                       # ...and it is visibly smaller


def test_the_reversion_gap_is_consistent_with_the_MULTIPLE_ratio():
    """The check that would have caught the live bug on sight: a name trading at its own
    median must show a gap near zero, whatever currency its accounts are in. NVO's
    "+1830%" against a 2.8x multiple move was impossible on its face."""
    bars, f, band = _cross_currency_band(rate=0.15)
    rev = reversion_value(band, f, last_close=100.0, currency="USD")

    # this fixture's band is flat, so today's multiple IS the median
    assert band.current == pytest.approx(band.median_multiple, rel=1e-6)
    # ...therefore the reversion price must sit near the quoted price, not 19x it
    assert abs(rev.gap) < 0.5, (rev.price, rev.gap)


def test_the_rate_used_is_the_CURRENT_month_and_is_disclosed():
    bars, f, band = _cross_currency_band(rate=0.15)
    rev = reversion_value(band, f, last_close=100.0, currency="USD")

    assert band.fx_rate_asof == band.current_point      # the band's own current month
    assert "DKK->USD" in rev.fx_note and "0.1500" in rev.fx_note
    assert band.fx_rate_asof.isoformat() in rev.fx_note
    assert rev.fx_note in rev.display                   # the reader sees it


def test_the_rate_reaches_the_price_without_distorting_a_flat_band():
    """NOT a proportionality test — the band's own median is itself rate-dependent
    (value = (mcap + nd*r) / (e*r)), so doubling the rate does not double the price.
    What must hold is the property that matters: whatever the rate, a name sitting on
    its own median reverts to about its own price."""
    for rate in (0.10, 0.15, 0.20):
        _, f, band = _cross_currency_band(rate=rate)
        rev = reversion_value(band, f, last_close=100.0, currency="USD")
        assert rev.available, rev.note
        assert band.fx_rate_current == pytest.approx(rate, rel=1e-9)
        assert abs(rev.gap) < 0.5, (rate, rev.price, rev.gap)


# --------------------------------------------------------------------------- #
# 2. THE SHARE CLASS — the GSK case
# --------------------------------------------------------------------------- #
def test_the_price_is_per_QUOTED_unit_not_per_ordinary_share():
    """A depositary receipt can bundle several ordinary shares, so dividing the implied
    equity by `shares_outstanding` prices the wrong instrument. The quoted-unit count is
    derived from market cap / price — both already recorded, neither guessed."""
    bars, f, band = _cross_currency_band(rate=0.15)
    assert band.quoted_units is not None
    # the two counts genuinely differ on this fixture, which is what makes the choice
    # observable rather than incidental
    assert band.current_shares is not None

    rev = reversion_value(band, f, last_close=100.0, currency="USD")
    per_ordinary = (band.current_earnings * band.median_multiple
                    - band.current_net_debt) * 0.15 / band.current_shares
    per_quoted = (band.current_earnings * band.median_multiple
                  - band.current_net_debt) * 0.15 / band.quoted_units
    assert rev.price == pytest.approx(per_quoted, rel=1e-6)
    if abs(per_ordinary - per_quoted) > 1e-9:
        assert rev.price != pytest.approx(per_ordinary, rel=1e-6)


def test_an_unknowable_quoted_unit_count_abstains_with_the_reason():
    """House rule: do not guess a share-class ratio. Without a usable unit count the row
    says so, in words, rather than rendering a number against the wrong instrument."""
    bars, f, band = _cross_currency_band(rate=0.15)
    broken = type(band)(**{**band.__dict__, "quoted_units": None})

    rev = reversion_value(broken, f, last_close=100.0, currency="USD")
    assert not rev.available
    assert "share-class mismatch" in rev.note
    assert "not evaluated" in rev.display


def test_a_missing_current_rate_abstains_rather_than_mixing_currencies():
    bars, f, band = _cross_currency_band(rate=0.15)
    broken = type(band)(**{**band.__dict__, "fx_rate_current": None})

    rev = reversion_value(broken, f, last_close=100.0, currency="USD")
    assert not rev.available
    assert "could not be converted" in rev.note


# --------------------------------------------------------------------------- #
# 3. THE DOMESTIC CASE — unchanged, byte for byte
# --------------------------------------------------------------------------- #
def test_a_same_currency_name_is_byte_identical_to_before():
    """MRK / ABBV / JNJ were already right. A fix for foreign listings must not move a
    domestic number by a cent: the rate is 1.0 and the quoted unit IS the ordinary
    share, so the arithmetic is the one it always was."""
    bars, f, band = _same_currency_band()
    assert band.fx_pair == "" and band.fx_rate_current == 1.0

    rev = reversion_value(band, f, last_close=100.0, currency="USD")
    assert rev.available, rev.note
    expected = (band.current_earnings * band.median_multiple
                - band.current_net_debt) / band.current_shares
    assert rev.price == pytest.approx(expected, rel=1e-12)
    assert rev.fx_note == ""                       # nothing to disclose
    assert "converted" not in rev.display


def test_the_gap_is_recomputed_from_the_corrected_price():
    bars, f, band = _cross_currency_band(rate=0.15)
    rev = reversion_value(band, f, last_close=100.0, currency="USD")
    assert rev.gap == pytest.approx(rev.price / 100.0 - 1.0, rel=1e-12)
