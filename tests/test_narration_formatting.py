"""NARR-2 ITEM 1 — number & currency formatting in narrator output.

Raw computed floats were being dumped into narrator prose ("return_12m of
0.22034839989419663", "fund_size of 149819249726.0"). Two layers are pinned here:

1. the pure formatting helper (percent / price / large-currency / currency-label);
2. the narrator's evidence block, which attaches human-formatted `display` strings
   to numeric tool outputs in NARRATOR mode ONLY — a non-narrator council's evidence
   is byte-identical (the raw values always stay for the provenance audit).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.agents.nodes import _evidence_block, make_gather_node
from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.pipeline import _screenless_frame, load_rank_strategy_from_id
from aristos_council.presentation import (
    format_factor_value, format_large_currency, format_percent, format_price)
from aristos_council.state import ResearchState

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


# --------------------------------------------------------------------------- #
# The pure helper
# --------------------------------------------------------------------------- #
def test_format_percent_one_decimal():
    assert format_percent(0.22034839989419663) == "22.0%"
    assert format_percent(0.11807632205693678) == "11.8%"
    assert format_percent(-0.1234) == "-12.3%"          # "X% below 52w high" style


def test_format_price_two_decimals_thousands_separated():
    assert format_price(100.5) == "100.50"
    assert format_price(1234.5) == "1,234.50"


def test_format_large_currency_has_label_and_abbreviation():
    assert format_large_currency(149819249726.0, "USD") == "USD 149.8bn"
    assert format_large_currency(2e10, "EUR") == "EUR 20.0bn"
    assert format_large_currency(3.4e6, "USD") == "USD 3.4mn"


def test_format_large_currency_never_invents_a_missing_label():
    # No currency known -> thousands-separated, NO fabricated symbol (omit, never invent).
    out = format_large_currency(149819249726.0, None)
    assert "USD" not in out and "$" not in out
    assert out == "149.8bn"


def test_currency_label_present_via_dispatch():
    disp = format_factor_value("market_cap", 149819249726.0, currency="USD")
    assert disp == "USD 149.8bn"
    assert "USD" in disp                                 # currency label present


def test_dispatch_by_field_name():
    assert format_factor_value("return_12m", 0.2203) == "22.0%"
    assert format_factor_value("annualized_volatility", 0.118) == "11.8%"
    assert format_factor_value("sma_50", 100.5) == "100.50"
    assert format_factor_value("fund_size", 2e10, currency="USD") == "USD 20.0bn"
    assert format_factor_value("pe_ratio", 10.0) == "10.00"        # ratio, not percent
    # expense_ratio's vendor value is already percent-points (0.06 == 0.06%).
    assert format_factor_value("expense_ratio", 0.06) == "0.06%"


def test_dispatch_omits_unknown_and_non_numeric():
    assert format_factor_value("dividend_streak", 25) is None       # unknown -> omit
    assert format_factor_value("market_cap", None) is None
    assert format_factor_value("return_12m", "n/a") is None
    assert format_factor_value("passed", True) is None              # bool is not a number


# --------------------------------------------------------------------------- #
# The narrator evidence block — display strings in narrator mode only
# --------------------------------------------------------------------------- #
class _Adapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=f"{ticker} Fund", quote_type="ETF",
                            market_cap=149819249726.0, currency="USD")

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _frame():
    return _screenless_frame(load_rank_strategy_from_id("etf_dividend_v1", STRAT_DIR))


def _state():
    frame = _frame()
    return make_gather_node(_Adapter(), frame)(
        ResearchState(ticker="SCHD", strategy_id=frame.id)), frame


def test_narrator_block_formats_numbers_and_currency():
    state, frame = _state()
    block = _evidence_block(state, frame, narrator=True)

    # market cap reads with an explicit currency label, never a raw float.
    assert "USD 149.8bn" in block
    assert '"display"' in block                          # display map attached
    # a volatility / return renders as a rounded percent somewhere in the block.
    assert "%" in block
    # the raw values are STILL present (provenance audit resolves against them).
    assert "149819249726" in block


def test_non_narrator_block_is_byte_unchanged():
    state, frame = _state()
    plain = _evidence_block(state, frame)                 # default: narrator=False
    assert "display" not in plain
    assert "USD 149.8bn" not in plain
    assert "149819249726" in plain                        # raw float, unformatted
