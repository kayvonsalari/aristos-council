"""Deterministic evidence-coverage score (hardening ITEM 3) — the replacement for the
narrator's self-assigned confidence in the low-confidence escalation. Pure, no LLM.
"""

from __future__ import annotations

from aristos_council.coverage import (
    WEIGHTS,
    coverage_from_state,
    evidence_coverage_score,
)
from aristos_council.state import ResearchState, ToolCall


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_full_data_scores_high():
    assert evidence_coverage_score(criteria=1.0, factors=1.0, provenance=1.0,
                                   fundamentals=1.0, price=1.0) == 1.0


def test_two_criteria_only_screen_is_discounted():
    # Half the screen NOT-EVAL -> criteria component 0.5 -> score drops by 0.30*0.5.
    full = evidence_coverage_score()
    half = evidence_coverage_score(criteria=0.5)
    assert half < full
    assert abs((full - half) - WEIGHTS["criteria"] * 0.5) < 1e-9


def test_imputed_factors_discount():
    # 40% of factors imputed -> factors component 0.6 -> score drops by 0.20*0.4.
    base = evidence_coverage_score()
    imputed = evidence_coverage_score(factors=0.6)
    assert abs((base - imputed) - WEIGHTS["factors"] * 0.4) < 1e-9


def test_failed_fundamentals_penalized():
    assert evidence_coverage_score(fundamentals=0.0) < evidence_coverage_score()


def test_components_clamped():
    # out-of-range inputs are clamped, never producing a score outside [0,1]
    assert evidence_coverage_score(criteria=5.0, factors=-3.0) <= 1.0
    assert evidence_coverage_score(criteria=-9.0, factors=-9.0, provenance=-9.0,
                                   fundamentals=-9.0, price=-9.0) == 0.0


# --------------------------------------------------------------------------- #
# Extraction from a completed ResearchState
# --------------------------------------------------------------------------- #
def _state(**kw):
    return ResearchState(ticker="T", strategy_id="s", **kw)


def test_coverage_from_state_full_run():
    s = _state(
        ranker_imputed_fraction=0.0,
        provenance_audit={"figures_audited": 4, "verified": 4},
        tool_calls=[
            ToolCall(call_id="s", tool_name="run_strategy_screen",
                     output={"criteria": [{"name": "a", "passed": True},
                                          {"name": "b", "passed": False}]}),
            ToolCall(call_id="f", tool_name="get_fundamentals",
                     output={"market_cap": 1e10, "pe_ratio": 15.0, "eps": 5.0,
                             "free_cash_flow": 1e9}),
            ToolCall(call_id="t", tool_name="technical_snapshot",
                     output={"last_close": 100.0}),
        ])
    cov = coverage_from_state(s)
    assert cov["score"] == 1.0
    assert cov["components"]["criteria"] == 1.0


def test_coverage_from_state_discounts_blind_screen_and_failed_fundamentals():
    s = _state(
        tool_calls=[
            ToolCall(call_id="s", tool_name="run_strategy_screen",
                     output={"criteria": [{"name": "a", "passed": None},
                                          {"name": "b", "passed": None}]}),
            ToolCall(call_id="f", tool_name="get_fundamentals", output=None, ok=False),
        ])
    cov = coverage_from_state(s)
    assert cov["components"]["criteria"] == 0.0          # blind screen
    assert cov["components"]["fundamentals"] == 0.0      # failed fetch
    assert cov["score"] < 0.6                            # would escalate


def test_absent_components_default_to_full_not_penalized():
    # A bare state (no tool calls, no ranker) is NOT penalized for context it never
    # gathered — every absent component defaults to 1.0.
    cov = coverage_from_state(_state())
    assert cov["score"] == 1.0
