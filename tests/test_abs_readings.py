"""ABS-READINGS-1 — debt-and-cash and the growth record, on fabricated statements.

These two readings exist because every lens in this repo is RELATIVE: a BUY means "near
the top of the list this ran on", which is no answer at all to "what is this company
like". They do not vote and no strategy selects them.

What is actually pinned here is the abstention contract, because that is where a reading
like this goes wrong. A company with no interest bill does not have infinite cover. A
company whose earnings were negative ten years ago does not have a ten-year compound
growth rate. A missing cash balance is not a zero cash balance. Each of those is a
sentence, not a number, and each has a test.
"""
from __future__ import annotations

import pytest

from aristos_council.abs_readings import (GROWTH_WINDOWS, MIN_GROWTH_YEARS, Reading,
                                          debt_and_cash, growth_record)
from aristos_council.data.adapter import Fundamentals


def _f(**kw) -> Fundamentals:
    aligned = kw.pop("aligned", None)
    base = dict(ticker="X", currency="USD")
    base.update(kw)
    if aligned:
        base["aligned_annual"] = aligned
    return Fundamentals(**base)


# =========================================================================== #
# A. debt and cash
# =========================================================================== #
def test_net_debt_is_debt_minus_cash_and_reads_as_a_sentence():
    out = debt_and_cash(_f(total_debt=50e9, total_cash=10e9))
    assert out.net_debt.value == pytest.approx(40e9)
    assert out.net_debt.label == "owes 40.0bn USD net of cash"


def test_more_cash_than_debt_is_said_the_other_way_round():
    out = debt_and_cash(_f(total_debt=5e9, total_cash=20e9))
    assert out.net_debt.value == pytest.approx(-15e9)
    assert "holds 15.0bn USD more cash than debt" in out.net_debt.label


def test_a_MISSING_cash_balance_is_disclosed_not_treated_as_zero():
    """house rule 3 on the balance sheet: an absent value is not a zero one. The figure is
    still given — it is gross debt — and it says which it is."""
    out = debt_and_cash(_f(total_debt=50e9))
    assert out.net_debt.value == pytest.approx(50e9)
    assert "cash not reported, so this is gross debt" in out.net_debt.label


def test_years_of_cash_flow_to_repay_the_debt():
    out = debt_and_cash(_f(total_debt=50e9, total_cash=10e9, operating_cash_flow=8e9))
    assert out.net_debt_to_ocf.value == pytest.approx(5.0)
    assert out.net_debt_to_ocf.label == (
        "would take 5.0 years of operating cash flow to repay its debt")


def test_negative_operating_cash_flow_abstains_and_says_why():
    out = debt_and_cash(_f(total_debt=50e9, total_cash=0.0, operating_cash_flow=-2e9))
    assert not out.net_debt_to_ocf.available
    assert "not positive" in out.net_debt_to_ocf.note
    assert "no number of years" in out.net_debt_to_ocf.note


def test_a_company_with_no_net_debt_has_nothing_to_repay():
    out = debt_and_cash(_f(total_debt=1e9, total_cash=9e9, operating_cash_flow=2e9,
                           free_cash_flow=1e9))
    assert out.net_debt_to_ocf.label == "has no net debt to repay"
    assert out.years_to_repay.label == "has no net debt to repay"


def test_interest_cover_is_operating_income_over_the_interest_bill():
    out = debt_and_cash(_f(operating_income=12e9,
                           aligned={"interest_expense": [1.5e9]}))
    assert out.interest_cover.value == pytest.approx(8.0)
    assert out.interest_cover.label == "earns 8.0 times its interest bill"


def test_a_negative_interest_line_is_read_as_its_size():
    """Providers sign the interest line either way; the bill is its magnitude."""
    out = debt_and_cash(_f(operating_income=12e9,
                           aligned={"interest_expense": [-1.5e9]}))
    assert out.interest_cover.value == pytest.approx(8.0)


def test_ZERO_interest_expense_abstains_rather_than_reporting_infinity():
    """A company with no interest bill does not have infinite cover — it has no ratio, and
    a huge number would read as a strength measured on the same scale as a real one."""
    out = debt_and_cash(_f(operating_income=12e9, aligned={"interest_expense": [0.0]}))
    assert not out.interest_cover.available
    assert "no interest expense" in out.interest_cover.note
    assert "inf" not in out.interest_cover.text().lower()


def test_a_MISSING_interest_expense_abstains_differently_from_a_zero_one():
    out = debt_and_cash(_f(operating_income=12e9))
    assert not out.interest_cover.available
    assert "not reported" in out.interest_cover.note


def test_years_to_repay_from_free_cash_flow():
    out = debt_and_cash(_f(total_debt=50e9, total_cash=10e9, free_cash_flow=5e9))
    assert out.years_to_repay.value == pytest.approx(8.0)
    assert "8.0 years of free cash flow" in out.years_to_repay.label


def test_non_positive_free_cash_flow_abstains_and_says_what_that_means():
    out = debt_and_cash(_f(total_debt=50e9, total_cash=0.0, free_cash_flow=-1e9))
    assert not out.years_to_repay.available
    assert "not being repaid out of it at all" in out.years_to_repay.note


