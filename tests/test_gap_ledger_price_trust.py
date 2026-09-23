"""GAP-PRICE-TRUST-1 — a gap is only as good as the print behind it.

The first full run produced **98 candidates and roughly 80 were junk**: XEL +11%, LNT +12%
on no news, dozens of spreads at 40-57%. With no pre-market volume published, one odd print
reads as a gap and nothing contradicts it.

**The spread is not the test.** ALNY showed a 1.99% spread and was junk — it opened 21% away
from its pre-market price — while VKTX showed 7.28% and was the day's most genuine mover. So
the spread is a deliberately loose backstop at 10%.

**What separates them is TAPE DENSITY**, measured on the twelve names the amendment named,
against the live 2026-09-22 tape. yfinance OMITS a five-minute slot in which nothing traded
rather than forward-filling it, so the number of bars in a window IS the number of printed
intervals:

===========  ==========================  =====================
verdict      bars in the final 30 min    bars in the 5h window
===========  ==========================  =====================
junk (7)     1, 1, 1, 1, 1, 2, 3         2 - 13
genuine (5)  6, 6, 6, 6, 6               55 - 60
===========  ==========================  =====================

Six is the ceiling for a 30-minute window, so every genuine mover printed in EVERY slot of
the final half hour. This is effectively a volume proxy — the leg the provider will not serve.

Two things this file exists to stop coming back:

* **counting DISTINCT PRICES instead of prints.** The first cut did, on the theory that a
  quiet tape is forward-filled into many identical bars. It is not. XEL's stray +11% arrived
  as five bars with five different prices, so that rule trusted it — and a live check on these
  twelve names kept all seven junk names and rejected all five real ones.
* **trusting DRIFT on its own.** Five of the seven junk names scored 0.000% drift, because a
  one-bar window agrees with itself perfectly, while genuine VKTX scored 2.797% and ONON
  1.128%. The amendment's 1% limit would have rejected two real movers and kept five strays.

Every failure is a NOT-EVALUATED **marking**, never a rejection, each with its own reason so
the CSV says which test fired. An untrusted name may not enter the control group: an
unbelievable price is a missing reading about a name, not a finding about it.
"""
from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from aristos_council.gap_ledger.bars import IntradayBar, Quote
from aristos_council.gap_ledger.config import DEFAULT_CONFIG, GapConfig, at_ny
from aristos_council.gap_ledger.ledger import GROUP_CANDIDATE, LedgerRow, read_day, write_day
from aristos_council.gap_ledger.outcomes import fill_day, premarket_vs_open
from aristos_council.gap_ledger.run import format_report, run_screen
from aristos_council.gap_ledger.screen import (PRICE_NOT_CONFIRMED, PRICE_SINGLE_PRINT,
                                               PRICE_WIDE_SPREAD, confirm_prints,
                                               confirmation_average, confirmed,
                                               is_price_untrusted, premarket_prints,
                                               premarket_window, price_trust, screen_one)

from .gap_ledger_fakes import (FakeBars, daily_series, intraday_window, prior_sessions,
                               regular_session, sparse_window)

DAY = date(2026, 9, 22)
RUN_AT = at_ny(DAY, time(9, 0))
START, END = premarket_window(DAY, RUN_AT)


def _bar(moment: time, price: float, volume: int = 0) -> IntradayBar:
    return IntradayBar(start=at_ny(DAY, moment), open=price, high=price, low=price,
                       close=price, volume=volume)


def _dense(price: float):
    """A tape a real mover leaves: every five-minute slot printed, no volume (the provider
    publishes none pre-market)."""
    return intraday_window(DAY, end=time(9, 0), price=price, volume_per_bar=0)


def _history():
    """Twenty prior sessions with NO pre-market volume — the real provider's shape, so the
    relative-volume leg abstains and the TRUST gate is what decides."""
    return prior_sessions(before=DAY, count=20, premarket_volume_per_bar=0)


