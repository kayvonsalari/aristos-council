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
