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
from aristos_council.rank_engine import FactorSpec, RankedTicker, rank_universe
from aristos_council.strategy.rank_loader import load_rank_strategy


def test_explain_phrases_the_combined_rank_as_a_cohort_statement():
    # ITEM 3 (narrator source wording): "combined rank-sum N across an M-name cohort",
    # never "combined rank of N/M-name cohort" — this text is fed to the narrator.
    r = RankedTicker(
        ticker="AAA", factor_ranks={"roic": 10.0, "earnings_yield": 14.0},
        factor_values={"roic": 0.2, "earnings_yield": 0.08},
        combined_rank=24.0, universe_size=23, verdict="buy")
    expl = r.explain()
    assert "combined rank-sum 24 across a 23-name cohort" in expl
    assert "combined 24 " not in expl                     # the old bare phrasing is gone

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


# --------------------------------------------------------------------------- #
# 'neutral' missing-mode — a name missing one factor is judged on the rest
# --------------------------------------------------------------------------- #
def test_neutral_missing_judges_on_present_factors_not_worst():
    # B has no payout (None) under 'neutral': it should be imputed with B's mean rank
    # over low_vol + momentum, NOT dumped to worst (3). B is strong on the other two,
    # so it must NOT land at the bottom.
    rows = [
        ("A", {"low_volatility": 0.30, "net_payout_yield": 0.03, "momentum_12m": 0.05}),
        ("B", {"low_volatility": 0.10, "net_payout_yield": None, "momentum_12m": 0.40}),
        ("C", {"low_volatility": 0.20, "net_payout_yield": 0.02, "momentum_12m": 0.10}),
    ]
    specs = [FactorSpec("low_volatility"), FactorSpec("net_payout_yield"),
             FactorSpec("momentum_12m")]
    ranked = rank_universe(rows, specs, cut="top_k", k=1, missing="neutral")
    by = {r.ticker: r for r in ranked}
    # B: low_vol rank 1 (lowest), momentum rank 1 (highest) -> mean 1.0; payout imputed
    # 1.0. combined 3.0. A worst combined. So B ranks FIRST (a BUY), not last.
    assert by["B"].factor_ranks["low_volatility"] == 1.0
    assert by["B"].factor_ranks["momentum_12m"] == 1.0
    assert by["B"].imputed_factors == ["net_payout_yield"]
    assert by["B"].factor_ranks["net_payout_yield"] == 1.0     # imputed = mean(1,1)
    assert by["B"].combined_rank == 3.0
    assert ranked[0].ticker == "B" and ranked[0].verdict == "buy"


def test_neutral_is_strictly_better_than_worst_for_a_name_missing_payout():
    # The actual fix: a non-dividend name strong on the OTHER factors must not be
    # dumped purely for lacking a payout figure. Under 'worst' its payout = n drags
    # it; under 'neutral' it's imputed to its own mean present rank -> strictly lower
    # (better) combined rank.
    rows = [
        ("NOPAY", {"low_volatility": 0.10, "momentum_12m": 0.40, "net_payout_yield": None}),
        ("A", {"low_volatility": 0.30, "momentum_12m": 0.05, "net_payout_yield": 0.03}),
        ("B", {"low_volatility": 0.20, "momentum_12m": 0.10, "net_payout_yield": 0.02}),
    ]
    base = [FactorSpec("low_volatility"), FactorSpec("momentum_12m")]
    neutral = {r.ticker: r for r in rank_universe(
        rows, base + [FactorSpec("net_payout_yield", missing="neutral")],
        cut="top_k", k=1)}
    worst = {r.ticker: r for r in rank_universe(
        rows, base + [FactorSpec("net_payout_yield")], cut="top_k", k=1,
        missing="worst")}
    assert neutral["NOPAY"].combined_rank < worst["NOPAY"].combined_rank
    assert neutral["NOPAY"].imputed_factors == ["net_payout_yield"]
    # strong on low-vol + momentum -> with payout neutralised, NOPAY is the BUY
    assert neutral["NOPAY"].verdict == "buy"


def test_per_factor_missing_overrides_strategy_default():
    # strategy default 'worst', but payout overridden to 'neutral'.
    rows = [("X", {"roic": 0.3, "net_payout_yield": None})]
    specs = [FactorSpec("roic"), FactorSpec("net_payout_yield", missing="neutral")]
    ranked = rank_universe(rows, specs, missing="worst")
    r = ranked[0]
    # roic present (rank 1 of 1); payout neutral-imputed to 1.0 (not worst=1 here, but
    # the point is it's flagged imputed, not a 'worst' fill).
    assert r.imputed_factors == ["net_payout_yield"]


def test_worst_and_exclude_modes_unchanged():
    # Regression: with no neutral factor, behaviour is byte-identical to before.
    rows = [("A", {"roic": 0.3}), ("B", {"roic": None})]
    worst = {r.ticker: r for r in rank_universe(rows, [FactorSpec("roic")],
                                                missing="worst")}
    assert worst["B"].factor_ranks["roic"] == 2.0 and not worst["B"].imputed_factors
    excl = {r.ticker: r for r in rank_universe(rows, [FactorSpec("roic")],
                                               missing="exclude")}
    assert excl["B"].excluded is True


def test_conservative_plus_uses_neutral_payout():
    cons = load_rank_strategy(
        Path(__file__).resolve().parents[1] / "strategies" / "conservative_plus_v1.yaml")
    payout = next(f for f in cons.factors if f.name == "net_payout_yield")
    assert payout.missing == "neutral"


# --------------------------------------------------------------------------- #
# Sector exclusion (Magic Formula drops financials — ROIC invalid there)
# --------------------------------------------------------------------------- #
def test_sector_exclusion_is_case_insensitive_and_confirmed_only():
    from aristos_council.factors import is_sector_excluded

    excl = ["Financial Services", "Utilities"]
    assert is_sector_excluded("financial services", excl) is True   # case-insensitive
    assert is_sector_excluded("Technology", excl) is False
    assert is_sector_excluded(None, excl) is False                  # unknown -> keep
    assert is_sector_excluded("Financial Services", []) is False    # no exclusions


def test_magic_formula_declares_sector_exclusions():
    assert any(s.lower() == "financial services" for s in MAGIC.exclude_sectors)
    assert any(s.lower() == "utilities" for s in MAGIC.exclude_sectors)


def test_rank_loader_rejects_unknown_factor(tmp_path):
    import pytest
    from aristos_council.strategy.rank_loader import load_rank_strategy

    bad = tmp_path / "bad_v1.yaml"
    bad.write_text("id: bad_v1\nname: Bad\nversion: 1\nfactors:\n  - name: not_a_factor\n",
                   encoding="utf-8")
    with pytest.raises(Exception):
        load_rank_strategy(bad)
