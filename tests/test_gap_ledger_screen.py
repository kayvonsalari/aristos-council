"""GAP-LEDGER-1 — the screen's arithmetic and its abstention paths.

The screen is the only place in the package where a number is produced, so these are the
tests that matter most. They come in two halves:

* the maths — the gap, the pre-market window, the relative-volume baseline, the spread;
* the abstentions — the readings that CANNOT be computed, each of which must come back as
  ``passed is None`` with a stated reason and never as a passing or a failing number.

The second half is the one with a live scar behind it. This repo's oldest bug class is
``None`` treated as ``False``: a missing input read as a confirmed failure. In a gap screen
the same mistake would be a name with no pre-market print reading as a 0% gap (a confirmed
"did not move"), or a name with no pre-market history reading as 0x relative volume (a
confirmed "no interest"). Both are wrong, and the second is wrong about the most
interesting case there is.
"""
from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from aristos_council.gap_ledger.bars import IntradayBar, Quote
from aristos_council.gap_ledger.config import DEFAULT_CONFIG, GapConfig, at_ny
from aristos_council.gap_ledger.screen import (baseline_volumes, gap_fraction, last_price,
                                               premarket_window, relative_volume,
                                               screen_one, session_dates, spread_flag,
                                               spread_percent, window_volume)

from .gap_ledger_fakes import intraday_window, prior_sessions, regular_session

DAY = date(2026, 9, 22)                      # a Tuesday
RUN_AT = at_ny(DAY, time(9, 0))


# --------------------------------------------------------------------------- #
# the gap
# --------------------------------------------------------------------------- #
def test_gap_is_premarket_against_previous_close():
    assert gap_fraction(55.0, 50.0) == pytest.approx(0.10)
    assert gap_fraction(45.0, 50.0) == pytest.approx(-0.10)
    assert gap_fraction(50.0, 50.0) == 0.0


@pytest.mark.parametrize("premarket, previous", [(None, 50.0), (55.0, None),
                                                 (55.0, 0.0), (55.0, -1.0)])
def test_gap_abstains_rather_than_inventing_a_number(premarket, previous):
    """A missing or unusable side is None, never 0.0 — 0.0 means "did not move"."""
    assert gap_fraction(premarket, previous) is None


# --------------------------------------------------------------------------- #
# the window
# --------------------------------------------------------------------------- #
def test_window_runs_from_0400_to_the_run_time():
    start, end = premarket_window(DAY, at_ny(DAY, time(9, 0)))
    assert start == at_ny(DAY, time(4, 0))
    assert end == at_ny(DAY, time(9, 0))


def test_window_is_clamped_at_the_open_so_session_volume_is_never_premarket_volume():
    """A run at 11:00 must not count 90 minutes of regular trade as pre-market volume."""
    _start, end = premarket_window(DAY, at_ny(DAY, time(11, 0)))
    assert end == at_ny(DAY, time(9, 30))


def test_window_before_premarket_open_is_empty_and_the_screen_abstains():
    start, end = premarket_window(DAY, at_ny(DAY, time(3, 0)))
    assert start == end
    row = screen_one("AAA", bars=intraday_window(DAY), previous_close=50.0, as_of=DAY,
                     run_at=at_ny(DAY, time(3, 0)))
    assert row.passed is None
    assert "before the 04:00 ET pre-market open" in row.reason


def test_window_volume_is_half_open_so_the_0930_bar_is_the_session_not_premarket():
    bars = [IntradayBar(start=at_ny(DAY, time(9, 25)), open=1, high=1, low=1, close=1,
                        volume=100),
            IntradayBar(start=at_ny(DAY, time(9, 30)), open=1, high=1, low=1, close=1,
                        volume=900)]
    start, end = premarket_window(DAY, at_ny(DAY, time(9, 30)))
    assert window_volume(bars, start, end) == 100


def test_last_price_is_the_last_print_in_the_window():
    bars = intraday_window(DAY, end=time(9, 0), price=50.0)
    bars.append(IntradayBar(start=at_ny(DAY, time(8, 55)), open=52.0, high=52.0, low=52.0,
                            close=52.5, volume=10))
    start, end = premarket_window(DAY, RUN_AT)
    assert last_price(bars, start, end) == 52.5


