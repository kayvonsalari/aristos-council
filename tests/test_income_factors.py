"""FACTOR-COVER-1 / FACTOR-NDOI-1 — the two income-durability ranking legs.

A cyclical income lens cannot rank on how LARGE the payout is alone; the question is
whether the payout survives the cycle. These two legs answer it:

  payout_coverage_fcf           how much of the cash generated is being handed out
  net_debt_to_operating_income  how much debt sits on top of the payout

Both are LOW-direction and both smooth over four years, because a single peak year makes
a cyclical look both better covered and less levered than it is. Valero earned 15,751m of
operating income in FY2022 and 4,312m in FY2025: the same balance sheet reads three and a
half times heavier on the second number, and neither year is the truth.

The property that matters most is SHARED ARITHMETIC. ``payout_coverage_fcf`` is the same
helper the ``max_payout_ratio_fcf`` screen criterion calls, so the number in the rules
table and the number in the ranked table are one number — not two implementations that
happen to agree today. That is asserted to the float below, and it is the same discipline
FORENSIC-1's legs follow with their like-named criteria.
"""

from __future__ import annotations

import pytest

from aristos_council.data.adapter import Fundamentals
from aristos_council.factors import FACTOR_REGISTRY, FactorInputs
from aristos_council.tools.screening import (
    max_payout_fcf_criterion,
    net_debt_to_operating_income,
    payout_coverage_fcf,
)

_COVER = FACTOR_REGISTRY["payout_coverage_fcf"]
_NDOI = FACTOR_REGISTRY["net_debt_to_operating_income"]


def _fund(**kw):
    base = dict(ticker="X", name="X",
                dividend_per_share=2.0, dividends_paid=1_000.0,
                free_cash_flow_annual=[2_000.0, 2_000.0, 2_000.0, 2_000.0],
                operating_income=[1_000.0, 1_000.0, 1_000.0, 1_000.0],
                total_debt=3_000.0, total_cash=1_000.0)
    base.update(kw)
    return Fundamentals(**base)


def _fi(**kw):
    return FactorInputs(ticker="X", fundamentals=_fund(**kw))


def _value(factor, **kw):
    return factor.fn(_fi(**kw))


def _source(factor, **kw):
    return factor.source_fn(_fi(**kw))


# --------------------------------------------------------------------------- #
# payout_coverage_fcf
# --------------------------------------------------------------------------- #
def test_coverage_is_dividends_over_four_year_free_cash_flow():
    # 1,000 paid against a 2,000 mean -> half the cash generated is handed out.
    assert _value(_COVER) == pytest.approx(0.5)


def test_coverage_smooths_over_the_window_not_the_latest_year():
    """One crushed year must not read as a broken dividend."""
    lumpy = _value(_COVER, free_cash_flow_annual=[500.0, 2_500.0, 2_500.0, 2_500.0])
    assert lumpy == pytest.approx(1_000.0 / 2_000.0)      # mean is still 2,000


def test_coverage_equals_the_criterions_observed_value_to_the_float():
    """The property the shared helper exists for."""
    for fcf in ([2_000.0] * 4, [1_100.0] * 4, [500.0, 2_500.0, 2_500.0, 2_500.0]):
        f = _fund(free_cash_flow_annual=fcf)
        factor = _COVER.fn(FactorInputs(ticker="X", fundamentals=f))
        criterion = max_payout_fcf_criterion(f, max_payout=0.80)
        assert criterion.basis == "fcf"
        assert factor == criterion.observed        # identical, not merely close


def test_coverage_abstains_on_non_positive_four_year_cash_flow():
    """The utilities lesson: investment-driven, not dividend distress. Never a zero."""
    assert _value(_COVER, free_cash_flow_annual=[-100.0, -100.0, -100.0, -100.0]) is None
    assert "≤ 0" in _source(_COVER, free_cash_flow_annual=[-100.0] * 4)


def test_coverage_abstains_when_the_dividend_figure_is_missing():
    assert _value(_COVER, dividend_per_share=None) is None
    assert "dividend_per_share is null" in _source(_COVER, dividend_per_share=None)
    assert _value(_COVER, dividends_paid=None) is None
    assert "dividends paid unavailable" in _source(_COVER, dividends_paid=None)


