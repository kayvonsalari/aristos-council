"""GAP-MARKET-BENCH-1 — compare with the market, not only the control group.

The control group answers "did the screen's names carry on more than comparable names that did
not qualify?". It cannot answer whether they simply rode the market: on a day SPY rose 1% every
long carried on, and a candidate rate of 70% is then a fact about the day. So ``outcomes`` also
records SPY's move (open to 10:00, 11:30, close) and every row gets its result raw and relative.

Properties, each with a way of being wrong:

* the market move is SIGNED and the name's is in the gap's direction — subtracting one from the
  other without putting them in the same direction scores a short against the market backwards;
* a missing SPY reading is skipped for the relative number only, never counted as a 0% market day;
* control-group rows get the same columns as candidates — a benchmark filled for one group only
  would compare the screen against nothing;
* a refill must never blank a price an earlier fill recorded (the provider keeps 5-minute bars
  for only a couple of months, and old days are refilled to add SPY);
* an old CSV loads, and a day complete except for SPY costs ONE request, not a refetch of every
  name.
"""
from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from aristos_council.data.adapter import PriceBar
from aristos_council.gap_ledger.bars import IntradayBar
from aristos_council.gap_ledger.config import BENCHMARK, GapConfig, at_ny
from aristos_council.gap_ledger.ledger import (FIELDS, GROUP_BASELINE, GROUP_CANDIDATE,
                                               LedgerRow, ledger_path, read_day, write_day)
from aristos_council.gap_ledger.outcomes import (MarketDay, apply_market, fill_day, fill_row,
                                                 has_market, market_day, relative_to_market)
from aristos_council.gap_ledger.score import score
from aristos_council.gap_ledger.viewer import (details_of, market_markdown, market_triplets)

from .gap_ledger_fakes import FakeBars, daily_series, regular_session

DAY = date(2026, 9, 22)
CLOSED = at_ny(DAY, time(16, 30))


def _bar(moment: time, price: float) -> IntradayBar:
    return IntradayBar(start=at_ny(DAY, moment), open=price, high=price, low=price,
                       close=price, volume=1_000)


def _spy_daily(open_: float = 500.0, close: float = 505.0) -> list[PriceBar]:
    return [PriceBar(day=DAY, open=open_, high=max(open_, close), low=min(open_, close),
                     close=close, adj_close=close, volume=50_000_000)]


def _spy_intraday(p1000: float = 502.0, p1130: float = 503.0) -> list[IntradayBar]:
    return [_bar(time(10, 0), p1000), _bar(time(11, 30), p1130)]


def _row(ticker="AAA", *, gap=0.05, group=GROUP_CANDIDATE, open_=100.0, p1000=101.0,
         p1130=102.0, close=103.0, **kw) -> LedgerRow:
    return LedgerRow(date=DAY.isoformat(), ticker=ticker, group=group, gap_pct=gap,
                     open_price=open_, price_1000=p1000, price_1130=p1130, close_price=close,
                     **kw)


# --------------------------------------------------------------------------- #
# SPY's own day
# --------------------------------------------------------------------------- #
def test_the_benchmark_is_spy():
    assert BENCHMARK == "SPY"


def test_spys_moves_run_from_its_open_to_each_checkpoint():
    day = market_day(_spy_daily(500.0, 505.0), _spy_intraday(502.0, 503.0), DAY)
    assert day.move("price_1000") == pytest.approx(0.004)
    assert day.move("price_1130") == pytest.approx(0.006)
    assert day.move("close_price") == pytest.approx(0.010)


def test_the_market_move_is_signed_not_direction_adjusted():
    down = market_day(_spy_daily(500.0, 495.0), _spy_intraday(499.0, 497.0), DAY)
    assert down.move("close_price") == pytest.approx(-0.010)


