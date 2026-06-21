"""Tests for run-report persistence — reports/<TICKER>/<run_at>.json.

Where verdicts/<TICKER>.json (Sprint 2) keeps a thin append-only log for the
next run's vetoes, the run report keeps the FULL deliberation so Council Station
(Sprint 3) can re-render any past run without re-spending API credits. These
tests pin the round-trip and that every section the UI renders is persisted.
"""

import json
from datetime import datetime, timezone

from aristos_council.persistence.reports import (
    RunReport,
    list_reports,
    load_report,
    load_reports,
    report_from_state,
    report_path,
    save_report,
)
from aristos_council.state import (
    CriticReport,
    Decision,
    Figure,
    Provenance,
    Recommendation,
    ResearchState,
    SpecialistName,
    SpecialistOpinion,
    Stance,
    ToolCall,
    VetoFlag,
    VetoTrigger,
)


def _state(**kw) -> ResearchState:
    return ResearchState(ticker="JNJ", strategy_id="dividend_aristocrats_v1", **kw)


def _figure(label="dividend_yield", value=0.0301) -> Figure:
    return Figure(
        label=label,
        value=value,
        unit="ratio",
        provenance=Provenance(
            tool_name="run_dividend_aristocrat_screen",
            call_id="c1",
            field_path="metrics.dividend_yield",
        ),
    )


def _full_state() -> ResearchState:
    s = _state(as_of=datetime(2026, 6, 11, tzinfo=timezone.utc))
    s.tool_calls = [
        ToolCall(call_id="c1", tool_name="run_dividend_aristocrat_screen",
                 output={"metrics": {"dividend_yield": 0.0301}})
    ]
    s.specialist_opinions = [
        SpecialistOpinion(
            specialist=SpecialistName.FUNDAMENTAL, stance=Stance.BULLISH,
            confidence=0.8, thesis="Durable payout.",
            figures=[_figure()], caveats=["streak is a floor"],
        ),
        SpecialistOpinion(
            specialist=SpecialistName.TECHNICAL, stance=Stance.BULLISH,
            confidence=0.7, thesis="Uptrend intact.",
        ),
        SpecialistOpinion(
            specialist=SpecialistName.SENTIMENT, stance=Stance.BULLISH,
            confidence=0.6, thesis="Coverage constructive.",
        ),
        SpecialistOpinion(
            specialist=SpecialistName.RISK, stance=Stance.NEUTRAL,
            confidence=0.5, thesis="Debt load unconfirmed.",
            caveats=["balance sheet not in evidence"],
        ),
    ]
    s.critic_report = CriticReport(
        targets_stance=Stance.BULLISH,
        counter_thesis="Yield is price-decline driven.",
        weaknesses_found=["streak unverifiable", "payout trending up"],
        challenged_figures=["dividend_yield"],
        figures=[_figure("payout_ratio", 0.71)],
        open_questions=["What is net debt / EBITDA?"],
    )
    s.decision = Decision(
        recommendation=Recommendation.HOLD, confidence=0.62,
        rationale="Bullish council but data quality forces a hold.",
        dissent=[SpecialistName.RISK],
    )
    s.veto_flags = [
        VetoFlag(trigger=VetoTrigger.DATA_QUALITY, detail="streak unverifiable"),
        VetoFlag(trigger=VetoTrigger.MAJORITY_OVERRIDE,
                 detail="decision hold vs majority buy (3 bullish)"),
    ]
    s.provenance_audit = {
        "figures_audited": 9, "verified": 5, "mismatch": 3,
        "unresolvable": 1, "unverifiable": 0, "unit_scaled": 0,
        "violations": ["provenance value mismatch: risk cited ..."],
        "unit_scaled_notes": [],
    }
    return s