def test_last_price_is_none_when_the_name_did_not_trade_premarket():
    start, end = premarket_window(DAY, RUN_AT)
    assert last_price(regular_session(DAY), start, end) is None


# --------------------------------------------------------------------------- #
# the relative-volume baseline
# --------------------------------------------------------------------------- #
def test_session_dates_need_a_regular_session_not_just_a_stray_premarket_bar():
    """A partial feed can serve a pre-market bar on a day the market never opened. Counting
    it as a session would pull a zero into the baseline median."""
    yesterday = DAY - timedelta(days=1)
    stray = intraday_window(yesterday, end=time(5, 0))
    assert session_dates(stray, before=DAY) == []
    assert session_dates(stray + regular_session(yesterday), before=DAY) == [yesterday]


def test_baseline_uses_the_same_clock_window_on_each_prior_session():
    """04:00-09:00 is 60 bars of 1,000 shares on each prior day, whatever today does."""
    bars = prior_sessions(before=DAY, count=20, premarket_volume_per_bar=1_000)
    volumes = baseline_volumes(bars, as_of=DAY, window_end=time(9, 0), sessions=20)
    assert len(volumes) == 20
    assert set(volumes) == {60_000}


def test_baseline_takes_only_the_most_recent_sessions_requested():
    bars = prior_sessions(before=DAY, count=30)
    assert len(baseline_volumes(bars, as_of=DAY, window_end=time(9, 0), sessions=20)) == 20


def test_baseline_shrinks_with_the_window_when_the_run_is_early():
    """A 05:00 run compares against 04:00-05:00 on each prior day, not against 04:00-09:00."""
    bars = prior_sessions(before=DAY, count=20, premarket_volume_per_bar=1_000)
    volumes = baseline_volumes(bars, as_of=DAY, window_end=time(5, 0), sessions=20)
    assert set(volumes) == {12_000}          # twelve 5-minute bars


def test_relative_volume_divides_by_the_median_not_the_mean():
    """One earnings morning in the baseline must not raise the bar for the next month."""
    baseline = [1_000] * 19 + [1_000_000]
    ratio, note = relative_volume(5_000, baseline)
    assert ratio == pytest.approx(5.0)
    assert note == ""


def test_relative_volume_abstains_when_the_baseline_median_is_zero():
    """A name that has not traded pre-market in twenty sessions has no computable ratio.
    It must not read as 0x, which would be a CONFIRMED fail on the most interesting case."""
    ratio, note = relative_volume(50_000, [0] * 20)
    assert ratio is None
    assert "baseline is zero" in note


def test_relative_volume_abstains_with_no_prior_sessions():
    ratio, note = relative_volume(50_000, [])
    assert ratio is None
    assert "no prior sessions" in note


def test_zero_volume_today_is_a_real_zero_not_an_abstention():
    ratio, note = relative_volume(0, [1_000] * 20)
    assert ratio == 0.0
    assert note == ""


# --------------------------------------------------------------------------- #
# the spread
# --------------------------------------------------------------------------- #
def test_spread_is_a_percentage_of_the_midpoint():
    assert spread_percent(Quote(bid=99.9, ask=100.1)) == pytest.approx(0.002)


@pytest.mark.parametrize("quote", [None, Quote(), Quote(bid=10.0), Quote(ask=10.0),
                                   Quote(bid=10.0, ask=10.0),      # zero width
                                   Quote(bid=10.0, ask=9.5)])      # crossed
def test_spread_is_unknown_rather_than_tight_when_the_book_is_unusable(quote):
    """A zero-width or crossed book is a stale quote, not a free round trip. Reporting it as
    0% would mislead in the expensive direction."""
    assert spread_percent(quote) is None
    assert spread_flag(spread_percent(quote)) == "spread unknown"


def test_wide_spread_is_marked_and_tight_is_marked_too():
    assert "wide spread" in spread_flag(0.02)
    assert spread_flag(0.0005) == "spread ok"


def test_a_missing_or_wide_spread_never_drops_the_name():
    """Both readings are marks. The gap and the volume decide; the spread only describes."""
    bars = (intraday_window(DAY, end=time(9, 0), price=55.0, volume_per_bar=10_000)
            + prior_sessions(before=DAY, count=20, premarket_volume_per_bar=1_000))
    for quote in (None, Quote(bid=50.0, ask=60.0)):
        row = screen_one("AAA", bars=bars, previous_close=50.0, as_of=DAY, run_at=RUN_AT,
                         quote=quote)
        assert row.passed is True, row.reason


