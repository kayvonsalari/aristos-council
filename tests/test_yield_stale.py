"""YIELD-STALE-1 — a dividend record that stops short must not understate the yield.

The US-listed series for CNQ and Eni both ended mid-2025, two payments short of the year
(docs/diagnosis_dividend_history_2026-09-16.md). The CUT rule survives that: a payment that
is merely ABSENT does not make the typical payment fall. The YIELD does not survive it. A
trailing-twelve-month dividend built from half a year of payments, divided by a full year of
price, is a yield roughly half what the company actually pays — and nothing about the
resulting number looks wrong, which is exactly what makes it worth catching.

The test is RELATIVE, not absolute: an annual payer 300 days after its payment is perfectly
current and a quarterly payer 300 days after its last is not. So the yardstick is the gap
the name usually pays at.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from aristos_council.tools.screening import (STALE_ANNUAL_DAYS, STALE_GAP_MULTIPLE,
                                             STALE_MIN_DAYS, dividend_record_staleness,
                                             min_yield_criterion)
from aristos_council.data.adapter import Fundamentals

TODAY = date(2026, 9, 17)


def _series(n, every, last_days_ago):
    end = TODAY - timedelta(days=last_days_ago)
    return sorted(end - timedelta(days=every * i) for i in range(n))


def _stale(dates):
    return dividend_record_staleness(dates, today=TODAY)


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #
def test_the_CNQ_shape_is_stale():
    """Quarterly, last payment 200 days ago — two payments missing from the record."""
    is_stale, reason = _stale(_series(8, 91, 200))
    assert is_stale
    assert "dividend record stops 2026-03-01" in reason
    assert "last payment 200 days ago against a usual gap of 91 days" in reason
    assert reason.endswith("yield not stated")


def test_a_current_quarterly_payer_is_not_stale():
    assert _stale(_series(8, 91, 40))[0] is False


def test_a_quarterly_payer_between_payments_is_not_stale():
    """A name one quarter-and-a-bit out is between payments, not missing any."""
    assert 130 < 91 * STALE_GAP_MULTIPLE             # inside twice the usual gap
    assert _stale(_series(8, 91, 130))[0] is False


def test_the_day_floor_protects_a_fast_payer():
    """A MONTHLY payer's usual gap is ~30 days, so twice it is 60 — without a floor, a
    name six weeks past its payment would read as a record that had stopped. The floor is
    what makes the relative test safe at the fast end."""
    assert _stale(_series(8, 30, 100))[0] is False   # 100 days, but under the floor
    assert STALE_MIN_DAYS == 120
    assert _stale(_series(8, 30, 150))[0] is True    # past the floor AND the multiple


def test_an_annual_payer_is_not_stale_at_300_days():
    """The case an absolute threshold would get wrong: 300 days after an annual dividend
    is normal, and a 120-day floor would flag every annual payer on earth."""
    assert _stale(_series(6, 365, 300))[0] is False


def test_an_annual_payer_IS_stale_after_two_years():
    is_stale, reason = _stale(_series(6, 365, 800))
    assert is_stale and "800 days ago" in reason
    assert STALE_ANNUAL_DAYS == 730


def test_too_few_payments_is_never_stale():
    """With two payments there is no USUAL gap to be late against, and asserting one from
    two points would be inventing the yardstick."""
    assert _stale(_series(2, 91, 400))[0] is False
    assert _stale([])[0] is False
    assert _stale(None)[0] is False


def test_a_cadence_change_uses_the_RECENT_gap_not_the_old_one():
    """A name that moved from annual to quarterly is judged on how it pays NOW."""
    old = [date(2020, 6, 1), date(2021, 6, 1), date(2022, 6, 1)]
    recent = _series(6, 91, 200)
    assert _stale(old + recent)[0] is True           # 200 days out on a 91-day cadence


# --------------------------------------------------------------------------- #
# What it does to the yield
# --------------------------------------------------------------------------- #
def _fund(dates, **kw):
    base = dict(ticker="X", dividend_per_share=2.0,
                dividend_payment_dates=[d.isoformat() for d in dates])
    base.update(kw)
    return Fundamentals(**base)


def test_the_yield_criterion_abstains_on_a_stale_record():
    r = min_yield_criterion(_fund(_series(8, 91, 200)), min_yield=0.015, last_close=100.0)
    assert r.passed is None                       # NOT-EVAL: never a fail, house rule 3
    assert r.observed is None and r.basis == "abstained"
    assert "dividend record stops" in r.note and "yield not stated" in r.note


def test_the_yield_criterion_is_unchanged_on_a_current_record():
    r = min_yield_criterion(_fund(_series(8, 91, 40)), min_yield=0.015, last_close=100.0)
    assert r.passed is True and r.observed == pytest.approx(0.02)


def test_a_record_with_no_dates_behaves_exactly_as_before():
    """Every Fundamentals written before this field existed carries none, and must be
    judged exactly as it was."""
    r = min_yield_criterion(Fundamentals(ticker="X", dividend_per_share=2.0),
                            min_yield=0.015, last_close=100.0)
    assert r.passed is True and r.observed == pytest.approx(0.02)


def test_the_factor_abstains_with_the_same_reason():
    """One helper, so the screen and the rank cannot disagree about whether a record still
    reaches the present."""
    from aristos_council.factors import FACTOR_REGISTRY, FactorInputs

    spec = FACTOR_REGISTRY["net_payout_yield"]
    stale = FactorInputs(ticker="X",
                         fundamentals=_fund(_series(8, 91, 200), dividend_yield=0.04))
    current = FactorInputs(ticker="X",
                           fundamentals=_fund(_series(8, 91, 40), dividend_yield=0.04))

    assert spec.fn(stale) is None
    assert "dividend record stops" in spec.source_fn(stale)
    assert spec.fn(current) == pytest.approx(0.04)
    assert "abstained" not in spec.source_fn(current)


def test_KNOWN_LIMIT_staleness_is_measured_against_TODAY():
    """Fundamentals carries no as_of date, so staleness is judged against the clock.

    The consequence, stated rather than discovered: a FROZEN run replayed months later is
    judged as of the replay date, so its dividend record will read as stale even though it
    was current when the run was made. Replay is for reproducing a verdict, and a verdict
    that abstained on a stale record still reproduces as an abstention — but the reason
    text will name a gap measured from the wrong day. Threading the run's own `today` into
    the criterion would fix it and is a wider change than this item.
    """
    from aristos_council.tools.screening import _today_for

    # No as_of field -> today. The getattr is defensive, not aspirational.
    assert _today_for(Fundamentals(ticker="X")) == date.today()