def test_a_reading_spy_lacks_is_none_not_zero():
    """No intraday bars for an old day: the 10:00 and 11:30 legs stay blank, and the close
    (a daily bar, which the provider always has) still stands."""
    day = market_day(_spy_daily(), [], DAY)
    assert day.move("price_1000") is None and day.move("price_1130") is None
    assert day.move("close_price") == pytest.approx(0.010)
    assert market_day([], [], DAY).move("close_price") is None


def test_a_zero_open_is_not_divided_by():
    assert MarketDay(open=0.0, close_price=5.0).move("close_price") is None


# --------------------------------------------------------------------------- #
# relative to the market, in the gap's direction
# --------------------------------------------------------------------------- #
def test_a_long_that_beat_a_rising_market_is_positive():
    # +3% raw against SPY +1%: 2 points beyond the market.
    assert relative_to_market(0.03, 0.01, 1) == pytest.approx(0.02)


def test_a_long_that_only_rode_the_market_is_zero():
    assert relative_to_market(0.01, 0.01, 1) == pytest.approx(0.0)


def test_a_gap_down_name_is_scored_against_the_market_in_its_own_direction():
    """The gap-down name fell 3% (raw +3% in its direction) on a day SPY fell 1% (+1% in that
    direction): it did 2 points better at going the gap's way. Subtracting the signed market move
    without flipping it would give 4 points, which is the bug this pins."""
    assert relative_to_market(0.03, -0.01, -1) == pytest.approx(0.02)


def test_a_short_on_a_rising_market_is_penalised_for_the_market_help():
    # It went the gap's way by 1% while SPY rose 2% (2% AGAINST its direction): 3 points better.
    assert relative_to_market(0.01, 0.02, -1) == pytest.approx(0.03)


def test_no_direction_or_no_reading_means_no_relative_number():
    assert relative_to_market(None, 0.01, 1) is None
    assert relative_to_market(0.03, None, 1) is None
    assert relative_to_market(0.03, 0.01, 0) is None


# --------------------------------------------------------------------------- #
# a row
# --------------------------------------------------------------------------- #
def test_apply_market_fills_spy_raw_and_relative():
    row = apply_market(_row(), market_day(_spy_daily(500.0, 505.0),
                                          _spy_intraday(502.0, 503.0), DAY))
    assert row.spy_move_1000 == pytest.approx(0.004)
    assert row.move_1000 == pytest.approx(0.01)
    assert row.rel_spy_1000 == pytest.approx(0.006)
    assert row.move_close == pytest.approx(0.03)
    assert row.rel_spy_close == pytest.approx(0.02)


def test_a_gap_down_row_gets_its_columns_in_its_own_direction():
    row = apply_market(_row(gap=-0.05, open_=100.0, p1000=99.0, p1130=98.0, close=97.0),
                       market_day(_spy_daily(500.0, 495.0), _spy_intraday(499.0, 497.0), DAY))
    assert row.move_close == pytest.approx(0.03)            # fell 3%: went the gap's way
    assert row.spy_move_close == pytest.approx(-0.01)       # the market's own signed move
    assert row.rel_spy_close == pytest.approx(0.02)


def test_a_row_with_no_gap_direction_gets_no_move_columns():
    row = apply_market(_row(gap=None), market_day(_spy_daily(), _spy_intraday(), DAY))
    assert row.move_close is None and row.rel_spy_close is None
    assert row.spy_move_close == pytest.approx(0.01)        # the market's day is still the day's


def test_a_later_poorer_fetch_never_blanks_an_earlier_reading():
    filled = apply_market(_row(), market_day(_spy_daily(), _spy_intraday(), DAY))
    again = apply_market(filled, MarketDay(open=500.0, close_price=505.0))   # no intraday now
    assert again.spy_move_1000 == pytest.approx(filled.spy_move_1000)
    assert again.rel_spy_1000 == pytest.approx(filled.rel_spy_1000)


