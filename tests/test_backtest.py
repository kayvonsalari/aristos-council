"""Backtest engine (Aristos v2 Phase 3) — point-in-time, no look-ahead, correct
portfolio returns. Deterministic synthetic price history; no network/LLM."""

from __future__ import annotations

from datetime import date

from aristos_council.backtest import (
    PriceSeries,
    rebalance_dates,
    run_backtest,
)
from aristos_council.rank_engine import FactorSpec

# Two names: A trends up then dips; B is sleepy. Yearly anchor closes.
_PRICES = {
    "A": [(date(2020, 1, 1), 100.0), (date(2021, 1, 1), 120.0),
          (date(2022, 1, 1), 150.0), (date(2023, 1, 1), 140.0)],
    "B": [(date(2020, 1, 1), 100.0), (date(2021, 1, 1), 105.0),
          (date(2022, 1, 1), 103.0), (date(2023, 1, 1), 110.0)],
}
_DATES = [date(2021, 1, 1), date(2022, 1, 1), date(2023, 1, 1)]


def _window_return(closes):
    # A factor computable from few closes (real momentum needs ~252) — return over
    # the available window; higher is better.
    if len(closes) < 2:
        return {"mom": None}
    return {"mom": closes[-1] / closes[0] - 1.0}


# --------------------------------------------------------------------------- #
# Point-in-time data layer
# --------------------------------------------------------------------------- #
def test_price_series_is_point_in_time():
    s = PriceSeries(_PRICES["A"])
    assert s.closes_up_to(date(2021, 1, 1)) == [100.0, 120.0]   # no future closes
    assert s.close_on_or_before(date(2021, 6, 1)) == 120.0      # last <= date
    assert s.close_on_or_before(date(2019, 1, 1)) is None


def test_rebalance_dates_spacing():
    ds = rebalance_dates(date(2021, 1, 1), date(2023, 1, 1), 365)
    assert ds[0] == date(2021, 1, 1)
    assert all((b - a).days == 365 for a, b in zip(ds, ds[1:]))


# --------------------------------------------------------------------------- #
# No look-ahead + correct returns
# --------------------------------------------------------------------------- #
def test_factor_selection_never_sees_future_closes():
    calls = []

    def spy(closes):
        calls.append(list(closes))
        return _window_return(closes)

    run_backtest(["A", "B"], _PRICES, dates=_DATES, factor_fn=spy,
                 rank_specs=[FactorSpec("mom", "high")], cut="top_k", top_k=1)
    # First rebalance (2021-01-01): A's closes are ONLY [100, 120] — the 2022/2023
    # prices are NOT visible at selection time. (calls order = A,B per period.)
    assert calls[0] == [100.0, 120.0]
    assert all(len(c) <= 3 for c in calls)            # never the full 4-point series


def test_portfolio_returns_match_hand_computation():
    res = run_backtest(["A", "B"], _PRICES, dates=_DATES, factor_fn=_window_return,
                       rank_specs=[FactorSpec("mom", "high")], cut="top_k", top_k=1)
    assert res.n_periods == 2
    p1, p2 = res.periods
    # Period 1: A wins (mom 0.20 > 0.05) -> hold A -> 150/120-1 = +0.25
    assert p1.holdings == ["A"]
    assert abs(p1.portfolio_return - 0.25) < 1e-9
    # benchmark = equal-weight both: (0.25 + (103/105-1)) / 2
    assert abs(p1.benchmark_return - ((0.25 + (103 / 105 - 1)) / 2)) < 1e-9
    # Period 2: still A -> 140/150-1 = -0.0666...
    assert p2.holdings == ["A"]
    assert abs(p2.portfolio_return - (140 / 150 - 1)) < 1e-9
    assert res.hit_rate == 0.5                         # wins period 1, loses period 2


def test_metrics_and_determinism():
    a = run_backtest(["A", "B"], _PRICES, dates=_DATES, factor_fn=_window_return,
                     rank_specs=[FactorSpec("mom", "high")], cut="top_k", top_k=1)
    b = run_backtest(["A", "B"], _PRICES, dates=_DATES, factor_fn=_window_return,
                     rank_specs=[FactorSpec("mom", "high")], cut="top_k", top_k=1)
    # deterministic
    assert ([p.portfolio_return for p in a.periods]
            == [p.portfolio_return for p in b.periods])
    # equity 1.25 * (140/150) over ~2 years -> ~+8%/yr annualized
    assert 0.05 < a.annualized_return < 0.11
    assert a.max_drawdown < 0.0                        # the period-2 dip shows up
    # survivorship caveat is always reported
    assert any("Survivorship" in c for c in a.caveats)
