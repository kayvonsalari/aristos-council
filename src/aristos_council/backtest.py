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

BACKTEST-1 (below the price-only engine) lifts the first limit for the LENS backtest: the real lens
runs at each month end on the accounts as they stood then (data/asof_adapter.py), at the cost of two
stated limits of its own - restated accounts and survivorship. See docs/BACKTEST.md.
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
class PriceBacktestResult:
    """The PRICE-ONLY engine's result (``run_backtest``). The lens backtest's ``BacktestResult`` is
    further down; this one kept its shape and lost the name (BACKTEST-1)."""

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
) -> PriceBacktestResult:
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


def _summarize(periods, dates, extra_caveats) -> PriceBacktestResult:
    n = len(periods)
    if n == 0:
        return PriceBacktestResult([], 0.0, 0.0, 0.0, 0.0, 0.0, 0, extra_caveats)

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
    return PriceBacktestResult(
        periods=periods,
        annualized_return=_annualize(eq, years),
        annualized_benchmark=_annualize(beq, years),
        sharpe=sharpe,
        max_drawdown=_max_drawdown(curve),
        hit_rate=wins / n,
        n_periods=n,
        caveats=caveats,
    )


# =========================================================================== #
# LENS BACKTEST (BACKTEST-1) - does a lens's BUY basket beat its own cohort?
# =========================================================================== #
# Everything above is the PRICE-ONLY engine and keeps its honesty header. What follows runs the REAL
# lens (``run_rank_pipeline``, ranker-only, no LLM anywhere) at each month end on the accounts as they
# stood then (``AsOfAdapter``) and measures what the BUY names went on to do.
#
# THE TWO HONESTY LIMITS, stamped on every result and CSV:
#   1. RESTATED ACCOUNTS. The statements are what EODHD carries TODAY (restated), dated by fiscal-period
#      end plus a filing lag. A reader on the day saw the ORIGINAL figures.
#   2. SURVIVORSHIP. The members are today's frozen cohort; names that were delisted, merged or left the
#      cohort are absent, and the benchmark shares that bias.
# Both flatter a lens; neither can be corrected from this data, so both are stated, never hidden.
import bisect
import calendar
import csv
import hashlib
import random
import subprocess
import sys
from pathlib import Path

import numpy as np

MIN_BUYS = 3                      # fewer BUY names than this is "no position", not a 1-stock bet
PRICE_TOLERANCE_DAYS = 10         # a close older than this (before a date) is "no price", not stale

# SIZE-FLOOR-1 — a lower min-cap floor increases survivorship bias more than the as-of scaling
# alone corrects for (a micro-cap that happened to survive AND grow into the cohort by today is
# exactly the kind of name a point-in-time feed cannot see fail), so below the lens's own $5bn
# floor a LIQUIDITY guard also applies: a name needs at least this much in mean daily $ value
# traded over the trailing ~30 calendar days, estimated AS OF the round (item 1c). Never applied
# at or above the $5bn default floor, where it would be redundant (nothing that illiquid clears
# $5bn anyway) and the existing, already-validated backtests must stay byte-identical.
MIN_ADV_USD_DEFAULT = 3_000_000.0
ADV_WINDOW_DAYS = 30

# OWNER'S RULING, 2026-09-26 - the pass bar. A lens is "proven" on a cohort when, over the measured
# calendar years, its BUY basket beat the cohort's equal-weight benchmark by at least 2% a year on
# average AND in at least 6 of 10 years (proportionally, if fewer than 10 years were measured). With
# fewer than 6 measured years, or fewer than 60 rounds holding a position, the run says
# "insufficient" - it does not guess. Change these only by a dated ruling of the owner.
PROOF_MIN_EXCESS = 0.02
PROOF_MIN_YEARS = 6
PROOF_OF_YEARS = 10
INSUFFICIENT_BELOW_YEARS = 6
INSUFFICIENT_BELOW_ROUNDS = 60
# A mean of exactly the bar (e.g. ten years at precisely 0.02) can land a float epsilon under it
# (0.019999999999999997) — the bar is inclusive by ruling, so the comparison tolerates that noise
# without softening the bar itself. The year test is an integer ratio, so it needs no tolerance.
PROOF_TOLERANCE = 1e-9

# BACKTEST-1C — skill versus luck. With 65 tests against a modest bar (2%/yr, 6 of 10 years),
# some passes are expected by chance alone; this asks not just "did the lens beat the bar" but
# "would a random stock-picker, drawing the same size basket from the same eligible names the
# lens actually had to choose from, have beaten it about as often." See docs/BACKTEST.md.
RANDOM_BASKETS_DEFAULT = 500       # random baskets drawn per round
MAX_LUCK = 0.05                    # "proven" needs luck_pct_mean <= this (5% of random pickers
                                   # as-good-or-better)
SEED_RULE = ("each round's random draw is seeded from a hash of cohort slug + lens id + round "
            "date, so a re-run draws the SAME baskets")

# BACKTEST-1D — a random basket redrawn from scratch every round has none of a real lens's
# STICKINESS (it holds most of the same names month to month, and its results come in streaks), so
# its ten-year average bunches far tighter around the mean than a real lens's does. Measured against
# that too-tight bunch, ANY persistent strategy reads as extreme luck in one direction or the other —
# the 1C watched-cohort run put 7 of 39 testable pairs at luck <= 2% and 14 at luck >= 95%, against
# an expected ~2 of 39 in each tail. "turnover" mode makes each random series persist and evolve with
# the SAME turnover the lens itself has, so the comparison is basket-composition-fair, not just
# basket-SIZE-fair. "independent" (the 1C behaviour) is kept available for comparison.
RANDOM_MODE_INDEPENDENT = "independent"
RANDOM_MODE_TURNOVER = "turnover"
RANDOM_MODE_DEFAULT = RANDOM_MODE_TURNOVER
RANDOM_MODES = (RANDOM_MODE_INDEPENDENT, RANDOM_MODE_TURNOVER)
TURNOVER_SEED_RULE = ("each random series is seeded from a hash of cohort slug + lens id + series "
                     "index, then persists and evolves round to round - not re-seeded per round")

# BACKTEST-1D — the calibration check. Bin luck_pct_mean into fifths of decreasing rarity; under a
# CALIBRATED (fair) test the bins hold 5% / 20% / 50% / 20% / 5% of the testable results. A tail
# holding much more than its expected share says the luck test itself is still not trustworthy.
CALIBRATION_BINS = ((0.0, 5.0), (5.0, 25.0), (25.0, 75.0), (75.0, 95.0), (95.0, 100.0))
CALIBRATION_LABELS = ("0-5%", "5-25%", "25-75%", "75-95%", "95-100%")
CALIBRATION_EXPECTED_SHARE = (0.05, 0.20, 0.50, 0.20, 0.05)
CALIBRATION_WARNING_FACTOR = 2.0   # a tail over ~2x its expected share triggers the warning

AS_OF_RULE = ("each round ranks on accounts whose fiscal period ended on or before the round date "
              "minus the filing lag, and on closes up to the round date; nothing later is read")
BENCHMARK_RULE = "equal-weight return of every ranked member of the cohort over the same window"
COST_RULE = "cost_bps deducted once per round trip from the BUY basket; the benchmark pays none"
RETURN_RULE = ("closes on or before the round date and the exit date; adjusted close, so "
               "dividends are reinvested (total return)")


@dataclass(frozen=True)
class Round:
    """One rebalance: rank at ``date``, hold to ``exit_date``. ``buy_return`` is net of cost; all
    three of buy_return / bench_return / excess are None when the round held no position."""
    date: date
    n_buys: int
    buy_return: Optional[float]
    bench_return: Optional[float]
    excess: Optional[float]
    tickers: tuple = ()
    exit_date: Optional[date] = None
    n_ranked: int = 0
    # BACKTEST-1B — the universe the lens was actually offered THIS round, after the as-of size
    # floor (below); n_ranked (>= 0, <= n_eligible) is how many of those the lens's own screen kept.
    n_eligible: int = 0
    # BACKTEST-1B — ``excess`` recomputed with the single best-returning BUY name removed (robustness
    # against "the lens is proven on one stock"). None when the round held no position or n_buys < 4
    # (see run_lens_backtest); NOT read by verdict().
    excess_drop_best: Optional[float] = None
    # BACKTEST-1C — the mean excess of this round's RANDOM_BASKETS_DEFAULT random baskets (same
    # size as this round's BUY basket, drawn from the same priced eligible names). None when the
    # round held no position (no random baskets are drawn for a no-position round).
    random_mean_excess: Optional[float] = None
    # BACKTEST-1D — names in the lens's BUY basket this round that were NOT in it last round (all
    # of them, on the very first round). Measured for EVERY round, positioned or not, off the
    # lens's own raw BUY list (not the priced subset) - a fact about the lens's turnover, not about
    # what could be scored. Drives the turnover-matched random baskets; not read by verdict().
    n_new: int = 0
    # SIZE-FLOOR-1 item 1c — how many names cleared the as-of size floor but were THEN excluded by
    # the liquidity guard this round (0 whenever the guard did not apply - the default $5bn+ floor,
    # or min_cap_usd not given). Reported, never silent: n_eligible already EXCLUDES these.
    n_illiquid: int = 0
    # SIZE-FLOOR-2 item 2d — share of n_eligible whose as-of Fundamentals carried ANY dated
    # accounts at all (non-empty period_ends — the opposite of AsOfAdapter._abstain's
    # identity-only shell). A data-coverage fact about the NAMES this round, independent of
    # which lens is running: 1.0 means every eligible name had SOMETHING to screen/rank on;
    # well below it is the as-of accounts gap the SIZE-FLOOR-1/2 bug reports found, made
    # visible per round rather than inferred from a lens's own n_ranked. None when there was
    # no cap data to determine eligibility at all (member_caps empty).
    accounts_coverage: Optional[float] = None

    @property
    def has_position(self) -> bool:
        return self.excess is not None


@dataclass(frozen=True)
class Summary:
    n_rounds: int
    n_positions: int
    n_no_position: int
    years_measured: int
    years_positive: int
    mean_annual_excess: Optional[float]
    hit_rate: Optional[float]
    worst_round_excess: Optional[float]
    worst_round_date: Optional[date]
    max_drawdown: Optional[float]
    caveats: tuple = ()
    # BACKTEST-1B — the same yearly averaging as mean_annual_excess, over excess_drop_best instead of
    # excess. Reading, not gating: verdict() never looks at this field.
    mean_annual_excess_drop_best: Optional[float] = None
    # BACKTEST-1C — copied from the BacktestResult (see there for what each means); kept here too
    # because Summary is the object verdict() reads. None when random_baskets=0 (opted out).
    luck_pct_mean: Optional[float] = None
    luck_pct_pass: Optional[float] = None
    drop_best_vs_random: Optional[float] = None


