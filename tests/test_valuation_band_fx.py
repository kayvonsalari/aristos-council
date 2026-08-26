"""VALBAND-2 — monthly FX conversion for cross-currency valuation bands.

v1 abstained whenever the accounts currency differed from the price currency, which was
right at the time and dark for most of a real portfolio: ASML, NVO, TSM and VALE all
report in one currency and trade in another.

The whole design turns on ONE decision — each month is valued at ITS OWN month-end rate.
Converting the history at today's rate would not shift one point, it would reshape the
BAND: every historical multiple rescaled by a currency move the company had nothing to do
with, so the percentile a reader is shown would be part currency chart. These tests pin
that distinction directly (``test_history_uses_each_months_own_rate_not_todays``), because
it is the one that would be invisible if it broke — the band would still render, still
look plausible, and be wrong.

Everything else follows the house discipline: a month with no rate DROPS and is counted,
never guessed; the provenance says which pair produced the number; and verdicts, ranks
and screens are untouched, since the band is context and decides nothing.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from aristos_council.tools.fx import DIRECT, INVERTED, monthly_fx_series
from aristos_council.tools.valuation_band import valuation_band

from tests.test_valuation_band import (
    TODAY,
    _bars,
    _flat_ev_ebit,
    _fundamentals,
)


class _Bar:
    def __init__(self, day, close):
        self.day, self.close = day, close


class _Series:
    def __init__(self, bars):
        self.bars = bars


class _FxAdapter:
    """Serves FX pairs only, from a per-pair {(y, m): rate} map.

    ``missing`` names pairs that return nothing at all — the "no direct pair" case."""

    def __init__(self, pairs, *, missing=()):
        self.pairs = pairs
        self.missing = set(missing)
        self.asked: list[str] = []

    def get_price_history(self, ticker, *, start, end):
        self.asked.append(ticker)
        if ticker in self.missing or ticker not in self.pairs:
            return _Series([])
        bars = []
        for (y, m), rate in sorted(self.pairs[ticker].items()):
            # a month-end-ish day; month_end logic takes the latest in the month
            bars.append(_Bar(date(y, m, 28), rate))
        return _Series(bars)


def _months(start: date, n: int):
    out, y, m = [], start.year, start.month
    for _ in range(n):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _all_months(rate=0.15):
    """A flat DKK->USD rate covering the whole window the tests request.

    Deliberately WIDER than the 61-month band fixture: a rate series that started exactly
    at the band's first month would leave the requested window's earliest months
    uncovered, and the filler would then fetch the reverse pair for months no band
    actually uses — which is a property of the fixture, not of the code."""
    start = TODAY - timedelta(days=round(365.25 * 6))
    return {k: rate for k in _months(date(start.year, start.month, 1), 80)}


# --------------------------------------------------------------------------- #
# 1. THE SOURCE ORDER — direct first, reverse inverted only for the gaps
# --------------------------------------------------------------------------- #
def test_the_direct_pair_is_used_and_the_reverse_is_not_even_fetched():
    adapter = _FxAdapter({"DKKUSD=X": _all_months(0.15)})
    fx = monthly_fx_series(adapter, "DKK", "USD",
                           start=TODAY - timedelta(days=2000), end=TODAY)

    assert fx.available and fx.used_inverted == 0
    assert adapter.asked == ["DKKUSD=X"], "the reverse pair cost a call it did not need"
    assert fx.provenance() == ("DKK accounts converted to USD, monthly FX, "
                               "source yfinance DKKUSD=X")


def test_the_reverse_pair_is_inverted_when_there_is_no_direct_pair():
    adapter = _FxAdapter({"USDDKK=X": _all_months(1 / 0.15)},
                         missing=("DKKUSD=X",))
    fx = monthly_fx_series(adapter, "DKK", "USD",
                           start=TODAY - timedelta(days=2000), end=TODAY)

    assert fx.available and fx.used_direct == 0 and fx.used_inverted > 0
    assert fx.rate_for(TODAY) == pytest.approx(0.15, rel=1e-9)
    assert fx.provenance().endswith("USDDKK=X inverted")


def test_a_gap_in_the_direct_pair_is_filled_from_the_inverted_reverse():
    direct = _all_months(0.15)
    hole = sorted(direct)[-20]              # a month the band actually values
    del direct[hole]
    adapter = _FxAdapter({"DKKUSD=X": direct, "USDDKK=X": _all_months(1 / 0.20)})
    fx = monthly_fx_series(adapter, "DKK", "USD",
                           start=TODAY - timedelta(days=2000), end=TODAY)

    assert fx.sources[hole] == INVERTED
    assert fx.rates[hole] == pytest.approx(0.20, rel=1e-9)
    other = sorted(direct)[-21]
    assert fx.sources[other] == DIRECT
    # ...and the provenance names BOTH, because the band mixed them
    prov = fx.provenance()
    assert "DKKUSD=X" in prov and "USDDKK=X inverted" in prov


def test_a_month_missing_from_BOTH_pairs_stays_missing():
    """Never guessed, never back-filled from a neighbour."""
    direct = _all_months(0.15)
    reverse = _all_months(1 / 0.15)
    hole = sorted(direct)[-20]              # inside the band window
    del direct[hole], reverse[hole]
    adapter = _FxAdapter({"DKKUSD=X": direct, "USDDKK=X": reverse})
    fx = monthly_fx_series(adapter, "DKK", "USD",
                           start=TODAY - timedelta(days=2000), end=TODAY)

    assert hole not in fx.rates
    assert fx.rate_for(date(hole[0], hole[1], 15)) is None


def test_no_pair_at_all_names_what_was_missing():
    adapter = _FxAdapter({}, missing=("DKKUSD=X", "USDDKK=X"))
    fx = monthly_fx_series(adapter, "DKK", "USD",
                           start=TODAY - timedelta(days=2000), end=TODAY)
    assert not fx.available
    assert set(fx.missing_pairs) == {"DKKUSD=X", "USDDKK=X"}


# --------------------------------------------------------------------------- #
# 2. THE BAND — computed, with provenance
# --------------------------------------------------------------------------- #
def test_a_cross_currency_band_computes_and_states_its_conversion():
    bars, _ = _flat_ev_ebit([100.0] * 61)
    f = _fundamentals(ebit=100.0, debt=200.0, cash=50.0,
                      currency="USD", financial_currency="DKK")
    adapter = _FxAdapter({"DKKUSD=X": _all_months(0.15)})
    fx = monthly_fx_series(adapter, "DKK", "USD",
                           start=TODAY - timedelta(days=2000), end=TODAY)

    band = valuation_band(bars, f, asof=TODAY, fx=fx)
    assert band.available, band.note
    assert "DKK accounts converted to USD" in band.fx_note
    assert "monthly FX" in band.fx_note and "DKKUSD=X" in band.fx_note
    assert "converted to USD" in band.display          # the reader sees it


def test_the_same_currency_band_is_byte_identical_to_before():
    """The conversion path must not touch a domestic name."""
    bars, _ = _flat_ev_ebit([100.0] * 61)
    f = _fundamentals(ebit=100.0, debt=200.0, cash=50.0,
                      currency="USD", financial_currency="USD")

    before = valuation_band(bars, f, asof=TODAY)
    after = valuation_band(bars, f, asof=TODAY, fx=None)
    assert before.display == after.display
    assert after.fx_note == "" and after.fx_months_missing == 0


def test_history_uses_each_months_own_rate_not_todays():
    """THE guarantee. A rate that moves through the window must change the SHAPE of the
    band, not merely rescale it — so a band built on per-month rates cannot equal one
    built on a single flat rate."""
    bars, _ = _flat_ev_ebit([100.0] * 61)
    f = _fundamentals(ebit=100.0, debt=200.0, cash=50.0,
                      currency="USD", financial_currency="DKK")

    flat = _all_months(0.15)
    drifting = {k: 0.10 + 0.001 * i for i, k in enumerate(sorted(flat))}

    band_flat = valuation_band(
        bars, f, asof=TODAY,
        fx=monthly_fx_series(_FxAdapter({"DKKUSD=X": flat}), "DKK", "USD",
                             start=TODAY - timedelta(days=2000), end=TODAY))
    band_drift = valuation_band(
        bars, f, asof=TODAY,
        fx=monthly_fx_series(_FxAdapter({"DKKUSD=X": drifting}), "DKK", "USD",
                             start=TODAY - timedelta(days=2000), end=TODAY))

    assert band_flat.available and band_drift.available
    # a flat rate leaves the shape alone; a drifting one must not
    assert band_drift.percentile != band_flat.percentile


def test_months_without_a_rate_drop_and_are_counted_honestly():
    bars, _ = _flat_ev_ebit([100.0] * 61)
    f = _fundamentals(ebit=100.0, debt=200.0, cash=50.0,
                      currency="USD", financial_currency="DKK")
    rates = _all_months(0.15)
    # eight months with no rate, taken from INSIDE the band's window (not the newest,
    # which would move the current point, and not the pre-window padding, which the band
    # never values)
    for key in sorted(rates)[-40:-32]:
        del rates[key]
    adapter = _FxAdapter({"DKKUSD=X": rates}, missing=("USDDKK=X",))
    fx = monthly_fx_series(adapter, "DKK", "USD",
                           start=TODAY - timedelta(days=2000), end=TODAY)

    full = valuation_band(bars, f, asof=TODAY,
                          fx=monthly_fx_series(
                              _FxAdapter({"DKKUSD=X": _all_months(0.15)}), "DKK", "USD",
                              start=TODAY - timedelta(days=2000), end=TODAY))
    partial = valuation_band(bars, f, asof=TODAY, fx=fx)

    assert partial.available, partial.note
    assert partial.fx_months_missing > 0
    assert partial.months_covered < full.months_covered   # the gap is REAL, not papered
    assert f"{partial.months_covered} of {partial.months_total} months" in partial.display


def test_a_cross_currency_band_with_no_rates_abstains_naming_the_pairs():
    bars, _ = _flat_ev_ebit([100.0] * 61)
    f = _fundamentals(ebit=100.0, debt=200.0, cash=50.0,
                      currency="USD", financial_currency="DKK")
    adapter = _FxAdapter({}, missing=("DKKUSD=X", "USDDKK=X"))
    fx = monthly_fx_series(adapter, "DKK", "USD",
                           start=TODAY - timedelta(days=2000), end=TODAY)

    band = valuation_band(bars, f, asof=TODAY, fx=fx)
    assert not band.available
    assert "DKKUSD=X" in band.note and "USDDKK=X" in band.note
    assert band.percentile is None                      # nothing mixed


# --------------------------------------------------------------------------- #
# 3. THE FENCE — the band is context and decides nothing
# --------------------------------------------------------------------------- #
def test_conversion_changes_no_verdict_rank_or_screen():
    """VALBAND-2 touches an absolute CONTEXT column only. If this ever fails, the band
    has acquired a vote it must not have."""
    from tests.test_multi_strategy_run import RAW, SCREENED, STRAT_DIR, TODAY as T
    from tests.test_multi_strategy_run import UNIVERSE, _Adapter
    from aristos_council.pipeline import run_multi_strategy_pipeline

    plain = run_multi_strategy_pipeline(
        UNIVERSE, [SCREENED, RAW], strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=T, with_valuation_band=False)
    banded = run_multi_strategy_pipeline(
        UNIVERSE, [SCREENED, RAW], strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=T, with_valuation_band=True)

    for sid in plain.strategy_ids:
        a, b = plain.results[sid], banded.results[sid]
        assert [r.ticker for r in a.ranked] == [r.ticker for r in b.ranked]
        assert [r.verdict for r in a.ranked] == [r.verdict for r in b.ranked]
        assert [r.combined_rank for r in a.ranked] == [r.combined_rank for r in b.ranked]
        assert a.excluded == b.excluded
