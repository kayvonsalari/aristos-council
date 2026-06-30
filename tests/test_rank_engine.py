"""Rank-combine engine + factor registry + magic_formula (Aristos v2 Phase 1).

Deterministic, no network/LLM. The engine ranks a fixed factor table; Greenblatt's
rank-sum is reproduced exactly; the magic_formula rank-strategy loads and validates.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.factors import (
    FACTOR_REGISTRY,
    FactorInputs,
    compute_factors,
)
from aristos_council.data.adapter import Fundamentals
from aristos_council.rank_engine import FactorSpec, rank_universe
from aristos_council.strategy.rank_loader import load_rank_strategy

MAGIC = load_rank_strategy(
    Path(__file__).resolve().parents[1] / "strategies" / "magic_formula_v1.yaml")


# --------------------------------------------------------------------------- #
# Per-factor ranks + combined rank-sum (Greenblatt's exact mechanic)
# --------------------------------------------------------------------------- #
def test_rank_sum_reproduces_greenblatt_on_a_known_table():
    # earnings_yield (high best) and roic (high best). Hand-rank to check.
    rows = [
        ("A", {"earnings_yield": 0.10, "roic": 0.30}),   # EY rank2, ROIC rank1
        ("B", {"earnings_yield": 0.12, "roic": 0.20}),   # EY rank1, ROIC rank2
        ("C", {"earnings_yield": 0.05, "roic": 0.10}),   # EY rank3, ROIC rank3
    ]
    specs = [FactorSpec("earnings_yield", "high"), FactorSpec("roic", "high")]
    ranked = rank_universe(rows, specs, cut="top_k", k=1)
    by = {r.ticker: r for r in ranked}
    assert by["A"].factor_ranks == {"earnings_yield": 2.0, "roic": 1.0}
    assert by["B"].factor_ranks == {"earnings_yield": 1.0, "roic": 2.0}
    assert by["A"].combined_rank == 3.0 and by["B"].combined_rank == 3.0
    assert by["C"].combined_rank == 6.0
    # A and B tie at 3; tie-break by ticker -> A first. C is worst.
    assert [r.ticker for r in ranked] == ["A", "B", "C"]
    assert by["C"].verdict == "hold"            # not in top_k=1


def test_low_direction_ranks_smaller_value_best():
    rows = [("LOWVOL", {"low_volatility": 0.10}),
            ("MIDVOL", {"low_volatility": 0.20}),
            ("HIGHVOL", {"low_volatility": 0.40})]
    ranked = rank_universe(rows, [FactorSpec("low_volatility")], cut="top_k", k=1)
    by = {r.ticker: r for r in ranked}
    assert by["LOWVOL"].factor_ranks["low_volatility"] == 1.0   # lowest vol = best
    assert by["HIGHVOL"].factor_ranks["low_volatility"] == 3.0
    assert by["LOWVOL"].verdict == "buy"


def test_ties_get_average_rank():
    rows = [("X", {"roic": 0.20}), ("Y", {"roic": 0.20}), ("Z", {"roic": 0.10})]
    ranked = rank_universe(rows, [FactorSpec("roic", "high")], cut="quintile")
    by = {r.ticker: r for r in ranked}
    # X and Y tie for ranks 1,2 -> average 1.5; Z is rank 3.
    assert by["X"].factor_ranks["roic"] == 1.5
    assert by["Y"].factor_ranks["roic"] == 1.5
    assert by["Z"].factor_ranks["roic"] == 3.0


# --------------------------------------------------------------------------- #
# Quintile cut + determinism + missing handling
# --------------------------------------------------------------------------- #
def test_quintile_cut_top_and_bottom_fifths():
    rows = [(f"T{i}", {"roic": float(i)}) for i in range(10)]   # T9 best (high)
    ranked = rank_universe(rows, [FactorSpec("roic", "high")], cut="quintile")
    buys = [r.ticker for r in ranked if r.verdict == "buy"]
    sells = [r.ticker for r in ranked if r.verdict == "sell"]
    assert buys == ["T9", "T8"]                 # top 20% of 10
    assert sells == ["T1", "T0"]                # bottom 20%


def test_ranking_is_deterministic():
    rows = [("A", {"roic": 0.2, "earnings_yield": 0.1}),
            ("B", {"roic": 0.3, "earnings_yield": 0.05})]
    specs = [FactorSpec("roic"), FactorSpec("earnings_yield")]
    a = rank_universe(rows, specs)
    b = rank_universe(rows, specs)
    assert [(r.ticker, r.combined_rank, r.verdict) for r in a] == \
           [(r.ticker, r.combined_rank, r.verdict) for r in b]


def test_missing_exclude_drops_name_from_buy():
    rows = [("GOOD", {"roic": 0.3, "earnings_yield": 0.1}),
            ("NODATA", {"roic": None, "earnings_yield": 0.1})]
    ranked = rank_universe(rows, [FactorSpec("roic"), FactorSpec("earnings_yield")],
                           cut="top_k", k=1, missing="exclude")
    by = {r.ticker: r for r in ranked}
    assert by["NODATA"].excluded is True and by["NODATA"].verdict != "buy"
    assert "missing factor" in by["NODATA"].reason
    assert by["GOOD"].verdict == "buy"


def test_missing_worst_keeps_name_at_worst_rank():
    rows = [("GOOD", {"roic": 0.3}), ("NODATA", {"roic": None})]
    ranked = rank_universe(rows, [FactorSpec("roic")], missing="worst", cut="top_k",
                           k=1)
    by = {r.ticker: r for r in ranked}
    assert by["NODATA"].excluded is False
    assert by["NODATA"].factor_ranks["roic"] == 2.0   # worst rank == universe size


# --------------------------------------------------------------------------- #
# Factor extraction + magic_formula strategy
# --------------------------------------------------------------------------- #
def test_earnings_yield_proxy_and_fallback():
    ebit_name = FactorInputs(ticker="X", fundamentals=Fundamentals(
        ticker="X", market_cap=1000.0, ebit=[100.0]))
    assert abs(compute_factors(ebit_name, ["earnings_yield"])["earnings_yield"]
               - 0.10) < 1e-9                   # 100/1000
    pe_only = FactorInputs(ticker="Y", fundamentals=Fundamentals(
        ticker="Y", market_cap=1000.0, pe_ratio=20.0))    # no ebit -> 1/PE
    assert abs(compute_factors(pe_only, ["earnings_yield"])["earnings_yield"]
               - 0.05) < 1e-9


def test_magic_formula_strategy_loads_and_validates():
    assert MAGIC.id == "magic_formula_v1"
    assert [f.name for f in MAGIC.factors] == ["roic", "earnings_yield"]
    assert MAGIC.missing == "worst" and MAGIC.min_market_cap == 5.0e9
    assert MAGIC.cut == "quintile"
    # all declared factors exist in the registry
    assert all(f.name in FACTOR_REGISTRY for f in MAGIC.factors)


def test_rank_loader_rejects_unknown_factor(tmp_path):
    import pytest
    from aristos_council.strategy.rank_loader import load_rank_strategy

    bad = tmp_path / "bad_v1.yaml"
    bad.write_text("id: bad_v1\nname: Bad\nversion: 1\nfactors:\n  - name: not_a_factor\n",
                   encoding="utf-8")
    with pytest.raises(Exception):
        load_rank_strategy(bad)