def test_has_market_tests_the_close_leg():
    assert not has_market(_row())
    assert has_market(_row(spy_move_close=0.01))
    # A day that only ever had the daily bar is NOT retried forever for its intraday legs.
    assert has_market(apply_market(_row(), market_day(_spy_daily(), [], DAY)))


def test_fill_row_fills_the_market_columns_for_the_control_group_too():
    market = market_day(_spy_daily(), _spy_intraday(), DAY)
    for group in (GROUP_CANDIDATE, GROUP_BASELINE):
        blank = LedgerRow(date=DAY.isoformat(), ticker="AAA", group=group, gap_pct=0.05)
        filled = fill_row(blank, daily=daily_series(end=DAY + timedelta(days=1), sessions=1,
                                                    close=103.0),
                          intraday=regular_session(DAY, price=101.0), day=DAY, now=CLOSED,
                          market=market)
        assert filled.spy_move_close == pytest.approx(0.01)
        assert filled.move_close is not None and filled.rel_spy_close is not None


# --------------------------------------------------------------------------- #
# a refill must not destroy what an earlier fill recorded
# --------------------------------------------------------------------------- #
def test_a_refill_that_finds_no_data_keeps_the_recorded_prices():
    """The provider keeps 5-minute bars for only a couple of months. An old day refilled to add
    SPY must not have its 10:00 and 11:30 prices blanked by the poorer fetch."""
    row = _row()
    refilled = fill_row(row, daily=[], intraday=[], day=DAY, now=CLOSED,
                        market=market_day(_spy_daily(), [], DAY))
    assert (refilled.open_price, refilled.price_1000, refilled.price_1130,
            refilled.close_price) == (100.0, 101.0, 102.0, 103.0)
    assert refilled.outcome_note == ""                       # nothing is still missing
    assert refilled.spy_move_close == pytest.approx(0.01)


def test_a_reading_that_is_still_missing_is_still_named():
    refilled = fill_row(_row(p1000=None), daily=[], intraday=[], day=DAY, now=CLOSED)
    assert "10:00 ET" in refilled.outcome_note


# --------------------------------------------------------------------------- #
# the day
# --------------------------------------------------------------------------- #
def _world(**extra) -> FakeBars:
    daily = {"AAA": daily_series(end=DAY + timedelta(days=1), sessions=1, close=103.0),
             BENCHMARK: _spy_daily()}
    daily.update(extra.get("daily", {}))
    intraday = {"AAA": regular_session(DAY, price=101.0), BENCHMARK: _spy_intraday()}
    intraday.update(extra.get("intraday", {}))
    return FakeBars(daily=daily, intraday=intraday)


def _asked(bars: FakeBars) -> set:
    return {t for call in bars.daily_calls for t in call[0]}


def test_outcomes_records_spy_beside_the_names(tmp_path):
    write_day(DAY, [_row(open_=None, p1000=None, p1130=None, close=None),
                    _row("BBB", group=GROUP_BASELINE, open_=None, p1000=None, p1130=None,
                         close=None)], root=tmp_path)
    bars = _world(daily={"BBB": daily_series(end=DAY + timedelta(days=1), sessions=1)},
                  intraday={"BBB": regular_session(DAY, price=50.0)})
    fill_day(DAY, daily=bars, intraday=bars, root=tmp_path, now=CLOSED)
    assert BENCHMARK in _asked(bars)
    back = {r.ticker: r for r in read_day(DAY, root=tmp_path)}
    for row in back.values():
        assert row.spy_move_close == pytest.approx(0.01)
        assert row.spy_move_1000 == pytest.approx(0.004)
        assert row.rel_spy_close is not None


def test_a_day_complete_except_for_spy_costs_one_request_not_a_refetch(tmp_path):
    """The point of the backfill: an old day gets its SPY columns without every name being
    fetched again."""
    write_day(DAY, [_row(), _row("BBB", group=GROUP_BASELINE)], root=tmp_path)
    bars = _world()
    fill_day(DAY, daily=bars, intraday=bars, root=tmp_path, now=CLOSED)
    assert _asked(bars) == {BENCHMARK}
    back = read_day(DAY, root=tmp_path)
    assert all(has_market(r) for r in back)
    assert back[0].open_price == 100.0                       # untouched


