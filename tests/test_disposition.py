"""Deterministic disposition-gate tests (is_gating build).

The pure ceiling function: a CONFIRMED fail (passed is False) of a gating
criterion caps the disposition at SELL; a clean pass, a non-gating fail, or a
NOT-EVAL (passed is None) yields no cap. The end-to-end override (LLM verdict
capped in the decision node) is covered in test_council_graph.py.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.agents.disposition import (
    disposition_ceiling,
    exceeds_ceiling,
    failed_gating_criteria,
)
from aristos_council.state import Recommendation
from aristos_council.strategy.loader import load_strategy

STREAK = "min_dividend_growth_streak"


def _crit(name, passed):
    # Mirror the asdict'd ScreenResult criterion shape the ledger carries.
    return {"name": name, "passed": passed, "observed": None,
            "threshold": None, "note": ""}


def test_gating_confirmed_fail_caps_at_sell():
    screen = [_crit("min_dividend_yield", True), _crit(STREAK, False)]
    assert disposition_ceiling(screen, {STREAK}) is Recommendation.SELL


def test_clean_pass_yields_no_cap():
    screen = [_crit("min_dividend_yield", True), _crit(STREAK, True)]
    assert disposition_ceiling(screen, {STREAK}) is None


def test_fail_but_not_gating_yields_no_cap():
    # v1 shape: the streak fails but NO criterion is gating -> no cap. Proves the
    # default-off field leaves v1 behaviour exactly as before.
    screen = [_crit(STREAK, False)]
    assert disposition_ceiling(screen, set()) is None


def test_not_eval_on_gating_criterion_does_not_cap():
    # passed is None (NOT-EVAL) is NOT a confirmed fail (identity check on False).
    screen = [_crit(STREAK, None)]
    assert disposition_ceiling(screen, {STREAK}) is None
    assert failed_gating_criteria(screen, {STREAK}) == []


def test_failed_gating_criteria_lists_only_confirmed_gating_fails():
    screen = [_crit("a", False), _crit("b", True), _crit("c", None), _crit("d", False)]
    assert failed_gating_criteria(screen, {"a", "c", "d"}) == ["a", "d"]  # not c (None)
    assert failed_gating_criteria(screen, {"b"}) == []                    # b passed


def test_ceiling_sell_exceeds_buy_and_hold_but_not_sell():
    # The node only overrides when the LLM verdict is MORE bullish than the cap.
    assert exceeds_ceiling(Recommendation.BUY, Recommendation.SELL) is True
    assert exceeds_ceiling(Recommendation.HOLD, Recommendation.SELL) is True
    assert exceeds_ceiling(Recommendation.SELL, Recommendation.SELL) is False


def test_v2_strategy_gating_names_are_streak_only():
    # The committed v2 marks ONLY the streak gating; v1 marks nothing.
    root = Path(__file__).resolve().parents[1] / "strategies"
    v1 = load_strategy(root / "dividend_aristocrats_v1.yaml")
    v2 = load_strategy(root / "dividend_aristocrats_v2.yaml")
    assert {c.name for c in v1.criteria if c.is_gating} == set()
    assert {c.name for c in v2.criteria if c.is_gating} == {STREAK}
