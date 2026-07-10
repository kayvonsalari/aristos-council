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
    visible_rank_strategies,
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
    # (growth_screen_v1/v2 are growth_garp_v1/v2's lenses)
    assert lens == {"conservative_screen_v1", "magic_value_screen_v1",
                    "growth_screen_v1", "growth_screen_v2"}


def test_every_live_strategy_gets_exactly_one_kind():
    expected = {
        "conservative_plus_v1": "rank",
        "magic_formula_v1": "rank",
        "magic_formula_momentum_v1": "rank",
        "growth_garp_v1": "rank",             # Sprint 4C: the GARP rank strategy (now hidden)
        "growth_garp_v2": "rank",             # 4C-FIX-1: non-gating-momentum GARP
        "magic_formula_raw_v1": "rank",       # RAW-1: canonical no-screen variant
        "financials_v1": "rank",              # FIN-1: financials lens (include_sectors)
        "dividend_aristocrats_v1": "council",
        "growth_v1": "council",
        "conservative_screen_v1": "lens",
        "magic_value_screen_v1": "lens",
        "growth_screen_v1": "lens",           # Sprint 4C: the GARP lens
        "growth_screen_v2": "lens",           # 4C-FIX-1: the v2 lens (no momentum gate)
    }
    for sid, kind in expected.items():
        assert _kind_of(sid) == kind, sid


def test_discovery_is_cwd_independent(monkeypatch, tmp_path):
    # Classification reads absolute paths — the launch cwd must not matter.
    monkeypatch.chdir(tmp_path)
    ids = {s.id for s in discover_strategies(STRAT_DIR)}
    assert "magic_formula_v1" in ids and "growth_v1" in ids


# --------------------------------------------------------------------------- #
# 4C ITEM 1 — visibility (ui: hidden) + discovery is the only strategy source
# --------------------------------------------------------------------------- #
def test_visible_rank_set_is_the_live_strategies():
    visible = {s.id for s in visible_rank_strategies(STRAT_DIR)}
    # 4C-FIX-1: growth_garp_v2 supersedes growth_garp_v1 (hidden) as the visible growth
    # rank strategy; RAW-1's magic_formula_raw_v1 is visible too; FIN-1 adds the
    # financials lens -> five.
    assert visible == {"conservative_plus_v1", "magic_formula_momentum_v1",
                       "growth_garp_v2", "magic_formula_raw_v1", "financials_v1"}


def test_hidden_flag_is_respected_and_screens_never_appear():
    ranks = {s.id: s for s in rank_strategies(STRAT_DIR)}
    # legacy rank strategy is discovered but flagged hidden (not in the visible set)
    assert ranks["magic_formula_v1"].hidden is True
    assert ranks["magic_formula_momentum_v1"].hidden is False
    # a hidden council strategy is flagged too
    assert next(s for s in council_strategies(STRAT_DIR)
                if s.id == "dividend_aristocrats_v1").hidden is True
    # screen-only lenses are never rank candidates
    assert "conservative_screen_v1" not in ranks
    assert "magic_value_screen_v1" not in ranks
    assert "growth_screen_v1" not in ranks


def test_hidden_strategies_still_load_via_the_loader():
    # hidden means NOT LISTED, not removed — the CLI/loader can still load them.
    from aristos_council.strategy.rank_loader import load_rank_strategy
    s = load_rank_strategy(STRAT_DIR / "magic_formula_v1.yaml")
    assert s.id == "magic_formula_v1" and s.ui == "hidden"