def test_a_day_that_already_has_spy_and_prices_fetches_nothing(tmp_path):
    write_day(DAY, [_row(spy_move_close=0.01)], root=tmp_path)
    bars = _world()
    fill_day(DAY, daily=bars, intraday=bars, root=tmp_path, now=CLOSED)
    assert bars.daily_calls == [] and bars.intraday_calls == []


def test_a_day_with_no_intraday_says_which_legs_stay_blank(tmp_path):
    write_day(DAY, [_row()], root=tmp_path)
    bars = FakeBars(daily={BENCHMARK: _spy_daily()})
    report = fill_day(DAY, daily=bars, intraday=bars, root=tmp_path, now=CLOSED)
    row = read_day(DAY, root=tmp_path)[0]
    assert row.spy_move_close == pytest.approx(0.01)
    assert row.spy_move_1000 is None and row.rel_spy_1000 is None
    assert any("no intraday bars" in note and "10:00/11:30" in note for note in report.notes)


def test_a_day_with_no_spy_at_all_is_said_not_silently_blank(tmp_path):
    """An absent section is indistinguishable from a feature that was never switched on."""
    write_day(DAY, [_row()], root=tmp_path)
    report = fill_day(DAY, daily=FakeBars(), intraday=FakeBars(), root=tmp_path, now=CLOSED)
    assert any(note.startswith("SPY: no daily bar") for note in report.notes)
    assert not has_market(read_day(DAY, root=tmp_path)[0])


# --------------------------------------------------------------------------- #
# the record: old CSVs still load
# --------------------------------------------------------------------------- #
def test_the_new_columns_round_trip(tmp_path):
    write_day(DAY, [apply_market(_row(), market_day(_spy_daily(), _spy_intraday(), DAY))],
              root=tmp_path)
    back = read_day(DAY, root=tmp_path)[0]
    assert back.spy_move_close == pytest.approx(0.01)
    assert back.rel_spy_close == pytest.approx(0.02)
    assert back.move_1130 == pytest.approx(0.02)