@dataclass
class BacktestResult:
    """A lens's backtest on a cohort. ``year_excess`` and ``summary`` are DERIVED from ``rounds`` on
    every access, so a result read back from CSV can never disagree with one just computed."""
    cohort: str
    lens_id: str
    start: date
    end: date
    hold_months: int = 12
    step_months: int = 1
    cost_bps: float = 50.0
    lag_days: int = 90
    rounds: list = field(default_factory=list)
    caveats: list = field(default_factory=list)
    lens_commit: str = ""
    cohort_version: Optional[int] = None
    n_members: int = 0
    # BACKTEST-1B — the as-of size floor applied this run (USD, or None when there was no
    # market-cap data to compute one) and one line per flagged price jump; both read by to_csv.
    size_floor: Optional[float] = None
    size_floor_note: str = ""
    price_warnings: list = field(default_factory=list)
    # SIZE-FLOOR-1 — a per-run override of the as-of size floor above ("what if it were $1bn"),
    # applied through the SAME as-of scaling (BACKTEST-1B) and through min_market_cap_override
    # (FLOOR-1/2) so the lens's own gate and screen agree with the floor this run actually used.
    # None -> the lens/cohort's own file-declared floor, exactly as before this item (default
    # $5bn runs are byte-identical). min_adv_usd is the item-1c liquidity guard's own threshold,
    # applied only when min_cap_usd is set AND below $5bn; total_illiquid sums every round's
    # n_illiquid, so "how many did the guard remove" is answered once, in the header.
    min_cap_usd: Optional[float] = None
    min_adv_usd: Optional[float] = None
    total_illiquid: int = 0
    # SIZE-FLOOR-2 item 2a — True when min_cap_usd was asked for BELOW the cohort's own
    # as-of membership floor (size_floor, above): never run (rounds stays empty), because
    # the cohort's own as-of eligibility — unchanged by this item, see min_cap_usd's own
    # docstring — would already have excluded every name a lower lens gate could admit.
    # "Below cohort floor" is a fact to report, not a result to compute.
    below_cohort_floor: bool = False
    # BACKTEST-1C — the random-basket luck baseline. These three cannot be recomputed from
    # ``rounds`` alone (only each round's MEAN random excess is persisted, not the underlying
    # draws), so they are set ONCE by run_lens_backtest and carried as plain fields, read straight
    # back by from_csv rather than re-derived.
    random_baskets: int = 0                    # 0 -> opted out; no luck fields below are set
    seed_rule: str = ""
    max_luck: float = MAX_LUCK
    # Share of the random_baskets random series whose OWN mean annual excess is >= this lens's.
    # Low is good: "luck 3%" means only 3 of 100 random pickers did as well or better.
    luck_pct_mean: Optional[float] = None
    # Share of the random series that would themselves clear the plain excess/years bar (the
    # cohort's OWN chance pass rate under the current bar) — feeds multiple_testing().
    luck_pct_pass: Optional[float] = None
    # This lens's mean_annual_excess_drop_best minus the MEDIAN of the random series' own
    # drop-best figure. Positive: the lens depends on its best pick LESS than random picking
    # does. None when there is no drop-best figure to compare (no round ever had 4+ BUYs).
    drop_best_vs_random: Optional[float] = None
    # BACKTEST-1D — "independent" (1C: redrawn from scratch every round) or "turnover" (default:
    # persists and evolves with the lens's own turnover). Stamped so a file can be read for what it
    # actually measured; an older (1C) file with no such line is "independent" on read (see
    # from_csv), never silently reinterpreted as the new default.
    random_mode: str = RANDOM_MODE_DEFAULT

    @property
    def cohort_slug(self) -> str:
        return cohort_slug(self.cohort)

    @property
    def year_excess(self) -> dict:
        """{calendar year of entry: mean excess of that year's positioned rounds}. Overlapping holds
        that start in one year are averaged, so a year counts once however many rounds it holds."""
        return _year_average(self.rounds, "excess")

    @property
    def summary(self) -> Summary:
        held = [r for r in self.rounds if r.has_position]
        years = self.year_excess
        drop_best_years = _year_average(self.rounds, "excess_drop_best")
        worst = min(held, key=lambda r: (r.excess, r.date)) if held else None
        return Summary(
            n_rounds=len(self.rounds), n_positions=len(held),
            n_no_position=len(self.rounds) - len(held),
            years_measured=len(years), years_positive=sum(1 for v in years.values() if v > 0),
            mean_annual_excess=(sum(years.values()) / len(years)) if years else None,
            hit_rate=(sum(1 for r in held if r.excess > 0) / len(held)) if held else None,
            worst_round_excess=worst.excess if worst else None,
            worst_round_date=worst.date if worst else None,
            max_drawdown=_buy_drawdown(self.rounds, self.hold_months, self.step_months),
            caveats=tuple(self.caveats),
            mean_annual_excess_drop_best=((sum(drop_best_years.values()) / len(drop_best_years))
                                          if drop_best_years else None),
            luck_pct_mean=self.luck_pct_mean, luck_pct_pass=self.luck_pct_pass,
            drop_best_vs_random=self.drop_best_vs_random)


