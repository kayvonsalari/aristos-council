"""GAP-EARLY-CHECKPOINT-1 — is acting earlier in the pre-market worth anything?

The run logs WHEN each IBKR-verified candidate's move first showed, and the record scores acting
at that moment against acting at the open. Five properties, each with a way of being wrong that
would flatter the earlier entry:

* the signal needs the PRICE and the VOLUME together — a price that moved on nothing is the stray
  print, and a busy bar that has not moved is not the move;
* the signal time is when the bar CLOSED, and a bar still forming at the run is not used —
  otherwise the record would "act" on information that did not exist yet;
* a usual volume of ZERO is not-evaluated, never a pass — 3 x 0 = 0 would let one 100-share print
  become the signal;
* a yfinance-only row carries none of it, and says so rather than guessing — that provider has no
  pre-market volume;
* a "move to 09:00" that starts after 09:00 is not a move.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, timedelta

import pytest

from aristos_council.gap_ledger.bars import IntradayBar, Quote
from aristos_council.gap_ledger.config import DEFAULT_CONFIG, GapConfig, at_ny
from aristos_council.gap_ledger.early import (NO_DIRECTION, NO_HISTORY, early_reading,
                                              first_signal, path_prices)
from aristos_council.gap_ledger.ledger import (FIELDS, GROUP_CANDIDATE, LedgerRow, ledger_path,
                                               read_day, write_day)
from aristos_council.gap_ledger.outcomes import directional_move, fill_row, signal_moves
from aristos_council.gap_ledger.run import run_screen
from aristos_council.gap_ledger.score import early_score, score
from aristos_council.gap_ledger.verify import SOURCE_IBKR, SOURCE_YFINANCE
from aristos_council.gap_ledger.viewer import (NO_EARLY_DATA, details_of, early_markdown,
                                               first_signal_of, path_markdown, price_path)

from .gap_ledger_fakes import (FakeBars, daily_series, intraday_window, prior_sessions,
                               regular_session)

DAY = date(2026, 9, 22)
RUN_AT = at_ny(DAY, time(9, 0))
PREV = 100.0


def _bar(moment: time, price: float, volume: int = 1_000, *, day: date = DAY) -> IntradayBar:
    return IntradayBar(start=at_ny(day, moment), open=price, high=price, low=price,
                       close=price, volume=volume)


def _history(today: list, *, baseline_volume: int = 1_000) -> list:
    """Today's bars plus twenty prior sessions whose every 5-minute slot holds
    ``baseline_volume`` shares."""
    return sorted(today + prior_sessions(before=DAY, count=20,
                                         premarket_volume_per_bar=baseline_volume),
                  key=lambda b: b.start)


def _quiet_until(moment: time, price: float = 100.0, volume: int = 1_000) -> list:
    """A dense tape at ``price`` from 04:00 up to (not including) ``moment``."""
    return intraday_window(DAY, end=moment, price=price, volume_per_bar=volume)


# --------------------------------------------------------------------------- #
# the signal: price AND volume, together
# --------------------------------------------------------------------------- #
def test_the_first_bar_with_the_move_and_the_volume_is_the_signal():
    today = _quiet_until(time(5, 30)) + [_bar(time(5, 30), 104.0, 5_000)]
    when, price, note = first_signal(_history(today), as_of=DAY, run_at=RUN_AT,
                                     previous_close=PREV, gap=0.04)
    assert price == 104.0
    assert note == ""
    # The bar STARTED at 05:30 and closed at 05:35: that is the earliest anyone could know.
    assert when == at_ny(DAY, time(5, 35))


def test_a_price_at_the_gap_on_ordinary_volume_is_not_a_signal():
    """The stray print the trust tests exist to catch: it moved, on nothing."""
    today = _quiet_until(time(5, 30)) + [_bar(time(5, 30), 104.0, 1_000)]
    when, price, note = first_signal(_history(today), as_of=DAY, run_at=RUN_AT,
                                     previous_close=PREV, gap=0.04)
    assert (when, price) == (None, None)
    assert "1 bar(s) reached the gap but none had volume 3x" in note


def test_heavy_volume_before_the_price_has_moved_is_not_a_signal():
    today = _quiet_until(time(5, 30)) + [_bar(time(5, 30), 101.0, 50_000)]
    when, _price, note = first_signal(_history(today), as_of=DAY, run_at=RUN_AT,
                                      previous_close=PREV, gap=0.04)
    assert when is None
    assert "no 5-minute bar reached the 3% gap" in note


def test_the_volume_bar_is_a_multiple_of_the_usual_volume_for_that_time_of_day():
    """Exactly 3x qualifies; just under does not."""
    at_bar = _quiet_until(time(5, 30)) + [_bar(time(5, 30), 104.0, 3_000)]
    when, _p, _n = first_signal(_history(at_bar), as_of=DAY, run_at=RUN_AT,
                                previous_close=PREV, gap=0.04)
    assert when is not None
    under = _quiet_until(time(5, 30)) + [_bar(time(5, 30), 104.0, 2_999)]
    when, _p, _n = first_signal(_history(under), as_of=DAY, run_at=RUN_AT,
                                previous_close=PREV, gap=0.04)
    assert when is None


def test_the_multiple_is_configurable():
    today = _quiet_until(time(5, 30)) + [_bar(time(5, 30), 104.0, 2_000)]
    strict = first_signal(_history(today), as_of=DAY, run_at=RUN_AT, previous_close=PREV,
                          gap=0.04)
    loose = first_signal(_history(today), as_of=DAY, run_at=RUN_AT, previous_close=PREV,
                         gap=0.04, config=GapConfig(early_volume_multiple=2.0))
    assert strict[0] is None and loose[0] is not None


def test_the_usual_volume_is_for_the_same_time_of_day_not_an_all_morning_average():
    """A slot that is normally busy needs a busier bar than a slot that is normally quiet."""
    prior = []
    day = DAY - timedelta(days=1)
    made = 0
    while made < 20:
        if day.weekday() < 5:
            prior += intraday_window(day, volume_per_bar=1_000) + regular_session(day)
            prior.append(_bar(time(5, 30), 100.0, 10_000, day=day))     # 05:30 is BUSY
            made += 1
        day -= timedelta(days=1)
    prior = [b for b in prior if not (b.start.time() == time(5, 30) and b.volume == 1_000)]
    today = _quiet_until(time(5, 30)) + [_bar(time(5, 30), 104.0, 5_000),   # 0.5x of usual
                                         _bar(time(5, 35), 104.0, 5_000)]   # 5x of usual
    when, _p, _n = first_signal(sorted(today + prior, key=lambda b: b.start), as_of=DAY,
                                run_at=RUN_AT, previous_close=PREV, gap=0.04)
    assert when == at_ny(DAY, time(5, 40))


def test_a_gap_down_is_read_in_its_own_direction():
    today = _quiet_until(time(6, 0)) + [_bar(time(6, 0), 95.0, 6_000)]
    when, price, _n = first_signal(_history(today), as_of=DAY, run_at=RUN_AT,
                                   previous_close=PREV, gap=-0.05)
    assert price == 95.0 and when == at_ny(DAY, time(6, 5))
    # ...and a bar up 5% is NOT a signal for a name that gapped down.
    up = _quiet_until(time(6, 0)) + [_bar(time(6, 0), 105.0, 6_000)]
    assert first_signal(_history(up), as_of=DAY, run_at=RUN_AT, previous_close=PREV,
                        gap=-0.05)[0] is None


def test_the_first_qualifying_bar_wins_not_the_biggest():
    today = (_quiet_until(time(5, 0)) + [_bar(time(5, 0), 103.5, 4_000),
                                         _bar(time(5, 5), 108.0, 90_000)])
    when, price, _n = first_signal(_history(today), as_of=DAY, run_at=RUN_AT,
                                   previous_close=PREV, gap=0.04)
    assert price == 103.5 and when == at_ny(DAY, time(5, 5))


# --------------------------------------------------------------------------- #
# NOT-EVALUATED is not a pass
# --------------------------------------------------------------------------- #
def test_a_zero_usual_volume_is_not_evaluated_never_a_pass():
    """Nobody usually trades at 04:20. 3 x 0 = 0 would let a 100-share print be the 'first
    signal' — so the bar is skipped, and COUNTED so the row says what happened."""
    today = [_bar(time(4, 20), 104.0, 100)]
    history = sorted(today + prior_sessions(before=DAY, count=20,
                                            premarket_volume_per_bar=0), key=lambda b: b.start)
    when, price, note = first_signal(history, as_of=DAY, run_at=RUN_AT, previous_close=PREV,
                                     gap=0.04)
    assert (when, price) == (None, None)
    assert "1 of them had a usual volume of zero and could not be evaluated" in note