def test_a_non_payer_has_coverage_zero_not_an_abstention():
    """It pays out none of its cash, which is a fact rather than a gap."""
    assert _value(_COVER, dividend_per_share=0.0) == 0.0


def test_coverage_abstains_where_the_criterion_takes_its_marked_eps_fallback():
    """A screen floor may accept a disclosed GAAP proxy; a RANK COLUMN may not — it would
    order names against two different measures without saying so."""
    f = _fund(free_cash_flow_annual=[2_000.0], payout_ratio=0.4)
    assert max_payout_fcf_criterion(f, max_payout=0.80).basis == "eps"   # falls back
    assert _COVER.fn(FactorInputs(ticker="X", fundamentals=f)) is None   # abstains
    assert "fewer than 2 years" in _COVER.source_fn(
        FactorInputs(ticker="X", fundamentals=f))


def test_coverage_is_registered_low_direction_and_explained():
    assert _COVER.direction == "low"
    assert _COVER.label == "Dividend coverage (dividends / 4-year free cash flow)"
    assert "cash" in _COVER.glossary.lower()


# --------------------------------------------------------------------------- #
# net_debt_to_operating_income
# --------------------------------------------------------------------------- #
def test_net_debt_over_through_cycle_operating_income():
    # (3,000 - 1,000) / a 1,000 mean = 2.0 years of average profit.
    assert _value(_NDOI) == pytest.approx(2.0)


def test_net_cash_is_negative_and_therefore_ranks_best():
    """Not a special case — the LOW direction handles it."""
    v = _value(_NDOI, total_debt=500.0, total_cash=2_000.0)
    assert v == pytest.approx(-1.5) and v < 0


def test_the_window_is_the_through_cycle_one_not_the_latest_year():
    """The Valero shape: a peak year would make the same balance sheet look light."""
    peaky = _value(_NDOI, operating_income=[400.0, 1_200.0, 1_200.0, 1_200.0])
    assert peaky == pytest.approx(2_000.0 / 1_000.0)      # mean 1,000, not the latest 400


def test_ndoi_abstains_on_a_loss_making_four_year_mean():
    """The ratio would invert and more debt would read as better."""
    assert _value(_NDOI, operating_income=[-500.0] * 4) is None
    assert "would invert" in _source(_NDOI, operating_income=[-500.0] * 4)


def test_ndoi_abstains_when_a_balance_sheet_figure_is_missing():
    assert _value(_NDOI, total_debt=None) is None
    assert "total debt unavailable" in _source(_NDOI, total_debt=None)
    assert _value(_NDOI, total_cash=None) is None
    assert "cash unavailable" in _source(_NDOI, total_cash=None)


def test_ndoi_abstains_without_operating_income_history():
    assert _value(_NDOI, operating_income=[]) is None
    assert "no operating-income history" in _source(_NDOI, operating_income=[])


def test_ndoi_is_registered_low_direction_and_explained():
    assert _NDOI.direction == "low"
    assert _NDOI.label == "Net debt to operating profit (4-year average)"
    assert "negative" in _NDOI.glossary.lower()


def test_ndoi_is_generic_enough_for_the_planned_quality_lens():
    """Built once, here: it reads only balance-sheet and operating-income fields and
    knows nothing about dividends or income lenses."""
    f = Fundamentals(ticker="Q", total_debt=1_000.0, total_cash=0.0,
                     operating_income=[500.0] * 4)      # no dividend fields at all
    value, note = net_debt_to_operating_income(f)
    assert value == pytest.approx(2.0) and "net debt" in note


def test_the_primitive_and_the_factor_report_the_same_number():
    f = _fund()
    assert _COVER.fn(FactorInputs(ticker="X", fundamentals=f)) == payout_coverage_fcf(f)[0]
    assert _NDOI.fn(FactorInputs(ticker="X", fundamentals=f)) == \
        net_debt_to_operating_income(f)[0]
