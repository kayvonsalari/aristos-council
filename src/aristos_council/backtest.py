"""Backtest engine — what turns "plausible" into "validated" (Aristos v2 Phase 3).

Replicates how the published studies validate (Schwartz & Hanauer 2024,
Greenblatt, van Vliet-Blitz): at each rebalance date, compute factor values AS OF
that date (point-in-time, NO look-ahead), rank the universe, form an EQUAL-WEIGHTED
top portfolio, hold to the next rebalance, and record the forward return. Report
annualized return vs a benchmark, Sharpe, max drawdown, hit-rate, and the per-period
holdings.

HONESTY (enforced + reported):
- NO LOOK-AHEAD: factor selection at date d may read only closes dated <= d. This is
  structural — the engine hands the factor function ``series.closes_up_to(d)``; it
  never sees a future price. Forward return uses d -> d_next (the realised held-period
  outcome), which is the measurement, not a selection input.
- FREE-DATA LIMIT: historical PRICES are available point-in-time, but historical
  POINT-IN-TIME FUNDAMENTALS are not (free sources show current/restated values). So
  only PRICE-DERIVED factors (momentum, low-vol) are honestly backtestable on free
  data; fundamental-rank strategies (Magic Formula) need a point-in-time fundamentals
  feed. The engine takes whatever factor function it's given; the CLI passes only the
  price sleeve and FLAGS the dropped fundamental factors. Survivorship bias is NOT
  corrected here (the universe is fixed) — reported as a caveat with the numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from .rank_engine import FactorSpec, rank_universe

# Factor function: given the closes UP TO the rebalance date, return {factor: value}.
FactorFn = Callable[[list[float]], dict[str, Optional[float]]]


class PriceSeries:
    """A ticker's (date, close) history. All point-in-time accessors are <= a date,
    so look-ahead is impossible by construction."""

    def __init__(self, points: list[tuple[date, float]]):
        self._pts = sorted(points, key=lambda dc: dc[0])

    def closes_up_to(self, d: date) -> list[float]:
        return [c for (dt, c) in self._pts if dt <= d]

    def close_on_or_before(self, d: date) -> Optional[float]:
        last = None
        for dt, c in self._pts:
            if dt <= d:
                last = c
            else:
                break
        return last


@dataclass
class PeriodResult:
    rebalance_date: date
    next_date: date
    holdings: list[str]
    portfolio_return: float
    benchmark_return: float


@dataclass
class BacktestResult:
    periods: list[PeriodResult]
    annualized_return: float
    annualized_benchmark: float
    sharpe: float
    max_drawdown: float
    hit_rate: float                      # fraction of periods beating the benchmark
    n_periods: int
    caveats: list[str] = field(default_factory=list)


def rebalance_dates(start: date, end: date, period_days: int) -> list[date]:
    out, d = [], start
    while d <= end:
        out.append(d)
        d = d + timedelta(days=period_days)
    return out


def _forward_return(s: PriceSeries, d0: date, d1: date) -> Optional[float]:
    p0 = s.close_on_or_before(d0)
    p1 = s.close_on_or_before(d1)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return p1 / p0 - 1.0


def _annualize(equity: float, years: float) -> float:
    if years <= 0 or equity <= 0:
        return 0.0
    return equity ** (1.0 / years) - 1.0


def _max_drawdown(equity_curve: list[float]) -> float:
    peak, mdd = equity_curve[0] if equity_curve else 1.0, 0.0
    for v in equity_curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return mdd


def run_backtest(
    universe: list[str],
    price_data: dict[str, list[tuple[date, float]]],
    *,
    dates: list[date],
    factor_fn: FactorFn,
    rank_specs: list[FactorSpec],
    cut: str = "top_k",
    top_k: int = 5,
    percentile: float = 0.2,
    missing: str = "exclude",
    extra_caveats: Optional[list[str]] = None,
) -> BacktestResult:
    """Run the point-in-time backtest. ``dates`` are the rebalance dates (last one is
    only an exit marker). ``factor_fn`` receives the closes UP TO each rebalance date."""
    series = {t: PriceSeries(price_data[t]) for t in universe if t in price_data}
    periods: list[PeriodResult] = []

    for d, d_next in zip(dates[:-1], dates[1:]):
        rows = []
        for t in universe:
            s = series.get(t)
            if s is None:
                continue
            rows.append((t, factor_fn(s.closes_up_to(d))))   # <= d ONLY (no look-ahead)
        ranked = rank_universe(rows, rank_specs, cut=cut, k=top_k,
                               percentile=percentile, missing=missing)
        holdings = [r.ticker for r in ranked
                    if r.verdict == "buy" and not r.excluded]

        port = [r for r in (_forward_return(series[t], d, d_next) for t in holdings)
                if r is not None]
        bench = [r for r in (_forward_return(series[t], d, d_next)
                             for t in universe if t in series) if r is not None]
        periods.append(PeriodResult(
            rebalance_date=d, next_date=d_next, holdings=holdings,
            portfolio_return=(sum(port) / len(port)) if port else 0.0,
            benchmark_return=(sum(bench) / len(bench)) if bench else 0.0))

    return _summarize(periods, dates, extra_caveats or [])


def _summarize(periods, dates, extra_caveats) -> BacktestResult:
    n = len(periods)
    if n == 0:
        return BacktestResult([], 0.0, 0.0, 0.0, 0.0, 0.0, 0, extra_caveats)

    eq, beq = 1.0, 1.0
    curve = [1.0]
    rets = []
    wins = 0
    for p in periods:
        eq *= (1.0 + p.portfolio_return)
        beq *= (1.0 + p.benchmark_return)
        curve.append(eq)
        rets.append(p.portfolio_return)
        if p.portfolio_return > p.benchmark_return:
            wins += 1

    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)
    periods_per_year = n / years
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / n
    std = var ** 0.5
    sharpe = (mean / std * (periods_per_year ** 0.5)) if std > 0 else 0.0

    caveats = list(extra_caveats)
    caveats.append("Survivorship bias is NOT corrected (fixed universe) — live "
                   "results would differ for names delisted over the period.")
    return BacktestResult(
        periods=periods,
        annualized_return=_annualize(eq, years),
        annualized_benchmark=_annualize(beq, years),
        sharpe=sharpe,
        max_drawdown=_max_drawdown(curve),
        hit_rate=wins / n,
        n_periods=n,
        caveats=caveats,
    )