def test_a_later_bar_with_a_measurable_baseline_can_still_signal_after_a_skipped_one():
    prior = prior_sessions(before=DAY, count=20, premarket_volume_per_bar=1_000)
    prior = [b for b in prior if b.start.time() != time(4, 20)]      # slot empty every session
    today = [_bar(time(4, 20), 104.0, 100), _bar(time(4, 25), 104.0, 9_000)]
    when, _p, _n = first_signal(sorted(today + prior, key=lambda b: b.start), as_of=DAY,
                                run_at=RUN_AT, previous_close=PREV, gap=0.04)
    assert when == at_ny(DAY, time(4, 30))


def test_no_prior_sessions_means_nothing_to_compare_against():
    when, _p, note = first_signal(_quiet_until(time(9, 0), 104.0), as_of=DAY, run_at=RUN_AT,
                                  previous_close=PREV, gap=0.04)
    assert when is None and note == NO_HISTORY


def test_no_gap_direction_means_no_signal():
    assert first_signal(_history(_quiet_until(time(9, 0))), as_of=DAY, run_at=RUN_AT,
                        previous_close=PREV, gap=None)[2] == NO_DIRECTION
    assert first_signal(_history(_quiet_until(time(9, 0))), as_of=DAY, run_at=RUN_AT,
                        previous_close=PREV, gap=0.0)[2] == NO_DIRECTION


