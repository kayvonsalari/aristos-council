"""Tests for the deterministic veto gate — all four triggers."""

from pathlib import Path

from aristos_council.agents.veto import make_veto_node
from aristos_council.state import (
    Decision,
    Recommendation,
    ResearchState,
    SpecialistName,
    SpecialistOpinion,
    Stance,
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


def test_data_quality_trigger_on_abstain():
    s = _state()
    s.specialist_opinions = [
        _opinion(SpecialistName.SENTIMENT, Stance.ABSTAIN),
    ]
    s.decision = _decision()
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.DATA_QUALITY in triggers(s)


def test_data_quality_trigger_on_provenance_violation():
    s = _state()
    s.errors.append("provenance violation: fundamental cited 'x'=1.0 ...")
    s.decision = _decision()
    make_veto_node(STRATEGY)(s)
    assert VetoTrigger.DATA_QUALITY in triggers(s)


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