def test_an_old_csv_without_the_market_columns_still_loads_and_scores(tmp_path):
    new = {"move_1000", "move_1130", "move_close", "spy_move_1000", "spy_move_1130",
           "spy_move_close", "rel_spy_1000", "rel_spy_1130", "rel_spy_close"}
    old = [f for f in FIELDS if f not in new]
    path = ledger_path(DAY, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    values = {"ticker": "AAA", "group": GROUP_CANDIDATE, "gap_pct": "0.05",
              "open_price": "100", "price_1000": "101", "price_1130": "102",
              "close_price": "103"}
    path.write_text(",".join(old) + "\n" + ",".join(values.get(f, "") for f in old) + "\n",
                    encoding="utf-8")
    row = read_day(DAY, root=tmp_path)[0]
    assert row.spy_move_close is None and row.rel_spy_close is None
    card = score({DAY: [row]}, config=GapConfig(min_days_to_score=1))
    close = next(c for c in card.checkpoints if c.label == "the close")
    assert close.candidate_move.mean == pytest.approx(0.03)     # raw is computed from prices
    assert close.candidate_vs_spy.n == 0                        # no SPY: skipped, not zero


# --------------------------------------------------------------------------- #
# the scorecard
# --------------------------------------------------------------------------- #
def _scored(ticker, group, *, close, spy=0.01, gap=0.05) -> LedgerRow:
    return _row(ticker, group=group, gap=gap, close=close, spy_move_close=spy,
                spy_move_1000=spy, spy_move_1130=spy)


def _card():
    return score({DAY: [_scored("AAA", GROUP_CANDIDATE, close=104.0),        # +4% raw, +3 rel
                        _scored("BBB", GROUP_CANDIDATE, close=102.0),        # +2% raw, +1 rel
                        _scored("CCC", GROUP_BASELINE, close=101.0),         # +1% raw, 0 rel
                        _scored("DDD", GROUP_BASELINE, close=99.0)]},        # -1% raw, -2 rel
                 config=GapConfig(min_days_to_score=1))


def test_the_scorecard_shows_raw_and_beyond_spy_for_candidates_and_control():
    close = next(c for c in _card().checkpoints if c.label == "the close")
    assert close.candidate_move.mean == pytest.approx(0.03)
    assert close.candidate_vs_spy.mean == pytest.approx(0.02)
    assert close.baseline_move.mean == pytest.approx(0.0)
    assert close.baseline_vs_spy.mean == pytest.approx(-0.01)


def test_the_screens_edge_can_be_the_markets_not_the_screens():
    """The reason this exists: both groups 'carried on' on a rising day, but only the relative
    number says whether the candidates beat what the market handed everyone."""
    close = next(c for c in _card().checkpoints if c.label == "the close")
    raw_edge = close.candidate_move.mean - close.baseline_move.mean
    market_edge = close.candidate_vs_spy.mean - close.baseline_vs_spy.mean
    assert raw_edge == pytest.approx(market_edge)     # SPY is common to both groups here
    assert close.candidate_vs_spy.mean > 0


def test_a_day_without_spy_is_skipped_for_the_relative_number_only():
    rows = [_scored("AAA", GROUP_CANDIDATE, close=104.0),
            _row("BBB", group=GROUP_CANDIDATE, close=102.0)]           # no SPY on this one
    card = score({DAY: rows}, config=GapConfig(min_days_to_score=1))
    close = next(c for c in card.checkpoints if c.label == "the close")
    assert close.candidate_move.n == 2
    assert close.candidate_vs_spy.n == 1
    assert close.candidate_vs_spy.mean == pytest.approx(0.03)


def test_the_printed_scorecard_carries_the_market_block():
    text = "\n".join(_card().lines())
    assert "Against the market (SPY), in the gap's direction, from the open:" in text
    assert "the close beyond SPY: candidates n=2: mean +2.00%" in text
    assert "control n=2: mean -1.00%" in text


def test_without_any_filled_outcome_there_is_no_market_block():
    card = score({DAY: [LedgerRow(date=DAY.isoformat(), ticker="AAA", gap_pct=0.05)]})
    assert "Against the market" not in "\n".join(card.lines())


# --------------------------------------------------------------------------- #
# the viewer
# --------------------------------------------------------------------------- #
def test_the_market_table_has_candidates_and_control_at_every_checkpoint():
    text = market_markdown(_card())
    assert text.count("| Candidates |") == 3 and text.count("| Control group |") == 3
    assert "| the close | Candidates | +3.00% / +3.00% | +2.00% / +2.00% | 2 | 2 |" in text
    assert market_markdown(score({})) == ""


def test_the_per_row_details_show_raw_spy_and_beyond():
    row = apply_market(_row(), market_day(_spy_daily(500.0, 505.0),
                                          _spy_intraday(502.0, 503.0), DAY))
    pairs = dict(details_of(row))
    assert pairs["Move from the open (10:00 / 11:30 / close)"] == "+1.00% / +2.00% / +3.00%"
    assert pairs["SPY over the same spans"] == "+0.40% / +0.60% / +1.00%"
    assert pairs["Beyond SPY"] == "+0.60% / +1.40% / +2.00%"


def test_a_row_with_no_spy_shows_its_raw_move_and_omits_the_rest():
    raw, spy, beyond = market_triplets(_row())
    assert raw == "+1.00% / +2.00% / +3.00%" and spy == "" and beyond == ""
    pairs = dict(details_of(_row()))
    assert "SPY over the same spans" not in pairs and "Beyond SPY" not in pairs
