"""Lens-appropriate FUNDAMENTAL specialist framing (NARR-PROMPT-1).

The narration specialists used to run one hardcoded dividend/quality brief on EVERY
strategy — so an ETF CORE lens (etf_core_v1), which deliberately has NO
distribution_yield factor, was framed with "dividend durability and payout
sustainability" language it never measures (live on VUSA.AS / SPYY.DE).

The fix derives the FUNDAMENTAL brief from what the lens ACTUALLY ranks, templated per
strategy KIND (stock / dividend-ETF / core-or-growth-ETF), interpolating the factor
names. These pin:
  - a CORE-lens brief carries no dividend-durability / payout-sustainability framing and
    explicitly disclaims income assessment;
  - a DIVIDEND-lens brief still assesses payout / income;
  - a GROWTH-lens brief also disclaims dividends (core & growth share the ETF template);
  - a STOCK/screen lens keeps today's brief byte-for-byte (the default stands);
  - the specialist prompt STRUCTURE (brief + hard rules + strategy intent) is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.agents.prompts import (
    SPECIALIST_BRIEFS, fundamental_brief_for_lens, specialist_system)
from aristos_council.pipeline import _screenless_frame
from aristos_council.state import SpecialistName
from aristos_council.strategy.loader import load_strategy
from aristos_council.strategy.rank_loader import load_rank_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"

_DEFAULT_FUNDAMENTAL = SPECIALIST_BRIEFS[SpecialistName.FUNDAMENTAL]


def _fundamental_sys(strategy) -> str:
    return specialist_system(SpecialistName.FUNDAMENTAL, strategy, "narrator")


def _core_frame():
    return _screenless_frame(load_rank_strategy(STRAT_DIR / "etf_core_v1.yaml"))


def _dividend_frame():
    return _screenless_frame(load_rank_strategy(STRAT_DIR / "etf_dividend_v1.yaml"))


def _growth_frame():
    return _screenless_frame(load_rank_strategy(STRAT_DIR / "etf_growth_v1.yaml"))


# --- CORE lens: no dividend framing, explicit income disclaimer -------------- #
def test_core_lens_has_no_dividend_durability_framing():
    sys = _fundamental_sys(_core_frame())
    # the two affirmative dividend phrasings from today's stock brief are GONE
    assert "dividend durability" not in sys
    assert "payout sustainability" not in sys
    # ...and the lens explicitly says it is not an income lens (the ACC/DIST doctrine)
    assert "NOT an income lens" in sys
    assert "share-class artefact" in sys


def test_core_lens_names_its_own_factors():
    # the brief is templated from what the lens RANKS — cost, size, trend
    brief = fundamental_brief_for_lens(load_rank_strategy(STRAT_DIR / "etf_core_v1.yaml"))
    assert "Expense ratio" in brief
    assert "Fund size" in brief
    assert "momentum" in brief.lower()


# --- DIVIDEND lens: still assesses payout / income --------------------------- #
def test_dividend_lens_still_assesses_payout():
    sys = _fundamental_sys(_dividend_frame())
    low = sys.lower()
    assert "payout" in low
    assert "income" in low
    assert "distribution" in low
    # it is not falsely disclaiming dividends the way the core/growth template does
    assert "NOT an income lens" not in sys


# --- GROWTH lens: shares the ETF template, also disclaims dividends ----------- #
def test_growth_lens_also_disclaims_dividends():
    sys = _fundamental_sys(_growth_frame())
    assert "dividend durability" not in sys
    assert "payout sustainability" not in sys
    assert "NOT an income lens" in sys


# --- STOCK / screen lens: byte-comparable to today --------------------------- #
def test_stock_screen_lens_keeps_todays_brief_verbatim():
    stock = load_strategy(STRAT_DIR / "dividend_aristocrats_v1.yaml")
    sys = _fundamental_sys(stock)
    # the default brief is used verbatim (the interpolated ETF wording never appears)
    assert _DEFAULT_FUNDAMENTAL in sys
    assert "NOT an income lens" not in sys
    # a screen strategy carries no override, so the default stands
    assert getattr(stock, "fundamental_brief", "") == ""


def test_screenless_equity_lens_keeps_todays_brief():
    # a screen-less EQUITY rank frame (magic_formula_raw_v1) is a stock lens -> default
    raw = load_rank_strategy(STRAT_DIR / "magic_formula_raw_v1.yaml")
    frame = _screenless_frame(raw)
    assert frame.fundamental_brief == ""
    assert _DEFAULT_FUNDAMENTAL in _fundamental_sys(frame)


def test_builder_returns_empty_for_a_stock_lens():
    raw = load_rank_strategy(STRAT_DIR / "magic_formula_raw_v1.yaml")
    assert fundamental_brief_for_lens(raw) == ""


# --- prompt STRUCTURE unchanged across lenses -------------------------------- #
def test_specialist_prompt_structure_unchanged_for_etf_lens():
    sys = _fundamental_sys(_core_frame())
    # same scaffold every specialist prompt has always carried
    assert "You are the FUNDAMENTAL specialist" in sys
    assert "Your brief:" in sys
    assert "HARD RULES" in sys
    assert "ONE FIGURE = ONE FIELD_PATH" in sys
    assert "STRATEGY INTENT" in sys


def test_only_fundamental_brief_is_lens_aware():
    # the other roles are unchanged — the override only touches FUNDAMENTAL
    core = _core_frame()
    for who in (SpecialistName.TECHNICAL, SpecialistName.SENTIMENT, SpecialistName.RISK):
        sys = specialist_system(who, core, "narrator")
        assert SPECIALIST_BRIEFS[who] in sys