# --------------------------------------------------------------------------- #
# one name, end to end
# --------------------------------------------------------------------------- #
def _bars_for(*, premarket_price: float, premarket_volume: int,
              baseline_volume: int = 1_000) -> list[IntradayBar]:
    return (intraday_window(DAY, end=time(9, 0), price=premarket_price,
                            volume_per_bar=premarket_volume)
            + prior_sessions(before=DAY, count=20,
                             premarket_volume_per_bar=baseline_volume))


def test_a_clean_candidate_passes_with_both_readings():
    row = screen_one("AAA", bars=_bars_for(premarket_price=55.0, premarket_volume=10_000),
                     previous_close=50.0, as_of=DAY, run_at=RUN_AT)
    assert row.passed is True
    assert row.gap == pytest.approx(0.10)
    assert row.relative_volume == pytest.approx(10.0)
    assert row.baseline_sessions == 20
    assert row.direction == 1


def test_a_small_gap_is_a_confirmed_fail_not_an_abstention():
    row = screen_one("AAA", bars=_bars_for(premarket_price=50.5, premarket_volume=10_000),
                     previous_close=50.0, as_of=DAY, run_at=RUN_AT)
    assert row.passed is False
    assert "inside" in row.reason
    assert row.gap == pytest.approx(0.01)


def test_thin_volume_on_a_real_gap_is_a_confirmed_fail():
    row = screen_one("AAA", bars=_bars_for(premarket_price=55.0, premarket_volume=1_000),
                     previous_close=50.0, as_of=DAY, run_at=RUN_AT)
    assert row.passed is False
    assert "below 3.0x" in row.reason


def test_a_gap_with_no_computable_baseline_is_kept_and_marked_not_failed():
    """Gapped 10%, but has no pre-market baseline to compare against. The ratio is a MISSING
    reading, so rule 3 forbids it acting as a confirmed fail: the name is kept and MARKED.
    ``test_gap_ledger_volume_gap.py`` holds the whole policy, including the
    ``require_relative_volume`` setting that abstains instead."""
    bars = intraday_window(DAY, end=time(9, 0), price=55.0, volume_per_bar=10_000)
    for day_back in range(1, 21):
        bars += regular_session(DAY - timedelta(days=day_back))
    row = screen_one("AAA", bars=bars, previous_close=50.0, as_of=DAY, run_at=RUN_AT)
    assert row.passed is True
    assert row.relative_volume is None
    assert "baseline is zero" in row.relative_volume_note
    assert row.gap == pytest.approx(0.10)    # the gap IS still recorded


def test_no_bars_at_all_abstains():
    row = screen_one("AAA", bars=[], previous_close=50.0, as_of=DAY, run_at=RUN_AT)
    assert row.passed is None
    assert "no intraday bars" in row.reason


def test_no_premarket_print_abstains_rather_than_reading_as_a_flat_gap():
    row = screen_one("AAA", bars=regular_session(DAY), previous_close=50.0, as_of=DAY,
                     run_at=RUN_AT)
    assert row.passed is None
    assert row.gap is None
    assert "no pre-market trade" in row.reason


def test_no_previous_close_abstains():
    row = screen_one("AAA", bars=_bars_for(premarket_price=55.0, premarket_volume=10_000),
                     previous_close=None, as_of=DAY, run_at=RUN_AT)
    assert row.passed is None
    assert "previous close" in row.reason


def test_a_gap_down_is_screened_the_same_way_as_a_gap_up():
    row = screen_one("AAA", bars=_bars_for(premarket_price=45.0, premarket_volume=10_000),
                     previous_close=50.0, as_of=DAY, run_at=RUN_AT)
    assert row.passed is True
    assert row.gap == pytest.approx(-0.10)
    assert row.direction == -1


def test_thresholds_come_from_the_config_not_from_the_code():
    strict = GapConfig(min_abs_gap=0.15, min_relative_volume=DEFAULT_CONFIG.min_relative_volume)
    row = screen_one("AAA", bars=_bars_for(premarket_price=55.0, premarket_volume=10_000),
                     previous_close=50.0, as_of=DAY, run_at=RUN_AT, config=strict)
    assert row.passed is False
    assert "15.0%" in row.reason
