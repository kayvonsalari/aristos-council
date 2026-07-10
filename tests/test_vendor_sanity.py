"""Vendor sanity flags on absurd values (VERIFY-2 ITEM 4).

Foreign listings ship junk: NVO's dividend_yield arrived as 0.2393 (23.9%; reality
~3.7%). Cheap plausibility checks at the adapter boundary FLAG (never correct, never
fail) such values. A flagged field is surfaced in Company Check's DATA INTEGRITY and is
WITHHELD from the narrator evidence packet — the narrator must not quote vendor junk.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from pathlib import Path

from aristos_council.agents.nodes import _SCREEN_LEDGER_TOOL, _evidence_block
from aristos_council.company_check import format_company_check, run_company_check
from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory, implausible_fields,
)
from aristos_council.state import ResearchState, ToolCall
from aristos_council.strategy.loader import load_strategy
from aristos_council.tools.criteria.registry import Evidence, run_screen

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
UNIV_DIR = Path(__file__).resolve().parents[1] / "universes"
RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"


# --- the pure boundary check ------------------------------------------------ #
def test_implausible_dividend_yield_is_flagged():
    flags = implausible_fields(Fundamentals(ticker="NVO", dividend_yield=0.2393))
    assert "dividend_yield" in flags
    assert "flagged" in flags["dividend_yield"]


def test_plausible_dividend_yield_is_not_flagged():
    # 3.7% — the real NVO yield — must pass untouched.
    assert implausible_fields(Fundamentals(ticker="NVO", dividend_yield=0.037)) == {}


def test_negative_market_cap_and_unit_confused_de_are_flagged():
    flags = implausible_fields(
        Fundamentals(ticker="X", market_cap=-1e9, debt_to_equity=250000.0))
    assert "market_cap" in flags and "debt_to_equity" in flags


def test_works_on_plain_dict_too():
    assert "dividend_yield" in implausible_fields({"dividend_yield": 0.5})


def test_none_input_is_empty():
    assert implausible_fields(None) == {}


# --- Company Check surfaces the flag in DATA INTEGRITY ---------------------- #
def _rising(n=260, base=100.0, step=0.002):
    closes = [base * (1 + step * i) for i in range(n)]
    return PriceHistory(ticker="X", bars=[
        PriceBar(day=date(2026, 1, 1), open=c, high=c, low=c, close=c,
                 adj_close=c, volume=10) for c in closes])


class _OneName(MarketDataAdapter):
    name = "fake"

    def __init__(self, fundamentals):
        self._f = fundamentals

    def get_fundamentals(self, ticker):
        return self._f

    def get_price_history(self, ticker, *, start, end):
        return _rising()

    def get_dividend_history(self, ticker, *, start, end):
        return []


_NVO = Fundamentals(
    ticker="NVO", company_name="Novo Nordisk", market_cap=2e11, sector="Healthcare",
    pe_ratio=25.0, dividend_yield=0.2393,                 # vendor junk: 23.9%
    ebit=[3000.0], operating_income=[500.0] * 4, tax_provision=[100.0] * 4,
    pretax_income=[480.0] * 4, invested_capital=[8000.0] * 4,
    total_revenue=[250.0, 200, 170, 150])


def test_company_check_reports_the_flag_in_data_integrity():
    r = run_company_check(
        "NVO", "magic_formula_momentum_v1", "", adapter=_OneName(_NVO),
        strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, runs_dir=RUNS_DIR,
        today=date(2026, 6, 30))
    assert any("dividend_yield" in flag and "flagged" in flag
               for flag in r.data_integrity.implausible)
    text = format_company_check(r)
    assert "DATA INTEGRITY:" in text
    assert "dividend_yield" in text and "flagged" in text


# --- the flagged field is WITHHELD from the narrator evidence packet -------- #
def _state(strategy, fundamentals) -> ResearchState:
    s = ResearchState(ticker=fundamentals.ticker, strategy_id=strategy.id)
    s.tool_calls.append(ToolCall(call_id="f1", tool_name="get_fundamentals",
                                 output=fundamentals))
    screen = run_screen(
        strategy.criteria,
        Evidence(fundamentals=fundamentals, dividends=[], last_close=100.0),
        ticker=fundamentals.ticker)
    s.tool_calls.append(ToolCall(call_id="s1", tool_name=_SCREEN_LEDGER_TOOL,
                                 output=asdict(screen)))
    return s


def test_flagged_field_is_withheld_from_narrator_evidence():
    dividend = load_strategy(STRAT_DIR / "dividend_aristocrats_v1.yaml")
    ev = _evidence_block(_state(dividend, _NVO), dividend)
    # the implausible 0.2393 value must NOT reach the narrator...
    assert "0.2393" not in ev
    # ...and the withholding is disclosed, not silent.
    assert "flagged_fields_note" in ev
    assert "dividend_yield" in ev            # named in the note