# --------------------------------------------------------------------------- #
# the thresholds, and why they are what they are
# --------------------------------------------------------------------------- #
def test_the_spread_limit_is_loose_and_is_not_the_decisive_test():
    """ALNY was junk at 1.99% and VKTX genuine at 7.28%, so a 2% limit would have kept the
    junk and dropped the best name of the day."""
    assert DEFAULT_CONFIG.max_trusted_spread == 0.10
    assert DEFAULT_CONFIG.wide_spread < 0.0728 < DEFAULT_CONFIG.max_trusted_spread


def test_the_decisive_threshold_is_density_and_it_clears_the_measured_gap():
    """Junk reached 3 bars in the final half hour at worst; every genuine name reached 6."""
    assert DEFAULT_CONFIG.min_confirm_prints == 4
    assert 3 < DEFAULT_CONFIG.min_confirm_prints < 6
    assert DEFAULT_CONFIG.confirm_window_minutes == 30


def test_the_drift_limit_is_a_loose_backstop_not_the_test():
    """1% would have rejected VKTX (2.797%) and ONON (1.128%), both genuine."""
    assert DEFAULT_CONFIG.max_confirm_drift == 0.05
    assert DEFAULT_CONFIG.max_confirm_drift > 0.02797


# --------------------------------------------------------------------------- #
# prints are INTERVALS, not distinct prices
# --------------------------------------------------------------------------- #
def test_prints_are_counted_as_printed_intervals():
    assert premarket_prints(_dense(100.0), START, END) == 60      # 5h of 5-minute slots
    assert premarket_prints(sparse_window(DAY), START, END) == 3


def test_a_sparse_tape_with_differing_prices_is_still_sparse():
    """The regression that matters: XEL's five bars carried five DIFFERENT prices, so a
    distinct-price rule trusted it."""
    scattered = [_bar(time(4, 0), 71.85), _bar(time(5, 30), 74.10),
                 _bar(time(7, 0), 77.40), _bar(time(8, 40), 79.83)]
    assert len({b.close for b in scattered}) == 4                 # four distinct prices...
    assert premarket_prints(scattered, START, END) == 4           # ...and only four prints
    assert confirm_prints(scattered, END, minutes=30) == 1        # one in the final half hour


def test_an_empty_window_has_no_prints():
    assert premarket_prints([], START, END) == 0
    assert confirm_prints([], END, minutes=30) == 0


def test_only_the_final_window_counts_towards_confirmation():
    bars = [_bar(time(4, 5), 50.0), _bar(time(8, 45), 100.0), _bar(time(8, 55), 101.0)]
    assert confirm_prints(bars, END, minutes=30) == 2


# --------------------------------------------------------------------------- #
# the live twelve, as regressions
# --------------------------------------------------------------------------- #
JUNK_SHAPES = {
    # ticker: (gap, the bars it actually printed in the final half hour)
    "XEL": (0.11, 1), "LNT": (0.12, 1), "BGC": (-0.23, 1), "IRDM": (-0.25, 1),
    "LKQ": (0.26, 1), "ALNY": (0.28, 2), "ROIV": (-0.28, 3),
}


@pytest.mark.parametrize("ticker", sorted(JUNK_SHAPES))
def test_every_junk_name_from_the_live_run_abstains(ticker):
    gap, final_bars = JUNK_SHAPES[ticker]
    previous = 100.0
    price = previous * (1 + gap)
    # a scattered early tape, plus however many bars it printed inside the final half hour
    bars = ([_bar(time(4, 0), price), _bar(time(6, 30), price)]
            + [_bar(time(8, 35 + 5 * n), price) for n in range(final_bars)]
            + _history())
    row = screen_one(ticker, bars=bars, previous_close=previous, as_of=DAY, run_at=RUN_AT)
    assert row.passed is None, f"{ticker} should abstain, not be a candidate"
    assert row.reason in {PRICE_SINGLE_PRINT, PRICE_NOT_CONFIRMED}
    assert row.gap == pytest.approx(gap)     # the gap is still RECORDED, for the diagnostic


@pytest.mark.parametrize("ticker, gap", [("VKTX", 0.2298), ("ONON", 0.1158),
                                         ("SHOP", 0.0581), ("GME", 0.0370),
                                         ("CCL", 0.0316)])