# --------------------------------------------------------------------------- #
# not from the future
# --------------------------------------------------------------------------- #
def test_a_bar_still_forming_at_the_run_time_is_not_a_signal():
    """A 08:58 run cannot know how the 08:55 bar ends."""
    run_at = at_ny(DAY, time(8, 58))
    today = _quiet_until(time(8, 55)) + [_bar(time(8, 55), 104.0, 9_000)]
    assert first_signal(_history(today), as_of=DAY, run_at=run_at, previous_close=PREV,
                        gap=0.04)[0] is None
    # ...and at 09:00 the same bar has closed, and is one.
    assert first_signal(_history(today), as_of=DAY, run_at=RUN_AT, previous_close=PREV,
                        gap=0.04)[0] == at_ny(DAY, time(9, 0))


def test_the_signal_never_reads_a_regular_session_bar():
    """A run at 11:00 clamps at the open: 09:30 onward is not pre-market."""
    run_at = at_ny(DAY, time(11, 0))
    today = _quiet_until(time(9, 30)) + [_bar(time(9, 30), 104.0, 9_000)]
    assert first_signal(_history(today), as_of=DAY, run_at=run_at, previous_close=PREV,
                        gap=0.04)[0] is None


# --------------------------------------------------------------------------- #
# the price path
# --------------------------------------------------------------------------- #
def _path_tape() -> list:
    return [_bar(time(4, 0), 100.0), _bar(time(6, 0), 102.0), _bar(time(7, 0), 103.0),
            _bar(time(8, 0), 104.0), _bar(time(9, 0), 105.0)]


def test_the_path_reads_the_open_of_the_bar_at_each_moment():
    assert path_prices(_path_tape(), DAY, RUN_AT) == (100.0, 102.0, 103.0, 104.0, 105.0)


def test_a_moment_with_no_print_is_blank_never_borrowed_from_a_neighbour():
    tape = [b for b in _path_tape() if b.start.time() != time(7, 0)]
    path = path_prices(tape, DAY, RUN_AT)
    assert path[2] is None
    assert path[1] == 102.0 and path[3] == 104.0


def test_a_print_inside_the_tolerance_counts_and_one_outside_does_not():
    near = [_bar(time(6, 10), 102.0)]                 # 10 minutes after 06:00
    far = [_bar(time(6, 20), 102.0)]                  # 20 minutes after
    assert path_prices(near, DAY, RUN_AT)[1] == 102.0
    assert path_prices(far, DAY, RUN_AT)[1] is None


def test_the_path_stops_at_the_run_time():
    """A run at 08:30 has no 09:00 price to record, and must not invent one."""
    path = path_prices(_path_tape(), DAY, at_ny(DAY, time(8, 30)))
    assert path[3] == 104.0 and path[4] is None


def test_the_path_ignores_other_days():
    yesterday = [_bar(time(4, 0), 77.0, day=DAY - timedelta(days=1))]
    assert path_prices(yesterday, DAY, RUN_AT)[0] is None


