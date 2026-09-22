"""GAP-LEDGER-1 — the day's CSV, the seeded control group, and the after-close readings.

The control group is what makes the record worth keeping, so its two properties are pinned
hard: the draw is REPRODUCIBLE for a given day (seeded from the date, sorted before the
draw so process hash randomization cannot change it) and it is drawn from the right pool.

The CSV's round trip matters for the same reason: an empty outcome cell must come back as
None and never as 0.0, because the scorecard counts continuations and a 0.0 open would make
every name look like it doubled.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from aristos_council.gap_ledger.config import NY, at_ny
from aristos_council.gap_ledger.ledger import (FIELDS, GROUP_BASELINE, GROUP_CANDIDATE,
                                               LedgerRow, day_seed, ledger_days,
                                               ledger_path, read_day, sample_baseline,
                                               write_day)
from aristos_council.gap_ledger.outcomes import (CHECKPOINT_TOLERANCE, SessionNotClosed,
                                                 daily_reading, fill_day, fill_row,
                                                 is_complete, price_at, session_is_closed)

from .gap_ledger_fakes import FakeBars, daily_series, regular_session

DAY = date(2026, 9, 22)
POOL = [f"T{n:03d}" for n in range(100)]


# --------------------------------------------------------------------------- #
# the control group
# --------------------------------------------------------------------------- #
def test_the_draw_is_the_same_every_time_for_a_given_day():
    assert sample_baseline(POOL, 8, DAY) == sample_baseline(POOL, 8, DAY)


def test_a_different_day_draws_a_different_group():
    assert sample_baseline(POOL, 8, DAY) != sample_baseline(POOL, 8, DAY + timedelta(days=1))


def test_the_draw_does_not_depend_on_the_order_the_pool_arrives_in():
    """``random.sample`` over an unordered set answers differently per process. Sorting
    before the draw is what makes the seed mean anything."""
    shuffled = list(reversed(POOL))
    assert sample_baseline(shuffled, 8, DAY) == sample_baseline(POOL, 8, DAY)


def test_the_draw_is_the_requested_size_and_holds_only_pool_names():
    drawn = sample_baseline(POOL, 8, DAY)
    assert len(drawn) == 8
    assert set(drawn) <= set(POOL)
    assert drawn == sorted(drawn)


def test_a_pool_smaller_than_the_request_comes_back_whole_rather_than_padded():
    """An under-filled control group is visible in the row count; an invented name is not."""
    assert sample_baseline(["AAA", "BBB"], 8, DAY) == ["AAA", "BBB"]


def test_no_candidates_means_no_control_group():
    assert sample_baseline(POOL, 0, DAY) == []


def test_the_seed_is_the_date_and_nothing_else():
    assert day_seed(date(2026, 9, 22)) == 20260922


# --------------------------------------------------------------------------- #
# the CSV
# --------------------------------------------------------------------------- #
def _row(ticker: str, **kwargs) -> LedgerRow:
    base = dict(date=DAY.isoformat(), ticker=ticker, gap_pct=0.08, relative_volume=5.0,
                previous_close=50.0, premarket_price=54.0, spread_note="spread ok",
                news_found="news found", headline="Alpha beats",
                news_link="https://example.com/a")
    base.update(kwargs)
    return LedgerRow(**base)


def test_a_day_round_trips_through_the_csv(tmp_path):
    rows = [_row("AAA"), _row("BBB", group=GROUP_BASELINE, gap_pct=-0.004)]
    write_day(DAY, rows, root=tmp_path)
    back = read_day(DAY, root=tmp_path)
    assert [r.ticker for r in back] == ["AAA", "BBB"]
    assert back[0].gap_pct == pytest.approx(0.08)
    assert back[1].group == GROUP_BASELINE
    assert back[1].gap_pct == pytest.approx(-0.004)


def test_candidates_are_written_before_the_control_group(tmp_path):
    write_day(DAY, [_row("ZZZ", group=GROUP_BASELINE), _row("AAA")], root=tmp_path)
    assert [r.group for r in read_day(DAY, root=tmp_path)] == [GROUP_CANDIDATE,
                                                               GROUP_BASELINE]


def test_an_unfilled_outcome_reads_back_as_missing_not_as_zero(tmp_path):
    """A 0.0 open would make every name look like it doubled by 10:00."""
    write_day(DAY, [_row("AAA")], root=tmp_path)
    back = read_day(DAY, root=tmp_path)[0]
    assert back.open_price is None and back.close_price is None
    assert not is_complete(back)


def test_the_header_names_every_field(tmp_path):
    write_day(DAY, [_row("AAA")], root=tmp_path)
    header = ledger_path(DAY, tmp_path).read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == list(FIELDS)


def test_a_missing_day_reads_as_empty_rather_than_raising(tmp_path):
    assert read_day(DAY, root=tmp_path) == []


def test_ledger_days_ignores_a_file_that_is_not_a_date(tmp_path):
    write_day(DAY, [_row("AAA")], root=tmp_path)
    (tmp_path / "notes.csv").write_text("hello", encoding="utf-8")
    assert ledger_days(tmp_path) == [DAY]


# --------------------------------------------------------------------------- #
# the after-close readings
# --------------------------------------------------------------------------- #
def test_price_at_is_the_open_of_the_bar_that_starts_at_the_checkpoint():
    """"The price at 10:00" is the price at 10:00. The close of the 10:00 bar is 10:05."""
    bars = regular_session(DAY, price=50.0)
    tagged = [b for b in bars if b.start == at_ny(DAY, time(10, 0))]
    assert tagged, "fixture should contain a 10:00 bar"
    assert price_at(bars, DAY, time(10, 0)) == 50.0


def test_price_at_is_missing_when_the_next_print_is_outside_the_tolerance():
    """A thin name whose next print is an hour later reads as MISSING rather than as a
    10:00 price that was nothing of the sort."""
    late = [b for b in regular_session(DAY) if b.start >= at_ny(DAY, time(11, 0))]
    assert price_at(late, DAY, time(10, 0)) is None


def test_price_at_accepts_a_print_inside_the_tolerance():
    minutes = int(CHECKPOINT_TOLERANCE.total_seconds() // 60)
    shifted = [b for b in regular_session(DAY)
               if b.start >= at_ny(DAY, time(10, 0)) + timedelta(minutes=minutes - 5)]
    assert price_at(shifted, DAY, time(10, 0)) is not None


def test_outcomes_refuse_a_session_that_has_not_closed(tmp_path):
    write_day(DAY, [_row("AAA")], root=tmp_path)
    with pytest.raises(SessionNotClosed):
        fill_day(DAY, daily=FakeBars(), intraday=FakeBars(), root=tmp_path,
                 now=at_ny(DAY, time(12, 0)))


def test_session_is_closed_reads_the_market_clock():
    assert not session_is_closed(DAY, at_ny(DAY, time(15, 59)))
    assert session_is_closed(DAY, at_ny(DAY, time(16, 0)))


def test_outcomes_fill_candidates_and_the_control_group_alike(tmp_path):
    write_day(DAY, [_row("AAA"), _row("BBB", group=GROUP_BASELINE)], root=tmp_path)
    bars = FakeBars(daily={t: daily_series(end=DAY + timedelta(days=1), sessions=1,
                                           close=52.0) for t in ("AAA", "BBB")},
                    intraday={t: regular_session(DAY, price=51.0) for t in ("AAA", "BBB")})
    report = fill_day(DAY, daily=bars, intraday=bars, root=tmp_path,
                      now=at_ny(DAY, time(16, 30)))
    assert report.rows == 2 and report.filled == 2
    back = read_day(DAY, root=tmp_path)
    assert all(is_complete(row) for row in back)
    assert {row.group for row in back} == {GROUP_CANDIDATE, GROUP_BASELINE}


def test_a_missing_checkpoint_is_written_empty_with_a_stated_reason(tmp_path):
    """Never filled with the nearest thing to hand: the scorecard would count it."""
    write_day(DAY, [_row("AAA")], root=tmp_path)
    bars = FakeBars(daily={"AAA": daily_series(end=DAY + timedelta(days=1), sessions=1)},
                    intraday={"AAA": [b for b in regular_session(DAY)
                                      if b.start < at_ny(DAY, time(10, 0))]})
    report = fill_day(DAY, daily=bars, intraday=bars, root=tmp_path,
                      now=at_ny(DAY, time(16, 30)))
    row = read_day(DAY, root=tmp_path)[0]
    assert row.price_1000 is None and row.price_1130 is None
    assert row.open_price is not None
    assert "10:00 ET" in row.outcome_note and "11:30 ET" in row.outcome_note
    assert report.incomplete == 1


def test_a_day_with_no_ledger_reports_zero_rather_than_raising(tmp_path):
    report = fill_day(DAY, daily=FakeBars(), intraday=FakeBars(), root=tmp_path,
                      now=at_ny(DAY, time(16, 30)))
    assert report.rows == 0
    assert report.notes == (f"no ledger for {DAY}",)


def test_fill_row_leaves_the_input_untouched():
    original = _row("AAA")
    filled = fill_row(original, daily=[], intraday=[], day=DAY,
                      now=at_ny(DAY, time(16, 30)))
    assert original.open_price is None
    assert filled.outcome_note


def test_daily_reading_picks_the_requested_session_only():
    bars = daily_series(end=DAY + timedelta(days=1), sessions=5)
    assert daily_reading(bars, DAY).day == DAY
    assert daily_reading(bars, DAY + timedelta(days=30)) is None


def test_the_fill_stamp_is_in_new_york_time(tmp_path):
    write_day(DAY, [_row("AAA")], root=tmp_path)
    fill_day(DAY, daily=FakeBars(), intraday=FakeBars(), root=tmp_path,
             now=datetime(2026, 9, 22, 22, 30, tzinfo=NY))
    row = read_day(DAY, root=tmp_path)[0]
    assert row.outcomes_filled_at_et.startswith("2026-09-22T22:30")