def test_every_genuine_mover_from_the_live_run_is_a_candidate(ticker, gap):
    previous = 100.0
    row = screen_one(ticker, bars=_dense(previous * (1 + gap)) + _history(),
                     previous_close=previous, as_of=DAY, run_at=RUN_AT)
    assert row.passed is True, f"{ticker} should be a candidate, not {row.reason!r}"
    assert row.confirm_prints == 6
    assert row.gap == pytest.approx(gap)


def test_a_single_print_reports_the_print_test():
    row = screen_one("AAA", bars=[_bar(time(8, 55), 126.0)] + _history(),
                     previous_close=100.0, as_of=DAY, run_at=RUN_AT)
    assert row.reason == PRICE_SINGLE_PRINT
    assert row.premarket_prints == 1


def test_a_sparse_final_window_reports_the_confirmation_test():
    row = screen_one("XEL", bars=sparse_window(DAY, price=111.0) + _history(),
                     previous_close=100.0, as_of=DAY, run_at=RUN_AT)
    assert row.reason == PRICE_NOT_CONFIRMED
    assert (row.premarket_prints or 0) >= 2      # not the single-print case
    assert (row.confirm_prints or 0) < DEFAULT_CONFIG.min_confirm_prints


# --------------------------------------------------------------------------- #
# the confirmation average
# --------------------------------------------------------------------------- #
def test_the_confirmation_average_is_a_simple_mean_when_volume_is_unpublished():
    """A VWAP with a zero denominator is not a small number, it is not a number."""
    bars = [_bar(time(8, 40), 100.0), _bar(time(8, 50), 102.0), _bar(time(8, 55), 104.0)]
    assert confirmation_average(bars, END, minutes=30) == pytest.approx(102.0)


def test_the_confirmation_average_is_volume_weighted_where_volume_exists():
    bars = [_bar(time(8, 40), 100.0, volume=1), _bar(time(8, 50), 110.0, volume=9)]
    assert confirmation_average(bars, END, minutes=30) == pytest.approx(109.0)


def test_an_empty_final_window_cannot_confirm_or_deny():
    assert confirmation_average([_bar(time(4, 5), 50.0)], END, minutes=30) is None
    assert confirmed(50.0, None) is None


def test_a_dense_tape_whose_last_print_spikes_away_is_not_confirmed():
    """The drift backstop earning its keep: six prints, and the last one absurd."""
    bars = ([_bar(time(8, m), 110.0) for m in (30, 35, 40, 45, 50)]
            + [_bar(time(8, 55), 160.0)] + _history())
    row = screen_one("AAA", bars=bars, previous_close=100.0, as_of=DAY, run_at=RUN_AT)
    assert row.passed is None
    assert row.reason == PRICE_NOT_CONFIRMED
    assert row.confirm_prints == 6            # dense, so it is the DRIFT that fired


def test_a_genuine_mover_drifting_within_the_backstop_is_confirmed():
    """VKTX drifted 2.797% through its final half hour and was the best name of the day."""
    bars = ([_bar(time(8, 30), 38.50), _bar(time(8, 35), 39.19), _bar(time(8, 40), 38.85),
             _bar(time(8, 45), 38.00), _bar(time(8, 50), 37.00), _bar(time(8, 55), 37.03)]
            + _history())
    row = screen_one("VKTX", bars=bars, previous_close=30.0, as_of=DAY, run_at=RUN_AT)
    assert row.passed is True, row.reason


# --------------------------------------------------------------------------- #
# the spread backstop
# --------------------------------------------------------------------------- #
def test_a_spread_over_the_limit_abstains_with_its_own_reason():
    row = screen_one("AAA", bars=_dense(110.0) + _history(), previous_close=100.0,
                     as_of=DAY, run_at=RUN_AT, quote=Quote(bid=100.0, ask=130.0))
    assert row.passed is None
    assert row.reason == PRICE_WIDE_SPREAD


def test_vktx_at_7_percent_is_still_trusted():
    row = screen_one("VKTX", bars=_dense(110.0) + _history(), previous_close=100.0,
                     as_of=DAY, run_at=RUN_AT, quote=Quote(bid=96.4, ask=103.6))
    assert row.passed is True, row.reason


