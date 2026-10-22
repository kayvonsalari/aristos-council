"""STATEMENTS-CCY-1 - Vale (XVALO.MC / VALE), 2026-10-22: EV/EBIT 43.5x for a business near 11x.

yfinance labels Vale's accounts ``financialCurrency = BRL``. Its headline ``totalRevenue`` is BRL 218bn, but the
annual statements (revenue 38.4bn, EBIT 6.7bn, total debt 21.8bn) are in USD. The EBIT was then converted with the
BRL rate, five-and-a-half times too small. The two figures are the same business months apart, so a gap of three
times or more says they are not in one currency; every figure that mixes them abstains.
"""
from __future__ import annotations

from datetime import date

from aristos_council.data.adapter import Fundamentals
from aristos_council.data.yfinance_adapter import _statements_currency_note
from aristos_council.tools.quality_epv import net_debt_to_ebit
from aristos_council.tools.screening import net_debt_to_operating_income


def test_vale_headline_against_statement_revenue_is_flagged():
    assert "5.7x" in _statements_currency_note(218_068_992_000, [38_403_000_000.0, 38_056_000_000.0])


def test_ordinary_growth_and_missing_figures_are_not_flagged():
    assert _statements_currency_note(110e9, [100e9]) == ""
    assert _statements_currency_note(250e9, [100e9]) == ""          # 2.5x: a hypergrower, one currency
    assert _statements_currency_note(None, [100e9]) == ""
    assert _statements_currency_note(100e9, []) == ""


def test_net_debt_ratios_abstain_on_the_note():
    f = Fundamentals(ticker="X", total_debt=110e9, total_cash=30e9, ebit=[6.7e9], operating_income=[11e9],
                     statements_currency_note="not in one currency")
    assert net_debt_to_ebit(f) == (None, "not in one currency")
    assert net_debt_to_operating_income(f)[0] is None
    assert net_debt_to_ebit(Fundamentals(ticker="X", total_debt=110e9, total_cash=30e9, ebit=[6.7e9]))[0] is not None


def test_the_valuation_band_abstains_with_the_reason():
    from aristos_council.factors import _gather_valuation_band
    f = Fundamentals(ticker="X", currency="EUR", financial_currency="BRL", statements_currency_note="differ 5.7x")
    band = _gather_valuation_band(object(), "X", f, today=date(2026, 10, 22))
    assert "differ 5.7x" in band.note and not band.note.startswith("not evaluated")


def test_the_debt_page_does_not_divide_across_currencies():
    from aristos_council.abs_readings import debt_and_cash
    f = Fundamentals(ticker="X", currency="EUR", financial_currency="BRL", total_debt=110e9, total_cash=30e9,
                     free_cash_flow=2.8e9, operating_cash_flow=8.8e9, operating_income=[11e9],
                     statements_currency_note="differ 5.7x")
    d = debt_and_cash(f)
    assert not d.years_to_repay.available and "differ 5.7x" in d.years_to_repay.note
    assert not d.net_debt_to_ocf.available and not d.interest_cover.available
    assert d.net_debt.available                      # the headline pair is one currency with itself