def test_report_from_state_captures_every_section():
    r = report_from_state(_full_state())
    assert r.ticker == "JNJ"
    assert r.run_at == datetime(2026, 6, 11, tzinfo=timezone.utc)
    assert r.strategy_id == "dividend_aristocrats_v1"

    # specialists — all four, with figures and caveats preserved
    assert len(r.specialist_opinions) == 4
    fund = r.specialist_opinions[0]
    assert fund.specialist == SpecialistName.FUNDAMENTAL
    assert fund.stance == Stance.BULLISH
    assert fund.confidence == 0.8
    assert fund.thesis == "Durable payout."
    assert fund.figures[0].label == "dividend_yield"
    assert fund.figures[0].provenance.field_path == "metrics.dividend_yield"
    assert fund.caveats == ["streak is a floor"]

    # critic
    assert r.critic_report.counter_thesis == "Yield is price-decline driven."
    assert r.critic_report.weaknesses_found == [
        "streak unverifiable", "payout trending up"]
    assert r.critic_report.open_questions == ["What is net debt / EBITDA?"]
    assert r.critic_report.figures[0].label == "payout_ratio"

    # decision
    assert r.decision.recommendation == Recommendation.HOLD
    assert r.decision.confidence == 0.62
    assert r.decision.dissent == [SpecialistName.RISK]

    # veto + provenance audit (FULL, including violation prose — unlike verdicts)
    assert {f.trigger for f in r.veto_flags} == {
        VetoTrigger.DATA_QUALITY, VetoTrigger.MAJORITY_OVERRIDE}
    assert r.provenance_audit["violations"] == [
        "provenance value mismatch: risk cited ..."]


def test_company_name_extracted_from_fundamentals_object():
    from aristos_council.data.adapter import Fundamentals
    s = _state()
    s.tool_calls = [ToolCall(call_id="f1", tool_name="get_fundamentals",
                             output=Fundamentals(ticker="MO",
                                                 name="Altria Group"))]
    assert report_from_state(s).company_name == "Altria Group"


def test_company_name_extracted_from_dict_output():
    # after (de)serialisation the output may be a plain dict
    s = _state()
    s.tool_calls = [ToolCall(call_id="f1", tool_name="get_fundamentals",
                             output={"ticker": "MO", "name": "Altria Group"})]
    assert report_from_state(s).company_name == "Altria Group"


def test_company_name_none_when_no_fundamentals_call():
    assert report_from_state(_state()).company_name is None


def test_company_name_round_trips():
    s = _full_state()
    s.tool_calls.append(ToolCall(call_id="f1", tool_name="get_fundamentals",
                                 output={"name": "Johnson & Johnson"}))
    r = report_from_state(s)
    assert r.company_name == "Johnson & Johnson"
    again = RunReport.model_validate(r.model_dump(mode="json"))
    assert again == r


def test_screen_extracted_from_state():
    s = _state()
    s.tool_calls = [ToolCall(
        call_id="s1", tool_name="run_dividend_aristocrat_screen",
        output={"ticker": "MO", "flags": [],
                "criteria": [{"name": "min_dividend_yield", "passed": True,
                              "observed": 0.05, "threshold": 0.025}]})]
    r = report_from_state(s)
    assert r.screen["criteria"][0]["name"] == "min_dividend_yield"
    assert r.screen["criteria"][0]["passed"] is True


def test_screen_none_when_no_screen_call():
    assert report_from_state(_state()).screen is None


def test_screen_round_trips():
    s = _state()
    s.tool_calls = [ToolCall(
        call_id="s1", tool_name="run_dividend_aristocrat_screen",
        output={"ticker": "MO", "flags": [],
                "criteria": [{"name": "max_payout_ratio", "passed": False,
                              "observed": 0.9, "threshold": 0.75}]})]
    r = report_from_state(s)
    again = RunReport.model_validate(r.model_dump(mode="json"))
    assert again == r
    assert again.screen["criteria"][0]["passed"] is False


def test_report_from_state_handles_empty_run():
    r = report_from_state(_state())
    assert r.specialist_opinions == []
    assert r.critic_report is None
    assert r.decision is None
    assert r.veto_flags == []
    assert r.provenance_audit is None


