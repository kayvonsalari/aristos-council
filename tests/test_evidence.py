"""Tests for the agent-facing evidence packet (nodes._evidence_block).

Strategy-scoped evidence (Sprint 4D): agents must not be handed dividend framing
on non-dividend runs. The screen tool shows a neutral label, and the rendered
fundamentals are scoped to the active strategy's criteria — while the STORED
ledger (audit substrate) is untouched.
"""

from __future__ import annotations

from dataclasses import asdict

from aristos_council.agents.nodes import _SCREEN_LEDGER_TOOL, _evidence_block
from aristos_council.data.adapter import Fundamentals
from aristos_council.state import ResearchState, ToolCall
from aristos_council.tools.criteria.registry import (
    CriterionSelection,
    Evidence,
    run_screen,
)


def _state_with_screen() -> ResearchState:
    s = ResearchState(ticker="JNJ", strategy_id="dividend_aristocrats_v1")
    screen = run_screen(
        [CriterionSelection("min_market_cap", 1e10)],
        Evidence(fundamentals=Fundamentals(ticker="JNJ", market_cap=2e10)),
        ticker="JNJ",
    )
    s.tool_calls.append(ToolCall(call_id="s1", tool_name=_SCREEN_LEDGER_TOOL,
                                 output=asdict(screen)))
    return s


def test_agent_evidence_shows_neutral_screen_label():
    s = _state_with_screen()
    ev = _evidence_block(s)
    assert '"tool": "run_screen"' in ev                  # neutral, agent-facing
    assert "run_dividend_aristocrat_screen" not in ev    # dividend framing gone


def test_stored_ledger_tool_name_is_unchanged():
    # The audit + saved reports match on the stored name; it must NOT change.
    s = _state_with_screen()
    _evidence_block(s)
    assert s.tool_calls[0].tool_name == "run_dividend_aristocrat_screen"
