"""Strategy discovery classifies the live strategies/ dir by SHAPE (Sprint: Universe
Run tab). Rank strategies feed the new tab; council strategies feed the single-ticker
page; council-lens screens are hidden — the lens set is DERIVED, not hardcoded.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.strategy.discovery import (
    council_strategies,
    discover_strategies,
    lens_strategy_ids,
    rank_strategies,
)

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


def _kind_of(sid: str) -> str:
    return next(s.kind for s in discover_strategies(STRAT_DIR) if s.id == sid)


def test_rank_strategies_have_factors():
    ids = {s.id for s in rank_strategies(STRAT_DIR)}
    assert {"conservative_plus_v1", "magic_formula_v1",
            "magic_formula_momentum_v1"} <= ids
    # a council/lens screen is never classified as rank
    assert "growth_v1" not in ids
    assert "conservative_screen_v1" not in ids


def test_council_strategies_are_the_single_ticker_ones():
    ids = {s.id for s in council_strategies(STRAT_DIR)}
    assert {"dividend_aristocrats_v1", "growth_v1"} <= ids
    # lens screens and rank strategies are excluded
    assert "conservative_screen_v1" not in ids
    assert "magic_value_screen_v1" not in ids
    assert "magic_formula_v1" not in ids


def test_lens_screens_are_derived_and_hidden():
    lens = lens_strategy_ids(STRAT_DIR)
    # exactly the screens referenced by a rank strategy's council_screen_strategy
    assert lens == {"conservative_screen_v1", "magic_value_screen_v1"}


def test_every_live_strategy_gets_exactly_one_kind():
    expected = {
        "conservative_plus_v1": "rank",
        "magic_formula_v1": "rank",
        "magic_formula_momentum_v1": "rank",
        "dividend_aristocrats_v1": "council",
        "growth_v1": "council",
        "conservative_screen_v1": "lens",
        "magic_value_screen_v1": "lens",
    }
    for sid, kind in expected.items():
        assert _kind_of(sid) == kind, sid


def test_discovery_is_cwd_independent(monkeypatch, tmp_path):
    # Classification reads absolute paths — the launch cwd must not matter.
    monkeypatch.chdir(tmp_path)
    ids = {s.id for s in discover_strategies(STRAT_DIR)}
    assert "magic_formula_v1" in ids and "growth_v1" in ids
