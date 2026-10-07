"""FX-PRICECASH-1 (Batch 17 item 3). Live: BYD (1211.HK) trades in HKD and reports in CNY. The
page said "trailing EPS HKD 3.77", "trailing P/E 19.6" (HKD 73.90 / CNY 3.77, the vendor's own
mixed ratio) and "forward P/E 16.7x (HKD 73.90 / HKD 4.41 consensus EPS)" while "What analysts
say" on the same page said CNY 4.41. Fixture: CNY accounts, HKD price, a month-end rate."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from aristos_council.abs_readings import (AccountsFx, AnalystTrend, ForecastRow,
                                          currency_relation, latest_accounts_fx, price_and_cash)
from aristos_council.data.adapter import Fundamentals, PriceBar, PriceHistory
from aristos_council.tools.technical import TechnicalSnapshot

RATE = 1.0912
FX = AccountsFx(from_ccy="CNY", to_ccy="HKD", rate=RATE, as_of="2026-09",
                source="yfinance CNYHKD=X")


def _snap(close):
    return TechnicalSnapshot(last_close=close, sma_50=None, sma_200=None, pct_off_52w_high=None,
                             annualized_volatility=None, return_6m=None, return_12m=None)


def _tech():
    return TechnicalSnapshot(last_close=73.90, sma_50=70.0, sma_200=80.0,
                             pct_off_52w_high=-0.2, annualized_volatility=0.4,
                             return_6m=0.1, return_12m=0.2)


def _f(**over):
    base = dict(ticker="1211.HK", currency="HKD", financial_currency="CNY", eps=3.77,
                pe_ratio=19.6,
                free_cash_flow_annual=[-97.7e9, 48e9, 36e9],
                aligned_annual={"free_cash_flow": [-97.7e9, 48e9, 36e9]},
                aligned_period_ends={"free_cash_flow": ["2025-12-31", "2024-12-31", "2023-12-31"]})
    base.update(over)
    return Fundamentals(**base)


def _trend(ccy="CNY"):
    return AnalystTrend(mark="flat", currency=ccy, rows=(
        ForecastRow(label="This year", now=4.41, currency=ccy),
        ForecastRow(label="Next year", now=5.49, currency=ccy)))


def test_every_ratio_is_formed_in_the_trading_currency():
    pac = price_and_cash(_tech(), _f(), trend=_trend(), fx=FX)
    assert pac.trailing_pe.value == pytest.approx(73.90 / (3.77 * RATE))
    assert pac.forward_pe_this_year.value == pytest.approx(73.90 / (4.41 * RATE))
    assert pac.forward_pe_next_year.value == pytest.approx(73.90 / (5.49 * RATE))
    # the vendor's mixed 19.6 is not what is shown
    assert "19.6" not in pac.trailing_pe.label


def test_each_figure_carries_its_true_currency():
    text = " | ".join(price_and_cash(_tech(), _f(), trend=_trend(), fx=FX).lines())
    assert "trailing EPS CNY 3.77" in text and "HKD 4.11" in text       # 3.77 x 1.0912
    assert "HKD 73.90 / HKD 4.81 consensus EPS" in text                 # 4.41 x 1.0912
    assert "CNY 4.41 converted at 1 CNY = 1.0912 HKD, Sep 2026" in text      # B22-B2 wording
    assert "CNY 3.77 (HKD 4.11, at 1 CNY = 1.0912 HKD, Sep 2026)" in text
    assert "->" not in text and " @ " not in text
    assert "free cash flow, oldest first:" in text and "CNY" in text
    assert "HKD 3.77" not in text and "HKD 4.41" not in text


def test_no_rate_means_the_ratios_abstain_never_mix():
    pac = price_and_cash(_tech(), _f(), trend=_trend(), fx=None)
    for reading in (pac.trailing_pe, pac.forward_pe_this_year, pac.forward_pe_next_year):
        assert reading.value is None
        assert "CNY" in reading.note and "HKD" in reading.note
    assert "trailing EPS CNY 3.77" in " | ".join(pac.lines())          # EPS itself still shown


def test_a_consensus_in_an_unmatchable_currency_abstains():
    pac = price_and_cash(_tech(), _f(), trend=_trend(ccy="USD"), fx=FX)
    assert pac.forward_pe_this_year.value is None
    assert "cannot be matched" in pac.forward_pe_this_year.note


def test_same_currency_is_unchanged():
    f = _f(currency="EUR", financial_currency="EUR", eps=5.36, pe_ratio=26.64)
    t = AnalystTrend(mark="flat", currency="EUR", rows=(
        ForecastRow(label="This year", now=7.25, currency="EUR"),))
    pac = price_and_cash(_snap(142.80), f, trend=t)
    assert "trailing EPS €5.36" in pac.lines() and any(
        ln.startswith("trailing P/E 26.6") for ln in pac.lines())
    assert pac.forward_pe_this_year.value == pytest.approx(142.80 / 7.25)
    assert pac.fx is None


def test_pence_quote_with_pound_accounts_is_an_exact_unit_change():
    kind, fx = currency_relation("GBp", "GBP")
    assert kind == "unit" and fx.rate == 100.0
    f = _f(currency="GBp", financial_currency="GBP", eps=1.20, pe_ratio=None)
    pac = price_and_cash(_snap(1500.0), f)
    assert pac.trailing_pe.value == pytest.approx(1500.0 / 120.0)
    assert currency_relation("USD", "USD")[0] == "same"
    assert currency_relation("HKD", "")[0] == "same"          # a gap is not a mix
    assert currency_relation("HKD", "CNY")[0] == "mixed"


class _FxAdapter:
    name = "fake"

    def __init__(self, direct=True):
        self.direct, self.asked = direct, []

    def get_price_history(self, ticker, *, start, end):
        self.asked.append(ticker)
        if ticker == "CNYHKD=X" and self.direct:
            bars = [PriceBar(day=date(2026, 8, 31), open=1.08, high=1.08, low=1.08, close=1.08,
                             adj_close=1.08, volume=0),
                    PriceBar(day=date(2026, 9, 30), open=RATE, high=RATE, low=RATE, close=RATE,
                             adj_close=RATE, volume=0)]
            return PriceHistory(ticker=ticker, bars=bars)
        if ticker == "HKDCNY=X" and not self.direct:
            r = 1 / RATE
            return PriceHistory(ticker=ticker, bars=[PriceBar(
                day=date(2026, 9, 30), open=r, high=r, low=r, close=r, adj_close=r, volume=0)])
        raise RuntimeError("no such pair")


def test_latest_rate_comes_from_the_band_s_monthly_fx_path():
    fx = latest_accounts_fx(_FxAdapter(), "CNY", "HKD", date(2026, 10, 3))
    assert fx.rate == pytest.approx(RATE) and fx.as_of == "2026-09"
    assert fx.source == "yfinance CNYHKD=X"
    inv = latest_accounts_fx(_FxAdapter(direct=False), "CNY", "HKD", date(2026, 10, 3))
    assert inv.rate == pytest.approx(RATE) and "inverted" in inv.source
    class Dead(_FxAdapter):
        def get_price_history(self, *a, **k):
            raise RuntimeError("down")
    assert latest_accounts_fx(Dead(), "CNY", "HKD", date(2026, 10, 3)) is None


def test_sources_names_the_fx_rate_and_the_page_runs_end_to_end():
    from aristos_council.company_check import company_sources, run_company_check
    from tests.test_company_check import STRAT_DIR, UNIV_DIR, RUNS_DIR, _STRAT, _rising

    class Adapter(_FxAdapter):
        def get_fundamentals(self, ticker):
            return _f(ticker="X")
        def get_price_history(self, ticker, *, start, end):
            if "=X" in ticker:
                return super().get_price_history(ticker, start=start, end=end)
            return _rising()
        def get_dividend_history(self, ticker, *, start, end):
            return []

    res = run_company_check("X", _STRAT, "", adapter=Adapter(), strategies_dir=STRAT_DIR,
                            universes_dir=UNIV_DIR, runs_dir=RUNS_DIR, today=date(2026, 10, 3),
                            with_price_and_cash=True, news_fetcher=lambda *a, **k: None)
    assert res.price_and_cash.fx is not None
    topics = {s.topic: s.text for s in company_sources(res)}
    assert "yfinance CNYHKD=X" in topics["Currency rate in price and cash"]
    assert "CNY" in " ".join(res.price_and_cash.lines())


def test_b22_b2_the_rate_tag_reads_straight_and_sources_say_the_same():
    """"CNY 3.59 (HKD 4.20, at 1 CNY = 1.1703 HKD, Oct 2026)" - no arrow, no @, a month name."""
    from aristos_council.abs_readings import AccountsFx
    fx = AccountsFx(from_ccy="CNY", to_ccy="HKD", rate=1.1703, as_of="2026-10", source="yfinance CNYHKD=X")
    assert fx.tag() == "at 1 CNY = 1.1703 HKD, Oct 2026"
    assert AccountsFx(from_ccy="CNY", to_ccy="HKD", rate=1.17).tag() == "at 1 CNY = 1.1700 HKD"
    pac = price_and_cash(_tech(), _f(eps=3.59), trend=_trend(), fx=fx)
    assert any("CNY 3.59 (HKD 4.20, at 1 CNY = 1.1703 HKD, Oct 2026)" in ln for ln in pac.lines())
    from types import SimpleNamespace
    from aristos_council.company_check import company_sources
    sources = company_sources(SimpleNamespace(price_and_cash=pac, fx_source="", providers={},
                                              accounts={}, analyst_trend=None, factors=()))
    fx_line = next(s for s in sources if s.topic == "Currency rate in price and cash")
    assert fx_line.text == "1 CNY = 1.1703 HKD, Oct 2026 (latest close), source yfinance CNYHKD=X"