def cohort_slug(name: str) -> str:
    """The directory name a cohort lives under - the same rule ``CohortDefinition.slug`` uses."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _year_average(rounds, field_name: str) -> dict:
    """{calendar year of entry: mean of ``field_name`` over that year's rounds where it is not
    None}. Shared by ``year_excess`` and the drop-best summary figure so the two can never diverge
    in how a year is averaged."""
    by_year: dict[int, list[float]] = {}
    for r in rounds:
        v = getattr(r, field_name)
        if v is not None:
            by_year.setdefault(r.date.year, []).append(v)
    return {y: sum(v) / len(v) for y, v in sorted(by_year.items())}


# --------------------------------------------------------------------------- #
# dates
# --------------------------------------------------------------------------- #
def month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, n: int) -> date:
    """The last day of the month ``n`` months after ``d``'s month."""
    index = d.year * 12 + (d.month - 1) + n
    return month_end(date(index // 12, index % 12 + 1, 1))


def round_dates(start: date, end: date, hold_months: int, step_months: int) -> list:
    """Month ends from ``start`` to ``end - hold_months``, ``step_months`` apart. A round whose exit
    would fall after ``end`` is not run - there is no realised outcome to measure yet."""
    if hold_months < 1 or step_months < 1:
        raise ValueError("hold_months and step_months must be at least 1")
    first = start if start == month_end(start) else month_end(start)
    out, d = [], first
    while add_months(d, hold_months) <= end:
        out.append(d)
        d = add_months(d, step_months)
    return out


# --------------------------------------------------------------------------- #
# returns
# --------------------------------------------------------------------------- #
class _Closes:
    """One ticker's (day, adjusted close) points with bisect lookups, plus (day, raw close x
    volume) for the SIZE-FLOOR-1 liquidity guard (item 1c)."""

    def __init__(self, bars) -> None:
        pts = sorted((b.day, b.adj_close) for b in bars if b.adj_close is not None)
        self._days = [d for d, _ in pts]
        self._vals = [v for _, v in pts]
        # $ value traded that day — RAW close (what actually changed hands), not the
        # dividend-adjusted one, x volume. Sorted once, same tolerance window as prices.
        traded = sorted((b.day, b.close * b.volume) for b in bars
                        if b.close is not None and b.volume is not None)
        self._adv_days = [d for d, _ in traded]
        self._adv_vals = [v for _, v in traded]

    def on_or_before(self, d: date) -> Optional[float]:
        """The close on ``d`` or the last one before it - None when there is none within the
        tolerance (a name that stopped trading is unpriced, not flat)."""
        i = bisect.bisect_right(self._days, d)
        if i == 0 or (d - self._days[i - 1]).days > PRICE_TOLERANCE_DAYS:
            return None
        return self._vals[i - 1]

    def latest(self) -> Optional[float]:
        """The most recent cached close - the "today" side of the as-of size-floor ratio
        (BACKTEST-1B): ``member_caps`` is a snapshot as of roughly now, so it is scaled against
        the price at roughly now, not against an arbitrary date."""
        return self._vals[-1] if self._vals else None

    def adv_usd(self, d: date, *, window_days: int = ADV_WINDOW_DAYS) -> Optional[float]:
        """Mean $ value traded over the ``window_days`` calendar days up to and including
        ``d`` (item 1c) — AS OF the round, never today's. ``None`` with no traded-value bars
        in the window (never a silent zero — a missing read is NOT a confirmed illiquid)."""
        lo = d - timedelta(days=window_days)
        i = bisect.bisect_right(self._adv_days, d)
        j = bisect.bisect_left(self._adv_days, lo)
        window = self._adv_vals[j:i]
        return (sum(window) / len(window)) if window else None


PRICE_JUMP_RATIO = 3.0             # a one-day adjusted-close move beyond this, either way, is flagged


def _price_jump_warnings(ticker: str, bars) -> list:
    """BACKTEST-1B price sanity: one bare string per adjacent-day adjusted-close ratio beyond
    ``PRICE_JUMP_RATIO`` (either way) in ``bars``, e.g. ``"TYT.LSE 2017-04-28 x9.9"``. A flag, never
    an exclusion — the series is still used; see docs/BACKTEST.md and data/size_corrections.yaml for
    names run down by hand."""
    pts = sorted((b.day, b.adj_close) for b in bars if b.adj_close is not None and b.adj_close > 0)
    out = []
    for (_, p0), (d1, p1) in zip(pts, pts[1:]):
        ratio = p1 / p0
        if ratio > PRICE_JUMP_RATIO or ratio < 1.0 / PRICE_JUMP_RATIO:
            out.append(f"{ticker} {d1.isoformat()} x{ratio:.3g}")
    return out


def _window_return(closes: Optional[_Closes], d0: date, d1: date) -> Optional[float]:
    if closes is None:
        return None
    p0, p1 = closes.on_or_before(d0), closes.on_or_before(d1)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return p1 / p0 - 1.0


def _buy_drawdown(rounds, hold_months: int, step_months: int) -> Optional[float]:
    """Worst peak-to-trough fall of the BUY basket's compounded NET returns, measured at round ends.

    Monthly rounds with 12-month holds overlap, so they cannot be compounded end to end; the rounds
    are split into the ``ceil(hold/step)`` non-overlapping chains (every k-th round) and the WORST
    chain's drawdown is reported. A no-position round contributes 0 (the money sat in cash)."""
    if not rounds:
        return None
    ordered = sorted(rounds, key=lambda r: r.date)
    k = max(1, -(-hold_months // step_months))
    worst = 0.0
    for offset in range(min(k, len(ordered))):
        curve = [1.0]
        for r in ordered[offset::k]:
            curve.append(curve[-1] * (1.0 + (r.buy_return if r.has_position else 0.0)))
        worst = min(worst, _max_drawdown(curve))
    return worst


def _passes_bar(s: Summary, min_excess: float, min_years: int, of_years: int) -> bool:
    """The plain excess/years bar, with no opinion about luck. Shared by ``verdict()`` and the
    random series' own pass/fail test (``luck_pct_pass``), so the two can never quietly diverge."""
    beats_by_enough = (s.mean_annual_excess is not None
                      and s.mean_annual_excess >= min_excess - PROOF_TOLERANCE)
    positive_enough = s.years_positive * of_years >= min_years * s.years_measured
    return beats_by_enough and positive_enough


def verdict(result: BacktestResult, min_excess: float = PROOF_MIN_EXCESS,
            min_years: int = PROOF_MIN_YEARS, of_years: int = PROOF_OF_YEARS,
            max_luck: float = MAX_LUCK) -> str:
    """"proven" | "not proven" | "not beyond luck" | "insufficient" - the owner's pass bar (see
    PROOF_* above), plus the BACKTEST-1C skill condition: even a lens that clears the excess/years
    bar is "not beyond luck" unless ``luck_pct_mean <= max_luck`` — at most ``max_luck`` of random
    stock-pickers, drawing the same-size basket from the same eligible names, would have done as
    well or better. Luck stats unmeasured (``luck_pct_mean is None`` — random_baskets=0) is treated
    the same as failing the luck test: never silently promoted to "proven" without the evidence."""
    s = result.summary
    if s.years_measured < INSUFFICIENT_BELOW_YEARS or s.n_positions < INSUFFICIENT_BELOW_ROUNDS:
        return "insufficient"
    if not _passes_bar(s, min_excess, min_years, of_years):
        return "not proven"
    if s.luck_pct_mean is None or s.luck_pct_mean > max_luck:
        return "not beyond luck"
    return "proven"


# --------------------------------------------------------------------------- #
# the cohort and the lens
# --------------------------------------------------------------------------- #
def load_cohort_members(cohort: str, cohorts_root=None) -> tuple:
    """``(yahoo tickers, version, market_caps)`` of the cohort's CURRENT frozen ``members.csv``.
    ``cohort`` is the display name or the slug. A cohort that is not built, or a name with no Yahoo
    symbol, is named - never skipped silently. ``market_caps`` is ``{yahoo ticker: market_cap_usd}``
    from the same file (BACKTEST-1B) - the frozen "today" snapshot the as-of size floor scales by
    price against; a member the index gave no USD figure to is simply absent from it."""
    from .cohorts.builder import DEFAULT_ROOT
    from .cohorts.freeze import MEMBERS_FILE, current_version, read_members, version_dir
    root = Path(cohorts_root) if cohorts_root else DEFAULT_ROOT
    slug = cohort_slug(cohort)
    version = current_version(root, slug)
    if version is None:
        raise FileNotFoundError(f"no frozen cohort {cohort!r} (slug {slug!r}) under {root} - build "
                                f"it first, or pass members=[...] explicitly")
    from .cohorts.freeze import _safe_yahoo
    tickers = []
    market_caps: dict[str, float] = {}
    for cand in read_members(version_dir(root, slug, version) / MEMBERS_FILE):
        symbol = _safe_yahoo(cand.ticker)
        if symbol:
            tickers.append(symbol)
            if cand.market_cap_usd is not None:
                market_caps[symbol] = cand.market_cap_usd
    return sorted(set(tickers)), version, market_caps


def _size_floor(cohort: str, cohorts_root, version: Optional[int],
                member_caps: dict) -> tuple:
    """``(floor USD or None, one-line note)`` for the as-of size floor (BACKTEST-1B): the cohort
    definition's own ``min_market_cap_usd`` when there is a built cohort to read it from, else the
    smallest ``market_cap_usd`` among today's members. Neither exists -> ``(None, "")`` and the floor
    is not applied that run (see run_lens_backtest)."""
    if version is not None:
        from .cohorts.builder import DEFAULT_ROOT
        from .cohorts.freeze import DEFINITION_FILE, version_dir
        root = Path(cohorts_root) if cohorts_root else DEFAULT_ROOT
        defn_path = version_dir(root, cohort_slug(cohort), version) / DEFINITION_FILE
        if defn_path.exists():
            import yaml
            try:
                doc = yaml.safe_load(defn_path.read_text(encoding="utf-8")) or {}
            except Exception:
                doc = {}
            raw = doc.get("min_market_cap_usd")
            if raw not in (None, ""):
                return float(raw), "the cohort definition's min_market_cap_usd"
    caps = [v for v in member_caps.values() if v is not None]
    if caps:
        return min(caps), "the smallest market_cap_usd among today's members (no floor on file)"
    return None, ""


def cohort_native_floor(cohort: str, cohorts_root=None) -> tuple:
    """``(floor USD or None, note)`` a DEFAULT run (``min_cap_usd=None``) actually applies for
    ``cohort`` — the exact value ``_size_floor`` would compute, without running a backtest.

    SIZE-FLOOR-1 bug report (2026-10-02), symptom 2: a grid row's ``min_cap_usd=5e9`` is a
    control that reproduces the committed/default run ONLY when this native floor happens to
    equal 5bn too — it is a COHORT-scoped tier value (COHORT-3's $1-10bn crowding rule), not
    the lens's own $5bn gate, and none of the five SIZE-FLOOR-1 grid cohorts carry one at
    exactly $5bn (Materials - Diversified Mining and Comms - Interactive Media & Gaming are
    both $2bn; Consumer - Auto Manufacturers and Industrials - Grid & Electrical Machinery are
    both $1bn; Tech - Semiconductors is $3bn). Surfaced so a reader comparing a grid row
    against ``SUMMARY.csv`` can see at a glance whether "$5bn" was actually a no-op there."""
    _, version, member_caps = load_cohort_members(cohort, cohorts_root)
    return _size_floor(cohort, cohorts_root, version, member_caps)


def lens_commit(lens_id: str, strategies_dir=None) -> str:
    """The git commit that last touched the lens YAML (and the screen lens it references), for the
    CSV header. "<hash>+uncommitted" when the working copy differs; "unknown" outside a checkout."""
    import yaml
    from .pipeline import _STRATEGIES_DIR
    base = Path(strategies_dir) if strategies_dir else _STRATEGIES_DIR
    files = [base / f"{lens_id}.yaml"]
    try:
        screen = (yaml.safe_load(files[0].read_text(encoding="utf-8")) or {}).get(
            "council_screen_strategy")
        if screen and (base / f"{screen}.yaml").exists():
            files.append(base / f"{screen}.yaml")
    except Exception:
        pass
    paths = [str(f) for f in files if f.exists()]
    if not paths:
        return "unknown"
    try:
        run = lambda *a: subprocess.run(["git", *a, "--", *paths], cwd=base, capture_output=True,
                                        text=True, timeout=30, check=True).stdout.strip()
        head = run("log", "-1", "--format=%h")
        if not head:
            return "unknown"
        return head + ("+uncommitted" if run("status", "--porcelain") else "")
    except Exception:
        return "unknown"


def _default_feed():
    """Accounts from EODHD (dated), prices and dividends from yfinance - retried, then day-cached
    so a re-run the same day costs nothing."""
    from .data.backtest_feed import BacktestFeed
    from .data.cache import DEFAULT_CACHE_DIR, CachingAdapter
    from .data.retry import RetryAdapter
    return CachingAdapter(RetryAdapter(BacktestFeed()), cache_dir=DEFAULT_CACHE_DIR,
                          today=date.today())


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
def run_lens_backtest(cohort: str, lens_id: str, *, start: date, end: date, hold_months: int = 12,
                      step_months: int = 1, cost_bps: float = 50, lag_days: int = 90,
                      adapter=None, progress=None, cohorts_root=None, members=None,
                      strategies_dir=None, member_caps=None,
                      random_baskets: int = RANDOM_BASKETS_DEFAULT,
                      max_luck: float = MAX_LUCK,
                      random_mode: str = RANDOM_MODE_DEFAULT,
                      min_cap_usd: Optional[float] = None,
                      min_adv_usd: float = MIN_ADV_USD_DEFAULT) -> BacktestResult:
    """Backtest one lens on one cohort. The first eight parameters are the contract; the rest of the
    keywords only say WHERE things live (a cohort directory, an explicit member list and its market
    caps for a machine with no built cohorts, a strategies directory) and change no rule.
    ``random_baskets`` (BACKTEST-1C, default 500; 0 opts out) and ``max_luck`` (default 0.05) are
    the luck baseline's own contract — see step 4 below and docs/BACKTEST.md "Skill versus luck".

    At each month end ``d`` from ``start`` to ``end - hold_months``:

    1. **As-of size floor (BACKTEST-1B).** Each member's market cap AT ``d`` is estimated as
       ``member_caps[ticker] x (adjusted close on d / the latest cached adjusted close)`` - scaling
       today's known cap by how much the price has moved since, so a member that was a micro-cap at
       ``d`` and is a giant today is not counted as if it always had today's size. A member is
       ELIGIBLE this round only if that estimate clears the cohort's USD floor (its definition's
       ``min_market_cap_usd``, else the smallest ``member_caps`` value) AND it has a price at ``d``.
       The SAME eligible set is what the lens ranks/can buy and what the benchmark is built from.
       When ``member_caps`` is empty (no cap data at all - the common case for an explicit
       ``members=`` list with no cohort behind it) this step is a no-op: every member is eligible,
       exactly as before BACKTEST-1B.
    2. The lens ranks the eligible set with ``run_rank_pipeline(..., ranker_only=True)`` behind an
       ``AsOfAdapter`` (accounts as of ``d`` less ``lag_days``).
    3. The BUY names are held equal-weight from ``d`` to ``d + hold_months`` less ``cost_bps`` once,
       and the excess is that minus the equal-weight return of every ranked (eligible, un-excluded)
       member. Fewer than MIN_BUYS priced BUY names -> "no position": counted, kept in the file,
       excluded from every average.
    4. **Random-basket luck baseline (BACKTEST-1C).** Every POSITIONED round also draws
       ``random_baskets`` random baskets, each the same size as that round's own BUY basket
       (``n_buys``), sampled without replacement from the SAME priced names the lens's benchmark is
       built from (the names it could actually have bought) — seeded from a hash of cohort + lens +
       round date, so a re-run draws the identical baskets. Scored exactly like the lens's own
       basket (same dates, same cost, same benchmark). After the loop, the ``random_baskets`` random
       "lenses" (basket i of every round) are each summarised the same way the real lens is, giving
       ``luck_pct_mean`` (share of them that beat this lens's own mean annual excess),
       ``luck_pct_pass`` (share that would themselves clear the plain bar - the cohort's own chance
       pass rate) and ``drop_best_vs_random`` (this lens's drop-best figure against the random
       series' median). ``verdict()`` reads ``luck_pct_mean``; ``0`` opts out (no luck fields set).
    5. **Turnover matching (BACKTEST-1D, ``random_mode``).** A random basket redrawn from scratch
       every round has none of a real lens's stickiness, so its multi-year average bunches too
       tightly around the mean and makes any persistent lens look extreme in the luck score, in
       either direction. ``"turnover"`` (the default) makes each of the ``random_baskets`` series a
       PERSISTENT basket: it keeps whatever it holds that is still eligible, then replaces exactly
       ``n_new`` names (the lens's OWN turnover that round, plus any of its own that fell out of
       eligibility) with fresh random draws, so its size still matches ``n_buys``. ``"independent"``
       is the 1C behaviour (redrawn from scratch every round) kept for comparison. Seeding changes
       accordingly: one seed per SERIES (persists across rounds), not per round.
    6. **Size-floor override + liquidity guard (SIZE-FLOOR-1), both off by default.**
       ``min_cap_usd`` REPLACES the as-of size floor in step 1 (instead of the cohort/lens file's
       own) — "what if the floor were $1bn" without touching a YAML — and is ALSO passed as
       ``min_market_cap_override`` to ``run_rank_pipeline``, so the lens's own ``min_market_cap``
       gate (and its screen's, if any — FLOOR-1/2) agrees with the floor this run actually used;
       without this second part the lens would re-exclude everything the loosened as-of floor just
       admitted, and the experiment would measure nothing. When (and only when) ``min_cap_usd`` is
       given AND below the $5bn default, a LIQUIDITY guard also applies: a name needs at least
       ``min_adv_usd`` (default $3m) in mean daily $ value traded (close x volume, from the same
       price feed) over the trailing 30 calendar days as of ``d`` — a lower cap floor alone
       increases survivorship bias more than the as-of scaling corrects for, and this is the
       second guard against it. ``Round.n_illiquid`` and ``BacktestResult.total_illiquid`` report
       exactly how many names it removed, every round and summed. ``min_cap_usd=None`` (the
       default) is a complete no-op: every existing backtest output is byte-identical.

    No LLM is called; ``adapter`` (default: EODHD accounts + yfinance prices) is the inner data
    source."""
    if random_mode not in RANDOM_MODES:
        raise ValueError(f"random_mode must be one of {RANDOM_MODES}, got {random_mode!r}")
    from .data.asof_adapter import AsOfAdapter
    from .data.backtest_feed import MemoAdapter, lookback_start
    from .pipeline import run_rank_pipeline

    version: Optional[int] = None
    if members is None:
        members, version, member_caps = load_cohort_members(cohort, cohorts_root)
    members = list(members)
    member_caps = dict(member_caps) if member_caps else {}
    # SIZE-FLOOR-2 item 2b — size_floor is ALWAYS the cohort's own as-of membership floor
    # now, never overridden: that is what the COMMITTED/default run (min_cap_usd=None) has
    # always applied in eligible_at, and it is what every named cohort's own definition.yaml
    # sets (a COHORT-3 size-TIER value — $1-10bn by industry crowding — which is NOT the
    # lens's own $5bn gate; see cohort_native_floor). SIZE-FLOOR-1 blended the two into one
    # number, which is exactly why its grid's $5bn row never reproduced SUMMARY.csv for any
    # of the five named cohorts (none carries a $5bn membership floor) and, per the
    # SIZE-FLOOR-2 bug report, is the most likely reason growth_garp_v2's screen read
    # universally blind on the grid path: eligibility was admitting a DIFFERENT population
    # (by floor value, hence by as-of accounts depth) than the lens's own file-declared gate
    # ever ranked under the committed/default run. min_cap_usd now moves ONLY the lens's own
    # gate (min_market_cap_override below) — membership eligibility is untouched by it.
    size_floor, size_floor_note = (_size_floor(cohort, cohorts_root, version, member_caps)
                                   if member_caps else (None, ""))
    # A lens gate BELOW the cohort's own membership floor can never admit a name membership
    # itself already excludes — not a stricter-or-looser experiment, just a no-op dressed up
    # as one. Reported, never silently run as if it measured something (item 2a).
    if min_cap_usd is not None and size_floor is not None and min_cap_usd < size_floor:
        result = BacktestResult(cohort=cohort, lens_id=lens_id, start=start, end=end,
                                hold_months=hold_months, step_months=step_months,
                                cost_bps=float(cost_bps), lag_days=lag_days,
                                cohort_version=version, n_members=len(members),
                                size_floor=size_floor, size_floor_note=size_floor_note,
                                lens_commit=lens_commit(lens_id, strategies_dir),
                                min_cap_usd=min_cap_usd, below_cohort_floor=True)
        result.caveats = [
            f"min_cap_usd (${min_cap_usd:,.0f}) is below this cohort's own as-of membership "
            f"floor (${size_floor:,.0f}, {size_floor_note}) — no round was run. A lens gate "
            "this low could never admit a name membership eligibility already excludes."]
        return result
    liquidity_guard_on = min_cap_usd is not None and min_cap_usd < 5e9
    dates = round_dates(start, end, hold_months, step_months)
    result = BacktestResult(cohort=cohort, lens_id=lens_id, start=start, end=end,
                            hold_months=hold_months, step_months=step_months,
                            cost_bps=float(cost_bps), lag_days=lag_days, cohort_version=version,
                            n_members=len(members), size_floor=size_floor,
                            size_floor_note=size_floor_note,
                            lens_commit=lens_commit(lens_id, strategies_dir),
                            random_baskets=max(0, int(random_baskets)),
                            seed_rule=((TURNOVER_SEED_RULE if random_mode == RANDOM_MODE_TURNOVER
                                       else SEED_RULE) if random_baskets > 0 else ""),
                            max_luck=float(max_luck), random_mode=random_mode,
                            min_cap_usd=min_cap_usd,
                            min_adv_usd=float(min_adv_usd) if liquidity_guard_on else None)
    if not dates:
        result.caveats = _caveats(result, 0)
        result.caveats.append(f"the window {start} to {end} is shorter than one {hold_months}-month "
                              f"hold, so no round could be measured")
        return result

    exit_of_last = add_months(dates[-1], hold_months)
    memo = MemoAdapter(adapter if adapter is not None else _default_feed(),
                       start=lookback_start(dates[0]), end=exit_of_last)
    closes: dict[str, Optional[_Closes]] = {}
    price_warnings: list = []

    def series(ticker: str) -> Optional[_Closes]:
        if ticker not in closes:
            try:
                bars = memo.get_price_history(ticker, start=lookback_start(dates[0]),
                                              end=exit_of_last).bars
                closes[ticker] = _Closes(bars)
                price_warnings.extend(_price_jump_warnings(ticker, bars))
            except Exception:
                closes[ticker] = None                    # no series -> unpriced, counted below
        return closes[ticker]

    def eligible_at(d: date) -> tuple:
        """``(eligible tickers, n_illiquid)`` for round ``d`` (step 1/6 above); ``members``
        unchanged (0 illiquid) when there is no cap data to apply a floor with. The liquidity
        guard (item 1c) is checked AFTER the size floor, so n_illiquid counts only names that
        cleared the cap but failed on traded value — a name cut by the cap alone is not
        double-counted as illiquid too."""
        if not member_caps:
            return members, 0
        out = []
        n_illiquid = 0
        for t in members:
            cap_today = member_caps.get(t)
            if cap_today is None:
                continue                                  # no cap snapshot -> can't confirm eligible
            cl = series(t)
            p_d = cl.on_or_before(d) if cl else None
            p_latest = cl.latest() if cl else None
            if p_d is None or not p_latest:
                continue                                  # no price on d, or ever -> excluded, as now
            estimate = cap_today * (p_d / p_latest)
            if size_floor is not None and estimate < size_floor:
                continue
            if liquidity_guard_on:
                adv = cl.adv_usd(d) if cl else None
                if adv is None or adv < min_adv_usd:
                    n_illiquid += 1
                    continue
            out.append(t)
        return out, n_illiquid

    cost = float(cost_bps) / 10_000.0
    random_baskets = max(0, int(random_baskets))
    unpriced = 0
    undersized_rounds = 0                                  # pool smaller than n_buys (rare)
    total_illiquid = 0                                     # SIZE-FLOOR-1 item 1c, summed below
    pos_dates: list = []
    pos_excess_cols: list = []                              # each shape (random_baskets,)
    dropbest_dates: list = []
    dropbest_cols: list = []                                # each shape (random_baskets,)
    previous_buys: Optional[set] = None                     # for n_new - tracked EVERY round
    turnover_baskets: Optional[_TurnoverBaskets] = None      # built lazily, on first use
    for i, d in enumerate(dates, 1):
        exit_date = add_months(d, hold_months)
        eligible, n_illiquid = eligible_at(d)
        total_illiquid += n_illiquid
        asof = AsOfAdapter(memo, d, lag_days)
        # SIZE-FLOOR-2 item 2d — a data-coverage fact about ELIGIBLE NAMES this round, not
        # about any one lens: non-empty period_ends means AsOfAdapter served real dated
        # accounts, not its identity-only abstain shell (_abstain, asof_adapter.py). Cheap —
        # MemoAdapter already has the raw fetch cached; this only re-runs the as-of cut.
        #
        # BASELINE-CHECK-1 (2026-10-04) — a per-ticker fetch failure (a provider 404, e.g.
        # EODHD's own ticker code for a Korean listing differing from the cohort's Yahoo
        # symbol: 034020.KO vs 034020.KS) must count as "not covered", exactly like an
        # empty period_ends, never crash the round. Before this fix it did: MemoAdapter
        # caches and RE-RAISES that one ticker's exception on every call, so the FIRST
        # round that includes it aborted the ENTIRE backtest for this cohort — silently,
        # for every lens, every round after. run_rank_pipeline's own factor-gathering
        # (gather_factor_inputs) already degrades a fetch failure to "no data" for that one
        # name rather than crashing; this line, added alongside it by the same PR, did not.
        # Live case: Industrials - Grid & Electrical Machinery (member 034020.KO/112610.KO)
        # — see docs/BACKTEST.md "SIZE-FLOOR-2 and the baseline check" for the full account.
        def _has_dated_accounts(ticker: str) -> bool:
            try:
                return bool(asof.get_fundamentals(ticker).period_ends)
            except Exception:                                   # noqa: BLE001 — a coverage
                return False                                     # FACT, never a crash here.
        accounts_coverage = (sum(1 for t in eligible if _has_dated_accounts(t))
                             / len(eligible)) if eligible else None
        pipeline_result = run_rank_pipeline(
            eligible, lens_id, ranker_only=True, adapter=asof, today=d,
            use_cache=True, strategies_dir=strategies_dir,
            min_market_cap_override=min_cap_usd)
        ranked = pipeline_result.ranked
        # SIZE-FLOOR-2 item 1b — a name whose SCREEN could not evaluate a single criterion
        # (every one abstained — not one confirmed pass or fail) is NOT ranked by this lens
        # this round, matching the app's own "does not apply" convention (a screen with
        # nothing to say about a name is not the same as a screen that cleared it). Without
        # this, such a name still reaches factor ranking, where a missing factor is imputed
        # (missing: worst) rather than the name being left out — silently turning "the
        # screen had no data" into "the screen passed it". getattr(..., None) or {} keeps
        # every fixture using _stub_pipeline (a fake with no screen_outcomes at all)
        # byte-unchanged: an empty dict excludes no one, exactly as before this item.
        outcomes = getattr(pipeline_result, "screen_outcomes", None) or {}
        screen_blind = set()
        for r in ranked:
            # min_market_cap is excluded from "every criterion abstained": it is a trivial
            # gate (market cap is almost never missing) already enforced separately as the
            # rank strategy's own direct cap gate (above, before the screen even runs) — its
            # own confirmed pass/fail says nothing about whether the SCREEN had anything
            # substantive to say about this name, which is what this check is for.
            substantive = {name: o for name, o in (outcomes.get(r.ticker) or {}).items()
                          if name != "min_market_cap"}
            if substantive and all(o.get("passed") is None for o in substantive.values()):
                screen_blind.add(r.ticker)
        live = [r for r in ranked if not r.excluded and r.ticker not in screen_blind]
        buys = sorted(r.ticker for r in live if r.verdict == "buy")
        # BACKTEST-1D — a fact about the LENS's own basket sequence, measured every round whether
        # or not it counts as positioned (rule 1): all new on the very first round.
        buys_set = set(buys)
        n_new = len(buys_set) if previous_buys is None else len(buys_set - previous_buys)
        previous_buys = buys_set
        bench_pairs, held = [], []
        for r in live:
            ret = _window_return(series(r.ticker), d, exit_date)
            if ret is None:
                unpriced += 1
                continue
            bench_pairs.append((r.ticker, ret))
            if r.ticker in buys:
                held.append((r.ticker, ret))
        bench = [v for _, v in bench_pairs]
        bench_ret = (sum(bench) / len(bench)) if bench else None
        if len(held) >= MIN_BUYS and bench_ret is not None:
            n_buys = len(buys)
            buy_ret = sum(v for _, v in held) / len(held) - cost
            drop_best = _drop_best_excess(held, n_buys, bench_ret, cost)
            random_mean_excess = None
            if random_baskets > 0:
                pool_tickers = [t for t, _ in bench_pairs]
                k = min(n_buys, len(pool_tickers))
                if k < n_buys:
                    undersized_rounds += 1
                basket_excess = None
                rest_means = None
                if k > 0 and random_mode == RANDOM_MODE_INDEPENDENT:
                    pool = np.asarray(bench, dtype=float)
                    rng = np.random.default_rng(_round_seed(result.cohort_slug, lens_id, d))
                    idx = _sample_baskets(rng, pool.size, k, random_baskets)     # (N, k)
                    basket_returns = pool[idx]                                  # (N, k)
                    basket_excess = (basket_returns.mean(axis=1) - cost) - bench_ret   # (N,)
                    if n_buys >= 4 and k >= 2:
                        best = basket_returns.max(axis=1)
                        rest_means = (basket_returns.sum(axis=1) - best) / (k - 1)
                elif k > 0:                                                     # "turnover"
                    if turnover_baskets is None:
                        turnover_baskets = _TurnoverBaskets(result.cohort_slug, lens_id,
                                                            random_baskets)
                    chosen_sets = turnover_baskets.draw(pool_tickers, n_buys, n_new)
                    ticker_to_return = dict(bench_pairs)
                    basket_means = np.array([sum(ticker_to_return[t] for t in s) / len(s)
                                            for s in chosen_sets])
                    basket_excess = (basket_means - cost) - bench_ret
                    if n_buys >= 4 and k >= 2:
                        rest_means = np.array([
                            _series_rest_mean_dropping_best(
                                [(t, ticker_to_return[t]) for t in sorted(s)])
                            for s in chosen_sets])
                if basket_excess is not None:
                    random_mean_excess = float(basket_excess.mean())
                    pos_dates.append(d)
                    pos_excess_cols.append(basket_excess)
                    if rest_means is not None:
                        dropbest_dates.append(d)
                        dropbest_cols.append((rest_means - cost) - bench_ret)
            result.rounds.append(Round(d, n_buys, buy_ret, bench_ret, buy_ret - bench_ret,
                                       tuple(t for t, _ in held), exit_date, len(live),
                                       n_eligible=len(eligible), excess_drop_best=drop_best,
                                       random_mean_excess=random_mean_excess, n_new=n_new,
                                       n_illiquid=n_illiquid, accounts_coverage=accounts_coverage))
        else:
            result.rounds.append(Round(d, len(buys), None, bench_ret, None, (), exit_date,
                                       len(live), n_eligible=len(eligible), n_new=n_new,
                                       n_illiquid=n_illiquid, accounts_coverage=accounts_coverage))
        if progress is not None:
            last = result.rounds[-1]
            illiquid_note = f", {n_illiquid} illiquid" if liquidity_guard_on else ""
            progress(f"{d} ({i}/{len(dates)}): {len(eligible)} eligible{illiquid_note}, "
                     f"{len(live)} ranked, {len(buys)} BUY ({n_new} new), "
                     + (f"excess {last.excess:+.1%}" if last.has_position else "no position"))
    result.price_warnings = sorted(set(price_warnings))
    result.total_illiquid = total_illiquid
    if random_baskets > 0 and pos_excess_cols:
        _apply_luck_stats(result, pos_dates=pos_dates, pos_excess_cols=pos_excess_cols,
                          dropbest_dates=dropbest_dates, dropbest_cols=dropbest_cols)
    result.caveats = _caveats(result, unpriced, undersized_rounds)
    return result


def _round_seed(cohort_slug_: str, lens_id: str, round_date: date) -> int:
    """A seed fixed by cohort + lens + round date (SEED_RULE) - the same round always draws the
    same random baskets, and a different round never repeats another round's draw."""
    key = f"{cohort_slug_}|{lens_id}|{round_date.isoformat()}"
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)


def _sample_baskets(rng: "np.random.Generator", pool_size: int, k: int, n: int) -> "np.ndarray":
    """``n`` independent random index combinations of size ``k``, without replacement, from
    ``range(pool_size)`` - vectorised (one call, no Python loop over ``n``): one random float per
    (basket, pool member), argsort each basket's row, keep the first ``k`` columns. Shape (n, k).
    ``random_mode="independent"`` (BACKTEST-1C) only - see ``_TurnoverBaskets`` for the default."""
    return np.argsort(rng.random((n, pool_size)), axis=1)[:, :k]


def _series_seed(cohort_slug_: str, lens_id: str, series_index: int) -> int:
    """A seed fixed by cohort + lens + SERIES index (TURNOVER_SEED_RULE, BACKTEST-1D) - one seed
    per series, consumed across every round that series evolves through, unlike ``_round_seed``'s
    one-seed-per-round (which redraws from scratch and needs no persistent state)."""
    key = f"{cohort_slug_}|{lens_id}|series{series_index}"
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)


class _TurnoverBaskets:
    """BACKTEST-1D — ``n`` persistent random baskets that EVOLVE one round at a time instead of
    being redrawn from scratch, so their stickiness matches a real lens's. One instance per
    (cohort, lens); call ``draw`` once per POSITIONED round, in round order — it is stateful, and
    a call is what consumes the "first round" case.
    """

    def __init__(self, cohort_slug_: str, lens_id: str, n: int) -> None:
        self._rngs = [random.Random(_series_seed(cohort_slug_, lens_id, i)) for i in range(n)]
        self._holdings: list = [frozenset() for _ in range(n)]
        self._started = False

    def draw(self, pool_tickers, n_buys: int, n_new: int) -> list:
        """``pool_tickers`` is THIS round's priced eligible names. Returns a list of ``n``
        frozensets (one evolved basket per series), each of size ``min(n_buys, len(pool_tickers))``
        — updates the persisted state for the next call.

        Per series, in order: (1) drop whatever it holds that fell out of ``pool_tickers`` (an
        automatic consequence of eligibility, never a choice); (2) ADDITIONALLY swap out
        ``min(n_new, what remains)`` of what is left, chosen at random — the LENS's own turnover
        this round, mimicked on a basket that owes it nothing itself; (3) draw fresh names to fill
        whatever is missing to reach ``n_buys`` (covering both the eligibility-driven drops from
        step 1 and the turnover-driven ones from step 2 in one draw), or trim down if ``n_buys``
        itself shrank below what survived steps 1-2. This ORDER matters: swapping out BEFORE
        drawing in is what keeps the replacement count exactly ``n_new`` (plus drops) rather than
        overshooting the target size and then needing an unrelated random trim to fix it. Every
        draw is from ``sorted(...)`` of its candidate pool, never a bare set, because Python's set
        iteration order is not reproducible across processes (hash randomisation) and this must be.
        """
        pool = set(pool_tickers)
        cap = min(n_buys, len(pool))
        out = []
        for i, rng in enumerate(self._rngs):
            if not self._started:
                chosen = set(rng.sample(sorted(pool), cap)) if cap else set()
            else:
                kept = self._holdings[i] & pool                        # step 1 - eligibility only
                to_swap_out = min(max(0, n_new), len(kept))
                removed = set()
                if to_swap_out:                                        # step 2 - the lens's turnover
                    removed = set(rng.sample(sorted(kept), to_swap_out))
                    kept -= removed
                needed = cap - len(kept)                                # step 3 - fill, or trim
                if needed > 0:
                    # exclude ``removed`` too - a name just swapped OUT must not be immediately
                    # drawn back IN, or the swap is not a swap.
                    available = sorted(pool - kept - removed)
                    kept |= set(rng.sample(available, min(needed, len(available))))
                elif needed < 0:
                    kept = set(rng.sample(sorted(kept), cap))
                chosen = kept
            chosen = frozenset(chosen)
            self._holdings[i] = chosen
            out.append(chosen)
        self._started = True
        return out


def _series_rest_mean_dropping_best(pairs: list) -> float:
    """``pairs`` is ``[(ticker, return), ...]``, length >= 2, from a SORTED iteration (so a tied
    return breaks the same way every run). The mean return with the single best-returning ticker
    dropped — the same construction ``_drop_best_excess`` uses, per random series."""
    best_ticker = max(pairs, key=lambda tv: tv[1])[0]
    rest = [v for t, v in pairs if t != best_ticker]
    return sum(rest) / len(rest)


def _year_group_means(matrix: "np.ndarray", dates: list) -> "np.ndarray":
    """``matrix`` is (random_baskets, len(dates)); group columns by ``dates[i].year`` and mean
    within each group, per row. Returns (n_years, random_baskets) - the per-series yearly means
    the real lens's ``year_excess`` computes one series (itself) of."""
    groups: dict = {}
    for col, d in enumerate(dates):
        groups.setdefault(d.year, []).append(col)
    return np.stack([matrix[:, idx].mean(axis=1) for idx in groups.values()], axis=0)


def _apply_luck_stats(result: BacktestResult, *, pos_dates: list, pos_excess_cols: list,
                      dropbest_dates: list, dropbest_cols: list) -> None:
    """Sets ``luck_pct_mean`` / ``luck_pct_pass`` / ``drop_best_vs_random`` on ``result`` from the
    accumulated per-round random-basket columns. Every random series shares the SAME positioned
    rounds (and so the same years_measured/n_positions) as the real lens - a round holds a
    position, or does not, independent of which basket is drawn - which is what lets this be a
    handful of vectorised numpy calls instead of ``random_baskets`` separate Summary objects."""
    lens_summary = result.summary
    all_excess = np.column_stack(pos_excess_cols)                  # (N, n_positioned_rounds)
    year_means = _year_group_means(all_excess, pos_dates)           # (n_years, N)
    mean_annual_excess_series = year_means.mean(axis=0)              # (N,)
    years_positive_series = (year_means > 0).sum(axis=0)             # (N,)
    n_years = year_means.shape[0]

    result.luck_pct_mean = float(np.mean(
        mean_annual_excess_series >= lens_summary.mean_annual_excess))
    beats = mean_annual_excess_series >= (PROOF_MIN_EXCESS - PROOF_TOLERANCE)
    positive_enough = (years_positive_series * PROOF_OF_YEARS) >= (PROOF_MIN_YEARS * n_years)
    result.luck_pct_pass = float(np.mean(beats & positive_enough))

    if dropbest_cols and lens_summary.mean_annual_excess_drop_best is not None:
        dropbest_matrix = np.column_stack(dropbest_cols)
        db_year_means = _year_group_means(dropbest_matrix, dropbest_dates)
        mean_annual_drop_best_series = db_year_means.mean(axis=0)
        result.drop_best_vs_random = float(lens_summary.mean_annual_excess_drop_best
                                           - np.median(mean_annual_drop_best_series))


def _drop_best_excess(held: list, n_buys: int, bench_ret: float, cost: float) -> Optional[float]:
    """``excess`` with the single best-returning BUY name dropped - None when there are fewer than
    4 BUY names (a robustness check on 3 needs at least 4 to have anything left to drop from)."""
    if n_buys < 4 or len(held) < 2:
        return None
    best_ticker = max(held, key=lambda kv: kv[1])[0]
    rest = [ret for t, ret in held if t != best_ticker]
    return sum(rest) / len(rest) - cost - bench_ret


def _caveats(result: BacktestResult, unpriced: int, undersized_rounds: int = 0) -> list:
    out = [
        "RESTATED ACCOUNTS: statements are the ones EODHD carries today (restated), dated by "
        f"fiscal-period end plus a {result.lag_days}-day filing lag; a reader on the day saw the "
        "original figures, so a lens may look better here than it would have then.",
        "SURVIVORSHIP: a company delisted, merged, or acquired out of today's frozen cohort"
        + (f" (v{result.cohort_version})" if result.cohort_version else "")
        + " is still absent from every round, including ones it would have qualified for. This "
          "file's as_of_size_floor line corrects a SURVIVOR's own past size; it cannot resurrect a "
          "name that did not survive to be in today's membership at all.",
        f"Prices are ADJUSTED closes (dividends reinvested), so returns are total returns; "
        f"{result.cost_bps:g} bps is deducted once per round trip from the BUY basket, none from the "
        "benchmark.",
    ]
    if result.step_months < result.hold_months:
        out.append(f"Rounds are {result.step_months} month(s) apart with {result.hold_months}-month "
                   "holds, so they overlap and are not independent; read the calendar years, not "
                   "the round count.")
    out.append("Max drawdown is measured at round ends over non-overlapping chains of rounds (worst "
               "chain, no-position rounds as cash), so it understates a dip inside a hold.")
    s = result.summary
    if s.n_no_position:
        out.append(f"{s.n_no_position} of {s.n_rounds} rounds had fewer than {MIN_BUYS} priced BUY "
                   "names (no position); they are excluded from every average.")
    if unpriced:
        out.append(f"{unpriced} name-round(s) had no usable price at the entry or exit date and were "
                   "left out of both the BUY basket and the benchmark.")
    if result.random_baskets:
        mode_note = ("redrawn from scratch every round (independent mode)"
                    if result.random_mode == RANDOM_MODE_INDEPENDENT
                    else "a PERSISTENT basket evolving with the lens's own turnover each round "
                         "(turnover mode, BACKTEST-1D)")
        out.append(f"RANDOM-BASKET LUCK BASELINE: {result.random_baskets} random baskets per "
                   "positioned round, each the same size as that round's own BUY basket, drawn "
                   f"without replacement from the same priced names the benchmark is built from - "
                   f"{mode_note} ({result.seed_rule}). luck_pct_mean <= {result.max_luck:.0%} is "
                   "required for \"proven\"; below that a lens that clears the excess/years bar "
                   "reads \"not beyond luck\" instead. See docs/BACKTEST.md, Skill versus luck.")
    if undersized_rounds:
        out.append(f"{undersized_rounds} round(s) had fewer priced eligible names than the lens's "
                   "own BUY basket size, so that round's random baskets were drawn smaller than "
                   "n_buys (the largest basket the priced pool could support).")
    return out


# --------------------------------------------------------------------------- #
# the file
# --------------------------------------------------------------------------- #
_ROUND_COLUMNS = ("date", "exit_date", "n_eligible", "n_ranked", "n_buys", "n_new", "n_illiquid",
                  "accounts_coverage", "buy_return", "bench_return", "excess", "excess_drop_best",
                  "random_mean_excess", "tickers")


def _num(x: Optional[float]) -> str:
    return "" if x is None else repr(float(x))


def _unnum(text: str) -> Optional[float]:
    return float(text) if text != "" else None


def csv_path(result: BacktestResult, out) -> Path:
    """``backtests/<cohort_slug>/<lens_id>.csv`` under ``out``; an ``out`` that already ends in
    ``.csv`` is used as the file itself."""
    out = Path(out)
    return out if out.suffix == ".csv" else out / result.cohort_slug / f"{result.lens_id}.csv"


def to_csv(result: BacktestResult, path) -> Path:
    """One row per round under a ``# key: value`` header block (as-of rule, lag, costs, benchmark,
    caveats, verdict, the lenses' git commit). Deterministic: no timestamps, sorted rows, exact
    float text. Returns the path written."""
    target = csv_path(result, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    head = [
        ("cohort", result.cohort), ("cohort_slug", result.cohort_slug),
        ("cohort_version", "" if result.cohort_version is None else result.cohort_version),
        ("cohort_members", result.n_members), ("lens", result.lens_id),
        ("lens_commit", result.lens_commit), ("start", result.start), ("end", result.end),
        ("hold_months", result.hold_months), ("step_months", result.step_months),
        ("cost_bps", repr(float(result.cost_bps))), ("lag_days", result.lag_days),
        ("as_of_rule", AS_OF_RULE), ("benchmark", BENCHMARK_RULE), ("costs", COST_RULE),
        ("returns", RETURN_RULE),
        ("as_of_size_floor", (f"{result.size_floor:,.0f} USD, estimated from today's cap and price "
                              f"ratio ({result.size_floor_note})") if result.size_floor is not None
         else "n/a - no market-cap data for this run"),
        ("min_cap_usd_override", (f"{result.min_cap_usd:,.0f}" if result.min_cap_usd is not None
                                  else "n/a - the lens/cohort's own floor")),
        ("min_adv_usd", (f"{result.min_adv_usd:,.0f} (liquidity guard applied — item 1c)"
                         if result.min_adv_usd is not None
                         else "n/a - not applied this run (floor at or above $5bn, or no override)")),
        ("total_illiquid_removed", result.total_illiquid),
        ("random_baskets", result.random_baskets),
        ("random_mode", result.random_mode if result.random_baskets else "n/a - random_baskets=0"),
        ("seed_rule", result.seed_rule or "n/a - random_baskets=0"),
        ("max_luck", repr(float(result.max_luck))),
        ("luck_pct_mean", _num(result.luck_pct_mean) or "n/a"),
        ("luck_pct_pass", _num(result.luck_pct_pass) or "n/a"),
        ("drop_best_vs_random", _num(result.drop_best_vs_random) or "n/a"),
        ("verdict", verdict(result, max_luck=result.max_luck)),
    ]
    with target.open("w", newline="", encoding="utf-8") as fh:
        fh.write("# aristos-council lens backtest (BACKTEST-1) - see docs/BACKTEST.md\n")
        for key, value in head:
            fh.write(f"# {key}: {value}\n")
        for text in result.caveats:
            fh.write(f"# caveat: {' '.join(str(text).split())}\n")
        for text in result.price_warnings:
            fh.write(f"# price_warning: {text}\n")
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(_ROUND_COLUMNS)
        for r in sorted(result.rounds, key=lambda r: r.date):
            writer.writerow([r.date.isoformat(), r.exit_date.isoformat() if r.exit_date else "",
                             r.n_eligible, r.n_ranked, r.n_buys, r.n_new, r.n_illiquid,
                             _num(r.accounts_coverage),
                             _num(r.buy_return), _num(r.bench_return), _num(r.excess),
                             _num(r.excess_drop_best), _num(r.random_mean_excess),
                             " ".join(r.tickers)])
    return target


def _parse_size_floor(text: str) -> tuple:
    """The ``as_of_size_floor`` header value back into ``(floor, note)`` - the inverse of the
    f-string ``to_csv`` writes. ``"n/a - ..."`` (or anything unparseable, e.g. an older file with no
    such line) -> ``(None, "")``."""
    import re
    m = re.match(r"^([\d,]+(?:\.\d+)?) USD, estimated from today's cap and price ratio \((.*)\)$",
                text)
    return (float(m.group(1).replace(",", "")), m.group(2)) if m else (None, "")


def from_csv(path) -> BacktestResult:
    """Read a file written by ``to_csv`` back. The summary and verdict are recomputed from the rounds
    (the header's verdict line is informational), so they cannot drift from the data."""
    meta: dict[str, str] = {}
    caveats: list[str] = []
    price_warnings: list[str] = []
    body: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            key, sep, value = line[2:].partition(": ")
            if not sep:
                continue
            if key == "caveat":
                caveats.append(value)
            elif key == "price_warning":
                price_warnings.append(value)
            else:
                meta[key] = value
        elif line.strip():
            body.append(line)
    rounds = []
    for row in csv.DictReader(body):
        rounds.append(Round(
            date=date.fromisoformat(row["date"]), n_buys=int(row["n_buys"]),
            buy_return=_unnum(row["buy_return"]), bench_return=_unnum(row["bench_return"]),
            excess=_unnum(row["excess"]), tickers=tuple(row["tickers"].split()),
            exit_date=date.fromisoformat(row["exit_date"]) if row["exit_date"] else None,
            n_ranked=int(row["n_ranked"]),
            n_eligible=int(row["n_eligible"]) if row.get("n_eligible") not in (None, "") else 0,
            excess_drop_best=_unnum(row.get("excess_drop_best") or ""),
            random_mean_excess=_unnum(row.get("random_mean_excess") or ""),
            n_new=int(row["n_new"]) if row.get("n_new") not in (None, "") else 0,
            # SIZE-FLOOR-1: absent from a file written before this item -> 0 (the guard never
            # applied there, so 0 is the true count, not a filled-in default).
            n_illiquid=int(row["n_illiquid"]) if row.get("n_illiquid") not in (None, "") else 0,
            # SIZE-FLOOR-2 item 2d: absent from a file written before this item -> None (the
            # coverage fact was never measured there, not "zero coverage").
            accounts_coverage=_unnum(row.get("accounts_coverage") or "")))
    size_floor, size_floor_note = _parse_size_floor(meta.get("as_of_size_floor", ""))

    def _meta_num(key: str) -> Optional[float]:
        text = meta.get(key, "")
        return None if text in ("", "n/a") else float(text)

    def _meta_num_or_tagged_na(key: str) -> Optional[float]:
        """Like ``_meta_num``, for a header value that is a descriptive "n/a - ..." sentence
        rather than the bare "n/a" the luck fields use (SIZE-FLOOR-1's min_cap/min_adv lines)."""
        text = meta.get(key, "")
        if not text or text.startswith("n/a"):
            return None
        return float(text.split()[0].replace(",", ""))

    random_baskets_n = int(meta.get("random_baskets") or 0)
    if meta.get("random_mode") in RANDOM_MODES:
        random_mode_value = meta["random_mode"]
    elif random_baskets_n:
        # BACKTEST-1D — a file with baskets but no random_mode line at all predates this build,
        # and 1C only ever drew independently; read it as what it actually is, never the new
        # default.
        random_mode_value = RANDOM_MODE_INDEPENDENT
    else:
        random_mode_value = RANDOM_MODE_DEFAULT

    return BacktestResult(
        cohort=meta["cohort"], lens_id=meta["lens"], start=date.fromisoformat(meta["start"]),
        end=date.fromisoformat(meta["end"]), hold_months=int(meta["hold_months"]),
        step_months=int(meta["step_months"]), cost_bps=float(meta["cost_bps"]),
        lag_days=int(meta["lag_days"]), rounds=rounds, caveats=caveats,
        lens_commit=meta.get("lens_commit", ""),
        cohort_version=int(meta["cohort_version"]) if meta.get("cohort_version") else None,
        n_members=int(meta.get("cohort_members") or 0),
        size_floor=size_floor, size_floor_note=size_floor_note, price_warnings=price_warnings,
        min_cap_usd=_meta_num_or_tagged_na("min_cap_usd_override"),
        min_adv_usd=_meta_num_or_tagged_na("min_adv_usd"),
        total_illiquid=int(meta.get("total_illiquid_removed") or 0),
        random_baskets=random_baskets_n, random_mode=random_mode_value,
        seed_rule=(meta.get("seed_rule", "") if random_baskets_n else ""),
        max_luck=float(meta["max_luck"]) if meta.get("max_luck") else MAX_LUCK,
        luck_pct_mean=_meta_num("luck_pct_mean"), luck_pct_pass=_meta_num("luck_pct_pass"),
        drop_best_vs_random=_meta_num("drop_best_vs_random"))


def _pct(x: Optional[float], signed: bool = True) -> str:
    return "n/a" if x is None else f"{x:{'+' if signed else ''}.1%}"


def summary_line(result: BacktestResult) -> str:
    """One line: cohort x lens, the verdict, and the numbers it rests on."""
    s = result.summary
    luck = (f", luck {s.luck_pct_mean:.0%}" if s.luck_pct_mean is not None else "")
    return (f"{result.cohort_slug} x {result.lens_id}: {verdict(result, max_luck=result.max_luck)} "
            f"- mean annual excess {_pct(s.mean_annual_excess)}, {s.years_positive} of "
            f"{s.years_measured} years positive, hit rate {_pct(s.hit_rate, False)}, "
            f"{s.n_positions} of {s.n_rounds} rounds held a position, worst round "
            f"{_pct(s.worst_round_excess)}, max drawdown {_pct(s.max_drawdown, False)}{luck}")


def summarize_directory(root) -> list:
    """One ``summary_line`` per ``<root>/<cohort>/<lens>.csv``, in file order; an unreadable file
    is named, not skipped."""
    lines = []
    for path in sorted(Path(root).glob("*/*.csv")):
        try:
            lines.append(summary_line(from_csv(path)))
        except Exception as exc:                              # noqa: BLE001 - named, not hidden
            lines.append(f"{path.parent.name} x {path.stem}: UNREADABLE ({exc})")
    return lines


# --------------------------------------------------------------------------- #
# BACKTEST-1C — the multiple-testing line
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MultipleTestingStats:
    """Over a set of cohort x lens results: how many were even testable, how many came out
    "proven", and how many "proven"s chance alone would produce at this sample size."""
    tests_run: int                     # non-"insufficient" results
    proven: int
    expected_by_chance: float          # sum of each test's OWN luck_pct_pass

    def sentence(self) -> str:
        return (f"Multiple testing: {self.tests_run} test(s) run (insufficient excluded), "
               f"{self.proven} proven, {self.expected_by_chance:.1f} expected to pass by chance "
               f"alone (sum of each test's own chance pass rate).")


def multiple_testing(results) -> MultipleTestingStats:
    """``results`` is any iterable of ``BacktestResult``. A result with no measured
    ``luck_pct_pass`` (random_baskets=0) contributes 0 to the chance total - it is still counted
    in ``tests_run`` if not insufficient, but honestly cannot say how much of its own pass, if
    any, chance would explain."""
    tested = [r for r in results if verdict(r, max_luck=r.max_luck) != "insufficient"]
    proven = sum(1 for r in tested if verdict(r, max_luck=r.max_luck) == "proven")
    expected = sum((r.luck_pct_pass or 0.0) for r in tested)
    return MultipleTestingStats(tests_run=len(tested), proven=proven, expected_by_chance=expected)


def multiple_testing_for_directory(root) -> MultipleTestingStats:
    """``multiple_testing`` over every readable ``<root>/<cohort>/<lens>.csv``."""
    return multiple_testing(_read_directory(root))


def _read_directory(root) -> list:
    """Every readable ``<root>/<cohort>/<lens>.csv``, as ``BacktestResult`` objects, in file
    order. An unreadable file is left out silently here (``summarize_directory`` is where an
    unreadable file gets NAMED for a human); shared by ``multiple_testing_for_directory`` and
    ``calibration_report_for_directory`` so the two can never read a different set of files."""
    results = []
    for path in sorted(Path(root).glob("*/*.csv")):
        try:
            results.append(from_csv(path))
        except Exception:                                      # noqa: BLE001 - just left out
            continue
    return results


# --------------------------------------------------------------------------- #
# BACKTEST-1D — the calibration check
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CalibrationReport:
    """Is the luck test itself trustworthy? Bins ``luck_pct_mean`` over every testable result and
    compares the counts against what a CALIBRATED (fair) test would put in each bin."""
    n_tests: int
    counts: tuple                # 5 ints, one per CALIBRATION_BINS
    expected: tuple               # 5 floats, ``n_tests * CALIBRATION_EXPECTED_SHARE``
    warn: bool                    # a tail holds > CALIBRATION_WARNING_FACTOR x its expected share

    def lines(self) -> list:
        if self.n_tests == 0:
            return ["Calibration: no luck-measured, testable result yet."]
        out = ["Calibration (luck_pct_mean bins, observed vs expected under a fair test):"]
        for label, c, e in zip(CALIBRATION_LABELS, self.counts, self.expected):
            out.append(f"  {label:>8}: {c} observed, {e:.1f} expected")
        if self.warn:
            out.append("luck test may still be over-confident")
        return out


def _calibration_bin(pct: float) -> int:
    for i, (lo, hi) in enumerate(CALIBRATION_BINS):
        if pct < hi or (i == len(CALIBRATION_BINS) - 1 and pct <= hi):
            return i
    return len(CALIBRATION_BINS) - 1              # pragma: no cover - unreachable, pct in [0, 100]


def calibration_report(results) -> CalibrationReport:
    """``results`` is any iterable of ``BacktestResult``. Only non-"insufficient", luck-measured
    results are binned — the same population ``multiple_testing`` counts as ``tests_run``, minus
    any whose luck was never measured (``random_baskets=0``): a calibration claim needs the actual
    number to bin, not a guess."""
    tested = [r for r in results
             if verdict(r, max_luck=r.max_luck) != "insufficient" and r.luck_pct_mean is not None]
    n = len(tested)
    counts = [0] * len(CALIBRATION_BINS)
    for r in tested:
        counts[_calibration_bin(r.luck_pct_mean * 100.0)] += 1
    expected = tuple(n * share for share in CALIBRATION_EXPECTED_SHARE)
    tails_observed = counts[0] + counts[-1]
    tails_expected = expected[0] + expected[-1]
    warn = tails_expected > 0 and tails_observed > CALIBRATION_WARNING_FACTOR * tails_expected
    return CalibrationReport(n_tests=n, counts=tuple(counts), expected=expected, warn=warn)


def calibration_report_for_directory(root) -> CalibrationReport:
    """``calibration_report`` over every readable ``<root>/<cohort>/<lens>.csv``."""
    return calibration_report(_read_directory(root))


# --------------------------------------------------------------------------- #
# BACKTEST-2 — plain-English track-record badges
#
# Display only: a badge NEVER changes a vote, a rank or a verdict, and every lens keeps its vote
# whatever its badge says. It is derived entirely from the committed CSVs under ``backtests/`` (or
# an explicit ``root``) — read once per process and cached, never re-run.
# --------------------------------------------------------------------------- #
_BACKTESTS_ROOT = Path(__file__).resolve().parents[2] / "backtests"

# {resolved root path (str) -> {(cohort_slug, lens_id): BacktestResult}}, filled on first use.
_TRACK_RECORD_CACHE: dict = {}

NO_BACKTESTS_NOTICE = "no backtest results found"

# The five labels, in the order the badge rule checks them (see ``track_record``), each with the
# ONE sentence that explains what it means — written once, reused by the UI (the hover/expander)
# and by docs/BACKTEST.md's badge-scale table (BACKTEST-2 item 4a), so the two can never drift.
BADGE_MEANINGS = {
    "proven here": ("Beat its benchmark in this cohort by enough, often enough, for long enough, "
                    "that fewer than 1 in 20 random stock-pickers matched it."),
    "promising here": ("Beat its benchmark in this cohort by a real margin most years, and did "
                       "better than most random stock-pickers - not yet enough history or "
                       "separation from luck to call it proven."),
    "worked against you here": ("In this cohort, picking names at random would have matched or "
                                "beaten this lens at least 9 times in 10."),
    "no edge shown here": ("In this cohort, this lens's record does not clear the bar, and does "
                           "no better than picking names at random."),
    "untested here": ("There is not yet enough measured history for this cohort and this lens to "
                      "say anything."),
}
BADGE_LABELS = tuple(BADGE_MEANINGS)  # display order


@dataclass(frozen=True)
class Badge:
    """One lens's plain-English track record in one cohort (BACKTEST-2). ``verdict`` is the raw
    four-valued ``verdict()`` string when a committed result exists, else ``None`` (nothing was
    ever run for this cohort/lens pair). Every other field is read straight off that result's
    ``Summary`` and is ``None`` when there is no result to read it from."""
    label: str                          # one of BADGE_LABELS
    verdict: Optional[str]
    mean_excess: Optional[float]        # Summary.mean_annual_excess
    luck_pct: Optional[float]           # Summary.luck_pct_mean
    years_positive: Optional[int]
    years_measured: Optional[int]
    rounds_held: Optional[int]          # Summary.n_positions
    note: str = ""                      # why "untested here", when it needs saying; else ""

    def detail_line(self) -> str:
        """"mean excess +4.5%/yr · 7 of 10 years positive · luck 4% · 108 rounds held" - the numbers
        behind the badge, for a hover/expander; "no numbers measured" when there is nothing to show."""
        if self.years_measured is None:
            return self.note or "no numbers measured"
        luck = f"{self.luck_pct:.0%}" if self.luck_pct is not None else "n/a"
        return (f"mean excess {_pct(self.mean_excess)}/yr · {self.years_positive} of "
               f"{self.years_measured} years positive · luck {luck} · {self.rounds_held} "
               f"round(s) held")


def _untested_badge(note: str) -> Badge:
    return Badge(label="untested here", verdict=None, mean_excess=None, luck_pct=None,
                years_positive=None, years_measured=None, rounds_held=None, note=note)


def _load_backtests(root=None) -> dict:
    """Every committed backtest result under ``root`` (default ``backtests/`` at the repo root), as
    ``{(cohort_slug, lens_id): BacktestResult}`` — read once and cached in memory for the rest of
    the process (BACKTEST-2 item 1). A missing root, or a root with no CSVs, caches an empty dict
    rather than raising; an unreadable individual file is left out silently (same contract as
    ``_read_directory``, which this mirrors but keys by the committed DIRECTORY/FILE names, the
    authoritative cohort slug and lens id, rather than by re-deriving them from the file's own
    ``# cohort:`` line)."""
    base = Path(root) if root else _BACKTESTS_ROOT
    key = str(base.resolve()) if base.exists() else f"missing:{base}"
    if key not in _TRACK_RECORD_CACHE:
        found: dict = {}
        if base.is_dir():
            for path in sorted(base.glob("*/*.csv")):
                try:
                    found[(path.parent.name, path.stem)] = from_csv(path)
                except Exception:                          # noqa: BLE001 - left out, not fatal
                    continue
        _TRACK_RECORD_CACHE[key] = found
    return _TRACK_RECORD_CACHE[key]


def has_track_record(cohort_slug: str, *, root=None) -> bool:
    """True if ANY committed result exists for this cohort slug — lets a caller decide whether a
    track-record section is worth showing at all before asking about individual lenses."""
    return any(slug == cohort_slug for slug, _lens in _load_backtests(root))


def track_record(cohort_slug: str, lens_id: str, *, root=None) -> Badge:
    """The plain-English track-record badge for one (cohort, lens) pair (BACKTEST-2). Reads the
    committed CSVs under ``backtests/`` (cached after the first call); never gates — this is
    display only and is never fed back into a vote, rank or verdict.

    Checked in this order:
      - no committed result for this cohort+lens at all, or its own ``verdict()`` is
        "insufficient" (too little measured history to trust ANY number below) -> "untested here"
      - ``verdict()`` == "proven" -> "proven here"
      - the plain excess/years bar met (mean excess >= 2%/yr, years positive >= 6 of 10) AND
        ``luck_pct_mean`` <= 25% -> "promising here"
      - ``luck_pct_mean`` >= 90% (a random stock-picker matched or beat it at least 9 times in
        10) — regardless of the bar -> "worked against you here"
      - everything else that was actually tested -> "no edge shown here"
    """
    cache = _load_backtests(root)
    result = cache.get((cohort_slug, lens_id))
    if result is None:
        base = Path(root) if root else _BACKTESTS_ROOT
        note = NO_BACKTESTS_NOTICE if not base.is_dir() else (
            "no backtest result for this cohort and lens")
        return _untested_badge(note)

    s = result.summary
    v = verdict(result, max_luck=result.max_luck)
    common = dict(verdict=v, mean_excess=s.mean_annual_excess, luck_pct=s.luck_pct_mean,
                 years_positive=s.years_positive, years_measured=s.years_measured,
                 rounds_held=s.n_positions)
    if v == "insufficient":
        return Badge(label="untested here", note=(
            f"only {s.years_measured} year(s) measured, {s.n_positions} round(s) held - not "
            f"enough history yet"), **common)
    if v == "proven":
        return Badge(label="proven here", **common)
    if (_passes_bar(s, PROOF_MIN_EXCESS, PROOF_MIN_YEARS, PROOF_OF_YEARS)
            and s.luck_pct_mean is not None and s.luck_pct_mean <= 0.25):
        return Badge(label="promising here", **common)
    if s.luck_pct_mean is not None and s.luck_pct_mean >= 0.90:
        return Badge(label="worked against you here", **common)
    return Badge(label="no edge shown here", **common)


def track_record_caption(cohort_slug: str, *, root=None) -> Optional[str]:
    """"Track record from the <short name> cohort, N years to <Mon YYYY>" - or None when nothing
    is committed for this cohort at all. Every lens in a cohort shares the same window, so any one
    committed result names it."""
    cache = _load_backtests(root)
    result = next((r for (slug, _lens), r in cache.items() if slug == cohort_slug), None)
    if result is None:
        return None
    years = round((result.end - result.start).days / 365.25)
    _, sep, short = result.cohort.partition(" - ")
    name = short if sep else result.cohort
    return f"Track record from the {name} cohort, {years} years to {result.end.strftime('%b %Y')}"


def format_track_record_summary(badges) -> str:
    """"Track record: 2 proven, 1 promising, 2 no edge shown" - counts, over ``badges`` (an
    iterable of ``Badge``), of each label actually present, in the fixed badge-scale order, each
    label's trailing " here" dropped for a shorter line. "" when ``badges`` is empty."""
    from collections import Counter
    counts = Counter(b.label for b in badges)
    parts = [f"{counts[label]} {label[:-len(' here')]}" for label in BADGE_LABELS if counts[label]]
    return "Track record: " + ", ".join(parts) if parts else ""


def cohort_for_industry(industry: Optional[str], gics_subindustry: Optional[str] = None, *,
                        definitions_path=None) -> Optional[str]:
    """The backtested cohort SLUG whose ``data/cohort_definitions.yaml`` rule matches this
    industry label — the SAME matching rule the cohort builder applies when it selects members
    (``cohorts.source.build_pool_from_index``'s industry-code match, narrowed by
    ``cohorts.cleanup.rule_exclusions``'s GICS sub-industry filter when the definition names one).
    ``None`` when no definition's industry list contains the label, or (for a definition narrowed
    to a GICS sub-industry) the label carries none of those sub-industries, or no company's
    sub-industry at all. This is a pure textual match against the DEFINITIONS file — it does not
    care whether that cohort has ever actually been built or backtested (``track_record`` already
    degrades to "untested here" when it has not)."""
    from .cohorts.definitions import load_definitions

    industry_norm = (industry or "").replace("\xa0", " ").strip()
    if not industry_norm:
        return None
    sub_norm = (gics_subindustry or "").strip().lower()
    path = Path(definitions_path) if definitions_path else (
        _BACKTESTS_ROOT.parent / "data" / "cohort_definitions.yaml")
    try:
        defs = load_definitions(path)
    except Exception:                                       # noqa: BLE001 - no match, not a crash
        return None
    for defn in defs:
        if industry_norm not in set(defn.industry):
            continue
        if defn.gics_subindustry:
            wanted = {s.lower() for s in defn.gics_subindustry}
            if not sub_norm or sub_norm not in wanted:
                continue
        return defn.slug
    return None


# --------------------------------------------------------------------------- #
# CLI:  python -m aristos_council.backtest run|summary
# --------------------------------------------------------------------------- #
def _build_parser():
    import argparse
    p = argparse.ArgumentParser(prog="python -m aristos_council.backtest",
                                description="Lens backtest (BACKTEST-1): does a lens's BUY basket "
                                            "beat its own cohort? No LLM is called.")
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run one cohort x one lens and write its CSV")
    run.add_argument("--cohort", required=True, help='cohort name or slug, e.g. "Semiconductors"')
    run.add_argument("--lens", required=True, help="rank strategy id, e.g. magic_formula_momentum_v1")
    run.add_argument("--years", type=int, default=10, help="window length ending at --end (default 10)")
    run.add_argument("--end", type=date.fromisoformat, default=None,
                     help="window end, YYYY-MM-DD (default: the last month end)")
    run.add_argument("--hold", type=int, default=12, help="hold months (default 12)")
    run.add_argument("--step", type=int, default=1, help="months between rounds (default 1)")
    run.add_argument("--cost-bps", type=float, default=50.0, help="round-trip cost (default 50)")
    run.add_argument("--lag-days", type=int, default=90, help="filing lag (default 90)")
    run.add_argument("--out", default="backtests", help="output root (default backtests/)")
    run.add_argument("--cohorts-root", default=None, help="where frozen cohorts live")
    run.add_argument("--random-baskets", type=int, default=RANDOM_BASKETS_DEFAULT,
                     help=f"random baskets per round for the luck baseline (default "
                          f"{RANDOM_BASKETS_DEFAULT}; 0 opts out) - BACKTEST-1C")
    run.add_argument("--max-luck", type=float, default=MAX_LUCK,
                     help=f"\"proven\" needs luck_pct_mean <= this (default {MAX_LUCK:g})")
    run.add_argument("--random-mode", choices=RANDOM_MODES, default=RANDOM_MODE_DEFAULT,
                     help=f"\"turnover\" (default) matches the lens's own turnover; "
                          f"\"independent\" is the 1C behaviour, kept for comparison")
    summ = sub.add_parser("summary", help="one line per cohort x lens under a backtests directory")
    summ.add_argument("root", nargs="?", default="backtests")
    return p


def _load_env() -> None:
    """EODHD_API_KEY from a local .env, as the other CLIs do (a no-op under pytest)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except Exception:
        pass


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if args.cmd == "summary":
        lines = summarize_directory(args.root)
        print("\n".join(lines) if lines else f"no backtests under {args.root}")
        if lines:
            print(multiple_testing_for_directory(args.root).sentence())
            for line in calibration_report_for_directory(args.root).lines():
                print(line)
        return 0
    _load_env()
    end = args.end or month_end(date.today().replace(day=1) - timedelta(days=1))
    start = date(end.year - args.years, end.month, 1)
    result = run_lens_backtest(
        args.cohort, args.lens, start=start, end=end, hold_months=args.hold, step_months=args.step,
        cost_bps=args.cost_bps, lag_days=args.lag_days, cohorts_root=args.cohorts_root,
        random_baskets=args.random_baskets, max_luck=args.max_luck,
        random_mode=args.random_mode, progress=lambda msg: print(msg, flush=True))
    path = to_csv(result, args.out)
    print(f"wrote {path}")
    print(summary_line(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
