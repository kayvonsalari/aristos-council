"""KIND-1 / THESIS-1 — what a lens is FOR, and what a list was built for.

The reading rule these encode, established by two runs on 2026-09-16: ONE selector lens
per run, chosen to match what the cohort was built for, decides "worth owning"; a CHECK
lens (Forensic today; quality_v1 and epv_v1 when they land) only says "reason to doubt the
selector's yes". A BUY under a check is not a recommendation and a SELL under one is not a
rejection — it is a doubt raised about someone else's pick.

Both vocabularies are CLOSED, so a typo fails at load rather than reading as a lens that
fits nothing and warns on every cohort. Both are advisory: nothing here filters a strategy
out of a picker or blocks a run. The 2026-08-10 lesson was that hiding a runnable lens is
worse than letting a reader choose badly with a caption in front of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aristos_council.strategy.discovery import visible_rank_strategies
from aristos_council.strategy.rank_loader import load_rank_strategy
from aristos_council.universe import Universe

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
UNIVERSES = Path(__file__).resolve().parents[1] / "universes"


def _load(sid):
    return load_rank_strategy(STRAT_DIR / f"{sid}.yaml")


# --------------------------------------------------------------------------- #
# What each shipped lens declares
# --------------------------------------------------------------------------- #
EXPECTED = {
    "magic_formula_raw_v1": ("selector", ["value"]),
    "magic_formula_momentum_v1": ("selector", ["value"]),
    "growth_garp_v2": ("selector", ["growth"]),
    "conservative_plus_v1": ("selector", ["income"]),
    "financials_v1": ("selector", ["value"]),
    "etf_dividend_v1": ("selector", ["funds"]),
    "etf_growth_v1": ("selector", ["funds"]),
    "etf_core_v1": ("selector", ["funds"]),
    "forensic_v1": ("check", []),
    "cyclical_income_v1": ("selector", ["income"]),
}


@pytest.mark.parametrize("sid", sorted(EXPECTED))
def test_each_shipped_lens_declares_its_kind_and_thesis(sid):
    path = STRAT_DIR / f"{sid}.yaml"
    if not path.exists():
        pytest.skip(f"{sid} not present")
    s = load_rank_strategy(path)
    kind, thesis = EXPECTED[sid]
    assert (s.kind, s.thesis) == (kind, thesis), sid


def test_forensic_is_the_only_check_lens_today():
    checks = [s.id for s in visible_rank_strategies(STRAT_DIR)
              if load_rank_strategy(s.path).kind == "check"]
    assert checks == ["forensic_v1"]


def test_a_check_lens_declares_no_thesis():
    """It doubts every cohort alike, so a thesis would be a claim it does not make."""
    assert _load("forensic_v1").thesis == []


def test_every_visible_selector_declares_a_thesis():
    missing = [s.id for s in visible_rank_strategies(STRAT_DIR)
               if load_rank_strategy(s.path).kind == "selector"
               and not load_rank_strategy(s.path).thesis]
    assert missing == [], f"selectors with no thesis: {missing}"


def test_the_default_is_selector_so_an_unmarked_strategy_keeps_its_meaning():
    assert _load("magic_formula_v1").kind == "selector"      # legacy, declares neither
    assert _load("magic_formula_v1").thesis == []


# --------------------------------------------------------------------------- #
# Closed vocabularies
# --------------------------------------------------------------------------- #
# The fit line is GONE (SHORTLIST-3)
# --------------------------------------------------------------------------- #
# It warned when the PRIMARY lens answered a different question than the list was built
# for, or was a check lens that could not select at all. Both sentences were about a lens
# elected above the others, and there is no such lens any more: every ticked lens is a
# vote of equal weight, so "the lens for this list" is not a thing the run has.
#
# `kind` and `thesis` both survive it and both still do work — see the tests above. `kind`
# is what tells a VOTE from a MARK in the agreement table, which is the load-bearing use;
# `thesis` still describes what a list was built for and still reaches the run's summary.

def test_the_fit_line_builder_is_gone():
    import aristos_council.pipeline as pipeline

    assert not hasattr(pipeline, "cohort_fit_line")


def test_kind_still_decides_which_lenses_VOTE():
    """The replacement for what the fit line was reaching for. A check does not select —
    that was true before and is still true; it is now expressed by not casting a vote
    rather than by a warning about having been picked as primary."""
    assert _load("forensic_v1").kind == "check"
    assert _load("magic_formula_raw_v1").kind == "selector"


def test_thesis_still_describes_a_list_and_a_lens():
    """Kept on both, because the agreement note and the run's summary still read them."""
    assert "value" in (_load("magic_formula_raw_v1").thesis or [])
    assert "income" in (_load("conservative_plus_v1").thesis or [])
