"""GAP-LEDGER-1 — the one run entry, shared by the CLI and the tests.

``run_screen`` is the whole morning: pool, pre-filter, gap, relative volume, spread, news,
optional one-line reason, CSV, Todoist. It is ONE function with injected sources because
the alternative — the CLI orchestrating and something else orchestrating slightly
differently — is a drift this repo has already paid for once (the v2 rank pipeline was
split between ``examples/run_pipeline.py`` and the UI until ``run_rank_pipeline`` merged
them). ``format_report`` is what the CLI prints, so there is one description of a run.

**Two fetch passes, on purpose.** The brief's filters are a gap AND a relative volume, and
the relative volume needs twenty sessions of 5-minute bars per name. Fetching that for
every liquid US stock would be tens of thousands of bar-days for a list that ends up
holding a handful of names. So:

  pass A  today's 5-minute bars only, for every pre-filter survivor → the gap
  pass B  twenty sessions of 5-minute bars, for the gappers only → the relative volume

Pass B's window includes today, so the screen sees one continuous series and the maths is
identical to a single-pass run. What changes is the bill, by about two orders of magnitude.
The ORDER of the filters is unchanged: a name is a candidate only if it clears both.

**The control group** is drawn from names that passed step 1 and not step 2, restricted to
those whose gap could actually be READ. A name that did not trade pre-market has no
direction, so it can never be scored either way — including it would pad the file with rows
the scorecard must skip, and would make the control group less comparable to the candidates
rather than more (every candidate, by definition, traded pre-market). The restriction is
recorded in the result so the draw is never a mystery.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, Sequence

from .bars import DailyCache, DailySource, IntradaySource, lookback_start
from .config import (DEFAULT_CONFIG, DEFAULT_RUN_TIME, DEFAULT_ROOT, GapConfig, at_ny,
                     now_ny)
from .explain import NO_REASON, ExplainOutcome, explanations
from .ledger import (GROUP_BASELINE, GROUP_CANDIDATE, LedgerRow, et_stamp,
                     sample_baseline, write_day)
from .news import Headline, MatchedNews, NewsSource, gather_news
from .screen import SPREAD_HISTORICAL, SPREAD_UNKNOWN, ScreenRow, screen_one
from .todoist import DeliveryOutcome, TodoistClient, deliver
from .universe import (NO_DAILY_BARS, PreFilterResult, PreFilterRow, pre_filter,
                       split_common_stock)

_log = logging.getLogger(__name__)

Progress = Callable[[str], None]

# The cache lives beside the day's CSVs, one directory down: they are both local data with
# the same lifetime, and one gitignore entry covers them.
CACHE_DIR = "cache/daily"


def _noop(_message: str) -> None:
    pass


# --------------------------------------------------------------------------- #
# the result
# --------------------------------------------------------------------------- #
def premarket_volume_unavailable(rows) -> bool:
    """Did the provider serve NO pre-market volume at all, for any screened name?

    The distinction this draws is between one name with a quiet morning and a provider that
    does not publish the figure. Probed on 2026-09-22: yfinance is the latter — every
    extended-hours bar comes back with ``Volume == 0``. Emitting the same per-name
    abstention for all seven gappers reads like seven data faults; saying it ONCE, at the
    run, says the true thing.

    True only when at least one name was measured AND every measured name showed zero
    pre-market shares today and a zero baseline median. One busy name anywhere disproves it.
    """
    measured = [r for r in rows if r.premarket_volume is not None]
    if not measured:
        return False
    return all((row.premarket_volume or 0) == 0
               and not (row.baseline_median_volume or 0) for row in measured)


@dataclass
class RunResult:
    """Everything one morning produced. ``excluded`` names every name that fell out, and why."""

    day: date
    run_at: datetime
    config: GapConfig = DEFAULT_CONFIG
    pool_size: int = 0
    pool_source: str = ""
    # GAP-UNIVERSE-1 — warrants, units, rights and preferreds the index calls "Common
    # Stock". Dropped before any fetch, and COUNTED so the drop is visible rather than a
    # pool that quietly shrank.
    not_common_stock: list[tuple[str, str]] = field(default_factory=list)
    # How many names the provider served nothing for. Its own per-ticker ERROR lines are
    # suppressed (``bars.quiet_yfinance``), so this is where that information lives.
    no_provider_data: int = 0
    pre_filtered: PreFilterResult = field(default_factory=PreFilterResult)
    gapped: int = 0
    screened: dict[str, ScreenRow] = field(default_factory=dict)
    candidates: list[ScreenRow] = field(default_factory=list)
    baseline: list[str] = field(default_factory=list)
    baseline_pool: int = 0
    rows: list[LedgerRow] = field(default_factory=list)
    news: dict[str, MatchedNews] = field(default_factory=dict)
    news_enabled: bool = True
    explain: ExplainOutcome = field(default_factory=lambda: ExplainOutcome({}))
    delivery: DeliveryOutcome = field(default_factory=DeliveryOutcome)
    csv_path: Optional[Path] = None
    # Set when the provider served no pre-market volume for anything (see
    # ``premarket_volume_unavailable``). A RUN-level fact, said once.
    volume_note: str = ""

    @property
    def excluded(self) -> list[tuple[str, str]]:
        """Every name that did not become a candidate, with the reason it did not.

        Ordered by stage: the pre-filter first, then the screen. A name absent from BOTH
        lists never existed in the pool, which is the only way to be absent from this.
        """
        out = [(row.ticker, row.reason) for row in self.pre_filtered.excluded]
        out += [(row.ticker, row.reason) for row in self.screened.values()
                if row.passed is not True]
        return out

    @property
    def not_evaluated(self) -> list[tuple[str, str]]:
        """Names the screen could NOT evaluate (``passed is None``) — a missing reading, not
        a rejection. Kept separate because conflating the two is this repo's oldest bug
        class (rule 3)."""
        return [(row.ticker, row.reason) for row in self.screened.values()
                if row.passed is None]


# --------------------------------------------------------------------------- #
# assembling a ledger row
# --------------------------------------------------------------------------- #
def _headline_fields(news: Optional[MatchedNews], *, enabled: bool) -> dict:
    """The newest MATCHED headline, flattened into the row.

    GAP-NEWS-MATCH-1: only a story attributed to this name reaches the headline columns.
    Stories the provider returned but that are about someone else are counted and kept in
    the ``related_*`` columns — evidence about the tagging, never printed as this name's
    news. ``news_found`` is a MARK, never a drop.
    """
    if not enabled:
        return {"news_found": "news not fetched", "headline_count": None}
    news = news or MatchedNews()
    fields = {"news_found": news.found, "news_match": news.how,
              "headline_count": len(news.matched), "related_count": len(news.related)}
    if news.matched:
        top = news.matched[0]
        fields.update(headline=top.title, news_source=top.source,
                      news_published_et=top.published_ny, news_link=top.link)
    if news.related:
        fields.update(related_headline=news.related[0].title,
                      related_link=news.related[0].link)
    return fields


def build_row(*, day: date, run_at: datetime, group: str, pre: Optional[PreFilterRow],
              screen: Optional[ScreenRow], headlines: Optional[MatchedNews],
              news_enabled: bool, reason: str, config: GapConfig) -> LedgerRow:
    """One CSV row out of the readings. Computes nothing — every number is copied."""
    row = LedgerRow(date=day.isoformat(), group=group, run_at_et=et_stamp(run_at),
                    reason=reason, **config.as_record())
    if pre is not None:
        row.ticker = pre.ticker
        row.previous_close = pre.previous_close
        row.previous_session = (pre.previous_session.isoformat()
                                if pre.previous_session else "")
        row.average_volume = pre.average_volume
        row.history_days = pre.history_days
    if screen is not None:
        row.ticker = screen.ticker
        row.window_start_et = et_stamp(screen.window_start)
        row.window_end_et = et_stamp(screen.window_end)
        row.premarket_price = screen.premarket_price
        row.gap_pct = screen.gap
        row.premarket_volume = screen.premarket_volume
        row.baseline_median_volume = screen.baseline_median_volume
        row.baseline_sessions = screen.baseline_sessions
        row.relative_volume = screen.relative_volume
        row.relative_volume_note = screen.relative_volume_note
        row.spread_pct = screen.spread
        row.spread_note = screen.spread_note
        row.screen_passed = "" if screen.passed is None else str(screen.passed).lower()
        row.screen_note = screen.reason
        if screen.previous_close is not None:
            row.previous_close = screen.previous_close
    for key, value in _headline_fields(headlines, enabled=news_enabled).items():
        setattr(row, key, value)
    return row


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
def run_screen(*, pool: Sequence[str], pool_source: str, daily: DailySource,
               intraday: IntradaySource, day: Optional[date] = None,
               run_at: Optional[datetime] = None, config: GapConfig = DEFAULT_CONFIG,
               root: str | Path = DEFAULT_ROOT, news_source: Optional[NewsSource] = None,
               company_names: Optional[dict] = None,
               explain_runner=None, todoist: Optional[TodoistClient] = None,
               write: bool = True, refresh: bool = False, live: Optional[bool] = None,
               progress: Progress = _noop) -> RunResult:
    """One pre-market run. Every external dependency is injected; nothing here is a factory.

    ``live`` says whether this run is screening THIS morning. It decides one thing: whether
    the bid/ask is read at all. A book is a snapshot of now, so a backfill of a past date
    cannot have one — reading today's would stamp an anachronism onto a historical row. Left
    as None it is inferred from the market's calendar, which is what a scheduled 09:00 run
    wants and what a ``--date`` backfill wants too.
    """
    day = day or now_ny().date()
    run_at = run_at or at_ny(day, DEFAULT_RUN_TIME)
    live = (day == now_ny().date()) if live is None else live
    spread_note = SPREAD_UNKNOWN if live else SPREAD_HISTORICAL
    offered = sorted({t.strip().upper() for t in pool if t and t.strip()})
    # GAP-UNIVERSE-1 — before any fetch, because the point is not to ask the provider
    # about a warrant at all.
    names, not_common = split_common_stock(offered)
    result = RunResult(day=day, run_at=run_at, config=config, pool_size=len(offered),
                       pool_source=pool_source, news_enabled=news_source is not None,
                       not_common_stock=not_common)
    if not_common:
        progress(f"{len(not_common)} of {len(offered)} are not common stock "
                 f"(warrants, units, rights, preferreds) — not screened")

    # -- step 1 ------------------------------------------------------------- #
    progress(f"pre-filtering {len(names)} names on yesterday's bars")
    cache = DailyCache(Path(root) / CACHE_DIR, source=daily)
    history_start = lookback_start(day, config.min_history_days)
    daily_bars = cache.daily_bars(names, start=history_start, end=day + timedelta(days=1),
                                  as_of=day, refresh=refresh)
    result.pre_filtered = pre_filter(names, daily_bars, as_of=day, config=config)
    survivors = result.pre_filtered.tickers
    result.no_provider_data = sum(1 for row in result.pre_filtered.excluded
                                  if row.reason == NO_DAILY_BARS)
    if result.no_provider_data:
        # ONE line, in place of the provider's per-ticker ERROR storm (GAP-UNIVERSE-1).
        progress(f"{result.no_provider_data} names returned no data")
    pre_by_ticker = result.pre_filtered.by_ticker()
    progress(f"{len(survivors)} of {len(names)} cleared price, volume and history")
    if not survivors:
        return _finish(result, root=root, write=write, todoist=todoist, progress=progress)

    # -- step 2, pass A: the gap, on today's bars only ---------------------- #
    progress(f"reading today's pre-market bars for {len(survivors)} names")
    today_bars = intraday.intraday_bars(survivors, start=day, end=day + timedelta(days=1))
    first_pass = {
        ticker: screen_one(ticker, bars=today_bars.get(ticker, []),
                           previous_close=pre_by_ticker[ticker].previous_close,
                           as_of=day, run_at=run_at, spread_unknown_note=spread_note,
                           config=config)
        for ticker in survivors
    }
    gappers = [t for t, row in first_pass.items()
               if row.gap is not None and abs(row.gap) >= config.min_abs_gap]
    result.gapped = len(gappers)
    progress(f"{len(gappers)} gapped at least {config.min_abs_gap * 100:.1f}%")

    # -- step 2, pass B: the relative volume, on twenty sessions ------------ #
    result.screened = dict(first_pass)
    if gappers:
        progress(f"reading {config.relative_volume_days} sessions of pre-market history "
                 f"for {len(gappers)} names")
        window_start = lookback_start(day, config.relative_volume_days)
        history = intraday.intraday_bars(gappers, start=window_start,
                                         end=day + timedelta(days=1))
        # A quote only exists for a run screening this morning — see ``live`` above.
        quotes = intraday.quotes(gappers) if live else {}
        for ticker in gappers:
            result.screened[ticker] = screen_one(
                ticker, bars=history.get(ticker, today_bars.get(ticker, [])),
                previous_close=pre_by_ticker[ticker].previous_close, as_of=day,
                run_at=run_at, quote=quotes.get(ticker),
                spread_unknown_note=spread_note, config=config)
    result.candidates = [result.screened[t] for t in gappers
                         if result.screened[t].passed is True]
    if gappers and premarket_volume_unavailable([result.screened[t] for t in gappers]):
        result.volume_note = (
            "this provider served NO pre-market volume for any name, so the relative-volume "
            "leg could not be applied — these names were selected on the GAP ALONE")
        progress(f"note: {result.volume_note}")
    progress(f"{len(result.candidates)} candidate(s) after relative volume")

    # -- step 3 ------------------------------------------------------------- #
    picks = [row.ticker for row in result.candidates]
    if picks and news_source is not None:
        progress(f"fetching {config.news_lookback_hours}h of news for {len(picks)} names")
    result.news = gather_news(picks, source=news_source, run_at=run_at, config=config,
                              company_names=company_names)
    if picks and explain_runner is not None:
        progress("asking for one line per name")
    # Only MATCHED stories reach the model: a wrong reason beside a real gap is worse than
    # no reason, because it gets believed (GAP-NEWS-MATCH-1).
    result.explain = explanations(
        picks, {t: list(news.matched) for t, news in result.news.items()},
        runner=explain_runner)

    # -- step 4, the control group ----------------------------------------- #
    # Step-1 survivors that did not become candidates AND whose gap could be read — see
    # the module docstring for why an unreadable gap is not a usable control.
    pool_for_baseline = [t for t in survivors
                         if t not in set(picks)
                         and result.screened[t].gap is not None]
    result.baseline_pool = len(pool_for_baseline)
    result.baseline = sample_baseline(pool_for_baseline, len(picks), day)

    result.rows = [
        build_row(day=day, run_at=run_at, group=GROUP_CANDIDATE,
                  pre=pre_by_ticker.get(row.ticker), screen=row,
                  headlines=result.news.get(row.ticker),
                  news_enabled=result.news_enabled,
                  reason=result.explain.lines.get(row.ticker, ""), config=config)
        for row in result.candidates
    ] + [
        build_row(day=day, run_at=run_at, group=GROUP_BASELINE,
                  pre=pre_by_ticker.get(ticker), screen=result.screened.get(ticker),
                  headlines=None, news_enabled=False, reason="", config=config)
        for ticker in result.baseline
    ]
    return _finish(result, root=root, write=write, todoist=todoist, progress=progress)


def _finish(result: RunResult, *, root: str | Path, write: bool,
            todoist: Optional[TodoistClient], progress: Progress) -> RunResult:
    """Write the day's CSV and deliver it. Both are IO at the edge of the run."""
    if write:
        result.csv_path = write_day(result.day, result.rows, root=root)
        progress(f"wrote {len(result.rows)} rows to {result.csv_path}")
    result.delivery = deliver(result.day, result.rows, client=todoist)
    return result


