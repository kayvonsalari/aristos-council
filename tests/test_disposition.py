"""Deterministic disposition-gate tests (is_gating build).

The pure ceiling function: a CONFIRMED fail (passed is False) of a gating
criterion caps the disposition at SELL; a clean pass, a non-gating fail, or a
NOT-EVAL (passed is None) yields no cap. The end-to-end override (LLM verdict
capped in the decision node) is covered in test_council_graph.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aristos_council.agents.disposition import (
    _RANK,
    disposition_ceiling,
    exceeds_ceiling,
    failed_gating_criteria,
    insufficient_evidence,
    not_evaluated_gating_criteria,
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


# --------------------------------------------------------------------------- #
# INSUFFICIENT_EVIDENCE: a NOT-EVAL (passed is None) on a GATING criterion is an
# off-ladder short-circuit, NOT a confirmed-fail cap. Separate identity check.
# --------------------------------------------------------------------------- #
def test_insufficient_evidence_true_on_gating_not_eval():
    screen = [_crit("min_dividend_yield", True), _crit(STREAK, None)]
    assert insufficient_evidence(screen, {STREAK}) is True


def test_insufficient_evidence_false_on_non_gating_not_eval():
    # The NOT-EVAL is on a NON-gating criterion -> does NOT short-circuit.
    screen = [_crit("min_dividend_yield", None), _crit(STREAK, True)]
    assert insufficient_evidence(screen, {STREAK}) is False


def test_insufficient_evidence_false_on_confirmed_fail_only():
    # passed is False is a confirmed fail (a SELL cap), NOT a NOT-EVAL. Identity
    # check on None must not be fooled by False.
    screen = [_crit(STREAK, False)]
    assert insufficient_evidence(screen, {STREAK}) is False


def test_not_evaluated_gating_criteria_lists_only_not_eval_gating():
    screen = [_crit("a", None), _crit("b", True), _crit("c", None), _crit("d", False)]
    assert not_evaluated_gating_criteria(screen, {"a", "c", "d"}) == ["a", "c"]  # not d (False)
    assert not_evaluated_gating_criteria(screen, {"b"}) == []                    # b passed


def test_confirmed_fail_and_not_eval_coexist_independently():
    # Both functions are independent: a screen can have a confirmed gating fail
    # AND a NOT-EVAL gating criterion. The decision node decides precedence
    # (confirmed-fail wins); the pure functions just report.
    screen = [_crit("a", False), _crit("b", None)]
    assert disposition_ceiling(screen, {"a", "b"}) is Recommendation.SELL
    assert insufficient_evidence(screen, {"a", "b"}) is True


def test_insufficient_evidence_is_off_the_rank_ladder():
    # INSUFFICIENT_EVIDENCE must NEVER be comparable as more/less bullish.
    assert Recommendation.INSUFFICIENT_EVIDENCE not in _RANK
    with pytest.raises(ValueError):
        exceeds_ceiling(Recommendation.INSUFFICIENT_EVIDENCE, Recommendation.SELL)
    with pytest.raises(ValueError):
        exceeds_ceiling(Recommendation.BUY, Recommendation.INSUFFICIENT_EVIDENCE)


def test_v1_strategy_gates_streak_only():
    # v1 now bakes in streak gating (the former v2 collapsed into it): ONLY the
    # streak gates; the three financial criteria do not.
    root = Path(__file__).resolve().parents[1] / "strategies"
    v1 = load_strategy(root / "dividend_aristocrats_v1.yaml")
    assert {c.name for c in v1.criteria if c.is_gating} == {STREAK}