def test_no_fundamentals_at_all_abstains_on_every_leg():
    out = debt_and_cash(None)
    assert all(not r.available for r in (out.net_debt, out.net_debt_to_ocf,
                                         out.interest_cover, out.years_to_repay))
    assert all("no fundamentals" in line for line in out.lines())


def test_every_abstention_reads_as_a_sentence_not_a_blank():
    for line in debt_and_cash(_f()).lines():
        assert line.startswith("not stated — ") and len(line) > 20


# =========================================================================== #
# B. the growth record
# =========================================================================== #
def _revenue(values):
    return _f(aligned={"total_revenue": list(values)})


def test_a_compound_revenue_rate_over_five_years():
    # newest-first: 10 years of doubling every 5
    out = growth_record(_revenue([200.0, 180, 160, 140, 120, 100.0]))
    five = out.revenue.cagr[5]
    assert five.value == pytest.approx(0.1487, abs=1e-3)      # 200/100 over 5y
    assert "over 5 years" in five.label


def test_it_uses_the_years_that_exist_and_SAYS_how_many():
    out = growth_record(_revenue([150.0, 130, 110, 100.0]))   # 4 points = 3 years
    ten = out.revenue.cagr[10]
    assert ten.available
    assert "only 3 of 10 years available" in ten.label


def test_below_three_years_it_abstains():
    out = growth_record(_revenue([120.0, 100.0]))
    assert MIN_GROWTH_YEARS == 3
    for window in GROWTH_WINDOWS:
        assert not out.revenue.cagr[window].available
        assert "at least 3" in out.revenue.cagr[window].note


def test_a_non_positive_start_abstains_rather_than_rooting_a_negative():
    """A rate off a negative base is not a growth rate — the same discipline the PEG and
    revenue-CAGR criteria already use."""
    out = growth_record(_f(aligned={"diluted_eps": [3.0, 2.0, 1.0, -1.0, -2.0, -3.0]}))
    five = out.eps.cagr[5]
    assert not five.available
    assert "was not positive 5 years ago" in five.note


def test_grew_in_n_of_the_years_reported():
    out = growth_record(_revenue([150.0, 140, 120, 130, 110, 100.0]))
    # pairs newest-first: 150>140, 140>120, 120<130, 130>110, 110>100  -> 4 of 5
    assert out.revenue.grew_in.value == 4.0
    assert out.revenue.grew_in.label == "revenue grew in 4 of the 5 years reported"


def test_the_growth_count_states_the_years_REPORTED_not_a_flat_ten():
    out = growth_record(_revenue([120.0, 110, 100.0]))
    assert "of the 2 years reported" in out.revenue.grew_in.label


def test_holes_in_the_series_are_dropped_and_the_span_says_so():
    """A company with 2018 and 2024 but nothing between has six years of span on two
    points; the label reports the span it actually spanned."""
    out = growth_record(_revenue([200.0, None, None, 150.0, None, 100.0]))
    five = out.revenue.cagr[5]
    assert five.available
    assert "over 2 years" in five.label          # three present points -> a 2-year span


def test_eps_is_derived_from_net_income_and_shares_when_the_line_is_missing_AND_SAYS_SO():
    out = growth_record(_f(aligned={
        "net_income": [200.0, 180, 160, 140, 120, 100.0],
        "shares_outstanding": [100.0, 100, 100, 100, 100, 100.0]}))
    five = out.eps.cagr[5]
    assert five.available
    assert "derived from net income and share count" in five.label


def test_the_reported_eps_line_is_preferred_over_the_derivation():
    out = growth_record(_f(aligned={
        "diluted_eps": [2.0, 1.8, 1.6, 1.4, 1.2, 1.0],
        "net_income": [999.0] * 6, "shares_outstanding": [1.0] * 6}))
    assert "derived" not in out.eps.cagr[5].label
    assert out.eps.cagr[5].value == pytest.approx(0.1487, abs=1e-3)


def test_no_earnings_data_at_all_abstains_on_the_eps_leg_only():
    out = growth_record(_revenue([150.0, 130, 110, 100.0]))
    assert out.revenue.cagr[5].available
    assert not out.eps.cagr[5].available


def test_the_record_counts_the_years_it_had():
    out = growth_record(_revenue([150.0, None, 130, 110, 100.0]))
    assert out.revenue.years_available == 4


# =========================================================================== #
# neither reading is a lens
# =========================================================================== #
def test_no_strategy_selects_either_reading():
    """They are facts on a page, not votes. If one ever became a factor it would change
    every ranking that selected it, and this is the line that would go red."""
    from pathlib import Path

    text = " ".join(p.read_text(encoding="utf-8")
                    for p in Path("strategies").glob("*.yaml"))
    assert "debt_and_cash" not in text
    assert "growth_record" not in text


def test_neither_reading_is_in_the_factor_registry():
    from aristos_council.factors import FACTOR_REGISTRY

    assert "debt_and_cash" not in FACTOR_REGISTRY
    assert "growth_record" not in FACTOR_REGISTRY


def test_a_reading_never_carries_both_a_value_and_an_abstention():
    for reading in (Reading(value=1.0, label="x"), Reading(note="why")):
        assert bool(reading.available) != bool(reading.note)
