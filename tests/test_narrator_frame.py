"""Narrator/council evidence uses the strategy's OWN lens — never a leaked default
(NARR-FRAME-1).

A screen-less rank strategy (magic_formula_raw_v1, financials_v1) used to resolve to the
default `growth_v1` lens for its narrator run — so the evidence carried a foreign GARP
screen, a partial-pass policy, and a GARP identity. The fix: the council is framed by the
strategy's own identity, no screen criteria enter the evidence, the partial-pass line is
dropped, and the header renders `screen: none`. Screened strategies are byte-unchanged;
the `resolve_council_screen_id` default (for genuine council-mode runs) is untouched.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.agents.nodes import (
    _evidence_block, _is_screen_tool, make_gather_node)
from aristos_council.agents.prompts import decision_system
from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.pipeline import (
    _screenless_frame, format_cli_report, resolve_council_screen_id, run_rank_pipeline)
from aristos_council.state import ResearchState
from aristos_council.strategy.loader import load_strategy
from aristos_council.strategy.rank_loader import load_rank_strategy

ROOT = Path(__file__).resolve().parents[1]
STRAT_DIR = ROOT / "strategies"
_RAW = load_rank_strategy(STRAT_DIR / "magic_formula_raw_v1.yaml")


class _Adapter(MarketDataAdapter):
    """One shaped Technology name (passes raw's sector/cap gate), any ticker."""

    name = "fake"
    _F = dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0, 2800, 2600, 2400],
              tax_provision=[600.0, 560, 520, 480],
              pretax_income=[2900.0, 2700, 2500, 2300], invested_capital=[5000.0] * 4,
              total_revenue=[200.0, 170, 150, 120])

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, **self._F)

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


# --- the screen-less frame carries the rank strategy's own identity ---------- #
def test_screenless_frame_has_rank_identity_and_no_criteria():
    frame = _screenless_frame(_RAW)
    assert frame.criteria == []
    assert frame.id == _RAW.id
    assert "screens nothing; quality enters via ranking" in frame.rationale


# --- the narrator evidence carries ZERO screen-criterion entries ------------- #
def test_screenless_gather_logs_no_screen_tool_and_no_criteria_block():
    frame = _screenless_frame(_RAW)
    gather = make_gather_node(_Adapter(), frame)
    state = gather(ResearchState(ticker="A", strategy_id=frame.id))
    # no screen tool at all in the ledger...
    assert not any(_is_screen_tool(tc.tool_name) for tc in state.tool_calls)
    # ...so the agent-facing evidence block has no screen block either.
    ev = _evidence_block(state, frame)
    assert '"tool": "run_screen"' not in ev


def test_screened_gather_still_logs_the_screen_tool():
    # regression: a screened strategy is unchanged — the screen tool is still logged.
    screened = load_strategy(STRAT_DIR / "magic_value_screen_v1.yaml")
    gather = make_gather_node(_Adapter(), screened)
    state = gather(ResearchState(ticker="A", strategy_id=screened.id))
    assert any(_is_screen_tool(tc.tool_name) for tc in state.tool_calls)


# --- the decision prompt drops the partial-pass line for a screen-less frame -- #
def test_decision_prompt_omits_partial_pass_for_screenless_but_keeps_it_screened():
    frame = _screenless_frame(_RAW)
    screened = load_strategy(STRAT_DIR / "magic_value_screen_v1.yaml")
    assert "partial_pass_allows_hold" not in decision_system(frame, "narrator")
    assert "partial_pass_allows_hold" in decision_system(screened, "narrator")
    # the frame's own rationale (not a GARP lens) frames the prompt identity.
    assert _RAW.id in decision_system(frame, "narrator")


# --- the header renders "screen: none" for screen-less; screened unchanged ---- #
def test_pipeline_header_renders_screen_none_for_screenless():
    result = run_rank_pipeline(
        ["A", "B", "C"], "magic_formula_raw_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_Adapter(), today=date(2026, 6, 30))
    assert result.meta["screen_strategy_id"] == "none"
    assert "screen: none" in format_cli_report(result)


def test_pipeline_header_unchanged_for_screened_garp_v2():
    # the 2026-07-11 regression anchor: growth_garp_v2 keeps its own lens in the header.
    result = run_rank_pipeline(
        ["A", "B", "C"], "growth_garp_v2", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_Adapter(), today=date(2026, 6, 30))
    assert result.meta["screen_strategy_id"] == "growth_screen_v2"
    assert "screen: growth_screen_v2" in format_cli_report(result)


# --- the resolver default (for genuine council-mode) is untouched ------------ #
def test_resolve_council_screen_id_default_is_untouched():
    assert resolve_council_screen_id(_RAW) == "growth_v1"          # default preserved
    garp = load_rank_strategy(STRAT_DIR / "growth_garp_v2.yaml")
    assert resolve_council_screen_id(garp) == "growth_screen_v2"   # declared lens wins
