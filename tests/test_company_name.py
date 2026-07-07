"""Company display names on every report surface (ITEM 1).

`display_name(ticker, company_name)` is the single label builder — "Micron
Technology (MU)" when the name is known, the bare ticker when it isn't. The yfinance
adapter surfaces `longName` as `Fundamentals.company_name` (None-guarded), and the
rank pipeline threads a ticker->name map onto the result so the CLI report (and the UI)
lead each line with the name.
"""

from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path

from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
    display_name,
)
from aristos_council.pipeline import format_cli_report, run_rank_pipeline

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


# --------------------------------------------------------------------------- #
# display_name — the one label builder
# --------------------------------------------------------------------------- #
def test_display_name_renders_name_and_ticker():
    assert display_name("MU", "Micron Technology Incorporated") == \
        "Micron Technology Incorporated (MU)"


def test_display_name_falls_back_to_bare_ticker_when_name_missing():
    assert display_name("MU", None) == "MU"
    assert display_name("MU", "") == "MU"
    assert display_name("MU", "   ") == "MU"          # whitespace-only is not a name


# --------------------------------------------------------------------------- #
# Adapter — longName -> Fundamentals.company_name (with / without longName)
# --------------------------------------------------------------------------- #
class _FakeTicker:
    def __init__(self, info):
        self.info = info
        self.financials = None
        self.balance_sheet = None
        self.cashflow = None
        self.dividends = None

    def history(self, *a, **k):        # never reached in get_fundamentals
        return None


def _adapter_with_info(monkeypatch, info):
    fake_yf = types.ModuleType("yfinance")
    fake_yf.Ticker = lambda symbol: _FakeTicker(info)
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    from aristos_council.data.yfinance_adapter import YFinanceAdapter
    return YFinanceAdapter()


def test_adapter_surfaces_longname_as_company_name(monkeypatch):
    adapter = _adapter_with_info(
        monkeypatch, {"longName": "Micron Technology Incorporated",
                      "shortName": "Micron Tech"})
    f = adapter.get_fundamentals("MU")
    assert f.company_name == "Micron Technology Incorporated"
    assert display_name(f.ticker, f.company_name) == \
        "Micron Technology Incorporated (MU)"


def test_adapter_company_name_is_none_without_longname(monkeypatch):
    # No longName in the info block -> company_name None (NOT silently the shortName),
    # so the display path falls back to the bare ticker.
    adapter = _adapter_with_info(monkeypatch, {"shortName": "Micron Tech"})
    f = adapter.get_fundamentals("MU")
    assert f.company_name is None
    assert display_name(f.ticker, f.company_name) == "MU"


# --------------------------------------------------------------------------- #
# Pipeline threads names onto the result; the CLI report leads with the name
# --------------------------------------------------------------------------- #
_NAMES = {"A": "Alpha Corp", "B": "Bravo Inc", "C": "Charlie Ltd"}
_FUND = {
    "A": dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0, 2800, 2600, 2400],
              tax_provision=[600.0, 560, 520, 480],
              pretax_income=[2900.0, 2700, 2500, 2300], invested_capital=[5000.0] * 4,
              total_revenue=[200.0, 170, 150, 120]),
    "B": dict(market_cap=2e10, sector="Technology", ebit=[1500.0], pe_ratio=20.0,
              operating_income=[1500.0, 1450, 1400, 1350],
              tax_provision=[300.0, 290, 280, 270],
              pretax_income=[1450.0, 1400, 1350, 1300], invested_capital=[5000.0] * 4,
              total_revenue=[150.0, 140, 130, 120]),
    "C": dict(market_cap=2e10, sector="Technology", ebit=[500.0], pe_ratio=40.0,
              operating_income=[500.0, 490, 480, 470],
              tax_provision=[100.0, 98, 96, 94],
              pretax_income=[480.0, 470, 460, 450], invested_capital=[5000.0] * 4,
              total_revenue=[125.0, 120, 115, 110]),
}


class _NamedAdapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        if ticker == "DEAD":
            return Fundamentals(ticker="DEAD")
        return Fundamentals(ticker=ticker, name=ticker,
                            company_name=_NAMES[ticker], **_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        if ticker == "DEAD":
            raise RuntimeError("no timezone found, symbol may be delisted")
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_pipeline_carries_names_and_cli_report_leads_with_them():
    result = run_rank_pipeline(
        ["A", "B", "C", "DEAD"], "magic_formula_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_NamedAdapter(), today=date(2026, 6, 30))

    # names map is populated for ranked AND excluded names.
    assert result.names["A"] == "Alpha Corp"
    assert result.names["C"] == "Charlie Ltd"

    text = format_cli_report(result)
    assert "Alpha Corp (A)" in text                      # ranked line leads with name
    assert "Charlie Ltd (C)" in text                     # excluded line leads with name
    # DEAD has no fundamentals -> no name -> bare ticker (fallback), never a phantom name.
    assert "DEAD" in text
