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

from aristos_council.pipeline import cohort_fit_line
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
    # TODO(CYCLICAL-INCOME-1): absent on this branch — when feat/cyclical-income-1 lands,
    # cyclical_income_v1 declares ("selector", ["income"]). The loop skips missing ids.
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
def _strategy_kwargs(**over):
    base = dict(id="x_v1", name="X", version=1,
                factors=[{"name": "momentum_12m"}])
    base.update(over)
    return base


def test_an_unknown_kind_is_rejected_at_load():
    from aristos_council.strategy.rank_loader import RankStrategy

    with pytest.raises(ValidationError) as e:
        RankStrategy(**_strategy_kwargs(kind="filter"))
    assert "kind must be one of" in str(e.value)


def test_an_unknown_strategy_thesis_is_rejected_at_load():
    from aristos_council.strategy.rank_loader import RankStrategy

    with pytest.raises(ValidationError) as e:
        RankStrategy(**_strategy_kwargs(thesis=["momentum"]))
    assert "thesis entries must be in" in str(e.value)


def test_an_unknown_universe_thesis_is_rejected_at_load():
    with pytest.raises(ValidationError) as e:
        Universe(id="u_v1", tickers=["AAPL"], thesis="cheapness")
    assert "universe thesis must be one of" in str(e.value)


def test_a_universe_without_a_thesis_loads_exactly_as_before():
    u = Universe(id="u_v1", tickers=["AAPL"])
    assert u.thesis == ""


def test_the_shipped_etf_universes_are_marked_funds():
    from aristos_council.universe import load_universe

    for p in sorted(UNIVERSES.glob("etf_*.yaml")):
        assert load_universe(p).thesis == "funds", p.name


# --------------------------------------------------------------------------- #
# The fit line
# --------------------------------------------------------------------------- #
def test_a_value_lens_on_an_income_list_warns():
    line = cohort_fit_line(_load("magic_formula_raw_v1"), "income")
    assert line.startswith("⚠")
    assert "Magic Formula RAW is a value lens" in line
    assert "this list is marked income" in line
    assert "answer a different question" in line


def test_a_matching_lens_says_nothing():
    """Silence is the correct output when there is no mismatch to report."""
    assert cohort_fit_line(_load("conservative_plus_v1"), "income") == ""
    assert cohort_fit_line(_load("magic_formula_raw_v1"), "value") == ""


def test_an_unmarked_list_never_triggers_the_warning():
    """Most local lists make no claim; a claim is required before it can be contradicted."""
    assert cohort_fit_line(_load("magic_formula_raw_v1"), "") == ""


def test_a_check_lens_as_primary_says_it_cannot_select():
    line = cohort_fit_line(_load("forensic_v1"), "income")
    assert line.startswith("ℹ")
    assert "is a check lens; it doubts, it does not select" in line
    assert "Pick a selector as primary for a shortlist" in line


def test_the_check_notice_fires_regardless_of_the_cohort_thesis():
    """A check cannot select on ANY list, marked or not."""
    for thesis in ("", "value", "growth", "income", "funds"):
        assert "check lens" in cohort_fit_line(_load("forensic_v1"), thesis)


def test_a_lens_with_no_declared_thesis_never_warns():
    """The legacy config claims nothing, so it cannot contradict a list."""
    assert cohort_fit_line(_load("magic_formula_v1"), "income") == ""


def test_the_fit_line_is_safe_on_a_missing_primary():
    assert cohort_fit_line(None, "income") == ""
