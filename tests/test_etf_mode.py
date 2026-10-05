"""ETF-MODE-1 (list SPY, SCHD, VWRL.L, AAPL under Dividend ETFs).

(a) the header names the stock instead of "excluded by the screen";   (b) no company-page picker;
(c) fund sizes in USD at a stated rate, abstaining with the reason;   (d) no valuation band for
funds;  (e) the Forensic caption only where Forensic is offered;      (f) VWRL.L's missing yield/fee
is the vendor's absence, reported not invented (see the hand test and the final report)."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from aristos_council.data.adapter import (Fundamentals, MarketDataAdapter, PriceBar,
                                          PriceHistory)
from aristos_council.fund_currency import FUND_SIZE_CCY, UNVERIFIED_CCY_NOTE
from aristos_council.pipeline import (run_multi_strategy_pipeline, run_rank_pipeline,
                                      summary_line, union_valuation_band_table,
                                      valuation_band_table)
from aristos_council.report_language import kind_gated_note

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
TODAY = date(2026, 6, 30)
ETF = "etf_dividend_v1"

_ROWS = {   # ticker -> (quote_type, currency, yield, fee, total_assets)
    "SPY": ("ETF", "USD", 0.012, 0.09, 5.0e11),
    "SCHD": ("ETF", "USD", 0.033, 0.06, 9.5e10),
    "VWRL.L": ("ETF", "GBP", None, None, 8.5e10),
    "AAPL": ("EQUITY", "USD", 0.005, None, None),
}


class _Adapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        qt, ccy, dy, fee, ta = _ROWS[ticker]
        return Fundamentals(ticker=ticker, company_name=ticker, quote_type=qt, currency=ccy,
                            dividend_yield=dy, net_expense_ratio=fee, total_assets=ta,
                            market_cap=3e12 if qt == "EQUITY" else None, sector="Technology")

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=1000)
            for i in range(300)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _run(**kw):
    return run_rank_pipeline(list(_ROWS), ETF, strategies_dir=STRAT_DIR, ranker_only=True,
                             adapter=_Adapter(), today=TODAY, **kw)


# --- (a) ------------------------------------------------------------------------------------- #
def test_the_header_names_the_stock_instead_of_excluded_by_the_screen():
    line = summary_line(_run())
    assert "1 is a stock, not graded in ETF mode: AAPL" in line
    assert "excluded by the screen" not in line
    assert "3 of 4 names ranked" in line


def test_the_wording_for_one_or_several_and_for_funds_in_stock_mode():
    assert kind_gated_note(["AAPL"], ["etf"]) == "1 is a stock, not graded in ETF mode: AAPL"
    assert kind_gated_note(["A", "B"], ["etf"]) == "2 are stocks, not graded in ETF mode: A, B"
    assert kind_gated_note(["SPY"], ["equity"]) == "1 is a fund, not graded in stock mode: SPY"
    assert kind_gated_note([], ["etf"]) == ""


def test_a_real_screen_exclusion_is_still_called_one():
    from aristos_council.report_language import format_summary_line

    class R:
        def __init__(self):
            self.verdict, self.excluded = "hold", False
    line = format_summary_line([R(), R(), R()], universe_size=5, excluded=2,
                               kind_gated="1 is a stock, not graded in ETF mode: AAPL")
    assert line.endswith("1 is a stock, not graded in ETF mode: AAPL, 2 excluded by the screen")


# --- (b) and (e) ----------------------------------------------------------------------------- #
def test_no_company_picker_in_etf_mode_and_the_forensic_caption_only_where_offered():
    pytest.importorskip("streamlit")
    import app
    assert app.company_page_offered(app.ETFS) is False
    assert app.company_page_offered("stocks") is True
    etf_choices = [NS(strategy=NS(kind="selector"))]
    stock_choices = [NS(strategy=NS(kind="selector")), NS(strategy=NS(kind="check"))]
    assert "Forensic" not in app.equal_vote_caption(etf_choices)
    assert app.equal_vote_caption(etf_choices) == "Every ticked lens is an equal vote."
    assert "Forensic marks; it does not vote." in app.equal_vote_caption(stock_choices)


# --- (c) ------------------------------------------------------------------------------------- #
def test_fund_sizes_are_in_usd_and_a_fund_with_no_stated_currency_abstains_with_the_reason():
    assert FUND_SIZE_CCY == "USD"
    res = _run()
    by = {r.ticker: r for r in res.ranked}
    assert by["SPY"].factor_values["fund_size"] == 5.0e11          # USD listing, taken as USD
    assert "USD, taken from the listing currency" in by["SPY"].factor_sources["fund_size"]
    assert by["VWRL.L"].factor_values["fund_size"] is None          # GBP listing, currency unstated
    assert by["VWRL.L"].factor_sources["fund_size"] == UNVERIFIED_CCY_NOTE
    assert "EUR" not in UNVERIFIED_CCY_NOTE and "not stated" in UNVERIFIED_CCY_NOTE


def test_the_factor_label_says_usd_not_eur():
    from aristos_council.factors import FACTOR_REGISTRY
    d = FACTOR_REGISTRY["fund_size"]
    assert "USD" in d.label and "EUR" not in d.label and d.currency == "USD"


def test_the_rate_source_is_named_in_the_sources_block():
    from aristos_council.company_check import FactorCell, company_sources
    cell = FactorCell("fund_size", "Fund size", 3.48e9,
                      "static: 2026-07-29, EODHD — 3bn EUR @ 1.16 USD/EUR, 2026-07-29, rate from "
                      "the market data provider's EURUSD=X rate", "")
    lines = company_sources(NS(factors=[cell], providers={}, peer_group=None))
    assert any(l.topic == "Currency rate for fund size (converted to USD)"
               and "EURUSD=X" in l.text and "2026-07-29" in l.text for l in lines)


# --- (d) ------------------------------------------------------------------------------------- #
def test_no_valuation_band_section_for_a_fund_list():
    res = _run(with_valuation_band=True)
    assert valuation_band_table(res) is None
    multi = run_multi_strategy_pipeline(list(_ROWS), [ETF], strategies_dir=STRAT_DIR,
                                        adapter=_Adapter(), today=TODAY, with_valuation_band=True)
    assert union_valuation_band_table(multi) is None


def test_a_stock_lens_still_gets_its_price_or_band_table():
    from tests.test_multi_strategy_run import RAW, UNIVERSE4, _Adapter4
    res = run_rank_pipeline(UNIVERSE4, RAW, ranker_only=True, strategies_dir=STRAT_DIR,
                            adapter=_Adapter4(), today=date(2026, 6, 30))
    assert valuation_band_table(res) is not None
