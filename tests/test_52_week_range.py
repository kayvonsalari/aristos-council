"""PART A — the 12-month low/high, pinned against the three ways they could go wrong.

REPORTED: "12-month" ranges wider than 60% on 13 of 27 names (INTC $24.00 -> $140.94),
with several names sitting exactly at their stated high.

DIAGNOSED (live, 2026-08-24, against the real adapter — see the done-report):
  (a) NOT the 5-year series. The 400-day fetch returned 275 bars, 2025-07-21 -> 2026-08-21
      (1.08y); the band's separate 5-year fetch returned 1260 bars over 5.01y. The price
      context consumed the 400-day one.
  (b) The trailing-52-week cutoff IS applied: 251 of those 275 bars, 2025-08-22 ->
      2026-08-21, weeks_covered 52.0. Excluding the older bars moved INTC's low from
      $19.31 (whole 400d) to $24.00 (trailing 52w) — the cutoff demonstrably fires.
  (c) No field mixing: low, high and the current close all read PriceBar.close. For NVDA,
      where close and adj_close differ, the rendered $165.17/$235.74 match close and NOT
      adj_close ($164.98/$235.47).

The ranges were CORRECT: INTC's own close path over those 52 weeks runs
24.35 -> 33.55 -> 46.47 -> 94.48 -> 139.63 -> 90.07 with no split-shaped discontinuity
(largest single day +23.6%), and MRK's 52-week high genuinely falls on the last bar.

So this module changes nothing and guards everything: each of the three failure modes now
has a test that would catch it if it ever appeared.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from aristos_council.data.adapter import Fundamentals, MarketDataAdapter, PriceBar, PriceHistory
from aristos_council.factors import gather_factor_inputs
from aristos_council.tools.price_context import MIN_52W_WEEKS, price_context

TODAY = date(2026, 6, 30)


def _daily(closes: list[float], *, end: date = TODAY, adj_factor: float = 1.0
           ) -> list[PriceBar]:
    """One bar per calendar day, newest last. ``adj_factor`` scales adj_close away from
    close, so a test can tell WHICH field a number came from."""
    n = len(closes)
    return [PriceBar(day=end - timedelta(days=(n - 1 - i)), open=c, high=c, low=c,
                     close=c, adj_close=c * adj_factor, volume=1)
            for i, c in enumerate(closes)]


# --------------------------------------------------------------------------- #
# (a) The window is the trailing 52 weeks — never the whole series
# --------------------------------------------------------------------------- #
def test_five_years_of_bars_yield_a_range_from_the_last_52_weeks_only():
    """The reported symptom's shape: a multi-year series must NOT produce a multi-year
    "12-month" range. The old extremes sit far outside the window and must not appear."""
    old = [10.0] * 1000 + [500.0] * 300        # ~3.5y of history, wild extremes
    recent = [100.0] * 300 + [140.0] + [80.0] + [120.0] * 64   # a FULL trailing year
    ctx = price_context(_daily(old + recent), currency="USD")

    assert ctx.range_available
    assert ctx.low_52w == 80.0 and ctx.high_52w == 140.0
    assert ctx.low_52w != 10.0 and ctx.high_52w != 500.0       # the old extremes: gone
    assert ctx.weeks_covered == pytest.approx(52.0, abs=0.2)


def test_the_window_is_anchored_on_the_last_close_not_on_the_whole_series():
    """262 days of history: only the last 364 count, so the 60 oldest bars — which hold
    the series minimum — are excluded."""
    closes = [1.0] * 60 + [100.0] * 400
    ctx = price_context(_daily(closes), currency="USD")
    assert ctx.low_52w == 100.0                                # not 1.0
    assert ctx.closes_in_window == 365                         # 364 days back, inclusive


def test_widening_the_window_would_change_the_answer_so_the_cutoff_demonstrably_fires():
    """Guards against a silent fallback to "all available bars": the same series scored
    over its whole length gives a DIFFERENT low, so a passing test above cannot be an
    accident of the fixture."""
    closes = [50.0] * 100 + [100.0] * 400
    bars = _daily(closes)
    windowed = price_context(bars, currency="USD")
    whole = price_context(bars, weeks=500, currency="USD")     # deliberately widened
    assert windowed.low_52w == 100.0
    assert whole.low_52w == 50.0
    assert windowed.low_52w != whole.low_52w


# --------------------------------------------------------------------------- #
# (c) ONE price field for all three numbers
# --------------------------------------------------------------------------- #
def test_low_high_and_current_close_all_read_the_same_price_field():
    """An adj_close low against a raw current close would make a name look far cheaper
    than it traded. The fixture's adj_close is deliberately 0.5x the close, so any number
    sourced from the wrong field is unmistakable."""
    closes = [100.0] * 300 + [140.0] + [80.0] + [120.0] * 64
    bars = _daily(closes, adj_factor=0.5)

    ctx = price_context(bars, currency="USD")
    assert ctx.last_close == 120.0                             # close, not 60.0
    assert ctx.high_52w == 140.0                               # close, not 70.0
    assert ctx.low_52w == 80.0                                 # close, not 40.0
    # ...and every one of them is drawn from PriceBar.close specifically.
    window = [b for b in bars if b.day >= bars[-1].day - timedelta(weeks=52)]
    assert ctx.last_close == window[-1].close
    assert ctx.high_52w == max(b.close for b in window)
    assert ctx.low_52w == min(b.close for b in window)


def test_the_rendered_line_never_mixes_a_raw_price_with_an_adjusted_one():
    closes = [90.0] * 300 + [100.0]
    ctx = price_context(_daily(closes, adj_factor=0.5), currency="USD")
    line = ctx.display
    assert "$100.00" in line and "$90.00" in line
    for adjusted in ("$50.00", "$45.00"):                      # the adj_close values
        assert adjusted not in line


# --------------------------------------------------------------------------- #
# A name genuinely AT its 52-week high — not an artefact of a short window
# --------------------------------------------------------------------------- #
def test_a_name_whose_close_genuinely_is_the_52_week_high_renders_correctly():
    """Several reported names sat exactly at their stated high. That is what a stock at a
    52-week high looks like — the test proves it is real by giving the fixture a FULL 52
    weeks of lower prices behind it."""
    closes = [80.0] * 300 + [90.0] * 60 + [150.0]              # rises to a new high today
    ctx = price_context(_daily(closes), currency="USD")

    assert ctx.high_52w == ctx.last_close == 150.0
    assert ctx.position_pct == 100.0
    assert ctx.weeks_covered >= MIN_52W_WEEKS                  # a FULL window, not a stub
    assert ctx.low_52w == 80.0


def test_a_high_that_is_only_the_high_because_the_window_was_truncated_cannot_happen():
    """The inverse guard: a series whose true 52-week high sits EARLY in the window still
    reports that high, so "today is the high" is never manufactured by dropping bars."""
    closes = [100.0] * 40 + [200.0] + [100.0] * 320 + [150.0]
    ctx = price_context(_daily(closes), currency="USD")
    assert ctx.high_52w == 200.0                               # the early peak, kept
    assert ctx.last_close == 150.0
    assert ctx.position_pct == pytest.approx(50.0)


# --------------------------------------------------------------------------- #
# (b) Short history abstains with its REAL span — never a widened window
# --------------------------------------------------------------------------- #
def test_short_history_abstains_with_its_true_span_rather_than_widening():
    closes = [100.0] * 210                                     # 29.9 weeks
    ctx = price_context(_daily(closes), currency="USD")

    assert ctx.available and ctx.last_close == 100.0           # the price still renders
    assert not ctx.range_available
    assert ctx.low_52w is None and ctx.high_52w is None
    assert "only 30 weeks of closes" in ctx.range_display


def test_the_40_week_floor_is_the_boundary_and_nothing_fills_the_column_below_it():
    # n bars span n-1 days, so exactly 40 weeks of SPAN needs 40*7 + 1 bars.
    assert price_context(_daily([100.0] * (40 * 7 + 1)), currency="USD").range_available
    just_short = price_context(_daily([100.0] * (40 * 7)), currency="USD")
    assert not just_short.range_available
    assert just_short.high_52w is None                         # not silently widened


# --------------------------------------------------------------------------- #
# The price context reads the SAME series the momentum legs do, and nothing else
# --------------------------------------------------------------------------- #
class _TwoWindowAdapter(MarketDataAdapter):
    """Serves a DIFFERENT series per requested window, so a test can prove which one the
    price context consumed: the 400-day window carries 100-140, the 5-year window carries
    a 10-500 range that must never reach the 12-month columns."""

    name = "fake-two-window"

    def __init__(self):
        self.windows: list[int] = []

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, currency="USD",
                            market_cap=1e10, quote_type="EQUITY")

    def get_price_history(self, ticker, *, start, end):
        days = (end - start).days
        self.windows.append(days)
        if days > 800:                                   # the band's 5-year request
            closes = [10.0] * 900 + [500.0] * 400 + [120.0] * 200
        else:                                            # the 400-day ranking request
            closes = [100.0] * 300 + [140.0] + [80.0] + [120.0] * 64
        return PriceHistory(ticker=ticker, bars=_daily(closes, end=TODAY))

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_the_price_context_consumes_the_400_day_series_not_the_bands_five_year_one():
    """The failure mode the VALBAND incident already cost two debugging rounds, in
    reverse: the band's long series must never be what the 12-month columns are built
    from — even though both fetches happen for the same name in the same call."""
    adapter = _TwoWindowAdapter()
    fi = gather_factor_inputs(adapter, "X", today=TODAY, with_valuation_band=True)
    ctx = fi.price_context

    assert max(adapter.windows) > 800                    # the 5-year fetch DID happen...
    assert min(adapter.windows) <= 400                   # ...alongside the 400-day one
    assert ctx.high_52w == 140.0 and ctx.low_52w == 80.0  # ...and the SHORT one was used
    assert ctx.high_52w != 500.0 and ctx.low_52w != 10.0


def test_asking_for_the_band_does_not_change_the_12_month_range():
    """Regression: the range is a property of the name, not of which optional columns the
    run asked for."""
    off = gather_factor_inputs(_TwoWindowAdapter(), "X", today=TODAY,
                               with_valuation_band=False).price_context
    on = gather_factor_inputs(_TwoWindowAdapter(), "X", today=TODAY,
                              with_valuation_band=True).price_context
    assert (off.low_52w, off.high_52w, off.last_close) == \
        (on.low_52w, on.high_52w, on.last_close)
