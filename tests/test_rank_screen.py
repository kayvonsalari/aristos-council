"""Fast screen-only ranking — scores, sort order, gated separation, and consistency
with the full matrix (screen-only == full matrix minus the stance terms)."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from aristos_council.agents.matrix import decision_matrix, screen_only_matrix
from aristos_council.data.adapter import Fundamentals
from aristos_council.ranking import (
    ScreenRanking,
    rank_screen_only,
    split_and_sort,
)
from aristos_council.state import (
    ResearchState,
    SpecialistName,
    SpecialistOpinion,
    Stance,
    ToolCall,
)
from aristos_council.strategy.loader import load_strategy
from aristos_council.tools.criteria.registry import Evidence, run_screen

GROWTH = load_strategy(
    Path(__file__).resolve().parents[1] / "strategies" / "growth_v1.yaml")


def _ev(fund: Fundamentals) -> Evidence:
    return Evidence(fundamentals=fund, dividends=[], last_close=100.0)


# Strong compounder: high revenue CAGR, healthy ROIC, modest PEG, large cap.
_GOOD = Fundamentals(
    ticker="GOOD", market_cap=1e11, pe_ratio=20.0,
    total_revenue=[180.0, 150.0, 125.0, 100.0],
    operating_income=[90.0, 78.0, 66.0, 55.0],
    tax_provision=[18.0, 15.0, 13.0, 11.0],
    pretax_income=[85.0, 74.0, 62.0, 52.0],
    invested_capital=[300.0, 300.0, 300.0, 300.0])

# Weak name: flat revenue (CAGR fail) and non-growing earnings (PEG fail).
_WEAK = Fundamentals(
    ticker="WEAK", market_cap=1e11, pe_ratio=30.0,
    total_revenue=[104.0, 103.0, 102.0, 100.0],
    operating_income=[50.0, 55.0, 58.0, 60.0],            # declining -> earnings fail
    tax_provision=[10.0, 11.0, 12.0, 12.0],
    pretax_income=[48.0, 53.0, 56.0, 58.0],
    invested_capital=[300.0, 300.0, 300.0, 300.0])


def test_screen_only_ranks_strong_above_weak():
    good = rank_screen_only(GROWTH, _ev(_GOOD), "GOOD")
    weak = rank_screen_only(GROWTH, _ev(_WEAK), "WEAK")
    assert good.score is not None and weak.score is not None
    assert good.score > weak.score
    assert good.verdict == "buy"


def test_split_and_sort_orders_by_score_descending():
    rankings = [rank_screen_only(GROWTH, _ev(_WEAK), "WEAK"),
                rank_screen_only(GROWTH, _ev(_GOOD), "GOOD")]
    scored, gated, other = split_and_sort(rankings)
    assert [r.ticker for r in scored] == ["GOOD", "WEAK"]
    assert gated == [] and other == []


def test_screen_only_equals_full_matrix_minus_stance_contributions():
    # Build the screen once, then compare the full matrix (with stances) to the
    # screen-only matrix (no stances) — they must differ by exactly the stance terms.
    screen = run_screen(GROWTH.criteria, _ev(_GOOD), ticker="GOOD")
    full_state = ResearchState(ticker="GOOD", strategy_id=GROWTH.id)
    full_state.tool_calls.append(ToolCall(
        call_id="s", tool_name="run_strategy_screen", output=asdict(screen)))
    full_state.specialist_opinions = [
        SpecialistOpinion(specialist=SpecialistName.TECHNICAL, stance=Stance.BEARISH,
                          confidence=0.7, thesis="t"),
        SpecialistOpinion(specialist=SpecialistName.FUNDAMENTAL, stance=Stance.BULLISH,
                          confidence=0.9, thesis="t"),
    ]
    full = decision_matrix(full_state, GROWTH)
    screen_only = screen_only_matrix(screen, GROWTH, ticker="GOOD")

    stance_points = sum(c.points for c in full.contributions
                        if c.name.startswith("stance:"))
    assert stance_points != 0.0                          # stances did contribute
    assert abs(screen_only.score - (full.score - stance_points)) < 1e-9
    # screen-only verdict equals the matrix run with empty stances
    assert screen_only.contributions and all(
        not c.name.startswith("stance:") for c in screen_only.contributions)


def test_split_and_sort_separates_gated_and_errors():
    rows = [
        ScreenRanking(ticker="A", verdict="buy", score=40.0),
        ScreenRanking(ticker="B", verdict="sell", score=-30.0),
        ScreenRanking(ticker="G", verdict="sell", score=None, gated=True),
        ScreenRanking(ticker="E", verdict="unknown", score=None, error="no data"),
    ]
    scored, gated, other = split_and_sort(rows)
    assert [r.ticker for r in scored] == ["A", "B"]      # sorted desc, excludes gated
    assert [r.ticker for r in gated] == ["G"]
    assert [r.ticker for r in other] == ["E"]
