"""Conservative Formula+ (Aristos v2 Phase 2) — the NVO-proof strategy.

A cheap-but-falling name (great trailing fundamentals, but down ~25% with high vol)
ranks OUTSIDE the BUY quintile on the momentum + low-vol legs — the falling-knife fix
as a PROPERTY OF THE RANKING, with NO tuned weight. Deterministic, no network/LLM.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.factors import FactorInputs, compute_factors
from aristos_council.data.adapter import Fundamentals
from aristos_council.rank_engine import FactorSpec, rank_universe
from aristos_council.strategy.rank_loader import load_rank_strategy

CONSERVATIVE = load_rank_strategy(
    Path(__file__).resolve().parents[1] / "strategies" / "conservative_plus_v1.yaml")
SPECS = [FactorSpec(f.name, f.direction) for f in CONSERVATIVE.factors]


def _row(ticker, *, vol, payout, mom):
    fi = FactorInputs(
        ticker=ticker,
        fundamentals=Fundamentals(ticker=ticker, dividend_yield=payout),
        return_12m=mom, annualized_volatility=vol)
    return (ticker, compute_factors(fi, [f.name for f in CONSERVATIVE.factors]))


def test_conservative_plus_loads_with_three_factors():
    assert CONSERVATIVE.id == "conservative_plus_v1"
    assert [f.name for f in CONSERVATIVE.factors] == [
        "low_volatility", "net_payout_yield", "momentum_12m"]


def test_falling_knife_ranks_outside_buy_despite_good_fundamentals():
    # NVO-shaped: it would WIN a value/quality screen, but conservative_plus ranks on
    # vol + payout + momentum, where a -25% high-vol name is near worst on two legs.
    universe = [
        _row("NVO",  vol=0.45, payout=0.02, mom=-0.25),   # falling knife
        _row("LLY",  vol=0.22, payout=0.01, mom=+0.25),   # uptrend, low-ish vol
        _row("KO",   vol=0.15, payout=0.03, mom=+0.08),   # steady defensive
        _row("PG",   vol=0.16, payout=0.025, mom=+0.10),
        _row("PEP",  vol=0.18, payout=0.028, mom=+0.05),
    ]
    ranked = rank_universe(universe, SPECS, cut=CONSERVATIVE.cut,
                           missing=CONSERVATIVE.missing)
    by = {r.ticker: r for r in ranked}
    # NVO is the WORST combined rank (bottom of the universe), and NOT a BUY.
    assert by["NVO"].verdict != "buy"
    assert by["NVO"].combined_rank == max(r.combined_rank for r in ranked)
    # the steady low-vol / positive-momentum names occupy the BUY quintile
    buys = [r.ticker for r in ranked if r.verdict == "buy"]
    assert "NVO" not in buys


def test_uptrending_low_vol_name_ranks_top():
    universe = [
        _row("WINNER", vol=0.14, payout=0.03, mom=+0.30),   # best on all three
        _row("MID",    vol=0.25, payout=0.02, mom=+0.05),
        _row("LOSER",  vol=0.50, payout=0.005, mom=-0.30),
    ]
    ranked = rank_universe(universe, SPECS, cut="top_k", k=1)
    assert ranked[0].ticker == "WINNER" and ranked[0].verdict == "buy"
    assert ranked[-1].ticker == "LOSER"
