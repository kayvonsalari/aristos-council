"""Magic Formula + Momentum — momentum as a third rank factor guards the value
screen's falling-knife blind spot (ADBE-type). classic magic_formula_v1 preserved as
the baseline. Deterministic: fixed factor tables, no network/LLM.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.rank_engine import FactorSpec, rank_universe
from aristos_council.strategy.rank_loader import load_rank_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
CLASSIC = load_rank_strategy(STRAT_DIR / "magic_formula_v1.yaml")
MOMENTUM = load_rank_strategy(STRAT_DIR / "magic_formula_momentum_v1.yaml")


def _specs(strat):
    return [FactorSpec(f.name, f.direction, f.missing) for f in strat.factors]


# A 5-name universe. A = cheapest + highest quality but CRASHING (worst momentum);
# B = 2nd on value/quality with GOOD momentum. Under classic (value+quality only) A
# wins; adding momentum demotes the knife A below B.
_ROWS = [
    ("A", {"roic": 0.30, "earnings_yield": 0.12, "momentum_12m": -0.45}),  # falling knife
    ("B", {"roic": 0.28, "earnings_yield": 0.11, "momentum_12m": 0.20}),
    ("C", {"roic": 0.26, "earnings_yield": 0.10, "momentum_12m": 0.15}),
    ("D", {"roic": 0.24, "earnings_yield": 0.09, "momentum_12m": 0.10}),
    ("E", {"roic": 0.22, "earnings_yield": 0.08, "momentum_12m": -0.30}),
]


def _order(strat):
    ranked = [r for r in rank_universe(_ROWS, _specs(strat), cut=strat.cut,
                                       missing=strat.missing) if not r.excluded]
    return [r.ticker for r in ranked]


# --------------------------------------------------------------------------- #
# Loaders — the two strategies genuinely differ; classic untouched
# --------------------------------------------------------------------------- #
def test_momentum_strategy_loads_with_three_factors():
    assert MOMENTUM.id == "magic_formula_momentum_v1"
    assert [f.name for f in MOMENTUM.factors] == \
        ["roic", "earnings_yield", "momentum_12m"]
    assert MOMENTUM.council_screen_strategy == "magic_value_screen_v1"
    assert MOMENTUM.min_market_cap == 5.0e9


def test_classic_magic_formula_is_unchanged_two_factors_no_momentum():
    assert [f.name for f in CLASSIC.factors] == ["roic", "earnings_yield"]
    assert all(f.name != "momentum_12m" for f in CLASSIC.factors)


# --------------------------------------------------------------------------- #
# The reorder — the guard demotes the knife, and the two strategies differ
# --------------------------------------------------------------------------- #
def test_falling_knife_tops_classic_but_is_demoted_by_momentum():
    classic = _order(CLASSIC)
    momentum = _order(MOMENTUM)
    # classic (value+quality only): the cheapest/highest-quality name A is #1
    assert classic[0] == "A"
    # +momentum: A (in a 45% drawdown) is demoted below B; B now leads
    assert momentum[0] == "B"
    assert momentum.index("A") > momentum.index("B")
    assert classic != momentum                    # the strategies genuinely differ


def test_modest_drawdown_quality_name_is_not_punished():
    # A cheap+quality name with only a MODEST drawdown (mid momentum rank) must stay
    # high — the guard reorders knives, it does not gut ordinary value entries.
    rows = [
        # MODEST: strongest value+quality, only a -4% dip (momentum mid-rank)
        ("MODEST", {"roic": 0.30, "earnings_yield": 0.12, "momentum_12m": -0.04}),
        # KNIFE: comparable value+quality but in a 45% freefall (momentum worst)
        ("KNIFE", {"roic": 0.28, "earnings_yield": 0.11, "momentum_12m": -0.45}),
        ("OK1", {"roic": 0.20, "earnings_yield": 0.08, "momentum_12m": 0.10}),
        ("OK2", {"roic": 0.18, "earnings_yield": 0.07, "momentum_12m": 0.08}),
        ("OK3", {"roic": 0.16, "earnings_yield": 0.06, "momentum_12m": 0.05}),
    ]
    order = [r.ticker for r in rank_universe(rows, _specs(MOMENTUM),
                                             cut=MOMENTUM.cut, missing=MOMENTUM.missing)]
    # the modest-drawdown quality name stays ABOVE the freefall knife...
    assert order.index("MODEST") < order.index("KNIFE")
    # ...and remains in the top half (not banished for a small dip)
    assert order.index("MODEST") < len(order) / 2
