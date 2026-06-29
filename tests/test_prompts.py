"""Tests for the hard-rule prompt guidance (Sprint 2.3).

These pin the two rules added after the live provenance incidents:
  - one figure cites exactly ONE field_path (no composite/computed paths), and
  - screen-criteria 'passed' is three-valued (true / false / null), where null
    means NOT EVALUATED and false means evaluated-and-failed.

They assert against the SYSTEM prompts every agent actually receives, so the
guidance can't silently regress out of one role's prompt.
"""

from pathlib import Path

from aristos_council.agents.nodes import (
    _HARD_RULES,
    _critic_system,
    _decision_system,
    _specialist_system,
)
from aristos_council.state import SpecialistName
from aristos_council.strategy.loader import load_strategy

STRATEGY = load_strategy(
    Path(__file__).resolve().parents[1] / "strategies" / "dividend_aristocrats_v1.yaml"
)


def _all_system_prompts():
    return [
        _specialist_system(SpecialistName.FUNDAMENTAL, STRATEGY),
        _specialist_system(SpecialistName.RISK, STRATEGY),
        _critic_system(STRATEGY),
        _decision_system(STRATEGY),
    ]


def test_hard_rules_state_one_figure_one_field_path():
    assert "ONE FIGURE = ONE FIELD_PATH" in _HARD_RULES
    # the forbidden composite-path example is shown explicitly
    assert "a + b" in _HARD_RULES
    assert "composite" in _HARD_RULES.lower()


def test_hard_rules_explain_three_valued_passed():
    low = _HARD_RULES.lower()
    assert "not evaluated" in low          # null
    assert "evaluated-and-failed" in low or "evaluated and failed" in low
    assert "provenance violation" in low   # citing null for a false field


def test_hard_rules_require_path_only_field_path():
    assert "FIELD_PATH IS PATH-ONLY" in _HARD_RULES
    assert "no spaces, commentary, or parentheses" in _HARD_RULES
    # context goes in the label, not the path
    assert "label" in _HARD_RULES


def test_hard_rules_forbid_synthetic_figures():
    assert "NO SYNTHETIC FIGURES" in _HARD_RULES
    assert "without a FigureRef" in _HARD_RULES


def test_hard_rules_require_non_empty_field_path():
    assert "FIELD_PATH IS REQUIRED" in _HARD_RULES
    assert "NON-EMPTY" in _HARD_RULES
    # a figure that can't carry a valid path is described in prose, not emitted
    assert "must not be emitted" in _HARD_RULES
    assert "thesis prose" in _HARD_RULES


def test_hard_rules_require_citing_the_originating_tool():
    assert "CITE THE RIGHT TOOL" in _HARD_RULES
    # call_id and tool_name must match the evidence line the value came from
    assert "call_id and tool_name must match" in _HARD_RULES
    # a screen criterion is cited as criteria[N].<field> on the screen tool
    assert "criteria[N].<field>" in _HARD_RULES
    assert "run_strategy_screen" in _HARD_RULES


def test_every_agent_prompt_carries_the_new_rules():
    for prompt in _all_system_prompts():
        assert "ONE FIGURE = ONE FIELD_PATH" in prompt
        assert "NOT EVALUATED" in prompt
        # field_path is path-only (no spaces/commentary/parentheses)
        assert "FIELD_PATH IS PATH-ONLY" in prompt
        assert "no spaces, commentary, or parentheses" in prompt
        # no figure without a backing ledger field
        assert "NO SYNTHETIC FIGURES" in prompt
        assert "without a FigureRef" in prompt
        # field_path must be non-empty (else describe in prose)
        assert "FIELD_PATH IS REQUIRED" in prompt
        assert "must not be emitted" in prompt
        # cite a field only on the tool that returned it; screen criteria path
        assert "CITE THE RIGHT TOOL" in prompt
        assert "criteria[N].<field>" in prompt


def test_prompts_are_externalized_and_versioned():
    # The canonical prompts live in agents.prompts; nodes.py re-exports them.
    from aristos_council.agents import nodes, prompts

    assert isinstance(prompts.PROMPT_VERSION, str) and prompts.PROMPT_VERSION
    # nodes still exposes the same builders (back-compat) -> identical output
    assert nodes._specialist_system is prompts.specialist_system
    assert nodes._critic_system(STRATEGY) == prompts.critic_system(STRATEGY)


def test_technical_brief_defaults_to_neutral_and_de_biases_drawdown():
    # FIX A: ambiguous structure -> NEUTRAL (stops the run-to-run flip), and a
    # drawdown is no longer reflexively bearish (stops fighting the GARP strategy).
    tech = _specialist_system(SpecialistName.TECHNICAL, STRATEGY)
    assert "DEFAULT TO NEUTRAL" in tech
    assert "drawdown is NOT by itself" in tech
    assert "prefer NEUTRAL over guessing" in tech


def test_risk_brief_drops_the_reflexive_pessimist_tilt():
    # FIX B: risk stays downside-focused but no longer manufactures a bearish tilt.
    risk = _specialist_system(SpecialistName.RISK, STRATEGY)
    assert "professional pessimist" not in risk
    assert "without manufacturing a bearish tilt" in risk
    assert "open question, not a negative finding" in risk


def test_report_records_prompt_version():
    from datetime import datetime, timezone

    from aristos_council.agents.prompts import PROMPT_VERSION
    from aristos_council.persistence.reports import report_from_state
    from aristos_council.state import ResearchState

    state = ResearchState(ticker="X", strategy_id="dividend_aristocrats_v1")
    rep = report_from_state(state, run_at=datetime(2026, 6, 29, tzinfo=timezone.utc))
    assert rep.prompt_version == PROMPT_VERSION
