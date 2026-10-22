"""VALE-EVEBIT-1 - the leverage rule compared two different monies on a secondary line.

Live, 2026-10-22 sweep, Diversified Mining list: Vale via its Madrid line XVALO.MC read "total debt 2.2x of
market value; the rule allows at most 1.0x" on Defensive Income and Cyclical Income. ``max_debt_to_market_cap``
divided debt (ACCOUNTS currency) by market cap (QUOTE currency, euros) with no conversion. House rule 8: a
screen criterion abstains rather than converts.
"""
from __future__ import annotations

from aristos_council.data.adapter import Fundamentals
from aristos_council.tools.criteria.registry import _max_debt_to_market_cap
from aristos_council.tools.criteria.registry import Evidence


def _run(**kw):
    return _max_debt_to_market_cap(Evidence(fundamentals=Fundamentals(ticker="X", **kw)), 1.0)


def test_a_euro_quote_on_real_accounts_is_not_compared():
    r = _run(currency="EUR", financial_currency="BRL", market_cap=50e9, total_debt=110e9)
    assert r.passed is None and r.observed is None
    assert "BRL" in r.note and "EUR" in r.note


def test_a_pound_line_with_dollar_accounts_is_not_compared():
    assert _run(currency="GBp", financial_currency="USD", market_cap=150e9, total_debt=70e9).passed is None


def test_a_pence_quote_on_pound_accounts_is_the_same_money():
    r = _run(currency="GBp", financial_currency="GBP", market_cap=20e9, total_debt=5e9)
    assert r.passed is True and r.observed == 0.25


def test_same_currency_and_missing_currency_evaluate_as_before():
    assert _run(currency="USD", financial_currency="USD", market_cap=10e9, total_debt=12e9).passed is False
    assert _run(market_cap=10e9, total_debt=5e9).observed == 0.5
    assert _run(currency="EUR", market_cap=10e9, total_debt=5e9).observed == 0.5


# FX-DEBT-MV-1 ---------------------------------------------------------------
from aristos_council.abs_readings import AccountsFx  # noqa: E402


def _run_fx(fx, **kw):
    return _max_debt_to_market_cap(Evidence(fundamentals=Fundamentals(ticker="X", **kw), accounts_fx=fx), 1.0)


def test_known_currencies_are_converted_with_a_dated_labelled_rate():
    # Shell shape: London pence quote, dollar accounts. Debt $73bn at 0.75 GBP/USD against GBP 223bn.
    fx = AccountsFx(from_ccy="USD", to_ccy="GBP", rate=0.75, as_of="2026-10", source="yfinance USDGBP=X")
    r = _run_fx(fx, currency="GBp", financial_currency="USD", market_cap=223e9, total_debt=73e9)
    assert r.passed is True and abs(r.observed - 73e9 * 0.75 / 223e9) < 1e-9
    assert "USD->GBP at 0.75 (2026-10, yfinance USDGBP=X)" in r.note


def test_no_rate_or_the_wrong_pair_still_abstains():
    assert _run_fx(None, currency="EUR", financial_currency="USD", market_cap=1e11, total_debt=6e10).passed is None
    wrong = AccountsFx(from_ccy="USD", to_ccy="EUR", rate=0.86, as_of="2026-10")
    assert _run_fx(wrong, currency="GBp", financial_currency="USD", market_cap=1e11, total_debt=6e10).passed is None


def test_the_vale_type_mismatch_abstains_even_with_a_rate():
    fx = AccountsFx(from_ccy="BRL", to_ccy="EUR", rate=0.177, as_of="2026-10")
    r = _run_fx(fx, currency="EUR", financial_currency="BRL", market_cap=49e9, total_debt=110e9,
                statements_currency_note="differ 5.7x")
    assert r.passed is None and "differ 5.7x" in r.note
