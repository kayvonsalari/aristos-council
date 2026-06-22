"""Runner configuration — temperature defaults + model/temp metadata.

No LLM, no network: these assert the CONFIG (the resolvers and the default
tables), not a live model. The point is to lock the temperature default at 0.0 so
a future change can't silently reintroduce Claude's 1.0 (the verdict-wobble bug).
"""

from __future__ import annotations

from aristos_council.agents.runners import (
    _DEFAULT_TEMPS,
    _DEFAULTS,
    _model_for,
    _temp_for,
    runner_metadata,
)


def test_default_temps_are_zero_on_every_tier():
    # Reproducibility first: the verdict (and everything feeding it) runs at temp 0.
    assert _DEFAULT_TEMPS == {"specialist": 0.0, "critic": 0.0, "decision": 0.0}
    for tier in ("specialist", "critic", "decision"):
        assert _DEFAULT_TEMPS[tier] == 0.0


def test_temp_for_defaults_to_zero(monkeypatch):
    for tier in ("specialist", "critic", "decision"):
        monkeypatch.delenv(f"ARISTOS_TEMP_{tier.upper()}", raising=False)
        assert _temp_for(tier) == 0.0


def test_temp_for_honors_env_override(monkeypatch):
    monkeypatch.setenv("ARISTOS_TEMP_SPECIALIST", "0.3")
    assert _temp_for("specialist") == 0.3
    # other tiers stay at the default
    monkeypatch.delenv("ARISTOS_TEMP_DECISION", raising=False)
    assert _temp_for("decision") == 0.0


def test_model_for_defaults_and_env_override(monkeypatch):
    monkeypatch.delenv("ARISTOS_MODEL_DECISION", raising=False)
    assert _model_for("decision") == _DEFAULTS["decision"]
    monkeypatch.setenv("ARISTOS_MODEL_DECISION", "anthropic:claude-opus-4-8")
    assert _model_for("decision") == "anthropic:claude-opus-4-8"


class _FakeRunner:
    def __init__(self, model_id, temperature):
        self.model_id = model_id
        self.temperature = temperature


def test_runner_metadata_records_model_and_temperature():
    runners = {
        "specialist": _FakeRunner("anthropic:claude-haiku-4-5", 0.0),
        "decision": _FakeRunner("anthropic:claude-sonnet-4-6", 0.0),
    }
    meta = runner_metadata(runners)
    assert meta["decision"] == {"model": "anthropic:claude-sonnet-4-6",
                                "temperature": 0.0}
    assert meta["specialist"]["temperature"] == 0.0


def test_runner_metadata_skips_fakes_without_config():
    # A bare test-fake runner (no model_id/temperature) is silently skipped, so
    # recording metadata is harmless on a fake-runner graph run.
    class _Bare:
        def invoke(self, system, user):
            return None
    assert runner_metadata({"specialist": _Bare()}) == {}


def test_report_models_field_round_trips():
    # The new RunReport.models field is optional and round-trips through JSON.
    import json

    from aristos_council.persistence.reports import RunReport
    from datetime import datetime, timezone

    rep = RunReport(
        ticker="NVDA", run_at=datetime(2026, 6, 22, tzinfo=timezone.utc),
        strategy_id="growth_v1",
        models={"decision": {"model": "anthropic:claude-sonnet-4-6",
                             "temperature": 0.0}})
    back = RunReport.model_validate(json.loads(rep.model_dump_json()))
    assert back.models["decision"]["temperature"] == 0.0
    # default is None for reports saved before the field existed
    assert RunReport(ticker="X", run_at=rep.run_at, strategy_id="s").models is None
