"""PRICE-STALE-1 — say when the price is old.

The valuation band printed "the last close on <date>" and left it there. Whether that
date was yesterday or a fortnight ago was a subtraction the reader had to do, against a
today they had to supply themselves — so a stale cache read exactly like current data.

FOUR calendar days, not one. A Friday close read on the following Tuesday is three days
old and completely normal. Modelling exchange calendars to shave that down would be a lot
of machinery to make the warning fire slightly sooner, and every holiday it got wrong
would be a false alarm about the one thing this line exists to make credible.

Nothing is blocked. The run is fine; the reader is told.
"""

from __future__ import annotations

from datetime import date

import pytest

from aristos_council.pipeline import (PRICE_STALE_DAYS, RankPipelineResult, header_lines,
                                      newest_close_date, price_age_days, price_stale_line)
from aristos_council.rank_engine import RankedTicker

TODAY = date(2026, 9, 18)


class _Price:
    def __init__(self, as_of):
        self.as_of = as_of


def _result(*as_ofs, meta=None, today=TODAY):
    """A result carrying the age the RUN recorded — which is what the line reads."""
    rows = []
    for i, as_of in enumerate(as_ofs):
        row = RankedTicker(ticker=f"T{i}", factor_ranks={}, factor_values={},
                           combined_rank=0.0, universe_size=len(as_ofs))
        row.price = _Price(as_of) if as_of is not None else None
        rows.append(row)
    result = RankPipelineResult(ranked=rows, excluded=[], unrateable=[], narratives={},
                                header="", meta=dict(meta or {}))
    if today is not None and "price_age_days" not in result.meta:
        result.meta["price_age_days"] = price_age_days(result, today=today)
    return result


def test_a_seven_day_old_close_renders_the_line():
    result = _result(date(2026, 9, 11))
    assert price_stale_line(result, today=TODAY) == (
        "Prices are from 2026-09-11, 7 days old; the cache may be stale.")


def test_a_two_day_old_close_renders_nothing():
    assert price_stale_line(_result(date(2026, 9, 16)), today=TODAY) == ""


@pytest.mark.parametrize("age", [0, 1, 2, 3, 4])
def test_nothing_fires_inside_the_weekend_and_holiday_allowance(age):
    """Four days is the allowance, so four days is silent and five is not."""
    from datetime import timedelta
    assert price_stale_line(_result(TODAY - timedelta(days=age)), today=TODAY) == ""


def test_five_days_is_the_first_age_that_fires():
    from datetime import timedelta
    assert PRICE_STALE_DAYS == 4
    line = price_stale_line(_result(TODAY - timedelta(days=5)), today=TODAY)
    assert line.startswith("Prices are from") and "5 days old" in line


def test_the_age_is_taken_from_the_NEWEST_close_not_the_oldest():
    """One name with a deep gap in its history must not make the whole run look stale."""
    result = _result(date(2026, 1, 2), date(2026, 9, 17))
    assert newest_close_date(result) == date(2026, 9, 17)
    assert price_age_days(result, today=TODAY) == 1
    assert price_stale_line(result, today=TODAY) == ""


def test_a_run_with_no_prices_at_all_says_nothing_rather_than_guessing():
    assert newest_close_date(_result(None)) is None
    assert price_age_days(_result(None), today=TODAY) is None
    assert price_stale_line(_result(None), today=TODAY) == ""


def test_a_close_dated_in_the_future_is_zero_days_old_not_negative():
    """A clock skew should not print '-2 days old'."""
    assert price_age_days(_result(date(2026, 9, 20)), today=TODAY) == 0


def test_the_age_is_recorded_on_the_run_whether_or_not_it_warns():
    """The record answers the question later, even on a run that was perfectly fresh."""
    from tests.test_multi_strategy_run import RAW, SCREENED, STRAT_DIR, TODAY as FIXTURE_TODAY, UNIVERSE, _Adapter
    from aristos_council.pipeline import run_rank_pipeline

    result = run_rank_pipeline(UNIVERSE, RAW, strategies_dir=STRAT_DIR,
                               adapter=_Adapter(), today=FIXTURE_TODAY, ranker_only=True)
    assert "price_age_days" in result.meta


def test_the_header_block_carries_the_line_when_it_fires_and_not_when_it_does_not():
    stale = _result(date(2026, 1, 2), meta={"universe_size": 1, "council_mode": "ranker-only"})
    fresh = _result(date(2026, 9, 17), meta={"universe_size": 1, "council_mode": "ranker-only"})
    assert any("cache may be stale" in line for line in header_lines(stale))
    assert not any("cache may be stale" in line for line in header_lines(fresh))


def test_a_multi_lens_run_reads_every_lenss_rows():
    """``ranked`` lives per-lens on a multi-result; the figure is the RUN's, not the first
    column's."""
    from aristos_council.pipeline import MultiStrategyResult

    old_lens = _result(date(2026, 1, 2))
    new_lens = _result(date(2026, 9, 17))
    multi = MultiStrategyResult(
        strategy_ids=["a", "b"], strategy_names={"a": "A", "b": "B"},
        results={"a": old_lens, "b": new_lens}, rows=[], meta={})
    assert newest_close_date(multi) == date(2026, 9, 17)
    assert price_age_days(multi, today=TODAY) == 1


def test_a_rendered_report_does_not_change_its_own_text_as_the_days_pass():
    """A report is a record of a MOMENT.

    Re-rendering a March run in September must not have it announce that its prices are
    six months stale, and a document whose text depends on the day it is read cannot be
    pinned by a golden at all. The line therefore reads the age the RUN recorded, not one
    computed against today — which is what the golden files caught when it was first
    written the other way round.
    """
    run = _result(date(2026, 9, 11), today=date(2026, 9, 18))
    assert run.meta["price_age_days"] == 7
    said = price_stale_line(run)
    assert "7 days old" in said
    # ...and it still says SEVEN a year later
    assert price_stale_line(run, today=date(2027, 9, 18)) == said


def test_a_result_with_no_recorded_age_says_nothing_rather_than_inventing_one():
    bare = RankPipelineResult(ranked=[], excluded=[], unrateable=[], narratives={},
                              header="", meta={})
    assert price_stale_line(bare) == ""
