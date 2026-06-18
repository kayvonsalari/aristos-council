"""Tests for verdict persistence — the append-only verdicts/<TICKER>.json log.

The module is IO-at-the-edge: the graph never touches disk. These tests pin
the record shape (the fields Sprint 2 promised), the append-only contract, and
the load_latest accessor that feeds prior_recommendation into the next run's
recommendation_flip veto.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from aristos_council.agents.veto import make_veto_node
from aristos_council.persistence.verdicts import (
    VerdictRecord,
    append_record,
    load_latest,
    load_records,
    record_from_state,
    verdict_path,
)
from aristos_council.strategy.loader import load_strategy
from aristos_council.state import (
    Decision,
    Recommendation,
    ResearchState,
    SpecialistName,
    SpecialistOpinion,
    Stance,
    VetoFlag,
    VetoTrigger,
)


def _state(**kw) -> ResearchState:
    return ResearchState(ticker="JNJ", strategy_id="dividend_aristocrats_v1", **kw)


def _opinion(who, stance, conf=0.8):
    return SpecialistOpinion(specialist=who, stance=stance, confidence=conf,
                             thesis="t")


def _full_state() -> ResearchState:
    s = _state(as_of=datetime(2026, 6, 11, tzinfo=timezone.utc))
    s.specialist_opinions = [
        _opinion(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
        _opinion(SpecialistName.TECHNICAL, Stance.BULLISH),
        _opinion(SpecialistName.SENTIMENT, Stance.BULLISH),
        _opinion(SpecialistName.RISK, Stance.NEUTRAL),
    ]
    s.decision = Decision(recommendation=Recommendation.HOLD, confidence=0.62,
                          rationale="r")
    s.veto_flags = [VetoFlag(trigger=VetoTrigger.DATA_QUALITY, detail="d")]
    s.provenance_audit = {
        "figures_audited": 9, "verified": 5, "mismatch": 3,
        "unresolvable": 1, "unverifiable": 0, "unit_scaled": 0,
        "violations": ["..."], "unit_scaled_notes": [],
    }
    return s


def test_record_from_state_extracts_fields():
    rec = record_from_state(_full_state())
    assert rec.ticker == "JNJ"
    assert rec.run_at == datetime(2026, 6, 11, tzinfo=timezone.utc)
    assert rec.strategy_id == "dividend_aristocrats_v1"
    assert rec.verdict == Recommendation.HOLD
    assert rec.confidence == 0.62
    assert rec.stances == {
        "fundamental": Stance.BULLISH,
        "technical": Stance.BULLISH,
        "sentiment": Stance.BULLISH,
        "risk": Stance.NEUTRAL,
    }
    assert rec.veto_triggers == [VetoTrigger.DATA_QUALITY]


def test_record_audit_counts_only_no_prose():
    rec = record_from_state(_full_state())
    # counts kept; the violation/notes prose is NOT part of the persisted record
    assert rec.provenance_audit == {
        "figures_audited": 9, "verified": 5, "mismatch": 3,
        "unresolvable": 1, "unverifiable": 0, "unit_scaled": 0,
    }


def test_record_from_state_handles_no_decision():
    rec = record_from_state(_state())
    assert rec.verdict is None
    assert rec.confidence is None
    assert rec.stances == {}
    assert rec.veto_triggers == []
    assert rec.provenance_audit is None


def test_veto_triggers_deduped_in_order():
    s = _state()
    s.veto_flags = [
        VetoFlag(trigger=VetoTrigger.DATA_QUALITY, detail="a"),
        VetoFlag(trigger=VetoTrigger.RECOMMENDATION_FLIP, detail="b"),
        VetoFlag(trigger=VetoTrigger.DATA_QUALITY, detail="c"),
    ]
    rec = record_from_state(s)
    assert rec.veto_triggers == [
        VetoTrigger.DATA_QUALITY, VetoTrigger.RECOMMENDATION_FLIP
    ]


def test_path_uppercases_ticker(tmp_path):
    assert verdict_path("jnj", tmp_path) == tmp_path / "JNJ.json"


def test_load_latest_missing_returns_none(tmp_path):
    assert load_latest("JNJ", tmp_path) is None
    assert load_records("JNJ", tmp_path) == []


def test_append_then_load_latest_roundtrips(tmp_path):
    rec = record_from_state(_full_state())
    append_record(rec, tmp_path)
    loaded = load_latest("JNJ", tmp_path)
    assert loaded == rec


def test_append_is_append_only(tmp_path):
    first = record_from_state(_full_state())
    second = record_from_state(_full_state())
    second.verdict = Recommendation.BUY
    append_record(first, tmp_path)
    append_record(second, tmp_path)
    records = load_records("JNJ", tmp_path)
    assert len(records) == 2
    assert records[0].verdict == Recommendation.HOLD
    assert records[1].verdict == Recommendation.BUY
    assert load_latest("JNJ", tmp_path) == second


def test_file_is_json_array_with_iso_timestamp(tmp_path):
    append_record(record_from_state(_full_state()), tmp_path)
    raw = json.loads((tmp_path / "JNJ.json").read_text())
    assert isinstance(raw, list) and len(raw) == 1
    # run_at must serialize as an ISO-8601 string, parseable back to the datetime
    assert datetime.fromisoformat(raw[0]["run_at"]) == datetime(
        2026, 6, 11, tzinfo=timezone.utc
    )
    assert raw[0]["verdict"] == "hold"
    assert raw[0]["stances"]["fundamental"] == "bullish"


def test_record_model_is_validatable_from_dump():
    rec = record_from_state(_full_state())
    again = VerdictRecord.model_validate(rec.model_dump(mode="json"))
    assert again == rec


# --------------------------------------------------------------------------- #
# recommendation_flip keys on ticker+strategy (Build batch — logic fix)
# --------------------------------------------------------------------------- #
STRATEGY_DIR = Path(__file__).resolve().parents[1] / "strategies"
GROWTH = load_strategy(STRATEGY_DIR / "growth_v1.yaml")


def _msft(strategy_id, verdict, day):
    return VerdictRecord(
        ticker="MSFT", run_at=datetime(2026, 6, day, tzinfo=timezone.utc),
        strategy_id=strategy_id, verdict=verdict, confidence=0.7, stances={})


def _flip_fired(prior_verdict, decision_verdict) -> bool:
    state = _state(
        prior_recommendation=prior_verdict,
        decision=Decision(recommendation=decision_verdict, confidence=0.7,
                          rationale="r"),
    )
    make_veto_node(GROWTH)(state)
    return VetoTrigger.RECOMMENDATION_FLIP in {f.trigger for f in state.veto_flags}


def test_load_latest_filters_by_strategy(tmp_path):
    append_record(_msft("growth_v1", Recommendation.BUY, 1), tmp_path)
    append_record(_msft("dividend_aristocrats_v1", Recommendation.HOLD, 2), tmp_path)
    # latest OVERALL is the dividend HOLD (last appended)...
    assert load_latest("MSFT", tmp_path).verdict == Recommendation.HOLD
    # ...but per-strategy lookups never cross strategies
    assert load_latest("MSFT", tmp_path,
                       strategy_id="growth_v1").verdict == Recommendation.BUY
    assert load_latest("MSFT", tmp_path,
                       strategy_id="dividend_aristocrats_v1").verdict == Recommendation.HOLD
    assert load_latest("MSFT", tmp_path, strategy_id="no_such_v1") is None


def test_no_false_flip_across_strategies(tmp_path):
    # MSFT: a dividend HOLD then a growth BUY (the committed MSFT shape).
    append_record(_msft("dividend_aristocrats_v1", Recommendation.HOLD, 1), tmp_path)
    append_record(_msft("growth_v1", Recommendation.BUY, 2), tmp_path)
    prior = load_latest("MSFT", tmp_path, strategy_id="growth_v1")
    assert prior.verdict == Recommendation.BUY            # growth prior, not HOLD
    # a fresh growth BUY run: prior BUY == decision BUY -> NO flip (the bug was
    # comparing growth BUY against the dividend HOLD)
    assert _flip_fired(prior.verdict, Recommendation.BUY) is False


def test_genuine_flip_same_strategy(tmp_path):
    append_record(_msft("growth_v1", Recommendation.BUY, 1), tmp_path)
    prior = load_latest("MSFT", tmp_path, strategy_id="growth_v1")
    # same strategy, real verdict change BUY -> SELL -> flip fires
    assert _flip_fired(prior.verdict, Recommendation.SELL) is True


def test_saved_msft_growth_prior_is_scoped_by_strategy():
    # load_latest must scope by strategy_id, returning each strategy's OWN record.
    # We assert the scoping invariant — each load returns a record tagged with the
    # requested strategy — never a specific BUY/HOLD value, since verdicts
    # legitimately drift with the underlying data over time.
    vd = Path(__file__).resolve().parents[1] / "verdicts"
    g = load_latest("MSFT", vd, strategy_id="growth_v1")
    d = load_latest("MSFT", vd, strategy_id="dividend_aristocrats_v1")
    assert g is not None and g.strategy_id == "growth_v1"
    assert d is not None and d.strategy_id == "dividend_aristocrats_v1"


# --------------------------------------------------------------------------- #
# Ephemeral override runs: not a flip, and not the flip baseline
# --------------------------------------------------------------------------- #
def test_override_run_does_not_fire_recommendation_flip():
    # The SAME verdict change (HOLD -> SELL) vs a prior fires flip for a DEFAULT
    # run but must NOT fire for an override run — the change is an artifact of the
    # setting, not market instability.
    default_state = _state(
        prior_recommendation=Recommendation.HOLD,
        decision=Decision(recommendation=Recommendation.SELL, confidence=0.7,
                          rationale="r"))
    make_veto_node(GROWTH)(default_state)
    assert VetoTrigger.RECOMMENDATION_FLIP in {
        f.trigger for f in default_state.veto_flags}          # control: fires

    override_state = _state(
        prior_recommendation=Recommendation.HOLD,
        decision=Decision(recommendation=Recommendation.SELL, confidence=0.7,
                          rationale="r"),
        applied_overrides={"criteria.min_dividend_growth_streak.is_gating": True})
    make_veto_node(GROWTH)(override_state)
    assert VetoTrigger.RECOMMENDATION_FLIP not in {
        f.trigger for f in override_state.veto_flags}          # suppressed


def test_load_latest_skips_override_runs_as_flip_baseline(tmp_path):
    append_record(_msft("growth_v1", Recommendation.BUY, 1), tmp_path)   # default
    ovr = _msft("growth_v1", Recommendation.SELL, 2)
    ovr.applied_overrides = {"criteria.min_dividend_growth_streak.is_gating": True}
    append_record(ovr, tmp_path)                                          # experiment
    # the experiment IS recorded (append-only history keeps both) ...
    assert len(load_records("MSFT", tmp_path)) == 2
    # ... but the flip baseline is the last DEFAULT verdict, never the override.
    assert load_latest("MSFT", tmp_path,
                       strategy_id="growth_v1").verdict == Recommendation.BUY
