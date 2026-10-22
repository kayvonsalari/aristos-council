"""GBX-YIELD-1 - London dividend yields read about 100x too low.

Live, 2026-10-22 sweep (list_oil_6, list_diversified_mining): "dividend yield 0.03%" for Shell
(SHEL.L), 0.02% for Glencore (GLEN.L), 0.01% for Anglo American (AAL.L). The price of a London line
is in pence ("GBp") while yfinance's dividend rate is in pounds, so dividend / price was a hundred
times too small and every London payer failed the income lenses' 1.5% floor.

The fix puts the dividend in the price's unit at the adapter: the last 365 days of payments from
the payment history (which Yahoo gives in the line's own unit), else the vendor rate x 100. The
payout ratio keeps the vendor's unit, because EPS is in pounds too. No network.
"""
from __future__ import annotations

import sys
import types
from datetime import date, timedelta

import pytest

pd = pytest.importorskip("pandas")

from aristos_council.data.adapter import (Fundamentals, listing_unit_dividend,  # noqa: E402
                                          minor_unit_scale)
from aristos_council.tools.screening import min_yield_criterion  # noqa: E402


class _FakeTicker:
    def __init__(self, info, dividends=None):
        self.info = info
        self.financials = None
        self.balance_sheet = None
        self.cashflow = None
        self.dividends = dividends

    def history(self, *a, **k):
        return None


def _adapter(monkeypatch, info, dividends=None):
    fake_yf = types.ModuleType("yfinance")
    fake_yf.Ticker = lambda symbol: _FakeTicker(info, dividends)
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    from aristos_council.data.yfinance_adapter import YFinanceAdapter
    return YFinanceAdapter()


def _payments(*amounts_days_ago):
    """A dividend Series like ``Ticker.dividends``: (amount, days before today) pairs."""
    today = pd.Timestamp(date.today())
    idx = pd.DatetimeIndex([today - pd.Timedelta(days=d) for _, d in amounts_days_ago])
    return pd.Series([a for a, _ in amounts_days_ago], index=idx)


# Shell, as the 2026-10-22 run saw it: price 3,909p, the vendor's rate 1.17 (pounds), four
# quarterly payments of about 27p in the last year in the payment history.
SHELL_INFO = {"currency": "GBp", "financialCurrency": "USD", "currentPrice": 3909.0,
              "dividendRate": 1.17, "trailingAnnualDividendYield": 0.0003,
              "trailingEps": 2.05, "payoutRatio": None}
SHELL_PAYMENTS = ((26.5, 30), (26.9, 120), (27.1, 210), (27.4, 300), (25.0, 400))


def test_minor_unit_scale_knows_the_pence_codes():
    for code in ("GBp", "GBX", "ZAc", "ILA"):
        assert minor_unit_scale(code) == 100.0
    for code in ("GBP", "USD", "EUR", None, ""):
        assert minor_unit_scale(code) == 1.0


def test_shell_yield_is_in_percent_not_hundredths_of_a_percent(monkeypatch):
    f = _adapter(monkeypatch, SHELL_INFO, _payments(*SHELL_PAYMENTS)).get_fundamentals("SHEL.L")
    # the last 365 days of payments, in pence (the 400-day-old one is outside the window)
    assert f.dividend_per_share == pytest.approx(26.5 + 26.9 + 27.1 + 27.4)
    assert f.dividend_yield == pytest.approx(107.9 / 3909.0)
    assert 0.02 < f.dividend_yield < 0.04                  # was 0.0003 (0.03%)
    # the screen derives dividend / last close, both in pence now: it clears 1.5%
    r = min_yield_criterion(f, last_close=3909.0, min_yield=0.015)
    assert r.passed is True and r.observed == pytest.approx(107.9 / 3909.0)


def test_payout_keeps_the_vendor_unit_because_eps_is_in_pounds(monkeypatch):
    f = _adapter(monkeypatch, SHELL_INFO, _payments(*SHELL_PAYMENTS)).get_fundamentals("SHEL.L")
    assert f.payout_ratio == pytest.approx(1.17 / 2.05)    # pounds over pounds, not pence


def test_without_a_payment_history_the_vendor_rate_is_scaled(monkeypatch):
    f = _adapter(monkeypatch, SHELL_INFO, None).get_fundamentals("SHEL.L")
    assert f.dividend_per_share == pytest.approx(117.0)
    assert f.dividend_yield == pytest.approx(117.0 / 3909.0)


def test_a_history_in_pounds_is_recognised_and_scaled():
    # a history that reads ~1/100 of the vendor rate in pence is itself in pounds
    assert listing_unit_dividend(1.17, 1.08, 100.0) == pytest.approx(108.0)
    # a history in pence is used as it is
    assert listing_unit_dividend(1.17, 107.9, 100.0) == pytest.approx(107.9)


def test_a_zero_stays_a_zero_and_a_gap_stays_a_gap():
    assert listing_unit_dividend(0.0, None, 100.0) == 0.0      # a genuine non-payer
    assert listing_unit_dividend(None, None, 100.0) is None    # no figure: NOT-EVAL, never 0
    assert listing_unit_dividend(None, 0.0, 100.0) is None


def test_a_dollar_line_is_unchanged(monkeypatch):
    info = {"currency": "USD", "currentPrice": 70.0, "dividendRate": 2.04,
            "trailingAnnualDividendYield": 0.029, "trailingEps": 2.5}
    f = _adapter(monkeypatch, info, _payments((0.51, 30), (0.51, 120))).get_fundamentals("KO")
    assert f.dividend_per_share == 2.04
    assert f.dividend_yield == 0.029


def test_a_pence_quoted_fund_keeps_its_own_yield_field(monkeypatch):
    info = {"currency": "GBp", "quoteType": "ETF", "yield": 0.018, "currentPrice": 9800.0,
            "trailingAnnualDividendRate": 1.7}
    f = _adapter(monkeypatch, info, None).get_fundamentals("VWRL.L")
    assert f.dividend_yield == pytest.approx(0.018)
    assert f.dividend_per_share == pytest.approx(170.0)


def test_the_window_is_the_last_365_days():
    from aristos_council.data.yfinance_adapter import _trailing_year_dividends
    tk = _FakeTicker({}, _payments((10.0, 10), (10.0, 364), (10.0, 366)))
    assert _trailing_year_dividends(tk) == pytest.approx(20.0)
    assert _trailing_year_dividends(_FakeTicker({}, None)) is None
    assert _trailing_year_dividends(_FakeTicker({}, _payments((10.0, 500)))) == 0.0
    tk2 = _FakeTicker({}, _payments((5.0, 1)))
    assert _trailing_year_dividends(tk2, today=date.today() - timedelta(days=30)) == 0.0


def test_the_fundamentals_contract_holds_on_a_london_line(monkeypatch):
    """dividend_per_share is in ``currency`` (the field's documented contract), so a yield is
    dividend / price with no unit step anywhere downstream."""
    f = _adapter(monkeypatch, SHELL_INFO, _payments(*SHELL_PAYMENTS)).get_fundamentals("SHEL.L")
    assert isinstance(f, Fundamentals) and f.currency == "GBp"
    assert f.dividend_per_share / 3909.0 == pytest.approx(f.dividend_yield)
