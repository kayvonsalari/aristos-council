"""Deep provenance audit tests.

The live-run corpus this guards against: across the June 2026 test battery
(KO, PG, NVDA, T, JNJ, MO) agents attached VALID call_ids to MISREAD values
seven times — every instance the same shape: citing ``criteria[N].passed:
None`` ("could not be evaluated") when the ledger held ``False`` ("evaluated
and failed"). The shallow parse-time check verifies traceability only; this
audit verifies the values themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from aristos_council.audit.provenance import (
    MISMATCH,
    UNIT_SCALED,
    UNRESOLVABLE,
    UNVERIFIABLE,
    VERIFIED,
    PathUnresolvable,
    audit_provenance,
    compare_cited,
    numbers_match,
    resolve_field_path,
)
from aristos_council.state import (
    Figure,
    Provenance,
    ResearchState,
    SpecialistName,
    SpecialistOpinion,
    Stance,
    ToolCall,
    VetoTrigger,
)
from aristos_council.strategy.loader import load_strategy

STRATEGY = load_strategy(
    Path(__file__).resolve().parents[1]
    / "strategies" / "dividend_aristocrats_v1.yaml"
)


# --------------------------------------------------------------------------- #
# Path resolution — every shape observed in live runs
# --------------------------------------------------------------------------- #
@dataclass
class _Event:
    ex_date: date
    amount: float


SCREEN_OUTPUT = {
    "passed": False,
    "criteria": [
        {"name": "min_dividend_yield", "passed": False,
         "observed": 0.022474736368579756, "threshold": 0.025},
        {"name": "max_payout_ratio", "passed": True,
         "observed": 0.6025, "threshold": 0.75},
        {"name": "min_market_cap", "passed": True,
         "observed": 578406055936.0, "threshold": 1e10},
        {"name": "min_dividend_growth_streak", "passed": False,
         "observed": 16.0, "threshold": 25.0},
    ],
    "flags": [],
}


@dataclass
class _Fundamentals:
    eps: float = 8.63
    dividend_per_share: float = 5.36
    years_dividend_growth: object = None
    name: str = "Fake Corp"


def test_resolve_dict_list_path():
    assert resolve_field_path(SCREEN_OUTPUT, "criteria[0].observed") \
        == pytest.approx(0.022474736368579756)


def test_resolve_output_alias_prefix():
    # agents use both "criteria[1].passed" and "output.criteria[1].passed"
    assert resolve_field_path(SCREEN_OUTPUT, "output.criteria[1].passed") is True


def test_resolve_attribute_access_on_dataclass():
    f = _Fundamentals()
    assert resolve_field_path(f, "eps") == pytest.approx(8.63)
    assert resolve_field_path(f, "output.eps") == pytest.approx(8.63)


def test_resolve_negative_index_on_list_root():
    history = [_Event(date(2026, 3, 25), 1.06), _Event(date(2026, 5, 26), 1.34)]
    assert resolve_field_path(history, "output[-1].amount") == pytest.approx(1.34)
    assert resolve_field_path(history, "output[0].amount") == pytest.approx(1.06)


def test_resolve_null_field_is_a_value_not_an_error():
    assert resolve_field_path(_Fundamentals(), "years_dividend_growth") is None


def test_resolve_unknown_field_raises():
    with pytest.raises(PathUnresolvable):
        resolve_field_path(SCREEN_OUTPUT, "criteria[0].nonexistent")
    with pytest.raises(PathUnresolvable):
        resolve_field_path(SCREEN_OUTPUT, "criteria[99].observed")


# --------------------------------------------------------------------------- #
# Numeric comparison — rounding is not a lie, laundering is
# --------------------------------------------------------------------------- #
def test_exact_and_rounded_citations_verify():
    assert numbers_match(0.022474736368579756, 0.022474736368579756)
    assert numbers_match(0.0225, 0.022474736368579756)    # 4-dp rounding
    assert numbers_match(-0.019, -0.01904769604575307)    # 3-dp rounding
    assert numbers_match(73.13, 73.12999725341797)        # 2-dp rounding
    assert numbers_match(121.3e9, 121317433344.0)         # 4 sig figs


def test_zero_for_small_value_is_not_a_rounding():
    # round(0.0225, 0) == 0.0 — technically a rounding, information-destroying.
    assert not numbers_match(0.0, 0.0225)


def test_wrong_value_with_small_gap_is_still_wrong():
    # 0.61 is NOT a rounding of 0.6025 at any precision (round(.., 2) = 0.6)
    assert not numbers_match(0.61, 0.6025)
    assert not numbers_match(0.55, 0.65)


def test_booleans_compare_as_numbers():
    status, _ = compare_cited(0.0, False)
    assert status == VERIFIED
    status, _ = compare_cited(1.0, True)
    assert status == VERIFIED


def test_corpus_class_none_cited_for_false_field_is_mismatch():
    """THE seven-occurrence live-run misquote: None claimed, False in ledger."""
    status, note = compare_cited(None, False)
    assert status == MISMATCH
    assert "None" in note


def test_none_for_null_field_verifies():
    status, _ = compare_cited(None, None)
    assert status == VERIFIED


def test_value_cited_for_null_field_is_mismatch():
    status, _ = compare_cited(39.0, None)
    assert status == MISMATCH


def test_string_field_is_unverifiable_not_violation():
    # e.g. anchoring 32000000.0 to a "$32 Million Verdict" headline
    status, _ = compare_cited(32000000.0, "J&J Hit with $32 Million Verdict")
    assert status == UNVERIFIABLE
    status, _ = compare_cited(None, "2026-06-01")
    assert status == UNVERIFIABLE


def test_unit_scaling_is_its_own_category():
    status, _ = compare_cited(2.25, 0.0225)   # percent cited for ratio
    assert status == UNIT_SCALED
    status, _ = compare_cited(0.0225, 2.25)
    assert status == UNIT_SCALED


# --------------------------------------------------------------------------- #
# The audit over a council state
# --------------------------------------------------------------------------- #
def _fig(label, value, call_id, path, tool="run_dividend_aristocrat_screen"):
    return Figure(label=label, value=value, unit="",
                  provenance=Provenance(tool_name=tool, call_id=call_id,
                                        field_path=path))


def _state_with(figures) -> ResearchState:
    state = ResearchState(ticker="FAKE", strategy_id=STRATEGY.id)
    state.tool_calls.append(ToolCall(
        call_id="screen1", tool_name="run_dividend_aristocrat_screen",
        inputs={}, output=SCREEN_OUTPUT,
    ))
    state.tool_calls.append(ToolCall(
        call_id="fund1", tool_name="get_fundamentals",
        inputs={}, output=_Fundamentals(),
    ))
    state.specialist_opinions.append(SpecialistOpinion(
        specialist=SpecialistName.RISK, stance=Stance.NEUTRAL,
        confidence=0.7, thesis="scripted", figures=figures,
    ))
    return state


def test_audit_flags_the_documented_misquote_and_nothing_else():
    figures = [
        # the corpus misquote: criteria[0].passed is False, agent cites None
        _fig("Yield Screen Passed", None, "screen1", "criteria[0].passed"),
        # clean citations in every observed shape
        _fig("Dividend Yield", 0.0225, "screen1", "criteria[0].observed"),
        _fig("Payout Passed", 1.0, "screen1", "output.criteria[1].passed"),
        _fig("EPS", 8.63, "fund1", "output.eps", tool="get_fundamentals"),
        _fig("years_dividend_growth (null)", None, "fund1",
             "years_dividend_growth", tool="get_fundamentals"),
    ]
    report = audit_provenance(_state_with(figures))
    summary = report.summary()
    assert summary["figures_audited"] == 5
    assert summary["mismatch"] == 1
    assert summary["verified"] == 4
    assert summary["unresolvable"] == 0
    [violation] = summary["violations"]
    assert "Yield Screen Passed" in violation
    assert "criteria[0].passed" in violation


def test_audit_flags_unresolvable_paths():
    report = audit_provenance(_state_with([
        _fig("Ghost", 1.0, "screen1", "criteria[0].not_a_field"),
    ]))
    assert report.summary()["unresolvable"] == 1
    assert len(report.violations) == 1


def test_fabricated_value_at_valid_path_is_caught():
    # valid call_id, valid path, wrong number — the gap the shallow check missed
    report = audit_provenance(_state_with([
        _fig("Dividend Growth Streak", 39.0, "screen1",
             "criteria[3].observed"),   # ledger holds 16.0
    ]))
    assert report.summary()["mismatch"] == 1


# --------------------------------------------------------------------------- #
# Integration: misquote -> audit node -> DATA_QUALITY veto fires
# --------------------------------------------------------------------------- #
def test_graph_audit_node_routes_mismatch_into_data_quality_veto(monkeypatch):
    from tests.test_council_graph import FakeAdapter, StaticRunner, ScriptedSpecialistRunner  # noqa: E501
    import aristos_council.agents.nodes as nodes_mod
    from aristos_council.agents.schemas import (
        CriticOutput, DecisionOutput, FigureRef, SpecialistOutput,
    )
    from aristos_council.graph import build_council

    # call_ids are random per run; pin them to a resettable counter so the id
    # learned in the probe run is valid in the rerun.
    def reset_ids():
        counter = iter(range(1, 100))
        monkeypatch.setattr(nodes_mod, "_new_call_id",
                            lambda: f"call{next(counter)}")

    def opinion(stance, figures=()):
        return SpecialistOutput(stance=stance, confidence=0.7,
                                thesis="scripted", figures=list(figures),
                                caveats=[])

    misquote = FigureRef(
        label="Streak Screen Passed", value=None, unit="",
        call_id="WILL_BE_PATCHED", field_path="criteria[3].passed",
    )

    runners = {
        "specialist": ScriptedSpecialistRunner([
            opinion("bullish"), opinion("bullish"),
            opinion("abstain"), opinion("neutral"),
        ]),
        "critic": StaticRunner(CriticOutput(
            targets_stance="bullish", counter_thesis="scripted",
            weaknesses_found=[], challenged_figures=[], figures=[],
            open_questions=[],
        )),
        "decision": StaticRunner(DecisionOutput(
            recommendation="hold", confidence=0.8, rationale="scripted",
            dissent=[],
        )),
    }

    # First run to learn the screen call_id, then rerun with the misquote
    # pointed at it (ids are deterministic and reset, so they line up).
    reset_ids()
    app = build_council(FakeAdapter(), STRATEGY, runners)
    probe = ResearchState(ticker="FAKE", strategy_id=STRATEGY.id)
    probe_result = ResearchState.model_validate(app.invoke(probe))
    screen_id = next(tc.call_id for tc in probe_result.tool_calls
                     if tc.tool_name == "run_dividend_aristocrat_screen")

    misquote.call_id = screen_id
    reset_ids()
    runners["specialist"] = ScriptedSpecialistRunner([
        opinion("bullish", [misquote]), opinion("bullish"),
        opinion("abstain"), opinion("neutral"),
    ])
    app = build_council(FakeAdapter(), STRATEGY, runners)
    result = ResearchState.model_validate(
        app.invoke(ResearchState(ticker="FAKE", strategy_id=STRATEGY.id))
    )

    # The audit ran and recorded the mismatch…
    assert result.provenance_audit is not None
    assert result.provenance_audit["mismatch"] >= 1
    assert any("provenance value mismatch" in e for e in result.errors)
    # …and the existing DATA_QUALITY veto fired on it.
    assert any(f.trigger == VetoTrigger.DATA_QUALITY
               for f in result.veto_flags)


def test_graph_audit_is_silent_on_clean_figures():
    from tests.test_council_graph import _run_with_sentiment
    state, _ = _run_with_sentiment(None)
    assert state.provenance_audit is not None
    assert state.provenance_audit["mismatch"] == 0
    assert state.provenance_audit["unresolvable"] == 0
    assert not any("provenance value mismatch" in e for e in state.errors)


# --------------------------------------------------------------------------- #
# Prompt-view aliases (live-run regression, JNJ June 2026: an agent cited
# get_price_history → last_adj_close — a field that exists only in the prompt
# summary, not the raw ledger object — and the audit flagged a correct value)
# --------------------------------------------------------------------------- #
def test_prompt_view_alias_resolves_summarized_price_fields():
    from aristos_council.data.adapter import PriceBar, PriceHistory

    bars = [PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + i, adj_close=100 + i, volume=1000)
            for i in range(5)]
    state = ResearchState(ticker="FAKE", strategy_id=STRATEGY.id)
    state.tool_calls.append(ToolCall(
        call_id="ph1", tool_name="get_price_history",
        inputs={}, output=PriceHistory(ticker="FAKE", bars=bars),
    ))
    state.specialist_opinions.append(SpecialistOpinion(
        specialist=SpecialistName.FUNDAMENTAL, stance=Stance.BULLISH,
        confidence=0.7, thesis="scripted", figures=[
            _fig("Last Close Price", 104.0, "ph1", "last_adj_close",
                 tool="get_price_history"),
            _fig("Bars", 5.0, "ph1", "n_bars", tool="get_price_history"),
            # wrong value through the alias must still be a mismatch
            _fig("Wrong Close", 999.0, "ph1", "output.last_adj_close",
                 tool="get_price_history"),
        ],
    ))
    summary = audit_provenance(state).summary()
    assert summary["verified"] == 2
    assert summary["mismatch"] == 1
    assert summary["unresolvable"] == 0


def test_unresolvable_violation_text_names_the_path_not_a_value_mismatch():
    report = audit_provenance(_state_with([
        _fig("Ghost", 1.0, "screen1", "criteria[0].not_a_field"),
    ]))
    [v] = report.violations
    assert v.violation_text().startswith("unresolvable provenance path")


# --------------------------------------------------------------------------- #
# STEP 1 prompt-view summaries: dividend history + recommendation trends are
# rendered as NAMED HANDLES, and the audit resolves those same paths (this is
# what kills the index/semantic + summed violation modes from the battery).
# --------------------------------------------------------------------------- #
def _div_state(events, figures) -> ResearchState:
    state = ResearchState(ticker="FAKE", strategy_id=STRATEGY.id)
    state.tool_calls.append(ToolCall(
        call_id="dh1", tool_name="get_dividend_history", inputs={}, output=events))
    state.specialist_opinions.append(SpecialistOpinion(
        specialist=SpecialistName.RISK, stance=Stance.NEUTRAL, confidence=0.7,
        thesis="scripted", figures=figures))
    return state


def test_dividend_prompt_view_handles_resolve():
    # ascending; T-shape: 0.52 held 2020-2021, cut to 0.278 in 2022.
    events = [_Event(date(2020, 3, 1), 0.52), _Event(date(2021, 3, 1), 0.52),
              _Event(date(2022, 3, 1), 0.278), _Event(date(2022, 6, 1), 0.278)]
    figs = [
        _fig("Latest dividend", 0.278, "dh1", "latest.amount",
             tool="get_dividend_history"),
        _fig("Earliest dividend", 0.52, "dh1", "earliest.amount",
             tool="get_dividend_history"),
        _fig("2021 rate", 0.52, "dh1", "by_year.2021",          # numeric key
             tool="get_dividend_history"),
        _fig("Event count", 4.0, "dh1", "n_events",
             tool="get_dividend_history"),
        # a legitimate raw-index citation must STILL fall through and resolve
        _fig("Raw latest", 0.278, "dh1", "output[-1].amount",
             tool="get_dividend_history"),
    ]
    s = audit_provenance(_div_state(events, figs)).summary()
    assert s["verified"] == 5
    assert s["mismatch"] == 0 and s["unresolvable"] == 0


def test_dividend_view_wrong_value_through_handle_is_a_mismatch():
    events = [_Event(date(2022, 6, 1), 0.278)]
    s = audit_provenance(_div_state(events, [
        _fig("Latest dividend", 0.52, "dh1", "latest.amount",   # ledger 0.278
             tool="get_dividend_history"),
    ])).summary()
    assert s["mismatch"] == 1 and s["unresolvable"] == 0


def test_recommendation_latest_period_total_is_citable():
    from aristos_council.data.sentiment import RecommendationTrend

    trends = [
        RecommendationTrend(period="2026-04-01", strong_buy=5, buy=10, hold=3,
                            sell=1, strong_sell=0),
        RecommendationTrend(period="2026-06-01", strong_buy=6, buy=12, hold=2,
                            sell=2, strong_sell=0),    # latest, total = 22
    ]
    state = ResearchState(ticker="FAKE", strategy_id=STRATEGY.id)
    state.tool_calls.append(ToolCall(
        call_id="rt1", tool_name="get_recommendation_trends", inputs={},
        output=trends))
    state.specialist_opinions.append(SpecialistOpinion(
        specialist=SpecialistName.SENTIMENT, stance=Stance.BULLISH,
        confidence=0.7, thesis="scripted", figures=[
            _fig("Analyst total (latest)", 22.0, "rt1", "latest_period.total",
                 tool="get_recommendation_trends"),
            _fig("Hold count (latest)", 2.0, "rt1", "latest_period.hold",
                 tool="get_recommendation_trends"),
        ]))
    s = audit_provenance(state).summary()
    assert s["verified"] == 2
    assert s["mismatch"] == 0 and s["unresolvable"] == 0