# --------------------------------------------------------------------------- #
# the run: IBKR-verified candidates carry it; yfinance-only rows carry nothing
# --------------------------------------------------------------------------- #
@dataclass
class _Gateway:
    today: dict = field(default_factory=dict)
    history: dict = field(default_factory=dict)

    def bars_for(self, ticker, *, start, end, bar_size="5 mins", duration=None):
        return list((self.history if duration else self.today).get(ticker, []))

    def quote_for(self, ticker):
        return Quote()

    def disconnect(self):
        pass


def _run(tmp_path, *, with_ibkr: bool):
    """REAL gaps +10% from 04:00 on ten times its usual volume; yfinance sees the same prices
    with no volume, so its own path can never produce a signal."""
    # Through 09:05: IB serves the bar that is forming at the 09:00 run, whose OPEN is the
    # 09:00 price. (The screen's own window still ends at 09:00.)
    tape = intraday_window(DAY, end=time(9, 5), price=110.0, volume_per_bar=10_000)
    priors = prior_sessions(before=DAY, count=20, premarket_volume_per_bar=1_000)
    bars = FakeBars(daily={"REAL": daily_series(end=DAY, sessions=300, close=PREV,
                                                volume=3_000_000)},
                    intraday={"REAL": intraday_window(DAY, end=time(9, 0), price=110.0,
                                                      volume_per_bar=0)
                              + prior_sessions(before=DAY, count=20,
                                               premarket_volume_per_bar=0)})
    gateway = _Gateway(today={"REAL": tape}, history={"REAL": sorted(tape + priors,
                                                                     key=lambda b: b.start)})
    run_screen(pool=["REAL"], pool_source="t", daily=bars, intraday=bars, day=DAY,
               run_at=RUN_AT, root=tmp_path, ibkr=gateway if with_ibkr else None)
    return read_day(DAY, root=tmp_path)


def test_a_verified_candidate_is_logged_with_its_first_signal_and_path(tmp_path):
    row = _run(tmp_path, with_ibkr=True)[0]
    assert row.source == SOURCE_IBKR and row.group == GROUP_CANDIDATE
    assert row.first_signal_time_et.startswith("2026-09-22T04:05")
    assert row.first_signal_price == pytest.approx(110.0)
    assert (row.price_0400, row.price_0600, row.price_0700, row.price_0800) == (110.0,) * 4
    assert row.price_0900 == pytest.approx(110.0)
    assert row.early_signal_note == ""


def test_a_yfinance_only_row_leaves_every_early_column_blank(tmp_path):
    """No IB, so no pre-market volume, so nothing to compare and nothing guessed."""
    row = _run(tmp_path, with_ibkr=False)
    # yfinance alone screens nothing through on this fixture (no volume), so build the row
    # the way the run would and assert the columns directly.
    from aristos_council.gap_ledger.run import build_row

    built = build_row(day=DAY, run_at=RUN_AT, group=GROUP_CANDIDATE, pre=None, screen=None,
                      headlines=None, news_enabled=False, reason="",
                      config=DEFAULT_CONFIG, reading=None)
    assert built.source == SOURCE_YFINANCE
    assert built.first_signal_time_et == "" and built.first_signal_price is None
    assert (built.price_0400, built.price_0600, built.price_0700, built.price_0800,
            built.price_0900) == (None,) * 5
    assert built.early_signal_note == ""
    assert all(r.first_signal_price is None for r in row)


def test_the_threshold_is_stamped_on_the_row(tmp_path):
    row = _run(tmp_path, with_ibkr=True)[0]
    assert row.cfg_early_volume_multiple == pytest.approx(3.0)


def test_a_verified_name_with_no_signal_says_why(tmp_path):
    """Verified, but never at the gap with volume: the row carries the reason, not a blank."""
    reading = early_reading(_history(_quiet_until(time(9, 0), 101.0)), as_of=DAY,
                            run_at=RUN_AT, previous_close=PREV, gap=0.04)
    assert reading.signal_time is None
    assert "no 5-minute bar reached the 3% gap" in reading.note


# --------------------------------------------------------------------------- #
# outcomes: what acting at the signal would have made
# --------------------------------------------------------------------------- #
def _row(**kw) -> LedgerRow:
    base = dict(date=DAY.isoformat(), ticker="AAA", group=GROUP_CANDIDATE, source=SOURCE_IBKR,
                gap_pct=0.10, first_signal_time_et="2026-09-22T05:35-04:00",
                first_signal_price=104.0, price_0900=108.0, open_price=110.0,
                close_price=113.0)
    base.update(kw)
    return LedgerRow(**base)


