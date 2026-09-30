"""Fake bar sources and fixtures for the GAP-LEDGER-1 tests.

Every gap-ledger test injects one of these: TEST-ISOLATION-1 refuses to construct
``gap_ledger.bars.YFinanceBars`` inside the suite, so there is no accidental route to the
network. These fakes also record what they were ASKED for, which is how the two-pass fetch
and the day cache are tested at all — the interesting property of both is the shape of the
request, not the shape of the answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from aristos_council.data.adapter import PriceBar
from aristos_council.gap_ledger.bars import IntradayBar, Quote
from aristos_council.gap_ledger.config import NY, at_ny


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def daily_series(*, end: date, sessions: int, close: float = 50.0,
                 volume: int = 2_000_000, step: float = 0.0) -> list[PriceBar]:
    """``sessions`` consecutive weekday bars ending the day BEFORE ``end``.

    Weekdays only, so a series never contains a Saturday the market was shut on — the
    pre-filter counts bars, and a test whose fixture contains weekends would be counting
    something the provider never serves.
    """
    bars: list[PriceBar] = []
    day = end - timedelta(days=1)
    while len(bars) < sessions:
        if day.weekday() < 5:
            price = close + step * len(bars)
            bars.append(PriceBar(day=day, open=price, high=price, low=price, close=price,
                                 adj_close=price, volume=volume))
        day -= timedelta(days=1)
    return sorted(bars, key=lambda b: b.day)


def intraday_window(day: date, *, start: time = time(4, 0), end: time = time(9, 0),
                    price: float = 50.0, volume_per_bar: int = 1_000,
                    every_minutes: int = 5) -> list[IntradayBar]:
    """A DENSE 5-minute tape across [start, end) on ``day``, all at ``price``.

    Dense is what makes it trustworthy: GAP-PRICE-TRUST-1 counts PRINTED INTERVALS, because
    the provider omits a five-minute slot in which nothing traded rather than forward-filling
    it. Use ``sparse_window`` for the untrustworthy shape.
    """
    out: list[IntradayBar] = []
    when = at_ny(day, start)
    stop = at_ny(day, end)
    while when < stop:
        out.append(IntradayBar(start=when, open=price, high=price, low=price,
                               close=price, volume=volume_per_bar))
        when += timedelta(minutes=every_minutes)
    return out


def sparse_window(day: date, *, price: float = 50.0, at: tuple = (time(4, 0), time(6, 30),
                                                                 time(8, 40)),
                  volume_per_bar: int = 0) -> list[IntradayBar]:
    """A few scattered prints and nothing else — the shape of a stray pre-market move.

    Measured on the live 2026-09-22 tape: XEL's +11% arrived as FIVE bars across five hours
    and LKQ's +26% as two, while every genuine mover printed 55-60 bars with all six slots of
    the final half hour filled. The prices differ from each other, which is exactly why
    counting distinct prices did not catch them.
    """
    return [IntradayBar(start=at_ny(day, moment), open=price, high=price, low=price,
                        close=price, volume=volume_per_bar) for moment in at]


def regular_session(day: date, *, price: float = 50.0,
                    volume_per_bar: int = 5_000) -> list[IntradayBar]:
    """A handful of regular-session bars, so ``session_dates`` counts ``day`` as a session."""
    out: list[IntradayBar] = []
    when = at_ny(day, time(9, 30))
    stop = at_ny(day, time(12, 0))
    while when < stop:
        out.append(IntradayBar(start=when, open=price, high=price, low=price,
                               close=price, volume=volume_per_bar))
        when += timedelta(minutes=5)
    return out


def prior_sessions(*, before: date, count: int, premarket_volume_per_bar: int = 1_000,
                   window_end: time = time(9, 0)) -> list[IntradayBar]:
    """``count`` prior weekday sessions, each with a pre-market window AND a regular session."""
    out: list[IntradayBar] = []
    day = before - timedelta(days=1)
    made = 0
    while made < count:
        if day.weekday() < 5:
            out += intraday_window(day, end=window_end,
                                   volume_per_bar=premarket_volume_per_bar)
            out += regular_session(day)
            made += 1
        day -= timedelta(days=1)
    return sorted(out, key=lambda b: b.start)


# --------------------------------------------------------------------------- #
# the fakes
# --------------------------------------------------------------------------- #
@dataclass
class FakeBars:
    """A bar source that answers from dicts and records every request it was handed."""

    daily: dict[str, list[PriceBar]] = field(default_factory=dict)
    intraday: dict[str, list[IntradayBar]] = field(default_factory=dict)
    quote_map: dict[str, Quote] = field(default_factory=dict)
    daily_calls: list[tuple[tuple[str, ...], date, date]] = field(default_factory=list)
    intraday_calls: list[tuple[tuple[str, ...], date, date]] = field(default_factory=list)
    quote_calls: list[tuple[str, ...]] = field(default_factory=list)

    def daily_bars(self, tickers, *, start: date, end: date):
        self.daily_calls.append((tuple(tickers), start, end))
        return {t: list(self.daily[t]) for t in tickers if t in self.daily}

    def intraday_bars(self, tickers, *, start: date, end: date):
        self.intraday_calls.append((tuple(tickers), start, end))
        out = {}
        for ticker in tickers:
            bars = self.intraday.get(ticker)
            if not bars:
                continue
            kept = [b for b in bars if start <= b.start.astimezone(NY).date() < end]
            if kept:
                out[ticker] = kept
        return out

    def quotes(self, tickers):
        self.quote_calls.append(tuple(tickers))
        return {t: self.quote_map[t] for t in tickers if t in self.quote_map}


@dataclass
class FakeNews:
    """Headlines from a dict. ``asked`` records the window, so the 18h lookback is testable."""

    by_ticker: dict[str, list] = field(default_factory=dict)
    asked: list[tuple[str, datetime, datetime]] = field(default_factory=list)

    def headlines(self, ticker: str, *, since: datetime, until: datetime):
        self.asked.append((ticker, since, until))
        return list(self.by_ticker.get(ticker, []))


@dataclass
class FakeTodoist:
    """A Todoist that records instead of posting.

    GAP-BACKFILL-1 — ``tasks`` entries carry an ``"id"`` so ``find_task``/``update_task`` can
    round-trip against them, exactly the shape ``deliver``'s dedup path (7c) exercises.
    """

    projects: dict[str, str] = field(default_factory=dict)
    created_projects: list[str] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    updated: list[dict] = field(default_factory=list)
    fail_on: str = ""

    def find_project(self, name: str):
        if self.fail_on == "find":
            raise RuntimeError("todoist down")
        return self.projects.get(name)

    def create_project(self, name: str) -> str:
        if self.fail_on == "create_project":
            raise RuntimeError("todoist refused")
        self.projects[name] = f"proj-{len(self.projects) + 1}"
        self.created_projects.append(name)
        return self.projects[name]

    def create_task(self, *, content: str, description: str, project_id: str) -> str:
        if self.fail_on == "create_task":
            raise RuntimeError("todoist refused the task")
        task_id = f"task-{len(self.tasks) + 1}"
        self.tasks.append({"id": task_id, "content": content, "description": description,
                           "project_id": project_id})
        return task_id

    def find_task(self, project_id: str, *, title_prefix: str):
        if self.fail_on == "find_task":
            raise RuntimeError("todoist down")
        for task in self.tasks:
            if task["project_id"] == project_id and task["content"].startswith(title_prefix):
                return task["id"]
        return None

    def update_task(self, task_id: str, *, content: str, description: str) -> None:
        if self.fail_on == "update_task":
            raise RuntimeError("todoist refused the update")
        for task in self.tasks:
            if task["id"] == task_id:
                task["content"], task["description"] = content, description
                self.updated.append(dict(task))
                return
        raise RuntimeError(f"no such task {task_id!r}")


@dataclass
class FakeIBKR:
    """A stand-in for ``gap_ledger.ibkr.IBKRBars`` (GAP-BACKFILL-1 — no FakeIBKR existed before
    this). Answers ``bars_for``/``intraday_bars``/``quote_for`` from dicts, exactly like
    ``FakeBars``, and records every ``bars_for`` request's ``(ticker, start, end, bar_size,
    duration)`` so a backfill's baseline-shaped call can be asserted on.

    ``unreachable`` raises ``IBKRUnavailable`` from every call, as a gateway that never answers
    (``ensure_gateway``'s retry loop exhausts against this). ``unreachable_after`` raises only
    once that many ``bars_for`` calls have already succeeded — the gateway going away MID-run
    (GAP-BACKFILL-1 7). A ticker simply absent from ``bars`` answers an empty list (IBKR has no
    history for it, not a failure) — the ordinary, expected backfill fallback case.
    """

    bars: dict = field(default_factory=dict)                   # {ticker: [IntradayBar, ...]}
    quote_map: dict = field(default_factory=dict)
    unreachable: bool = False
    unreachable_after: int = 0
    calls: list = field(default_factory=list)                  # (ticker, start, end, bar_size, duration)
    ping_calls: int = 0
    disconnected: bool = False
    connected: bool = True

    def _check(self) -> None:
        from aristos_council.gap_ledger.ibkr import IBKRUnavailable
        if self.unreachable or (self.unreachable_after and len(self.calls) >= self.unreachable_after):
            raise IBKRUnavailable("fake gateway unreachable")

    def ping(self) -> None:
        self.ping_calls += 1
        self._check()

    def bars_for(self, ticker: str, *, start, end, bar_size: str = "5 mins", duration=None):
        self._check()
        self.calls.append((ticker, start, end, bar_size, duration))
        return list(self.bars.get(ticker, []))

    def intraday_bars(self, tickers, *, start, end):
        out = {}
        for ticker in tickers:
            bars = self.bars_for(ticker, start=start, end=end)
            if bars:
                out[ticker] = bars
        return out

    def quote_for(self, ticker: str):
        self._check()
        from aristos_council.gap_ledger.bars import Quote
        return self.quote_map.get(ticker, Quote())

    def quotes(self, tickers):
        return {t: self.quote_for(t) for t in tickers if t in self.quote_map}

    def disconnect(self) -> None:
        self.disconnected = True


@dataclass
class FakeRunner:
    """A model that answers with whatever it was told to, and records the prompt."""

    answer: object = None
    raise_with: str = ""
    calls: list[tuple[str, str]] = field(default_factory=list)

    def invoke(self, system: str, user: str):
        self.calls.append((system, user))
        if self.raise_with:
            raise RuntimeError(self.raise_with)
        return self.answer
