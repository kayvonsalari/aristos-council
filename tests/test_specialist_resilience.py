"""A malformed specialist output must never kill the whole council run.

Two layers: (1) the schema tolerates a null figure call_id (coerce -> "", flagged
unverified downstream); (2) the specialist node retries once on a parse failure, then
degrades THAT specialist to ABSTAIN. Deterministic: fake runners, no network/LLM.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.agents.nodes import make_specialist_node
from aristos_council.agents.schemas import FigureRef, SpecialistOutput
from aristos_council.state import (
    FailureKind,
    ResearchState,
    SpecialistName,
    Stance,
    ToolCall,
)
from aristos_council.strategy.loader import load_strategy

STRATEGY = load_strategy(
    Path(__file__).resolve().parents[1]
    / "strategies" / "dividend_aristocrats_v1.yaml")


class _FlakyRunner:
    """Raises on the first ``fail_times`` invocations, then returns ``out``."""

    def __init__(self, out, fail_times=0):
        self._out = out
        self._fail_times = fail_times
        self.calls = 0

    def invoke(self, system, user):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ValueError("simulated structured-output validation error")
        return self._out


def _run(who, runner):
    state = ResearchState(ticker="X", strategy_id=STRATEGY.id)
    make_specialist_node(who, STRATEGY, runner)(state)
    return state


# --------------------------------------------------------------------------- #
# Layer 1 — the schema tolerates a null call_id / field_path (coerce -> "")
# --------------------------------------------------------------------------- #
def test_figure_ref_coerces_null_call_id_to_empty():
    f = FigureRef(label="y", value=0.03, call_id=None, field_path=None)
    assert f.call_id == "" and f.field_path == ""
    # via the structured-output validation path (explicit nulls in the payload)
    out = SpecialistOutput.model_validate({
        "stance": "bullish", "confidence": 0.8, "thesis": "t",
        "figures": [{"label": "y", "value": 0.03,
                     "call_id": None, "field_path": None}]})
    assert out.figures[0].call_id == ""       # parses instead of crashing the run


def test_null_call_id_figure_is_dropped_and_flagged_unverified():
    out = SpecialistOutput(stance=Stance.BULLISH, confidence=0.8, thesis="up",
                           figures=[FigureRef(label="yield", value=0.03,
                                              call_id=None, field_path=None)])
    state = _run(SpecialistName.RISK, _FlakyRunner(out))     # no crash
    op = state.specialist_opinions[0]
    assert op.figures == []                                  # dropped (no provenance)
    assert any("provenance violation" in e and "<missing>" in e
               for e in state.errors)                        # flagged unverified


# --------------------------------------------------------------------------- #
# Layer 3 — retry once, then abstain
# --------------------------------------------------------------------------- #
def test_specialist_retries_once_then_succeeds_no_degradation():
    runner = _FlakyRunner(
        SpecialistOutput(stance=Stance.BULLISH, confidence=0.8, thesis="up"),
        fail_times=1)
    state = _run(SpecialistName.FUNDAMENTAL, runner)
    assert runner.calls == 2                                 # one retry
    assert state.specialist_opinions[0].stance == Stance.BULLISH
    assert state.run_issues == [] and state.degraded is False


def test_specialist_abstains_after_two_failures_and_run_continues():
    runner = _FlakyRunner(None, fail_times=99)               # always fails
    state = _run(SpecialistName.SENTIMENT, runner)           # must NOT raise
    assert runner.calls == 2                                 # tried twice, gave up
    op = state.specialist_opinions[0]
    assert op.stance == Stance.ABSTAIN and op.agrees_with_ranker is None
    assert state.degraded is True
    assert any(i.source == "sentiment" and i.reason == FailureKind.FETCH_ERROR
               for i in state.run_issues)


# --------------------------------------------------------------------------- #
# Regression — a proper call_id is unchanged (figure kept, provenance bound)
# --------------------------------------------------------------------------- #
def test_valid_call_id_figure_is_preserved():
    state = ResearchState(ticker="X", strategy_id=STRATEGY.id)
    state.tool_calls.append(ToolCall(call_id="abc", tool_name="get_fundamentals",
                                     output={"market_cap": 1.0}))
    out = SpecialistOutput(stance=Stance.BULLISH, confidence=0.8, thesis="up",
                           figures=[FigureRef(label="mc", value=1.0, call_id="abc",
                                              field_path="market_cap")])
    make_specialist_node(SpecialistName.FUNDAMENTAL, STRATEGY,
                         _FlakyRunner(out))(state)
    op = state.specialist_opinions[0]
    assert len(op.figures) == 1 and op.figures[0].provenance.call_id == "abc"
    assert not any("provenance violation" in e for e in state.errors)
