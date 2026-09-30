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
import subprocess
import sys
from pathlib import Path

MIN_BUYS = 3                      # fewer BUY names than this is "no position", not a 1-stock bet
PRICE_TOLERANCE_DAYS = 10         # a close older than this (before a date) is "no price", not stale

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
                                          if drop_best_years else None))


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
    """One ticker's (day, adjusted close) points with bisect lookups."""

    def __init__(self, bars) -> None:
        pts = sorted((b.day, b.adj_close) for b in bars if b.adj_close is not None)
        self._days = [d for d, _ in pts]
        self._vals = [v for _, v in pts]

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


def verdict(result: BacktestResult, min_excess: float = PROOF_MIN_EXCESS,
            min_years: int = PROOF_MIN_YEARS, of_years: int = PROOF_OF_YEARS) -> str:
    """"proven" | "not proven" | "insufficient" - the owner's pass bar (see PROOF_* above)."""
    s = result.summary
    if s.years_measured < INSUFFICIENT_BELOW_YEARS or s.n_positions < INSUFFICIENT_BELOW_ROUNDS:
        return "insufficient"
    beats_by_enough = (s.mean_annual_excess is not None
                      and s.mean_annual_excess >= min_excess - PROOF_TOLERANCE)
    positive_enough = s.years_positive * of_years >= min_years * s.years_measured
    return "proven" if (beats_by_enough and positive_enough) else "not proven"


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
                      strategies_dir=None, member_caps=None) -> BacktestResult:
    """Backtest one lens on one cohort. The first eight parameters are the contract; the rest of the
    keywords only say WHERE things live (a cohort directory, an explicit member list and its market
    caps for a machine with no built cohorts, a strategies directory) and change no rule.

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

    No LLM is called; ``adapter`` (default: EODHD accounts + yfinance prices) is the inner data
    source."""
    from .data.asof_adapter import AsOfAdapter
    from .data.backtest_feed import MemoAdapter, lookback_start
    from .pipeline import run_rank_pipeline

    version: Optional[int] = None
    if members is None:
        members, version, member_caps = load_cohort_members(cohort, cohorts_root)
    members = list(members)
    member_caps = dict(member_caps) if member_caps else {}
    size_floor, size_floor_note = (_size_floor(cohort, cohorts_root, version, member_caps)
                                   if member_caps else (None, ""))
    dates = round_dates(start, end, hold_months, step_months)
    result = BacktestResult(cohort=cohort, lens_id=lens_id, start=start, end=end,
                            hold_months=hold_months, step_months=step_months,
                            cost_bps=float(cost_bps), lag_days=lag_days, cohort_version=version,
                            n_members=len(members), size_floor=size_floor,
                            size_floor_note=size_floor_note,
                            lens_commit=lens_commit(lens_id, strategies_dir))
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

    def eligible_at(d: date) -> list:
        """The as-of-size-floor-eligible universe for round ``d`` (step 1 above); ``members``
        unchanged when there is no cap data to apply a floor with."""
        if not member_caps:
            return members
        out = []
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
            out.append(t)
        return out

    cost = float(cost_bps) / 10_000.0
    unpriced = 0
    for i, d in enumerate(dates, 1):
        exit_date = add_months(d, hold_months)
        eligible = eligible_at(d)
        ranked = run_rank_pipeline(
            eligible, lens_id, ranker_only=True, adapter=AsOfAdapter(memo, d, lag_days), today=d,
            use_cache=True, strategies_dir=strategies_dir).ranked
        live = [r for r in ranked if not r.excluded]
        buys = sorted(r.ticker for r in live if r.verdict == "buy")
        bench, held = [], []
        for r in live:
            ret = _window_return(series(r.ticker), d, exit_date)
            if ret is None:
                unpriced += 1
                continue
            bench.append(ret)
            if r.ticker in buys:
                held.append((r.ticker, ret))
        bench_ret = (sum(bench) / len(bench)) if bench else None
        if len(held) >= MIN_BUYS and bench_ret is not None:
            buy_ret = sum(v for _, v in held) / len(held) - cost
            drop_best = _drop_best_excess(held, len(buys), bench_ret, cost)
            result.rounds.append(Round(d, len(buys), buy_ret, bench_ret, buy_ret - bench_ret,
                                       tuple(t for t, _ in held), exit_date, len(live),
                                       n_eligible=len(eligible), excess_drop_best=drop_best))
        else:
            result.rounds.append(Round(d, len(buys), None, bench_ret, None, (), exit_date,
                                       len(live), n_eligible=len(eligible)))
        if progress is not None:
            last = result.rounds[-1]
            progress(f"{d} ({i}/{len(dates)}): {len(eligible)} eligible, {len(live)} ranked, "
                     f"{len(buys)} BUY, "
                     + (f"excess {last.excess:+.1%}" if last.has_position else "no position"))
    result.price_warnings = sorted(set(price_warnings))
    result.caveats = _caveats(result, unpriced)
    return result