def test_alny_at_2_percent_is_not_rescued_by_its_tight_spread():
    """It showed 1.99% and opened 21% away. Density is what catches it."""
    row = screen_one("ALNY", bars=sparse_window(DAY, price=128.0) + _history(),
                     previous_close=100.0, as_of=DAY, run_at=RUN_AT,
                     quote=Quote(bid=126.7, ask=129.3))
    assert row.passed is None
    assert row.reason == PRICE_NOT_CONFIRMED


def test_a_missing_spread_is_never_a_trust_failure():
    verdict = price_trust(_dense(110.0), price=110.0, start=START, end=END, spread=None)
    assert verdict.trusted


def test_the_spread_limit_is_configurable():
    row = screen_one("VKTX", bars=_dense(110.0) + _history(), previous_close=100.0,
                     as_of=DAY, run_at=RUN_AT, quote=Quote(bid=96.4, ask=103.6),
                     config=GapConfig(max_trusted_spread=0.01))
    assert row.reason == PRICE_WIDE_SPREAD


# --------------------------------------------------------------------------- #
# the reasons, and what counts as a trust failure
# --------------------------------------------------------------------------- #
def test_the_three_reasons_are_distinct_strings():
    assert len({PRICE_SINGLE_PRINT, PRICE_NOT_CONFIRMED, PRICE_WIDE_SPREAD}) == 3


def test_the_print_test_is_reported_before_the_spread_test():
    """Cheapest and most decisive first, so the CSV names the real fault."""
    verdict = price_trust([_bar(time(8, 55), 100.0)], price=100.0, start=START, end=END,
                          spread=0.40)
    assert verdict.reason == PRICE_SINGLE_PRINT


def test_is_price_untrusted_recognises_only_the_trust_reasons():
    assert is_price_untrusted(screen_one("AAA", bars=sparse_window(DAY, price=110.0)
                                        + _history(), previous_close=100.0, as_of=DAY,
                                        run_at=RUN_AT))
    # A relative-volume abstention is NOT a trust failure: the first fetch pass abstains on
    # it by construction, and reading that as untrustworthy stopped every name reaching the
    # second pass once.
    no_history = screen_one("AAA", bars=_dense(110.0), previous_close=100.0, as_of=DAY,
                            run_at=RUN_AT, config=GapConfig(require_relative_volume=True))
    assert no_history.passed is None
    assert not is_price_untrusted(no_history)


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
def _world() -> FakeBars:
    daily = {t: daily_series(end=DAY, sessions=300, close=100.0, volume=3_000_000)
             for t in ("VKTX", "XEL", "QUIET")}
    return FakeBars(daily=daily, intraday={
        "VKTX": _dense(110.0) + _history(),
        "XEL": sparse_window(DAY, price=111.0) + _history(),
        "QUIET": _dense(100.2) + _history(),
    })


def _run(tmp_path, **kwargs):
    bars = _world()
    return bars, run_screen(pool=["VKTX", "XEL", "QUIET"], pool_source="t", daily=bars,
                            intraday=bars, day=DAY, run_at=RUN_AT, root=tmp_path, **kwargs)


def test_only_the_genuine_mover_is_a_candidate(tmp_path):
    _bars, result = _run(tmp_path)
    assert [r.ticker for r in result.candidates] == ["VKTX"]
    assert ("XEL", PRICE_NOT_CONFIRMED) in result.not_evaluated


def test_an_untrusted_name_never_reaches_the_volume_fetch(tmp_path):
    bars, _result = _run(tmp_path)
    second_pass = [call for call in bars.intraday_calls if call[1] < DAY]
    assert second_pass, "expected a history fetch"
    assert "XEL" not in set(second_pass[0][0])
    assert "VKTX" in set(second_pass[0][0])


def test_the_control_group_never_draws_an_untrusted_name(tmp_path):
    """An unbelievable price is a missing reading about a name, not a finding about it."""
    _bars, result = _run(tmp_path)
    assert result.baseline == ["QUIET"]
    assert "XEL" not in result.baseline


