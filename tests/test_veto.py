"""Tests for the deterministic veto gate — all triggers."""

from pathlib import Path

from aristos_council.agents.veto import make_veto_node
from aristos_council.state import (
    Decision,
    Recommendation,
    ResearchState,
    SpecialistName,
    SpecialistOpinion,
    Stance,
    ToolCall,
    VetoTrigger,
)
from aristos_council.strategy.loader import load_strategy

STRATEGY = load_strategy(
    Path(__file__).resolve().parents[1] / "strategies" / "dividend_aristocrats_v1.yaml"
)


def _state(**kw) -> ResearchState:
    return ResearchState(ticker="TEST", strategy_id=STRATEGY.id, **kw)


def _opinion(who, stance, conf=0.8):
    return SpecialistOpinion(
        specialist=who, stance=stance, confidence=conf, thesis="t"
    )


def _decision(rec=Recommendation.BUY, conf=0.9):
    return Decision(recommendation=rec, confidence=conf, rationale="r")


def triggers(state):
    return {f.trigger for f in state.veto_flags}


def test_clean_run_no_flags():
    s = _state()
    s.specialist_opinions = [
        _opinion(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
        _opinion(SpecialistName.TECHNICAL, Stance.NEUTRAL),
    ]
    s.decision = _decision(conf=0.9)
    make_veto_node(STRATEGY)(s)
    assert s.veto_flags == []
    assert s.requires_human_review is False


def test_low_confidence_trigger():
    s = _state()
    s.decision = _decision(conf=0.4)  # below YAML threshold 0.6
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.LOW_CONFIDENCE in triggers(s)


def test_specialist_conflict_trigger():
    s = _state()
    s.specialist_opinions = [
        _opinion(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
        _opinion(SpecialistName.RISK, Stance.BEARISH),
    ]
    s.decision = _decision()
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.SPECIALIST_CONFLICT in triggers(s)


def test_data_quality_trigger_on_provenance_violation():
    s = _state()
    s.errors.append("provenance violation: fundamental cited 'x'=1.0 ...")
    s.decision = _decision()
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.DATA_QUALITY in triggers(s)   # provenance error is MATERIAL


# --- trigger 3 SEVERITY: material (fires) vs minor (recorded, not escalated) -- #
def _screen_state(*flags, **kw) -> ResearchState:
    s = _state(**kw)
    s.tool_calls.append(ToolCall(
        call_id="s", tool_name="run_strategy_screen", ok=True,
        output={"criteria": [], "flags": list(flags)}))
    return s


def test_single_abstention_is_minor_no_data_quality():
    # Was firing DATA_QUALITY on every abstention — the cry-wolf bug.
    s = _state()
    s.specialist_opinions = [_opinion(SpecialistName.SENTIMENT, Stance.ABSTAIN)]
    s.decision = _decision()
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.DATA_QUALITY not in triggers(s)


def test_sentiment_source_403_is_minor_no_data_quality():
    s = _state()
    s.errors.append("get_company_news: Finnhub /company-news HTTP 403")
    s.decision = _decision()
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.DATA_QUALITY not in triggers(s)


def test_single_non_gating_not_eval_is_minor():
    s = _screen_state("unverifiable:min_dividend_yield:no last_close")
    s.decision = _decision()
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.DATA_QUALITY not in triggers(s)


def test_adapter_error_is_material_fires_and_detail_lists_all():
    s = _state()
    s.errors.append("get_fundamentals: provider timeout")          # MATERIAL
    s.specialist_opinions = [_opinion(SpecialistName.SENTIMENT, Stance.ABSTAIN)]  # minor
    s.decision = _decision()
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.DATA_QUALITY in triggers(s)
    detail = next(f.detail for f in s.veto_flags
                  if f.trigger == VetoTrigger.DATA_QUALITY)
    assert "get_fundamentals" in detail and "abstained" in detail  # full picture


def test_two_not_eval_criteria_are_material():
    s = _screen_state("unverifiable:min_dividend_yield:no last_close",
                      "unverifiable:max_payout_ratio:no eps")
    s.decision = _decision()
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.DATA_QUALITY in triggers(s)   # screen mostly blind


def test_minor_only_run_escalates_nothing():
    # The whole point: a run carrying ONLY benign noise (one NOT-EVAL, one
    # sentiment 403, one abstention) now fires NOTHING — previously DATA_QUALITY.
    s = _screen_state("unverifiable:max_payout_ratio:no eps")
    s.errors.append("get_recommendation_trends: HTTP 403")
    s.specialist_opinions = [_opinion(SpecialistName.SENTIMENT, Stance.ABSTAIN)]
    s.decision = _decision(rec=Recommendation.HOLD, conf=0.9)
    make_veto_node(STRATEGY)(s)
    assert s.veto_flags == []
    assert s.requires_human_review is False


# --- trigger 7 GATE_OVERRIDE_MATERIAL: escalate only the SURPRISING caps ------ #
def _capped(original, final, conf, crit="min_dividend_growth_streak") -> Decision:
    return Decision(recommendation=final, confidence=conf, rationale="r",
                    original_recommendation=original, gate_override_applied=True,
                    gating_criterion_fired=crit)


def test_gate_cap_buy_to_sell_fires():
    s = _state()
    s.decision = _capped(Recommendation.BUY, Recommendation.SELL, 0.9)
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.GATE_OVERRIDE_MATERIAL in triggers(s)
    detail = next(f.detail for f in s.veto_flags
                  if f.trigger == VetoTrigger.GATE_OVERRIDE_MATERIAL)
    assert "buy" in detail and "sell" in detail
    assert "min_dividend_growth_streak" in detail


def test_gate_cap_buy_to_sell_fires_even_at_low_confidence():
    # original==BUY escalates regardless of confidence (condition 1).
    s = _state()
    s.decision = _capped(Recommendation.BUY, Recommendation.SELL, 0.4)
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.GATE_OVERRIDE_MATERIAL in triggers(s)


def test_gate_cap_hold_to_sell_is_routine_no_fire():
    # 1-rung cap, even at high confidence -> routine gate work, no escalation.
    s = _state()
    s.decision = _capped(Recommendation.HOLD, Recommendation.SELL, 0.9)
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.GATE_OVERRIDE_MATERIAL not in triggers(s)


def test_gate_cap_not_fired_without_override():
    s = _state()
    s.decision = _decision(rec=Recommendation.SELL)   # gate_override_applied False
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.GATE_OVERRIDE_MATERIAL not in triggers(s)


def test_insufficient_evidence_does_not_also_fire_gate_override_material():
    # INSUFFICIENT_EVIDENCE caps set gate_override_applied=True but are excluded
    # from trigger 7 (trigger 6 handles them, unconditionally).
    s = _state()
    s.decision = Decision(
        recommendation=Recommendation.INSUFFICIENT_EVIDENCE, confidence=0.9,
        rationale="r", original_recommendation=Recommendation.BUY,
        gate_override_applied=True, insufficient_evidence=True,
        gating_criterion_fired="min_dividend_growth_streak")
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.INSUFFICIENT_EVIDENCE in triggers(s)
    assert VetoTrigger.GATE_OVERRIDE_MATERIAL not in triggers(s)


def test_flip_trigger():
    s = _state(prior_recommendation=Recommendation.BUY)
    s.decision = _decision(rec=Recommendation.SELL)
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.RECOMMENDATION_FLIP in triggers(s)


def test_no_flip_when_same():
    s = _state(prior_recommendation=Recommendation.BUY)
    s.decision = _decision(rec=Recommendation.BUY)
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.RECOMMENDATION_FLIP not in triggers(s)


# --- trigger 5: MAJORITY_OVERRIDE --------------------------------------- #
# Stance->verdict mapping: bullish->buy, neutral->hold, bearish->sell.
# Strict majority (>50% of non-abstaining specialists) that disagrees with the
# Decision verdict fires; ties / no-majority are silent. Motivated by the JNJ
# HOLD-vs-3-bullish live run.

def test_majority_override_fires_on_hold_vs_bullish_majority():
    s = _state()
    s.specialist_opinions = [
        _opinion(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
        _opinion(SpecialistName.TECHNICAL, Stance.BULLISH),
        _opinion(SpecialistName.SENTIMENT, Stance.BULLISH),
        _opinion(SpecialistName.RISK, Stance.NEUTRAL),
    ]
    s.decision = _decision(rec=Recommendation.HOLD)
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.MAJORITY_OVERRIDE in triggers(s)
    flag = next(f for f in s.veto_flags
                if f.trigger == VetoTrigger.MAJORITY_OVERRIDE)
    assert flag.detail == (
        "decision hold vs majority buy (3 bullish / 1 neutral / 0 bearish)"
    )


def test_majority_override_silent_without_strict_majority():
    # 2 bullish / 1 neutral / 1 bearish — no stance exceeds 50% of 4 voters.
    s = _state()
    s.specialist_opinions = [
        _opinion(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
        _opinion(SpecialistName.TECHNICAL, Stance.BULLISH),
        _opinion(SpecialistName.SENTIMENT, Stance.NEUTRAL),
        _opinion(SpecialistName.RISK, Stance.BEARISH),
    ]
    s.decision = _decision(rec=Recommendation.HOLD)
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.MAJORITY_OVERRIDE not in triggers(s)


def test_majority_override_silent_when_decision_aligned():
    s = _state()
    s.specialist_opinions = [
        _opinion(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
        _opinion(SpecialistName.TECHNICAL, Stance.BULLISH),
        _opinion(SpecialistName.SENTIMENT, Stance.BULLISH),
        _opinion(SpecialistName.RISK, Stance.NEUTRAL),
    ]
    s.decision = _decision(rec=Recommendation.BUY)  # matches the majority
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.MAJORITY_OVERRIDE not in triggers(s)


def test_majority_override_excludes_abstains():
    # 2 bullish + 2 abstain -> 2 voters, both bullish -> strict majority buy.
    s = _state()
    s.specialist_opinions = [
        _opinion(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
        _opinion(SpecialistName.TECHNICAL, Stance.BULLISH),
        _opinion(SpecialistName.SENTIMENT, Stance.ABSTAIN),
        _opinion(SpecialistName.RISK, Stance.ABSTAIN),
    ]
    s.decision = _decision(rec=Recommendation.SELL)
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.MAJORITY_OVERRIDE in triggers(s)
    flag = next(f for f in s.veto_flags
                if f.trigger == VetoTrigger.MAJORITY_OVERRIDE)
    assert flag.detail == (
        "decision sell vs majority buy (2 bullish / 0 neutral / 0 bearish)"
    )


def test_majority_override_silent_when_no_voters():
    s = _state()
    s.specialist_opinions = [
        _opinion(SpecialistName.SENTIMENT, Stance.ABSTAIN),
    ]
    s.decision = _decision(rec=Recommendation.BUY)
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.MAJORITY_OVERRIDE not in triggers(s)


# --- trigger 6: INSUFFICIENT_EVIDENCE ----------------------------------- #
# An INSUFFICIENT_EVIDENCE verdict (gating criterion NOT-EVAL) ALWAYS pauses for
# a human, unconditionally. It is off the buy/hold/sell ladder, so it is also
# excluded from the flip and majority-override comparisons.

def _insufficient(crit="min_dividend_growth_streak"):
    return Decision(recommendation=Recommendation.INSUFFICIENT_EVIDENCE,
                    confidence=0.9, rationale="r", insufficient_evidence=True,
                    gate_override_applied=True, gating_criterion_fired=crit)


def test_insufficient_evidence_always_fires_human_review():
    # High confidence, no conflict, no abstain -> a normal run would be clean.
    s = _state()
    s.specialist_opinions = [
        _opinion(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
        _opinion(SpecialistName.TECHNICAL, Stance.BULLISH),
    ]
    s.decision = _insufficient()
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.INSUFFICIENT_EVIDENCE in triggers(s)
    assert s.requires_human_review is True
    flag = next(f for f in s.veto_flags
                if f.trigger == VetoTrigger.INSUFFICIENT_EVIDENCE)
    assert "min_dividend_growth_streak" in flag.detail


def test_insufficient_evidence_is_not_a_flip_target():
    # Prior was a directional BUY; this run is INSUFFICIENT_EVIDENCE. That is not
    # a directional change, so the flip veto must stay silent.
    s = _state(prior_recommendation=Recommendation.BUY)
    s.decision = _insufficient()
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.RECOMMENDATION_FLIP not in triggers(s)
    assert VetoTrigger.INSUFFICIENT_EVIDENCE in triggers(s)


def test_insufficient_evidence_prior_is_not_a_flip_baseline():
    # Prior run was INSUFFICIENT_EVIDENCE; this run is a directional SELL. The
    # off-ladder prior must not manufacture a flip.
    s = _state(prior_recommendation=Recommendation.INSUFFICIENT_EVIDENCE)
    s.decision = _decision(rec=Recommendation.SELL)
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.RECOMMENDATION_FLIP not in triggers(s)


def test_insufficient_evidence_silences_majority_override():
    # 2 bullish voters would normally flag a non-buy verdict as a majority
    # override, but an off-ladder verdict is not comparable -> no override flag
    # (human review still fires via trigger 6).
    s = _state()
    s.specialist_opinions = [
        _opinion(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
        _opinion(SpecialistName.TECHNICAL, Stance.BULLISH),
    ]
    s.decision = _insufficient()
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.MAJORITY_OVERRIDE not in triggers(s)
    assert VetoTrigger.INSUFFICIENT_EVIDENCE in triggers(s)