def test_path_uses_ticker_subdir_and_safe_slug(tmp_path):
    run_at = datetime(2026, 6, 11, 14, 30, 5, tzinfo=timezone.utc)
    p = report_path("jnj", run_at, tmp_path)
    assert p.parent == tmp_path / "JNJ"
    # filesystem-safe: no colons (invalid on Windows)
    assert ":" not in p.name
    assert p.suffix == ".json"
    assert p.name == "2026-06-11T14-30-05Z.json"


def test_save_then_load_roundtrips(tmp_path):
    r = report_from_state(_full_state())
    path = save_report(r, tmp_path)
    assert path.exists()
    loaded = load_report(path)
    assert loaded == r


def test_saved_file_is_readable_json_with_iso_timestamp(tmp_path):
    save_report(report_from_state(_full_state()), tmp_path)
    path = report_path("JNJ", datetime(2026, 6, 11, tzinfo=timezone.utc), tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["ticker"] == "JNJ"
    assert datetime.fromisoformat(raw["run_at"]) == datetime(
        2026, 6, 11, tzinfo=timezone.utc)
    assert raw["decision"]["recommendation"] == "hold"
    assert raw["specialist_opinions"][0]["stance"] == "bullish"


def test_record_model_validatable_from_dump():
    r = report_from_state(_full_state())
    again = RunReport.model_validate(r.model_dump(mode="json"))
    assert again == r


def test_list_and_load_reports_sorted_oldest_first(tmp_path):
    older = report_from_state(_full_state(),
                              run_at=datetime(2026, 6, 10, tzinfo=timezone.utc))
    newer = report_from_state(_full_state(),
                              run_at=datetime(2026, 6, 12, tzinfo=timezone.utc))
    # save newer first to prove ordering is by run_at, not write order
    save_report(newer, tmp_path)
    save_report(older, tmp_path)

    paths = list_reports("JNJ", tmp_path)
    assert len(paths) == 2
    assert paths[0].name < paths[1].name  # lexical slug == chronological

    reports = load_reports("JNJ", tmp_path)
    assert [r.run_at for r in reports] == [older.run_at, newer.run_at]


def test_list_reports_missing_ticker_returns_empty(tmp_path):
    assert list_reports("NOPE", tmp_path) == []
    assert load_reports("NOPE", tmp_path) == []


# --------------------------------------------------------------------------- #
# Back-compat: the screen ledger tool was renamed run_dividend_aristocrat_screen
# -> run_strategy_screen. OLD saved reports (JNJ/MSFT/ASML/PG/000660.KS on disk)
# carry the LEGACY name; consumers match via _is_screen_tool, so those reports
# still load and their screen + flags are still recognized — no migration.
# --------------------------------------------------------------------------- #
def test_legacy_screen_tool_name_still_recognized():
    from pathlib import Path

    from aristos_council.agents.nodes import _is_screen_tool
    from aristos_council.agents.veto import make_veto_node
    from aristos_council.persistence.reports import _screen_from_state
    from aristos_council.strategy.loader import load_strategy

    legacy = "run_dividend_aristocrat_screen"
    assert _is_screen_tool(legacy) and _is_screen_tool("run_strategy_screen")

    s = _state()
    s.tool_calls = [ToolCall(
        call_id="c1", tool_name=legacy, ok=True,
        output={"criteria": [{"name": "min_dividend_yield", "passed": True}],
                # TWO NOT-EVAL flags -> MATERIAL, so severity-aware data_quality
                # still fires (a single flag would be MINOR and not escalate).
                "flags": ["unverifiable:min_dividend_growth_streak:short history",
                          "unverifiable:max_payout_ratio:no eps"]})]
    s.decision = Decision(recommendation=Recommendation.HOLD, confidence=0.9,
                          rationale="r")

    # reports.py captures the screen despite the legacy ledger name
    assert _screen_from_state(s) is not None

    # veto.py still surfaces the legacy screen's unverifiable flags as DATA_QUALITY
    strategy = load_strategy(
        Path(__file__).resolve().parents[1]
        / "strategies" / "dividend_aristocrats_v1.yaml")
    make_veto_node(strategy)(s)
    assert VetoTrigger.DATA_QUALITY in {f.trigger for f in s.veto_flags}
