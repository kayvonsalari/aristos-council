"""Hold, compare, and test against luck. Pure over prepared round inputs; no data access.

For one form and one hold: each round's basket (the form's names that have a price at entry and exit) is held equal-weight,
pays the round-trip cost once, and is turned into a yearly rate. The excess is that rate minus the yearly rate of the
comparison over the same window: the equal-weight basket of the industry (pays the same round-trip cost, once per hold, since
ABSOLUTE-BACKTEST-CORRECTED; before that it paid none and every excess was 0.5 points a year too low on a 12-month hold), the
index fund, cash. A round with
fewer than ``MIN_HELD`` priced names holds no position and is left out of every average.

Excess is averaged by calendar year of entry and then across years, as the existing backtest does. The luck test draws 500
random baskets of the same size, from the same priced pool, that turn over as often as the form's own basket does
(``backtest._TurnoverBaskets``); the luck score is the share of them whose mean yearly excess is at least the form's. Each
calendar year's random series are re-centred on zero (a random basket earns the pool's return minus the cost, so its excess over
a basket that pays the same cost is nil on average); the persistent baskets sat 0.3 to 0.4 points below that, which flattered
the forms. Their spread is kept.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np

from ..backtest import (INSUFFICIENT_BELOW_ROUNDS, INSUFFICIENT_BELOW_YEARS, PROOF_TOLERANCE, _TurnoverBaskets,
                        _series_rest_mean_dropping_best, _year_group_means)
from . import MIN_HELD, PASS_MAX_LUCK, PASS_MIN_EXCESS, THIN_BELOW
from .market import annualise

RANDOM_BASKETS = 500


@dataclass(frozen=True)
class RoundInput:
    """What one round offers a form: the pool's total returns over the hold, the form's chosen names, the comparisons."""
    date: date
    exit_date: date
    pool_returns: dict                  # {ticker: total return over the hold} for every eligible priced name
    selected: tuple = ()                # the form's names this round (before pricing)
    index_return: Optional[float] = None
    cash_return: Optional[float] = None


@dataclass(frozen=True)
class RoundScore:
    date: date
    exit_date: date
    n_pool: int
    n_selected: int
    n_held: int
    tickers: tuple
    n_new: int
    form_ann: Optional[float] = None    # net of cost, yearly
    pool_ann: Optional[float] = None
    index_ann: Optional[float] = None
    cash_ann: Optional[float] = None
    ex_pool: Optional[float] = None
    ex_index: Optional[float] = None
    ex_cash: Optional[float] = None
    drop_best: Optional[float] = None   # excess over the pool with the best name dropped (4 or more names)

    @property
    def positioned(self) -> bool:
        return self.ex_pool is not None


def _diff(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or b is None else a - b


def score_rounds(inputs, hold_months: int, cost: float) -> list[RoundScore]:
    years = hold_months / 12.0
    out: list[RoundScore] = []
    prev: Optional[set] = None
    for ri in inputs:
        held = [(t, ri.pool_returns[t]) for t in ri.selected if t in ri.pool_returns]
        names = {t for t, _ in held}
        n_new = len(names) if prev is None else len(names - prev)
        prev = names
        rets = list(ri.pool_returns.values())
        pool_ann = annualise(sum(rets) / len(rets) - cost, years) if rets else None      # the basket pays the cost too
        index_ann, cash_ann = annualise(ri.index_return, years), annualise(ri.cash_return, years)
        if len(held) >= MIN_HELD and pool_ann is not None:
            net = sum(v for _, v in held) / len(held) - cost
            form_ann = annualise(net, years)
            drop = None
            if len(held) >= THIN_BELOW:
                best = max(held, key=lambda kv: kv[1])[0]
                rest = [v for t, v in held if t != best]
                drop = annualise(sum(rest) / len(rest) - cost, years) - pool_ann
            out.append(RoundScore(ri.date, ri.exit_date, len(rets), len(ri.selected), len(held),
                                  tuple(sorted(names)), n_new, form_ann, pool_ann, index_ann, cash_ann,
                                  form_ann - pool_ann, _diff(form_ann, index_ann), _diff(form_ann, cash_ann), drop))
        else:
            out.append(RoundScore(ri.date, ri.exit_date, len(rets), len(ri.selected), len(held),
                                  tuple(sorted(names)), n_new, None, pool_ann, index_ann, cash_ann))
    return out


def year_means(scores, attr: str) -> dict:
    """{calendar year of entry: mean of ``attr`` over that year's rounds where it is not None}."""
    by_year: dict[int, list] = {}
    for s in scores:
        v = getattr(s, attr)
        if v is not None:
            by_year.setdefault(s.date.year, []).append(v)
    return {y: sum(v) / len(v) for y, v in sorted(by_year.items())}


def _mean(d: dict) -> Optional[float]:
    return (sum(d.values()) / len(d)) if d else None


@dataclass
class FormMetrics:
    n_rounds: int = 0
    n_positions: int = 0
    years_measured: int = 0
    years_positive: int = 0
    mean_form_return: Optional[float] = None
    mean_pool_return: Optional[float] = None
    ex_pool: Optional[float] = None
    ex_index: Optional[float] = None
    ex_cash: Optional[float] = None
    drop_best: Optional[float] = None
    share_thin: Optional[float] = None          # rounds (all scorable) with fewer than 4 names held
    median_held: Optional[float] = None
    year_ex_pool: dict = field(default_factory=dict)
    year_ex_index: dict = field(default_factory=dict)
    year_ex_cash: dict = field(default_factory=dict)
    year_drop_best: dict = field(default_factory=dict)
    luck: Optional[float] = None
    drop_best_vs_random: Optional[float] = None


def metrics_for(scores) -> FormMetrics:
    held = [s for s in scores if s.positioned]
    yp = year_means(scores, "ex_pool")
    m = FormMetrics(
        n_rounds=len(scores), n_positions=len(held), years_measured=len(yp),
        years_positive=sum(1 for v in yp.values() if v > 0),
        mean_form_return=_mean(year_means(scores, "form_ann")), mean_pool_return=_mean(year_means(held, "pool_ann")),
        ex_pool=_mean(yp), ex_index=_mean(year_means(scores, "ex_index")), ex_cash=_mean(year_means(scores, "ex_cash")),
        drop_best=_mean(year_means(scores, "drop_best")),
        share_thin=(sum(1 for s in scores if s.n_held < THIN_BELOW) / len(scores)) if scores else None,
        median_held=float(np.median([s.n_held for s in scores])) if scores else None,
        year_ex_pool=yp, year_ex_index=year_means(scores, "ex_index"), year_ex_cash=year_means(scores, "ex_cash"),
        year_drop_best=year_means(scores, "drop_best"))
    return m


@dataclass
class LuckResult:
    luck: Optional[float]
    drop_best_vs_random: Optional[float]
    years: tuple = ()                   # the entry years of the matrices below
    random_year_means: Optional[np.ndarray] = None      # (n_years, RANDOM_BASKETS) mean yearly excess of each random series


def luck_test(inputs, scores, hold_months: int, cost: float, *, industry_slug: str, label: str,
              metrics: FormMetrics, n_baskets: int = RANDOM_BASKETS) -> LuckResult:
    """500 random baskets of the same size and turnover from the same priced pool (``label`` fixes the seed)."""
    years = hold_months / 12.0
    tb = _TurnoverBaskets(industry_slug, label, n_baskets)
    pos_dates, cols, db_dates, db_cols = [], [], [], []
    for ri, s in zip(inputs, scores):
        if not s.positioned:
            continue
        pool = sorted(ri.pool_returns)
        sets = tb.draw(pool, s.n_held, s.n_new)
        means = np.array([sum(ri.pool_returns[t] for t in chosen) / len(chosen) for chosen in sets])
        nets = means - cost
        ann = np.where(nets <= -1.0, -1.0, (1.0 + np.maximum(nets, -1.0)) ** (1.0 / years) - 1.0)
        cols.append(ann - s.pool_ann)
        pos_dates.append(s.date)
        if s.n_held >= THIN_BELOW:
            rest = np.array([_series_rest_mean_dropping_best([(t, ri.pool_returns[t]) for t in sorted(chosen)])
                             for chosen in sets])
            rn = rest - cost
            rann = np.where(rn <= -1.0, -1.0, (1.0 + np.maximum(rn, -1.0)) ** (1.0 / years) - 1.0)
            db_cols.append(rann - s.pool_ann)
            db_dates.append(s.date)
    if not cols or metrics.ex_pool is None:
        return LuckResult(None, None)
    yearly = _year_group_means(np.column_stack(cols), pos_dates)          # (n_years, N)
    shift = yearly.mean(axis=1, keepdims=True)                            # how far the random baskets sit from the pool
    yearly = yearly - shift                                               # re-centred on the pool, spread kept
    shift_by_year = dict(zip(sorted({d.year for d in pos_dates}), shift[:, 0]))
    series_mean = yearly.mean(axis=0)
    luck = float(np.mean(series_mean >= metrics.ex_pool - PROOF_TOLERANCE))
    db_vs = None
    if db_cols and metrics.drop_best is not None:
        db_year = _year_group_means(np.column_stack(db_cols), db_dates)
        db_year = db_year - np.array([[shift_by_year[y]] for y in sorted({d.year for d in db_dates})])   # the same shift
        db_vs = float(metrics.drop_best - np.median(db_year.mean(axis=0)))
    return LuckResult(luck, db_vs, tuple(sorted({d.year for d in pos_dates})), yearly.astype(np.float32))


def bar_verdict(m: FormMetrics, luck: Optional[float]) -> str:
    """The pass bar of decision 13, in words that never claim proof."""
    if m.years_measured < INSUFFICIENT_BELOW_YEARS or m.n_positions < INSUFFICIENT_BELOW_ROUNDS:
        return "insufficient"
    if m.ex_pool is None:
        return "insufficient"
    if m.ex_pool >= PASS_MIN_EXCESS - PROOF_TOLERANCE and luck is not None and luck < PASS_MAX_LUCK:
        return "passes the bar"
    return "does not pass the bar"
