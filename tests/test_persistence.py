"""Tests for verdict persistence — the append-only verdicts/<TICKER>.json log.

The module is IO-at-the-edge: the graph never touches disk. These tests pin
the record shape (the fields Sprint 2 promised), the append-only contract, and
the load_latest accessor that feeds prior_recommendation into the next run's
recommendation_flip veto.
"""

import json
from datetime import datetime, timezone

from aristos_council.persistence.verdicts import (
    VerdictRecord,
    append_record,
    load_latest,
    load_records,
    record_from_state,
    verdict_path,
)
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
