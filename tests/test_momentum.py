"""Price momentum (value+momentum) — total_return primitive, the momentum criterion,
and its SIGNED matrix contribution that can pull a cheap-but-falling name out of BUY.
Deterministic: no network/LLM."""

from __future__ import annotations

from pathlib import Path

from aristos_council.agents.matrix import screen_only_matrix
from aristos_council.state import Recommendation
from aristos_council.strategy.loader import load_strategy
from aristos_council.tools.criteria.registry import (
    PRICE_MOMENTUM_CRITERION,
    REGISTRY,
    Evidence,
)
from aristos_council.tools.technical import _TD_12M, technical_snapshot, total_return

GROWTH = load_strategy(
    Path(__file__).resolve().parents[1] / "strategies" / "growth_v1.yaml")


# --------------------------------------------------------------------------- #
# BUILD 1: total_return + snapshot fields
# --------------------------------------------------------------------------- #
def test_total_return_value_and_short_history():
    # 11 closes; a 10-day total return of 100 -> 150 is +50%.
    closes = [100.0, 110, 120, 130, 140, 145, 150, 148, 152, 149, 150.0]
    assert abs(total_return(closes, 10) - 0.5) < 1e-9
    assert total_return(closes, 10000) is None           # too short
    assert total_return([100.0], 1) is None
    assert total_return([0.0, 50.0], 1) is None           # non-positive start


def test_snapshot_populates_returns_when_history_is_long_enough():
    closes = [100.0 + i for i in range(_TD_12M + 5)]       # rising
    snap = technical_snapshot(closes)
    assert snap.return_12m is not None and snap.return_12m > 0
    short = technical_snapshot([100.0, 101.0, 102.0])
    assert short.return_12m is None                        # honest abstain


# --------------------------------------------------------------------------- #
# BUILD 2: the momentum criterion
# --------------------------------------------------------------------------- #
def _momentum(return_12m, floor=0.0):
    return REGISTRY[PRICE_MOMENTUM_CRITERION].fn(
        Evidence(return_12m=return_12m), floor)


def test_momentum_criterion_fails_on_drawdown_passes_on_uptrend():
    drop = _momentum(-0.40)                                # NVO-style 40% drawdown
    assert drop.passed is False and abs(drop.observed + 0.40) < 1e-9
    up = _momentum(0.25)                                   # LLY-style +25%
    assert up.passed is True and abs(up.observed - 0.25) < 1e-9


def test_momentum_criterion_not_eval_on_missing_history():
    r = _momentum(None)
    assert r.passed is None and r.observed is None
    assert "insufficient price history" in r.note


# --------------------------------------------------------------------------- #
# BUILD 3: signed matrix contribution
# --------------------------------------------------------------------------- #
def _screen(momentum_observed):
    # A moderately strong name whose 4-criterion base score sits just above BUY_TH,
    # so the momentum term decides BUY vs HOLD.
    crits = [
        {"name": "min_revenue_cagr", "passed": True, "observed": 0.12, "threshold": 0.10},
        {"name": "min_roic", "passed": True, "observed": 0.13, "threshold": 0.12},
        {"name": "max_peg_ratio", "passed": True, "observed": 1.5, "threshold": 2.0},
        {"name": "min_market_cap", "passed": True, "observed": 1e11, "threshold": 5e9},
        {"name": PRICE_MOMENTUM_CRITERION,
         "passed": None if momentum_observed is None else momentum_observed >= 0,
         "observed": momentum_observed, "threshold": 0.0},
    ]
    return {"criteria": crits, "flags": []}


def _momentum_pts(m):
    return next(c.points for c in m.contributions if c.name == PRICE_MOMENTUM_CRITERION)


def test_negative_momentum_subtracts_and_flips_buy_to_hold():
    rising = screen_only_matrix(_screen(+0.20), GROWTH)
    falling = screen_only_matrix(_screen(-0.40), GROWTH)
    # same fundamentals, only the price trend flips -> BUY becomes HOLD (the NVO fix)
    assert rising.verdict == Recommendation.BUY
    assert falling.verdict == Recommendation.HOLD
    assert falling.score < rising.score
    assert _momentum_pts(rising) > 0 and _momentum_pts(falling) < 0
    # -0.40 x w20 = -8.0 (within the +/-0.5 cap)
    assert abs(_momentum_pts(falling) - (-8.0)) < 1e-9


def test_momentum_cap_limits_extreme_returns():
    # a -90% return is clamped to the -0.5 cap before scaling -> -10, not -18.
    m = screen_only_matrix(_screen(-0.90), GROWTH)
    assert abs(_momentum_pts(m) - (-0.5 * GROWTH.scoring.momentum_weight)) < 1e-9


def test_not_eval_momentum_contributes_zero():
    m = screen_only_matrix(_screen(None), GROWTH)
    assert _momentum_pts(m) == 0.0                         # no spurious penalty
    # equals the score of the same screen without a momentum row
    base = screen_only_matrix(
        {"criteria": _screen(None)["criteria"][:-1], "flags": []}, GROWTH)
    assert abs(m.score - base.score) < 1e-9
