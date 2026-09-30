"""GAP-BACKFILL-1 — self-healing catch-up for Gap Ledger.

A DATED EXCEPTION to the Gap Ledger feature freeze (docs/GAP_LEDGER.md), agreed 2026-09-30: the
freeze bars new filters, thresholds and checkpoints, but a missed day is not a new rule — it is
the SAME rule, applied to a day nobody was there to run it on. Two causes account for nearly every
missed day: the laptop being off (travel), and — more often — the IB Gateway having disconnected
while the laptop stayed on. Both need to be handled with nobody watching.

Three moving parts:

``missing_days``      step 1 — which NYSE trading days have no complete ledger file, between the
                       earliest one on disk and the last one whose session has closed.
``backfill_one_day``  step 2 — rebuild one such day from historical data only, up to that day's
                       normal cutoff time. Reuses the live screen's own pure functions
                       (``universe.pre_filter``, ``screen.screen_one``, ``news.gather_news``,
                       ``run.build_row``) rather than ``run.py``'s live-only IBKR verification gate
                       (``verify.check_one``/``check_two``), whose "no bars = no real move" reading
                       is correct for TODAY but wrong for an old day IBKR simply has no history for
                       — that would be exactly the null-as-false bug this repo has paid for before.
``run_backfill``      the batch driver: every missing day, oldest first, capped at ``max_days``.

Gateway health (``ensure_gateway``, ``write_gateway_down_stub``) and the live-window gate
(``within_live_window``) are the two safety mechanisms that make it safe to fire the SAME command
whenever the laptop happens to switch on — see docs/GAP_LEDGER.md, Catch-up and backfill section.

No LLM anywhere in this module.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, Sequence

from .bars import DailySource, IntradaySource, lookback_start
from .config import DEFAULT_CONFIG, DEFAULT_ROOT, DEFAULT_RUN_TIME, GapConfig, NY, at_ny, now_ny
from .ledger import (COMPLETE, GROUP_BASELINE, GROUP_CANDIDATE, INCOMPLETE, PARTIAL, LedgerRow,
                     day_status, ledger_days, ledger_path, read_day, sample_baseline,
                     stamp_backfill, status_row, write_day)
from .news import NewsSource, gather_news
from .nyse_calendar import last_completed_trading_day, trading_days_between
from .run import build_row
from .screen import SPREAD_HISTORICAL, screen_one
from .todoist import TodoistClient, deliver, deliver_alert
from .universe import pre_filter, split_common_stock
from .verify import SOURCE_IBKR, SOURCE_YFINANCE

_log = logging.getLogger(__name__)
Progress = Callable[[str], None]


def _noop(_message: str) -> None:
    pass


GATEWAY_UNREACHABLE = "ibkr unreachable"
FIELD_UNAVAILABLE_REASON = "some field(s) could not be sourced from IBKR or yfinance"
IBKR_WENT_AWAY_REASON = "IBKR went away partway through the backfill"
LIVE_WINDOW_TOLERANCE_MINUTES = 45          # GAP-BACKFILL-1 4
BACKFILL_MAX_DEFAULT = 15                   # GAP-BACKFILL-1 2
GATEWAY_RETRY_TIMEOUT_SECONDS = 600.0       # GAP-BACKFILL-1 7a — up to 10 minutes
GATEWAY_RETRY_INTERVAL_SECONDS = 60.0       # retry every 60s


# =========================================================================== #
# step 1 — detection
# =========================================================================== #
def missing_days(root: str | Path = DEFAULT_ROOT, *, now: Optional[datetime] = None) -> list:
    """Every NYSE trading day, oldest first, from the EARLIEST logged file up to and including
    the last COMPLETED trading day, that has no ledger file — or whose file exists but is
    flagged ``incomplete``/``partial`` (crash mid-run, IBKR unreachable, a backfill that had to
    guess less than it needed to).

    No file has ever been logged -> ``[]``: there is nothing to anchor the window to, and
    GAP-BACKFILL-1 does not reach back before the experiment's own first day.
    """
    logged = ledger_days(root)
    if not logged:
        return []
    earliest = logged[0]
    last = last_completed_trading_day(now or now_ny())
    out = []
    for day in trading_days_between(earliest, last):
        path = ledger_path(day, root)
        if not path.exists() or day_status(read_day(day, root)) != COMPLETE:
            out.append(day)
    return out


# =========================================================================== #
# step 4 — is a LIVE scan safe to run right now?
# =========================================================================== #
def within_live_window(now: datetime, *, scheduled=DEFAULT_RUN_TIME,
                       tolerance_minutes: int = LIVE_WINDOW_TOLERANCE_MINUTES) -> bool:
    """Is ``now`` within ``tolerance_minutes`` AFTER the scheduled run time, on the market's
    clock? A run outside this window is backfill-only — safe to fire whenever the laptop
    happens to switch on (GAP-BACKFILL-1 4), because it will never mistake a late catch-up for
    a fresh pre-market read."""
    now_ny = now.astimezone(NY)
    start = at_ny(now_ny.date(), scheduled)
    return start <= now_ny <= start + timedelta(minutes=tolerance_minutes)


# =========================================================================== #
# GAP-BACKFILL-1 7 — the gateway
# =========================================================================== #
def ensure_gateway(ibkr, *, timeout_seconds: float = GATEWAY_RETRY_TIMEOUT_SECONDS,
                   interval_seconds: float = GATEWAY_RETRY_INTERVAL_SECONDS,
                   sleep=None, progress: Progress = _noop) -> bool:
    """Try to reach the IB Gateway, retrying every ``interval_seconds`` for up to
    ``timeout_seconds`` (GAP-BACKFILL-1 7a: up to 10 minutes, every 60s). True as soon as
    ``ibkr.ping()`` succeeds; False only after every attempt failed."""
    import time as _time
    from .ibkr import IBKRUnavailable
    sleep = sleep or _time.sleep
    attempts = max(1, int(timeout_seconds // interval_seconds) + 1)
    for attempt in range(1, attempts + 1):
        try:
            ibkr.ping()
            return True
        except IBKRUnavailable as exc:
            progress(f"IB Gateway unreachable (attempt {attempt} of {attempts}): {exc}")
            if attempt < attempts:
                sleep(interval_seconds)
    return False


def write_gateway_down_stub(day: date, *, root: str | Path = DEFAULT_ROOT, run_at: datetime,
                            now: Optional[datetime] = None,
                            todoist: Optional[TodoistClient] = None) -> LedgerRow:
    """GAP-BACKFILL-1 7a — no degraded live scan is run: write a single GROUP_STATUS row
    flagged incomplete, so the day is picked up by ``missing_days`` on the next run, and post
    (or update) ONE Todoist alert so the owner sees it without watching the log."""
    now = now or now_ny()
    row = status_row(day, run_at=run_at, status=INCOMPLETE, reason=GATEWAY_UNREACHABLE,
                     backfilled=False)
    write_day(day, [row], root=root)
    if todoist is not None:
        deliver_alert(day, f"Gap Ledger {day.isoformat()} NOT RUN: IB Gateway unreachable",
                     client=todoist)
    return row


# =========================================================================== #
# step 2 — rebuild one day
# =========================================================================== #
@dataclass
class BackfillDayResult:
    day: date
    status: str
    n_candidates: int = 0
    n_baseline: int = 0
    n_eligible: int = 0
    notes: list = field(default_factory=list)

    def sentence(self) -> str:
        note = f" ({'; '.join(self.notes)})" if self.notes else ""
        return (f"{self.day.isoformat()}: {self.status} — {self.n_candidates} candidate(s), "
               f"{self.n_baseline} baseline, {self.n_eligible} screened{note}")


def backfill_one_day(day: date, *, root: str | Path = DEFAULT_ROOT,
                     config: GapConfig = DEFAULT_CONFIG, pool: Sequence[str],
                     pool_source: str = "", daily: DailySource, intraday: IntradaySource,
                     ibkr=None, news_source: Optional[NewsSource] = None,
                     company_names: Optional[dict] = None,
                     todoist: Optional[TodoistClient] = None,
                     run_at: Optional[datetime] = None, now: Optional[datetime] = None,
                     progress: Progress = _noop) -> BackfillDayResult:
    """Rebuild ONE missing or partial day, from historical data only, up to that day's normal
    cutoff time (GAP-BACKFILL-1 2). Never fabricates a value: a field neither IBKR history nor
    yfinance can supply is recorded ``backfill_*_source = "unavailable"`` and the day is
    flagged ``partial``, never silently guessed."""
    from .ibkr import BASELINE_BAR_SIZE, BASELINE_DURATION, IBKRUnavailable

    run_at = run_at or at_ny(day, DEFAULT_RUN_TIME)
    now = now or now_ny()
    existing_path = ledger_path(day, root)
    if existing_path.exists() and day_status(read_day(day, root)) == COMPLETE:
        # Detection would not have offered this day; defend against a stray direct call.
        return BackfillDayResult(day=day, status=COMPLETE, notes=["already complete — skipped"])

    offered = sorted({t.strip().upper() for t in pool if t and t.strip()})
    names, _not_common = split_common_stock(offered)
    history_start = lookback_start(day, config.min_history_days)
    daily_bars = daily.daily_bars(names, start=history_start, end=day + timedelta(days=1))
    pre = pre_filter(names, daily_bars, as_of=day, config=config)
    survivors = pre.tickers
    pre_by_ticker = pre.by_ticker()
    progress(f"{day}: {len(survivors)} of {len(names)} cleared price, volume and history")

    window_start = lookback_start(day, config.relative_volume_days)
    tomorrow = day + timedelta(days=1)
    yf_bars = (intraday.intraday_bars(survivors, start=window_start, end=tomorrow)
              if survivors else {})

    ibkr_bars: dict = {}
    ibkr_down = False
    if ibkr is not None and survivors:
        try:
            for ticker in survivors:
                ibkr_bars[ticker] = ibkr.bars_for(ticker, start=window_start, end=tomorrow,
                                                  bar_size=BASELINE_BAR_SIZE,
                                                  duration=BASELINE_DURATION)
        except IBKRUnavailable as exc:
            ibkr_down = True
            _log.warning("gap_ledger backfill %s: IBKR went away mid-backfill: %s", day, exc)
            progress(f"{day}: IBKR went away mid-backfill ({exc}); continuing on yfinance")

    screened: dict = {}
    gap_source: dict = {}
    volume_source: dict = {}
    field_unavailable = False
    for ticker in survivors:
        prev_close = pre_by_ticker[ticker].previous_close
        ib_row = None
        if ibkr_bars.get(ticker):
            ib_row = screen_one(ticker, bars=ibkr_bars[ticker], previous_close=prev_close,
                                as_of=day, run_at=run_at, spread_unknown_note=SPREAD_HISTORICAL,
                                config=config)
        yf_row = screen_one(ticker, bars=yf_bars.get(ticker, []), previous_close=prev_close,
                            as_of=day, run_at=run_at, spread_unknown_note=SPREAD_HISTORICAL,
                            config=config)
        if ib_row is not None and ib_row.gap is not None:
            chosen, from_ibkr, gsrc = ib_row, True, "ibkr-history"
        elif yf_row.gap is not None:
            chosen, from_ibkr, gsrc = yf_row, False, "yfinance"
        else:
            chosen, from_ibkr, gsrc = yf_row, False, "unavailable"
            field_unavailable = True
        vsrc = "unavailable"
        if chosen.relative_volume is not None:
            vsrc = "ibkr-history" if from_ibkr else "yfinance"
        elif chosen.passed is not False:
            # A missing ratio is only worth flagging when it did not otherwise fail outright —
            # a genuine gap-below-threshold reject never needed the ratio in the first place.
            field_unavailable = True
        screened[ticker] = chosen
        gap_source[ticker] = gsrc
        volume_source[ticker] = vsrc

    candidates = [row for row in screened.values() if row.passed is True]
    picks = [row.ticker for row in candidates]
    progress(f"{day}: {len(candidates)} candidate(s) (backfilled)")

    news = gather_news(picks, source=news_source, run_at=run_at, config=config,
                       company_names=company_names)

    pool_for_baseline = [t for t in survivors
                         if t not in set(picks) and screened[t].passed is False]
    baseline = sample_baseline(pool_for_baseline, len(picks), day)

    rows = [
        build_row(day=day, run_at=run_at, group=GROUP_CANDIDATE, pre=pre_by_ticker.get(row.ticker),
                  screen=row, headlines=news.get(row.ticker),
                  news_enabled=news_source is not None, reason="", config=config,
                  company=(company_names or {}).get(row.ticker, ""))
        for row in candidates
    ] + [
        build_row(day=day, run_at=run_at, group=GROUP_BASELINE, pre=pre_by_ticker.get(ticker),
                  screen=screened.get(ticker), headlines=None, news_enabled=False, reason="",
                  config=config, company=(company_names or {}).get(ticker, ""))
        for ticker in baseline
    ]
    for row in rows:
        row.backfill_gap_source = gap_source.get(row.ticker, "")
        row.backfill_volume_source = volume_source.get(row.ticker, "")
        # The pre-existing ``source`` column ("who verified this row") must agree with
        # backfill_gap_source, not default to build_row's yfinance-only assumption (it was
        # never handed an IBKRReading, so it cannot know this on its own).
        if row.backfill_gap_source == "ibkr-history":
            row.source = SOURCE_IBKR
        elif row.backfill_gap_source == "yfinance":
            row.source = SOURCE_YFINANCE
    status = PARTIAL if (field_unavailable or ibkr_down) else COMPLETE
    reasons = ([IBKR_WENT_AWAY_REASON] if ibkr_down else []) \
        + ([FIELD_UNAVAILABLE_REASON] if field_unavailable else [])
    if not rows:
        # Nothing was scoreable at all (no candidate, no rejected-and-comparable baseline
        # member) — still a real day, recorded with the status this loop actually found.
        rows = [status_row(day, run_at=run_at, status=status, reason="; ".join(reasons))]
    stamp_backfill(rows, status=status, reason="; ".join(reasons), at=now)

    if existing_path.exists():
        # GAP-BACKFILL-1 7b — audit the superseded attempt before overwriting it. Only ever
        # reached for a day whose OWN status was already non-complete (checked above), so a
        # genuinely complete file is never at risk here.
        partial_copy = existing_path.parent / f"{day.isoformat()}.partial.csv"
        existing_path.replace(partial_copy)
    write_day(day, rows, root=root)

    if picks and todoist is not None:
        outcome = deliver(day, rows, client=todoist, backfilled_on=now.astimezone(NY).date())
        if not outcome.sent and outcome.error:
            reasons.append(f"Todoist: {outcome.error}")

    return BackfillDayResult(day=day, status=status, n_candidates=len(candidates),
                             n_baseline=len(baseline), n_eligible=len(survivors), notes=reasons)


# =========================================================================== #
# the batch driver
# =========================================================================== #
@dataclass
class BackfillReport:
    filled: list = field(default_factory=list)          # list[BackfillDayResult]
    remaining: list = field(default_factory=list)        # list[date], beyond the cap

    def lines(self) -> list:
        out = [r.sentence() for r in self.filled]
        if self.remaining:
            shown = ", ".join(d.isoformat() for d in self.remaining[:10])
            more = " …" if len(self.remaining) > 10 else ""
            out.append(f"{len(self.remaining)} day(s) still missing (next run): {shown}{more}")
        if not self.filled and not self.remaining:
            out.append("catch-up: nothing missing")
        return out


def run_backfill(*, root: str | Path = DEFAULT_ROOT, config: GapConfig = DEFAULT_CONFIG,
                 pool: Sequence[str], pool_source: str = "", daily: DailySource,
                 intraday: IntradaySource, ibkr=None, news_source: Optional[NewsSource] = None,
                 company_names: Optional[dict] = None, todoist: Optional[TodoistClient] = None,
                 max_days: int = BACKFILL_MAX_DEFAULT, now: Optional[datetime] = None,
                 dry_run: bool = False, progress: Progress = _noop) -> BackfillReport:
    """Backfill every missing/partial day (GAP-BACKFILL-1 steps 1-3), oldest first, capped at
    ``max_days`` so a long absence catches up over a few runs rather than one very slow one.
    ``dry_run`` lists the missing days without fetching anything."""
    now = now or now_ny()
    missing = missing_days(root, now=now)
    todo, remaining = missing[:max_days], missing[max_days:]
    if dry_run or not todo:
        return BackfillReport(remaining=missing if dry_run else remaining)

    if ibkr is not None:
        from .ibkr import IBKRUnavailable
        try:
            ibkr.ping()
        except IBKRUnavailable as exc:
            progress(f"catch-up: IB Gateway unreachable ({exc}); backfilling on yfinance only")
            ibkr = None

    filled = []
    for day in todo:
        progress(f"backfilling {day.isoformat()}")
        result = backfill_one_day(day, root=root, config=config, pool=pool,
                                  pool_source=pool_source, daily=daily, intraday=intraday,
                                  ibkr=ibkr, news_source=news_source,
                                  company_names=company_names, todoist=todoist, now=now,
                                  progress=progress)
        filled.append(result)
        progress(result.sentence())
    return BackfillReport(filled=filled, remaining=remaining)