# --------------------------------------------------------------------------- #
# the report — one description of a run, printed by the CLI
# --------------------------------------------------------------------------- #
def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def _times(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.2f}x"


def _kinds(dropped: Sequence[tuple[str, str]]) -> str:
    """``"73 warrant, 57 unit, …"`` — what the not-common-stock drop actually consisted of.

    The count alone would be a number nobody can check; the breakdown is what makes an
    over-eager pattern visible (GAP-UNIVERSE-1).
    """
    counts: dict[str, int] = {}
    for _ticker, kind in dropped:
        counts[kind] = counts.get(kind, 0) + 1
    return ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items()))


def format_report(result: RunResult, *, max_excluded: int = 15) -> str:
    """The run, in plain text. What the CLI prints and what the done-report pastes."""
    cfg = result.config
    lines = [
        f"Gap Ledger — {result.day.isoformat()}, run {et_stamp(result.run_at)} "
        f"(New York)",
        f"Verdict: deterministic screen. Reason line: "
        + ("LLM (non-judging)" if result.explain.called else "off"),
        "",
        f"Pool: {result.pool_size} names from {result.pool_source}."
        + (f" {len(result.not_common_stock)} not common stock "
           f"({_kinds(result.not_common_stock)}) — not screened."
           if result.not_common_stock else ""),
        f"Step 1 — price >= ${cfg.min_price:,.0f}, "
        f"{cfg.avg_volume_days}-session volume >= {cfg.min_avg_volume:,.0f}, "
        f">= {cfg.min_history_days} trading days: "
        f"{len(result.pre_filtered.passed)} passed, "
        f"{len(result.pre_filtered.excluded)} excluded"
        + (f" ({result.no_provider_data} of them returned no data)."
           if result.no_provider_data else "."),
        f"Step 2 — |gap| >= {cfg.min_abs_gap * 100:.1f}% and relative pre-market volume "
        f">= {cfg.min_relative_volume:.1f}x: {result.gapped} gapped, "
        f"{len(result.candidates)} candidate(s).",
    ]
    if result.volume_note:
        # Said ONCE, at the top, where it cannot be missed: an absent section or a repeated
        # per-name note would both leave the reader guessing what the list actually means.
        lines.append(f"!! DATA GAP — {result.volume_note}.")
    lines.append("")

    if result.candidates:
        lines.append("CANDIDATES")
        for row in result.candidates:
            news = result.news.get(row.ticker) or MatchedNews()
            headlines = list(news.matched)
            mark = "news not fetched" if not result.news_enabled else news.found
            if news.related and not news.matched:
                mark += f" ({len(news.related)} related)"
            volume = (_times(row.relative_volume) if row.relative_volume is not None
                      else "unavailable")
            lines.append(f"  {row.ticker:<8} gap {_pct(row.gap):>9}  "
                         f"rel.vol {volume:>11}  "
                         f"{row.spread_note}  {mark}")
            # With --explain off there is no per-row line at all; the header says so once.
            reason = result.explain.lines.get(row.ticker, "")
            if reason:
                lines.append(f"           {reason}")
            if headlines:
                top = headlines[0]
                lines.append(f"           {top.title}")
                lines.append(f"           {top.link}")
    else:
        lines.append("CANDIDATES: none today.")
    if result.explain.note:
        lines.append(f"  note: {result.explain.note}")
    lines.append("")

    not_evaluated = result.not_evaluated
    if not_evaluated:
        lines.append(f"NOT EVALUATED ({len(not_evaluated)}) — a missing reading, not a "
                     f"rejection")
        for ticker, reason in not_evaluated[:max_excluded]:
            lines.append(f"  {ticker:<8} {reason}")
        if len(not_evaluated) > max_excluded:
            lines.append(f"  … and {len(not_evaluated) - max_excluded} more")
        lines.append("")

    rejected = [(t, r) for t, r in result.excluded
                if (t, r) not in set(not_evaluated)]
    if rejected:
        lines.append(f"EXCLUDED ({len(rejected)})")
        for ticker, reason in rejected[:max_excluded]:
            lines.append(f"  {ticker:<8} {reason}")
        if len(rejected) > max_excluded:
            lines.append(f"  … and {len(rejected) - max_excluded} more")
        lines.append("")

    lines.append(f"CONTROL GROUP: {len(result.baseline)} name(s) drawn with seed "
                 f"{result.day.strftime('%Y%m%d')} from {result.baseline_pool} that "
                 f"passed step 1 and not step 2"
                 + (f" — {', '.join(result.baseline)}" if result.baseline else ""))
    if result.csv_path:
        lines.append(f"Logged: {result.csv_path}")
    lines.append(result.delivery.sentence())
    lines.append("")
    lines.append("No recommendation. The screen selects on price action and volume only.")
    return "\n".join(lines)
