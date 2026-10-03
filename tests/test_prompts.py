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


def test_hard_rules_calibration_anchors_confidence_to_decisiveness_not_just_completeness():
    """COUNCIL-FIX-1(f) (Batch 15) — EL.PA: three specialists reading one evidence pack
    all reported 0.72, which the old wording ("reflect the completeness of the evidence,
    not the strength of your conviction") directly invited: completeness is near-identical
    across specialists on the SAME ledger, conviction is not."""
    low = _HARD_RULES.lower()
    assert "calibration" in low
    assert "decisively" in low
    assert "0.4-0.6" in _HARD_RULES and "0.8" in _HARD_RULES


def test_hard_rules_require_naming_the_valuation_band():
    """COUNCIL-FIX-1(a) — EL.PA's band read the 1st (cheapest) percentile while the risk
    specialist called the same name a 'valuation stretch' with no mention of the band."""
    assert "VALUATION BAND" in _HARD_RULES
    assert "percentile" in _HARD_RULES.lower()


def test_hard_rules_require_attributing_rules_to_the_owning_lens():
    """COUNCIL-FIX-1(b) — EL.PA's risk specialist attributed Growth's own qualifying
    thresholds (ROIC >= 12%, PEG <= 2.00) to Magic Formula RAW."""
    assert "RULE ATTRIBUTION" in _HARD_RULES
    assert "ROIC >= 12%" in _HARD_RULES or "ROIC" in _HARD_RULES


def test_sentiment_brief_reads_headlines_when_ratings_are_absent():
    """COUNCIL-FIX-1(c) — EL.PA had 5 real headlines (including the Meta AI-glasses
    announcement) and the specialist still returned not-assessed because analyst ratings
    were null. Headlines alone must be enough to form a stance."""
    from aristos_council.agents.prompts import SPECIALIST_BRIEFS
    from aristos_council.state import SpecialistName

    brief = SPECIALIST_BRIEFS[SpecialistName.SENTIMENT]
    assert "headlines alone are sufficient" in brief.lower()
    assert "both channels are" in brief.lower() or "both" in brief.lower()


def test_technical_and_fundamental_briefs_each_stay_in_their_own_lane():
    """COUNCIL-FIX-1(e) — EL.PA's technical specialist cited free cash flow and P/E,
    neither of which is technical_snapshot evidence."""
    from aristos_council.agents.prompts import SPECIALIST_BRIEFS
    from aristos_council.state import SpecialistName

    technical = SPECIALIST_BRIEFS[SpecialistName.TECHNICAL]
    fundamental = SPECIALIST_BRIEFS[SpecialistName.FUNDAMENTAL]
    assert "STAY IN YOUR LANE" in technical and "free cash flow" in technical.lower()
    assert "STAY IN YOUR LANE" in fundamental and "moving averages" in fundamental.lower()


def test_structured_narration_requires_saying_no_real_disagreement_plainly():
    """COUNCIL-FIX-1(g) — the old instruction told the narrator to OMIT
    disagreement_note on agreement, leaving a reader to infer silence means agreement.
    It must now say so."""
    from aristos_council.agents.prompts import STRUCTURED_NARRATION

    assert "no real disagreement" in STRUCTURED_NARRATION.lower()
    assert "never leave it out" in STRUCTURED_NARRATION.lower() \
        or "always fill it" in STRUCTURED_NARRATION.lower()


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
