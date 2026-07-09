"""Friendly display names for the demo surface (ITEM 1).

Universes and rank strategies carry an optional `display_name` (+ `role`) rendered in
dropdowns; the technical `id` stays the stable record key and is NEVER changed. The
label helpers fall back id/name when a display_name is absent, so nothing crashes on an
un-named asset, and no underscores or '_v1' leak into a label.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aristos_council.demo_surface import (
    strategy_label, strategy_role, universe_label, universe_role,
    visible_rank_strategies, visible_universes)
from aristos_council.strategy.rank_loader import load_rank_strategy
from aristos_council.universe import list_universes, load_universe_by_id

ROOT = Path(__file__).resolve().parents[1]
UNIV_DIR = ROOT / "universes"
STRAT_DIR = ROOT / "strategies"


def _universe(uid):
    return load_universe_by_id(uid, UNIV_DIR)


def _strategy(sid):
    return load_rank_strategy(STRAT_DIR / f"{sid}.yaml")


# --------------------------------------------------------------------------- #
# Universes — friendly names + roles, ids untouched
# --------------------------------------------------------------------------- #
def test_universe_display_names_and_roles():
    g = _universe("growth_40_v1")
    assert universe_label(g) == "Growth 40"
    assert "scoreboard universe" in universe_role(g)
    assert g.id == "growth_40_v1"                         # record key unchanged

    d = _universe("defensive_income_16_v1")
    assert universe_label(d) == "Defensive Income 16"
    assert "scoreboard universe" in universe_role(d)

    b = _universe("defensive_16_v1")
    assert universe_label(b) == "Validation Bench (defensive)"
    assert universe_role(b) == "known-trap controls — never graded"
    assert b.id == "defensive_16_v1"


# --------------------------------------------------------------------------- #
# Strategies — friendly names, ids untouched
# --------------------------------------------------------------------------- #
def test_strategy_display_names():
    assert strategy_label(_strategy("magic_formula_momentum_v1")) == \
        "Value + Momentum (flagship)"
    assert strategy_label(_strategy("conservative_plus_v1")) == \
        "Defensive Income (Conservative Formula+)"
    assert strategy_label(_strategy("magic_formula_v1")) == \
        "Classic Value (baseline — for comparison)"
    # ids are the stable record keys — unchanged.
    assert _strategy("magic_formula_v1").id == "magic_formula_v1"


def test_no_underscores_or_version_in_user_facing_labels():
    for u in list_universes(UNIV_DIR):
        assert "_" not in universe_label(u) and "_v" not in universe_label(u)
    for sid in ("magic_formula_momentum_v1", "conservative_plus_v1", "magic_formula_v1"):
        lab = strategy_label(_strategy(sid))
        assert "_" not in lab and "_v" not in lab


# --------------------------------------------------------------------------- #
# Fallbacks — never crash on an un-named asset
# --------------------------------------------------------------------------- #
@dataclass
class _Stub:
    id: str
    name: str = ""
    display_name: str = ""
    role: str = ""


def test_label_falls_back_to_name_then_id():
    assert strategy_label(_Stub(id="foo_v1", name="Foo")) == "Foo"       # no display_name
    assert strategy_label(_Stub(id="foo_v1")) == "foo_v1"                # no name either
    assert universe_label(_Stub(id="bar_v1")) == "bar_v1"
    assert strategy_role(_Stub(id="x_v1")) == ""                        # empty role ok


# --------------------------------------------------------------------------- #
# Visibility — validation assets hidden by default, revealed by the toggle (ITEM 2)
# --------------------------------------------------------------------------- #
_RANKS = ("magic_formula_momentum_v1", "conservative_plus_v1", "magic_formula_v1")


def test_visible_universes_hides_the_bench_by_default():
    manifests = list_universes(UNIV_DIR)
    off = {u.id for u in visible_universes(manifests, show_validation=False)}
    assert off == {"growth_40_v1", "defensive_income_16_v1"}            # scoreboard only
    on = {u.id for u in visible_universes(manifests, show_validation=True)}
    assert "defensive_16_v1" in on                                      # bench revealed
    # the energy-watch OBSERVATION universe is hidden by default, revealed under the toggle
    assert "energy_watch_v1" not in off and "energy_watch_v1" in on


def test_visible_rank_strategies_hides_the_baseline_by_default():
    strategies = [_strategy(s) for s in _RANKS]
    off = {s.id for s in visible_rank_strategies(strategies, show_validation=False)}
    assert off == {"magic_formula_momentum_v1", "conservative_plus_v1"}  # live only
    on = {s.id for s in visible_rank_strategies(strategies, show_validation=True)}
    assert "magic_formula_v1" in on                                     # baseline revealed


def test_hidden_assets_remain_fully_functional_ids_intact():
    # The bench/baseline are hidden, NOT deleted — still loadable, ids intact (records
    # reference ids, so hiding a demo asset can't change a persisted key).
    assert _universe("defensive_16_v1").id == "defensive_16_v1"
    assert _strategy("magic_formula_v1").id == "magic_formula_v1"
