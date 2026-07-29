"""Lens-appropriate framing across ALL narration agents (NARR-PROMPT-1).

The v6 fix reached only the FUNDAMENTAL specialist, so the stock-era emphasis still
travelled through the other three specialists (RISK's "payout stretch"), the CRITIC (its
open-question example asked whether "the dividend" was covered by free cash flow) and the
NARRATOR (which carried no lens emphasis at all — the live 2026-07-21 etf_core_v1
narration claimed "the strategy brief emphasises dividend durability and payout
sustainability" on a lens that deliberately has NO distribution_yield factor).

These pin the completed fix:
  - ONE builder (``prompts.lens_brief``) is the single source EVERY agent prompt is
    assembled from — its emphasis block appears verbatim in all six agent prompts;
  - a CORE-lens fixture carries NO dividend-durability framing in ANY agent (specialists,
    critic, narrator), in either council mode;
  - a DIVIDEND-lens fixture still assesses payout/distribution everywhere it should;
  - a STOCK lens keeps today's framing (no emphasis block, default briefs, today's
    critic example);
  - no stray hard-coded dividend framing survives anywhere else in the package.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.agents.prompts import (
    LENS_ETF_INCOME,
    LENS_ETF_NO_INCOME,
    LENS_STOCK,
    SPECIALIST_BRIEFS,
    critic_system,
    decision_system,
    lens_brief,
    specialist_system,
)
from aristos_council.pipeline import _screenless_frame
from aristos_council.state import SpecialistName
from aristos_council.strategy.loader import load_strategy
from aristos_council.strategy.rank_loader import load_rank_strategy

ROOT = Path(__file__).resolve().parents[1]
STRAT_DIR = ROOT / "strategies"
PKG = ROOT / "src" / "aristos_council"

_SPECIALISTS = tuple(SpecialistName)          # fundamental, technical, sentiment, risk
_MODES = ("narrator", "second_opinion")

# The affirmative stock-era dividend phrasings. None of these may reach a lens that does
# not rank income (an explicit DISCLAIMER mentioning dividends is not one of these).
_DIVIDEND_FRAMING = (
    "dividend durability",
    "payout sustainability",
    "payout stretch",
    "Is the dividend covered by free cash flow",
    "thin dividend coverage",
)


def _frame(strategy_file: str):
    """The council frame the pipeline hands the agents for a screen-less rank lens."""
    return _screenless_frame(load_rank_strategy(STRAT_DIR / strategy_file))


def _agent_prompts(frame, mode: str = "narrator") -> dict[str, str]:
    """EVERY LLM agent in the narration graph: the four specialists, the critic, and the
    decision/narrator. If a new agent is added, add it here."""
    prompts = {f"specialist:{who.value}": specialist_system(who, frame, mode)
               for who in _SPECIALISTS}
    prompts["critic"] = critic_system(frame)
    prompts["decision"] = decision_system(frame, mode)
    return prompts


# --- CORE lens: no dividend framing ANYWHERE --------------------------------- #
def test_core_lens_has_no_dividend_framing_in_any_agent():
    frame = _frame("etf_core_v1.yaml")
    for mode in _MODES:
        for agent, prompt in _agent_prompts(frame, mode).items():
            for phrase in _DIVIDEND_FRAMING:
                assert phrase not in prompt, f"{agent} ({mode}) carries '{phrase}'"


def test_core_lens_every_agent_disclaims_income_assessment():
    frame = _frame("etf_core_v1.yaml")
    for agent, prompt in _agent_prompts(frame).items():
        assert "NOT an income lens" in prompt, f"{agent} lacks the ACC/DIST disclaimer"
        assert "share-class artefact" in prompt


def test_core_lens_emphasis_names_the_ranked_factors():
    brief = lens_brief(_frame("etf_core_v1.yaml"))
    assert brief.kind == LENS_ETF_NO_INCOME
    emphasis = brief.emphasis()
    assert "Expense ratio" in emphasis          # cost
    assert "Fund size" in emphasis              # scale
    assert "momentum" in emphasis.lower()       # trend


# --- the builder is the SINGLE source every agent consumes ------------------- #
def test_the_brief_builder_is_the_single_source_for_every_agent():
    for strategy_file in ("etf_core_v1.yaml", "etf_dividend_v1.yaml",
                          "etf_growth_v1.yaml"):
        frame = _frame(strategy_file)
        emphasis = lens_brief(frame).emphasis().strip()
        assert emphasis, f"{strategy_file}: an ETF lens must carry an emphasis block"
        for mode in _MODES:
            for agent, prompt in _agent_prompts(frame, mode).items():
                assert emphasis in prompt, f"{agent} ({mode}) not built from lens_brief"


def test_no_stray_hardcoded_dividend_framing_in_the_package():
    # The stock-era phrasings may live ONLY in agents/prompts.py, where they are the
    # STOCK-lens defaults the builder selects — nowhere else may hand-write framing.
    offenders = sorted(
        str(p.relative_to(PKG)) for p in PKG.rglob("*.py")
        if any(phrase in p.read_text(encoding="utf-8")
               for phrase in _DIVIDEND_FRAMING))
    assert offenders == ["agents/prompts.py"]


def test_critic_and_narrator_carry_the_lens_honesty_note():
    # The critic and the decision/narrator have no STRATEGY INTENT section of their own,
    # so the emphasis block carries the strategy's OWN honesty note to them.
    frame = _frame("etf_core_v1.yaml")
    note = lens_brief(frame).honesty_note
    assert note                                  # the core lens carries one verbatim
    for prompt in (critic_system(frame), decision_system(frame, "narrator")):
        assert "HONESTY NOTE" in prompt
        assert note in prompt
    # specialists already receive it as STRATEGY INTENT — not duplicated as a note
    assert "HONESTY NOTE" not in specialist_system(
        SpecialistName.FUNDAMENTAL, frame, "narrator")


# --- DIVIDEND lens: income is still assessed -------------------------------- #
def test_dividend_lens_still_assesses_payout_across_agents():
    frame = _frame("etf_dividend_v1.yaml")
    assert lens_brief(frame).kind == LENS_ETF_INCOME
    for agent, prompt in _agent_prompts(frame).items():
        assert "NOT an income lens" not in prompt, f"{agent} wrongly disclaims income"
    low_fund = specialist_system(SpecialistName.FUNDAMENTAL, frame, "narrator").lower()
    assert "payout" in low_fund and "distribution" in low_fund and "income" in low_fund
    low_risk = specialist_system(SpecialistName.RISK, frame, "narrator").lower()
    assert "distribution durability" in low_risk
    assert "distribution" in critic_system(frame).lower()


# --- STOCK lens: today's framing is untouched -------------------------------- #
def test_stock_lens_keeps_todays_framing():
    stock = load_strategy(STRAT_DIR / "dividend_aristocrats_v1.yaml")
    brief = lens_brief(stock)
    assert brief.kind == LENS_STOCK
    assert brief.emphasis() == ""               # no ETF block is injected
    for mode in _MODES:
        for agent, prompt in _agent_prompts(stock, mode).items():
            assert "LENS EMPHASIS" not in prompt, f"{agent} ({mode}) gained a block"
    for who in _SPECIALISTS:
        assert SPECIALIST_BRIEFS[who] in specialist_system(who, stock, "narrator")
    # the critic keeps today's dividend-coverage example and ranker attacks
    critic = critic_system(stock)
    assert "Is the dividend covered by free cash flow" in critic
    assert "a buyback masking dilution" in critic
    # the second-opinion RANKER CHECK keeps today's defensive-name example
    assert "thin dividend coverage" in specialist_system(
        SpecialistName.RISK, stock, "second_opinion")


def test_screenless_equity_rank_lens_is_a_stock_lens():
    frame = _frame("magic_formula_raw_v1.yaml")
    assert frame.lens_kind == LENS_STOCK
    assert lens_brief(frame).emphasis() == ""
    for prompt in _agent_prompts(frame).values():
        assert "LENS EMPHASIS" not in prompt


# --- the frame the pipeline stamps carries the derived lens ------------------ #
def test_screenless_frame_stamps_the_derived_lens():
    core = _frame("etf_core_v1.yaml")
    assert core.lens_kind == LENS_ETF_NO_INCOME
    assert "Expense ratio (low best)" in core.lens_factor_labels
    assert _frame("etf_dividend_v1.yaml").lens_kind == LENS_ETF_INCOME
    assert _frame("etf_growth_v1.yaml").lens_kind == LENS_ETF_NO_INCOME
