"""GAP-LEDGER-1 — the scorecard: did the move carry on, and did it carry on MORE?

Four properties, each with a way of being wrong that would flatter the screen:

* continuation is measured FROM THE OPEN. From the previous close it would count the gap
  itself, so a name that opened up 8% and fell all morning would score as a continuation.
* a flat reading is NOT a continuation. Counting equality as a carry-on would inflate every
  rate by however many names went nowhere.
* a missing price is NOT a failure to continue. Counting it as one would penalise exactly
  the thin names the screen is least able to fill.
* below the day floor there is no finding, only a day count. A fortnight of mornings is a
  mood, and presenting a rate from one is the mistake the floor exists to prevent.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from aristos_council.gap_ledger.config import GapConfig
from aristos_council.gap_ledger.ledger import GROUP_BASELINE, GROUP_CANDIDATE, LedgerRow
from aristos_council.gap_ledger.score import (CHECKPOINT_COLUMNS, GroupRate, continued,
                                              score)

DAY = date(2026, 9, 22)
SHORT = GapConfig(min_days_to_score=2)


def _row(*, group=GROUP_CANDIDATE, gap=0.08, open_price=50.0, p1000=51.0, p1130=52.0,
         close=53.0, ticker="AAA") -> LedgerRow:
    return LedgerRow(date=DAY.isoformat(), ticker=ticker, group=group, gap_pct=gap,
                     open_price=open_price, price_1000=p1000, price_1130=p1130,
                     close_price=close)


# --------------------------------------------------------------------------- #
# one row
# --------------------------------------------------------------------------- #
def test_a_gap_up_that_kept_rising_carried_on():
    assert continued(_row(gap=0.08, open_price=50.0, p1000=51.0), "price_1000") is True


def test_a_gap_up_that_faded_did_not_carry_on():
    assert continued(_row(gap=0.08, open_price=50.0, p1000=49.0), "price_1000") is False


def test_a_gap_down_that_kept_falling_carried_on():
    assert continued(_row(gap=-0.08, open_price=50.0, p1000=49.0), "price_1000") is True


def test_a_gap_down_that_bounced_did_not_carry_on():
    assert continued(_row(gap=-0.08, open_price=50.0, p1000=51.0), "price_1000") is False


def test_continuation_is_measured_from_the_open_not_from_the_previous_close():
    """Opened up on the gap, then fell all morning: NOT a continuation, though the price is
    still far above the previous close."""
    row = LedgerRow(gap_pct=0.08, previous_close=50.0, open_price=54.0, price_1000=52.0)
    assert continued(row, "price_1000") is False


def test_a_flat_reading_is_not_a_continuation():
    assert continued(_row(open_price=50.0, p1000=50.0), "price_1000") is False


@pytest.mark.parametrize("kwargs", [{"open_price": None}, {"p1000": None}, {"gap": None},
                                    {"gap": 0.0}])
def test_an_unscoreable_row_is_none_not_false(kwargs):
    assert continued(_row(**kwargs), "price_1000") is None


# --------------------------------------------------------------------------- #
# the rates
# --------------------------------------------------------------------------- #
def test_a_group_with_nothing_scoreable_has_no_rate_rather_than_a_zero_rate():
    empty = GroupRate()
    assert empty.rate is None
    assert "no scoreable names" in empty.sentence()


def test_missing_prices_shrink_the_denominator_rather_than_counting_as_failures():
    days = {DAY: [_row(ticker="AAA", p1000=51.0), _row(ticker="BBB", p1000=None)]}
    card = score(days, config=SHORT)
    at_ten = next(c for c in card.checkpoints if c.label == "10:00 ET")
    assert at_ten.candidates.scored == 1
    assert at_ten.candidates.rate == 1.0


def test_both_groups_are_scored_and_the_edge_is_the_difference():
    days = {DAY: [_row(ticker="AAA", p1000=51.0),
                  _row(ticker="BBB", p1000=49.0),
                  _row(ticker="CCC", group=GROUP_BASELINE, gap=0.004, p1000=51.0),
                  _row(ticker="DDD", group=GROUP_BASELINE, gap=0.004, p1000=49.0),
                  _row(ticker="EEE", group=GROUP_BASELINE, gap=0.004, p1000=49.0),
                  _row(ticker="FFF", group=GROUP_BASELINE, gap=0.004, p1000=49.0)]}
    card = score(days, config=SHORT)
    at_ten = next(c for c in card.checkpoints if c.label == "10:00 ET")
    assert at_ten.candidates.rate == pytest.approx(0.5)
    assert at_ten.baseline.rate == pytest.approx(0.25)
    assert at_ten.edge == pytest.approx(0.25)


def test_the_close_is_scored_as_a_third_checkpoint():
    labels = [label for label, _ in CHECKPOINT_COLUMNS]
    assert labels == ["10:00 ET", "11:30 ET", "the close"]
    card = score({DAY: [_row()]}, config=SHORT)
    assert [c.label for c in card.checkpoints] == labels


# --------------------------------------------------------------------------- #
# the verdict
# --------------------------------------------------------------------------- #
def test_below_the_day_floor_there_is_no_finding():
    card = score({DAY: [_row()]})            # default floor is 40 days
    assert not card.enough_days
    assert "Not enough days: 1 scored of 40" in card.verdict
    assert "no rate here is a finding yet" in card.verdict.lower()


def test_the_day_floor_counts_days_with_filled_outcomes_not_days_logged():
    unfilled = [_row(ticker="AAA", open_price=None, p1000=None, p1130=None, close=None)]
    days = {DAY: unfilled, DAY - timedelta(days=1): [_row()],
            DAY - timedelta(days=2): [_row()]}
    card = score(days, config=SHORT)
    assert card.days_logged == 3
    assert card.days_scored == 2
    assert card.enough_days


def test_a_clean_lead_at_every_checkpoint_is_said_plainly():
    days = {DAY - timedelta(days=n): [
        _row(ticker="AAA", p1000=51.0, p1130=52.0, close=53.0),
        _row(ticker="CCC", group=GROUP_BASELINE, gap=0.004, p1000=49.0, p1130=49.0,
             close=49.0)] for n in range(3)}
    card = score(days, config=SHORT)
    assert "carried on more often than the control group at every checkpoint" in card.verdict


def test_no_lead_anywhere_is_said_just_as_plainly():
    days = {DAY - timedelta(days=n): [
        _row(ticker="AAA", p1000=49.0, p1130=49.0, close=49.0),
        _row(ticker="CCC", group=GROUP_BASELINE, gap=0.004, p1000=51.0, p1130=52.0,
             close=53.0)] for n in range(3)}
    card = score(days, config=SHORT)
    assert "did NOT carry on more often" in card.verdict


def test_a_mixed_record_is_reported_as_mixed():
    days = {DAY - timedelta(days=n): [
        _row(ticker="AAA", p1000=51.0, p1130=49.0, close=49.0),
        _row(ticker="CCC", group=GROUP_BASELINE, gap=0.004, p1000=49.0, p1130=52.0,
             close=53.0)] for n in range(3)}
    card = score(days, config=SHORT)
    assert card.verdict.startswith("Mixed:")


def test_days_logged_but_nothing_filled_says_to_run_outcomes():
    unfilled = [_row(open_price=None, p1000=None, p1130=None, close=None)]
    card = score({DAY: unfilled, DAY - timedelta(days=1): unfilled},
                 config=GapConfig(min_days_to_score=0))
    assert card.checkpoints == ()
    assert "Run `outcomes`" in card.verdict


def test_an_empty_record_scores_without_raising():
    card = score({})
    assert card.days_logged == 0
    assert not card.enough_days


def test_the_lines_carry_the_counts_and_the_verdict():
    lines = "\n".join(score({DAY: [_row()]}, config=SHORT).lines())
    assert "Days logged: 1" in lines
    assert "1 candidates, 0 baseline" in lines
    assert "10:00 ET" in lines
