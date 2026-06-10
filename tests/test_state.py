"""Tests for ResearchState — provenance resolution and veto gating."""

from __future__ import annotations

from aristos_council.state import (
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


def test_figure_provenance_resolves_to_tool_call():
    state = ResearchState(ticker="JNJ", strategy_id="dividend_aristocrats_v1")
    tc = ToolCall(
        call_id="c1",
        tool_name="run_dividend_aristocrat_screen",
        inputs={"ticker": "JNJ"},
        output={"metrics": {"dividend_yield": 0.031}},
    )
    state.tool_calls.append(tc)

    fig = Figure(
        label="dividend_yield",
        value=0.031,
        unit="ratio",
        provenance=Provenance(
            tool_name="run_dividend_aristocrat_screen",
            call_id="c1",
            field_path="metrics.dividend_yield",
        ),
    )
    resolved = state.tool_call_by_id(fig.provenance.call_id)
    assert resolved is not None
    assert resolved.tool_name == fig.provenance.tool_name


def test_unresolved_provenance_returns_none():
    state = ResearchState(ticker="JNJ", strategy_id="dividend_aristocrats_v1")
    assert state.tool_call_by_id("missing") is None


def test_opinion_lookup():
    state = ResearchState(ticker="PG", strategy_id="dividend_aristocrats_v1")
    op = SpecialistOpinion(
        specialist=SpecialistName.FUNDAMENTAL,
        stance=Stance.BULLISH,
        confidence=0.7,
        thesis="durable payout",
    )
    state.specialist_opinions.append(op)
    assert state.opinion_for(SpecialistName.FUNDAMENTAL) is op
    assert state.opinion_for(SpecialistName.TECHNICAL) is None


def test_requires_human_review_gating():
    state = ResearchState(ticker="KO", strategy_id="dividend_aristocrats_v1")
    assert state.requires_human_review is False  # no flags yet

    state.veto_flags.append(
        VetoFlag(trigger=VetoTrigger.DATA_QUALITY, detail="streak unverifiable")
    )
    assert state.requires_human_review is True

    state.human_reviewed = True
    assert state.requires_human_review is False  # reviewed clears the gate


def test_human_override_field_accepts_recommendation():
    state = ResearchState(ticker="KO", strategy_id="dividend_aristocrats_v1")
    state.human_override = Recommendation.HOLD
    assert state.human_override == Recommendation.HOLD