def test_the_signal_moves_are_measured_from_the_signal_price_in_the_gaps_direction():
    to_0900, to_open, to_close = signal_moves(_row())
    assert to_0900 == pytest.approx(4 / 104)
    assert to_open == pytest.approx(6 / 104)
    assert to_close == pytest.approx(9 / 104)


def test_a_gap_down_move_is_positive_when_the_name_kept_falling():
    row = _row(gap_pct=-0.10, first_signal_price=96.0, price_0900=92.0, open_price=90.0,
               close_price=88.0)
    to_0900, to_open, to_close = signal_moves(row)
    assert to_0900 > 0 and to_open > 0 and to_close > 0
    assert to_close == pytest.approx(8 / 96)


def test_a_signal_after_nine_has_no_move_to_nine():
    """A 'move to 09:00' that starts later than 09:00 is not a move."""
    row = _row(first_signal_time_et="2026-09-22T09:05-04:00")
    to_0900, to_open, _c = signal_moves(row)
    assert to_0900 is None and to_open is not None


def test_no_signal_means_no_moves_at_all():
    assert signal_moves(_row(first_signal_price=None, first_signal_time_et="")) == (None,) * 3


def test_a_missing_far_end_is_missing_not_zero():
    to_0900, to_open, to_close = signal_moves(_row(price_0900=None, close_price=None))
    assert to_0900 is None and to_close is None and to_open is not None


def test_directional_move_refuses_an_unusable_start():
    assert directional_move(0.0, 5.0, 1) is None
    assert directional_move(None, 5.0, 1) is None
    assert directional_move(5.0, 6.0, 0) is None


def test_fill_row_records_the_signal_moves(tmp_path):
    bars = FakeBars(daily={"AAA": daily_series(end=DAY + timedelta(days=1), sessions=1,
                                               close=113.0)},
                    intraday={"AAA": regular_session(DAY, price=111.0)})
    filled = fill_row(_row(open_price=None, close_price=None), daily=bars.daily["AAA"],
                      intraday=bars.intraday["AAA"], day=DAY, now=at_ny(DAY, time(16, 30)))
    assert filled.signal_move_close == pytest.approx((113.0 - 104.0) / 104.0)
    assert filled.signal_move_open is not None


# --------------------------------------------------------------------------- #
# the scorecard: first signal vs the open, same names, same floor
# --------------------------------------------------------------------------- #
def _scored_row(*, signal=104.0, open_=110.0, close=113.0, source=SOURCE_IBKR) -> LedgerRow:
    return _row(first_signal_price=signal, open_price=open_, close_price=close, source=source,
                price_1000=111.0, price_1130=112.0)


def test_the_comparison_is_over_the_same_names_on_both_sides():
    early = early_score([(DAY, _scored_row()), (DAY, _scored_row(close=100.0)),
                         (DAY, _scored_row(signal=None))])
    assert early.verified == 3 and early.signalled == 2 and early.paired == 2
    to_close_signal = dict(early.at_signal)["to the close"]
    to_close_open = dict(early.at_open)["to the close"]
    assert to_close_signal.n == to_close_open.n == 2


def test_acting_earlier_pays_when_the_move_carried_and_the_entry_was_cheaper():
    early = early_score([(DAY, _scored_row())])
    signal = dict(early.at_signal)["to the close"]
    at_open = dict(early.at_open)["to the close"]
    assert signal.mean == pytest.approx(9 / 104)
    assert at_open.mean == pytest.approx(3 / 110)
    assert signal.mean > at_open.mean


def test_below_the_day_floor_there_is_no_finding():
    early = early_score([(DAY, _scored_row())])
    assert not early.enough_days
    assert early.verdict.startswith("Not enough days: 1 with a first signal of 40 needed")


def test_at_the_floor_the_verdict_compares_the_two_entries():
    days = [(DAY - timedelta(days=i), _scored_row()) for i in range(40)]
    early = early_score(days)
    assert early.enough_days and early.days == 40
    assert "Acting at the first signal was worth more than acting at the open" in early.verdict


def test_a_later_entry_that_did_worse_is_said_plainly():
    days = [(DAY - timedelta(days=i), _scored_row(signal=112.0)) for i in range(40)]
    assert "was NOT worth more" in early_score(days).verdict