def test_the_csv_records_which_test_fired_and_the_readings_behind_it(tmp_path):
    _bars, _result = _run(tmp_path, write=True)
    rows = {r.ticker: r for r in read_day(DAY, root=tmp_path)}
    assert rows["VKTX"].premarket_prints == 60
    assert rows["VKTX"].confirm_prints == 6
    assert rows["VKTX"].confirm_average is not None
    assert rows["VKTX"].cfg_max_trusted_spread == pytest.approx(0.10)
    assert rows["VKTX"].cfg_min_confirm_prints == 4


def test_the_report_states_the_trust_thresholds_and_the_no_print_count(tmp_path):
    bars = _world()
    bars.intraday["DARK"] = regular_session(DAY - timedelta(days=1))
    bars.daily["DARK"] = daily_series(end=DAY, sessions=300, close=100.0, volume=3_000_000)
    result = run_screen(pool=["VKTX", "XEL", "QUIET", "DARK"], pool_source="t", daily=bars,
                        intraday=bars, day=DAY, run_at=RUN_AT, root=tmp_path)
    text = format_report(result)
    assert "price trust" in text
    assert "4 of them in the final 30min" in text
    assert "within 5% of their average" in text
    assert "spread <= 10%" in text
    assert result.no_premarket_trade == 1
    assert "1 of 4 had no pre-market print at all" in text


# --------------------------------------------------------------------------- #
# the outcomes diagnostic — how the thresholds get tuned
# --------------------------------------------------------------------------- #
def test_the_diagnostic_is_the_premarket_price_against_the_open():
    """AMD on 2026-09-21: 579.36 pre-market, 583.88 open — the print was real."""
    assert premarket_vs_open(LedgerRow(premarket_price=579.36, open_price=583.88)) \
        == pytest.approx((579.36 - 583.88) / 583.88)


def test_the_diagnostic_is_signed_so_the_direction_is_legible():
    assert premarket_vs_open(LedgerRow(premarket_price=121.0, open_price=100.0)) \
        == pytest.approx(0.21)               # the print was 21% ABOVE the open — ALNY
    assert premarket_vs_open(LedgerRow(premarket_price=79.0, open_price=100.0)) \
        == pytest.approx(-0.21)


@pytest.mark.parametrize("row", [LedgerRow(premarket_price=None, open_price=100.0),
                                 LedgerRow(premarket_price=100.0, open_price=None),
                                 LedgerRow(premarket_price=100.0, open_price=0.0)])
def test_the_diagnostic_is_missing_rather_than_zero_when_a_side_is_absent(row):
    """0.0 would read as perfect agreement that was never measured."""
    assert premarket_vs_open(row) is None


def test_outcomes_fills_the_diagnostic(tmp_path):
    write_day(DAY, [LedgerRow(date=DAY.isoformat(), ticker="AAA", group=GROUP_CANDIDATE,
                              gap_pct=0.10, premarket_price=110.0)], root=tmp_path)
    bars = FakeBars(daily={"AAA": daily_series(end=DAY + timedelta(days=1), sessions=1,
                                              close=100.0)},
                    intraday={"AAA": regular_session(DAY, price=100.0)})
    fill_day(DAY, daily=bars, intraday=bars, root=tmp_path, now=at_ny(DAY, time(16, 30)))
    row = read_day(DAY, root=tmp_path)[0]
    assert row.open_price == pytest.approx(100.0)
    assert row.premarket_vs_open == pytest.approx(0.10)


def test_a_csv_written_before_the_column_existed_still_loads(tmp_path):
    """Old records must not become unreadable because a diagnostic was added."""
    path = tmp_path / f"{DAY.isoformat()}.csv"
    path.write_text("date,ticker,group,gap_pct,open_price\n"
                    f"{DAY.isoformat()},AAA,{GROUP_CANDIDATE},0.08,100.0\n",
                    encoding="utf-8")
    rows = read_day(DAY, root=tmp_path)
    assert len(rows) == 1
    assert rows[0].gap_pct == pytest.approx(0.08)
    assert rows[0].premarket_vs_open is None     # absent, not zero
    assert rows[0].premarket_prints is None
    assert rows[0].confirm_prints is None
