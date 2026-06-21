"""Tests for the agent-facing evidence packet (nodes._evidence_block).

Strategy-scoped evidence (Sprint 4D): agents must not be handed dividend framing
on non-dividend runs. The screen tool shows a neutral label, and the rendered
fundamentals are scoped to the active strategy's criteria — while the STORED
ledger (audit substrate) is untouched.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from aristos_council.agents.nodes import _SCREEN_LEDGER_TOOL, _evidence_block
from aristos_council.data.adapter import Fundamentals
from aristos_council.state import ResearchState, ToolCall
from aristos_council.strategy.loader import load_strategy
from aristos_council.tools.criteria.registry import Evidence, run_screen

STRATEGY_DIR = Path(__file__).resolve().parents[1] / "strategies"
DIVIDEND = load_strategy(STRATEGY_DIR / "dividend_aristocrats_v1.yaml")
GROWTH = load_strategy(STRATEGY_DIR / "growth_v1.yaml")

# A fully-populated fundamentals object (both dividend and growth fields set), so
# scoping is the only thing that decides what an agent sees.
_FULL = Fundamentals(
    ticker="NVDA", name="NVIDIA", market_cap=3e12, pe_ratio=55.0,
    dividend_yield=0.0003, dividend_per_share=0.04, payout_ratio=0.02,
    eps=2.5, free_cash_flow=3e10, years_dividend_growth=5,
    total_revenue=[60.0, 27.0, 26.0, 16.0],
    operating_income=[33.0, 5.0, 10.0, 4.0], ebit=[34.0, 6.0],
    tax_provision=[4.0, 1.0], pretax_income=[37.0, 6.0],
    invested_capital=[40.0, 30.0],
)


def _state(strategy, fundamentals=_FULL) -> ResearchState:
    s = ResearchState(ticker=fundamentals.ticker, strategy_id=strategy.id)
    s.tool_calls.append(ToolCall(call_id="f1", tool_name="get_fundamentals",
                                 output=fundamentals))
    screen = run_screen(
        strategy.criteria,
        Evidence(fundamentals=fundamentals, dividends=[], last_close=100.0),
        ticker=fundamentals.ticker,
    )
    s.tool_calls.append(ToolCall(call_id="s1", tool_name=_SCREEN_LEDGER_TOOL,
                                 output=asdict(screen)))
    return s


# --- neutral screen label (4D.1) ------------------------------------------- #
def test_agent_evidence_shows_neutral_screen_label():
    ev = _evidence_block(_state(DIVIDEND), DIVIDEND)
    assert '"tool": "run_screen"' in ev                  # neutral, agent-facing
    # neither the current STORED ledger name nor the legacy one leaks to agents
    assert _SCREEN_LEDGER_TOOL not in ev
    assert "run_dividend_aristocrat_screen" not in ev


def test_stored_ledger_tool_name_is_run_strategy_screen():
    # The stored ledger name is the strategy-NEUTRAL run_strategy_screen (renamed
    # from run_dividend_aristocrat_screen); the audit/reports match on it.
    s = _state(DIVIDEND)
    _evidence_block(s, DIVIDEND)
    assert any(tc.tool_name == "run_strategy_screen" for tc in s.tool_calls)
    assert _SCREEN_LEDGER_TOOL == "run_strategy_screen"


# --- strategy-scoped fundamentals (4D.2) ----------------------------------- #
def test_dividend_evidence_surfaces_dividend_fields():
    ev = _evidence_block(_state(DIVIDEND), DIVIDEND)
    assert "years_dividend_growth" in ev
    assert "payout_ratio" in ev
    assert "dividend_yield" in ev


def test_growth_evidence_hides_dividend_fields():
    ev = _evidence_block(_state(GROWTH), GROWTH)
    for field in ("years_dividend_growth", "payout_ratio",
                  "dividend_yield", "dividend_per_share"):
        assert field not in ev, field
    # ...and surfaces the growth fields instead
    assert "total_revenue" in ev
    assert "operating_income" in ev
    assert "invested_capital" in ev


def test_core_fields_present_under_both_strategies():
    for strat in (DIVIDEND, GROWTH):
        ev = _evidence_block(_state(strat), strat)
        assert "market_cap" in ev and "pe_ratio" in ev
        assert "free_cash_flow" in ev


def test_full_fundamentals_object_unchanged_in_ledger():
    # Scoping is display-only: the stored tool call keeps the full object.
    s = _state(GROWTH)
    _evidence_block(s, GROWTH)
    stored = s.tool_calls[0].output
    assert stored.payout_ratio == 0.02            # dividend field still stored
    assert stored.total_revenue == [60.0, 27.0, 26.0, 16.0]


# --- criterion labels are strategy-scoped (4D.3 / confirms 4A) ------------- #
def test_growth_screen_criteria_labels_are_growth_scoped():
    ev = _evidence_block(_state(GROWTH), GROWTH)
    assert "min_revenue_cagr" in ev
    assert "min_dividend_yield" not in ev