def _drop_best_excess(held: list, n_buys: int, bench_ret: float, cost: float) -> Optional[float]:
    """``excess`` with the single best-returning BUY name dropped - None when there are fewer than
    4 BUY names (a robustness check on 3 needs at least 4 to have anything left to drop from)."""
    if n_buys < 4 or len(held) < 2:
        return None
    best_ticker = max(held, key=lambda kv: kv[1])[0]
    rest = [ret for t, ret in held if t != best_ticker]
    return sum(rest) / len(rest) - cost - bench_ret


def _caveats(result: BacktestResult, unpriced: int) -> list:
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
    return out


# --------------------------------------------------------------------------- #
# the file
# --------------------------------------------------------------------------- #
_ROUND_COLUMNS = ("date", "exit_date", "n_eligible", "n_ranked", "n_buys", "buy_return",
                  "bench_return", "excess", "excess_drop_best", "tickers")


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
        ("verdict", verdict(result)),
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
                             r.n_eligible, r.n_ranked, r.n_buys, _num(r.buy_return),
                             _num(r.bench_return), _num(r.excess), _num(r.excess_drop_best),
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
            excess_drop_best=_unnum(row.get("excess_drop_best") or "")))
    size_floor, size_floor_note = _parse_size_floor(meta.get("as_of_size_floor", ""))
    return BacktestResult(
        cohort=meta["cohort"], lens_id=meta["lens"], start=date.fromisoformat(meta["start"]),
        end=date.fromisoformat(meta["end"]), hold_months=int(meta["hold_months"]),
        step_months=int(meta["step_months"]), cost_bps=float(meta["cost_bps"]),
        lag_days=int(meta["lag_days"]), rounds=rounds, caveats=caveats,
        lens_commit=meta.get("lens_commit", ""),
        cohort_version=int(meta["cohort_version"]) if meta.get("cohort_version") else None,
        n_members=int(meta.get("cohort_members") or 0),
        size_floor=size_floor, size_floor_note=size_floor_note, price_warnings=price_warnings)


def _pct(x: Optional[float], signed: bool = True) -> str:
    return "n/a" if x is None else f"{x:{'+' if signed else ''}.1%}"


def summary_line(result: BacktestResult) -> str:
    """One line: cohort x lens, the verdict, and the numbers it rests on."""
    s = result.summary
    return (f"{result.cohort_slug} x {result.lens_id}: {verdict(result)} - mean annual excess "
            f"{_pct(s.mean_annual_excess)}, {s.years_positive} of {s.years_measured} years positive, "
            f"hit rate {_pct(s.hit_rate, False)}, {s.n_positions} of {s.n_rounds} rounds held a "
            f"position, worst round {_pct(s.worst_round_excess)}, max drawdown "
            f"{_pct(s.max_drawdown, False)}")


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
        return 0
    _load_env()
    end = args.end or month_end(date.today().replace(day=1) - timedelta(days=1))
    start = date(end.year - args.years, end.month, 1)
    result = run_lens_backtest(
        args.cohort, args.lens, start=start, end=end, hold_months=args.hold, step_months=args.step,
        cost_bps=args.cost_bps, lag_days=args.lag_days, cohorts_root=args.cohorts_root,
        progress=lambda msg: print(msg, flush=True))
    path = to_csv(result, args.out)
    print(f"wrote {path}")
    print(summary_line(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