def test_a_yfinance_only_ledger_has_no_early_section_at_all():
    """Absent, not a block of zeros that reads as a finding."""
    assert early_score([(DAY, _scored_row(source=SOURCE_YFINANCE))]) is None
    card = score({DAY: [_scored_row(source=SOURCE_YFINANCE)]})
    assert card.early is None
    assert not any("first signal" in line for line in card.lines())


def test_the_scorecard_prints_the_early_section():
    card = score({DAY: [_scored_row()]}, config=GapConfig(min_days_to_score=1))
    text = "\n".join(card.lines())
    assert "Acting at the first signal vs acting at the open" in text
    assert "first signal to the close" in text and "the open to the close" in text


def test_days_are_counted_from_the_names_that_were_scored_not_from_the_days_logged():
    early = early_score([(DAY, _scored_row()), (DAY, _scored_row()),
                         (DAY - timedelta(days=1), _scored_row(signal=None))])
    assert early.days == 1


# --------------------------------------------------------------------------- #
# the record and the viewer
# --------------------------------------------------------------------------- #
def test_the_new_columns_round_trip_through_the_csv(tmp_path):
    write_day(DAY, [_row(price_0400=100.0, early_signal_note="n")], root=tmp_path)
    back = read_day(DAY, root=tmp_path)[0]
    assert back.first_signal_price == pytest.approx(104.0)
    assert back.first_signal_time_et == "2026-09-22T05:35-04:00"
    assert back.price_0400 == pytest.approx(100.0) and back.price_0600 is None


def test_an_old_csv_without_the_columns_still_loads(tmp_path):
    """A file written before this batch: absent columns read as absent, never as 0."""
    old = [f for f in FIELDS if f not in {"first_signal_time_et", "first_signal_price",
                                          "price_0400", "price_0600", "price_0700",
                                          "price_0800", "price_0900", "early_signal_note",
                                          "signal_move_0900", "signal_move_open",
                                          "signal_move_close", "cfg_early_volume_multiple"}]
    path = ledger_path(DAY, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(old) + "\n" + ",".join(
        "AAA" if f == "ticker" else "" for f in old) + "\n", encoding="utf-8")
    row = read_day(DAY, root=tmp_path)[0]
    assert row.ticker == "AAA"
    assert row.first_signal_price is None and row.price_0400 is None
    assert path_markdown(row) == ""


def test_the_viewer_says_when_the_signal_came():
    assert first_signal_of(_row()) == "05:35 ET at $104.00"


def test_an_unverified_row_says_what_is_missing_instead_of_a_blank():
    row = _row(source=SOURCE_YFINANCE, first_signal_price=None, first_signal_time_et="")
    assert first_signal_of(row) == NO_EARLY_DATA
    assert ("First signal", NO_EARLY_DATA) in details_of(row)


def test_a_verified_row_with_no_signal_shows_its_reason_not_a_dash():
    row = _row(first_signal_price=None, first_signal_time_et="",
               early_signal_note="no 5-minute bar reached the 3% gap before the run")
    pairs = dict(details_of(row))
    assert "First signal" not in pairs
    assert pairs["First-signal note"].startswith("no 5-minute bar reached")


def test_the_path_runs_from_four_to_the_close():
    row = _row(previous_close=100.0, price_0400=101.0, price_0600=102.0, price_0700=103.0,
               price_0800=104.0, price_0900=105.0, price_1000=111.0, price_1130=112.0)
    labels = [label for label, _p in price_path(row)]
    assert labels == ["04:00", "06:00", "07:00", "08:00", "09:00", "09:30 open", "10:00",
                      "11:30", "Close"]
    text = path_markdown(row)
    assert "$101.00" in text and "$113.00" in text
    assert "+1.00%" in text                       # 04:00 against the previous close


def test_a_gap_in_the_path_is_drawn_as_a_gap():
    row = _row(previous_close=100.0, price_0400=101.0, price_0600=None)
    cells = path_markdown(row).splitlines()[2].split("|")
    assert "—" in cells[3]


def test_details_show_the_moves_from_the_first_signal():
    pairs = dict(details_of(_row(signal_move_close=0.0865)))
    assert pairs["Move from first signal to the close"] == "+8.65%"


def test_the_early_table_lists_both_entries_against_the_same_exits():
    early = early_score([(DAY, _scored_row())])
    text = early_markdown(early)
    assert "| First signal | to the close | 1 |" in text
    assert "| The open | to the close | 1 |" in text
    assert early_markdown(None) == ""
