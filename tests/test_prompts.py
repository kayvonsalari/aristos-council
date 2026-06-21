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
