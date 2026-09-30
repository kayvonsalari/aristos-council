"""GAP-BACKFILL-1 — which days the NYSE traded.

The rest of this repo deliberately AVOIDS modelling exchange calendars (see
``pipeline.PRICE_STALE_DAYS``'s reasoning, and ``bars.lookback_start``'s generous
calendar-day windows): a wrong holiday would be a false alarm, and a fuzzy lookback window
costs nothing when the caller only needs "enough" history.

Missing-day DETECTION is a different problem. "List every trading day between two dates and
find the ones with no file" needs to know a weekend and a holiday are NOT missing days —
get that wrong and every Thanksgiving becomes a phantom gap to chase. So this ONE module
builds an actual NYSE holiday calendar, used ONLY by ``backfill.py``'s detection step; it is
not a general-purpose calendar and nothing else in this package (or this repo) should import
it for a fuzzy lookback where the existing idiom already works.

Covers the standard, permanent NYSE closures: New Year's Day, Martin Luther King Jr. Day,
Washington's Birthday, Good Friday, Memorial Day, Juneteenth (a NYSE holiday since 2022),
Independence Day, Labor Day, Thanksgiving, Christmas — with the standard "falls on Saturday
-> observed Friday, falls on Sunday -> observed Monday" rule. It does NOT know about one-off
closures (a September 2001-style closure, a funeral, a storm) — those are rare, historical,
and outside this project's window; a day wrongly treated as a trading day here just means a
harmless attempted backfill that finds nothing to log, never a silent skip.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .config import MARKET_CLOSE, NY


def _easter(year: int) -> date:
    """Easter Sunday, Gregorian calendar (the Anonymous/Meeus algorithm) — pure arithmetic,
    no dependency. Good Friday is two days before it."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th (1-based) given weekday (Monday=0) of ``month``/``year``."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The LAST given weekday of ``month``/``year`` (Memorial Day: the last Monday of May)."""
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    """NYSE's weekend rule: a holiday landing on Saturday is observed the preceding Friday;
    on Sunday, the following Monday."""
    if day.weekday() == 5:              # Saturday
        return day - timedelta(days=1)
    if day.weekday() == 6:               # Sunday
        return day + timedelta(days=1)
    return day


def nyse_holidays(year: int) -> set:
    """Every NYSE closure date in ``year`` (weekday closures the exchange observes; a holiday
    landing on a weekend is mapped to its observed weekday, which may fall in the neighbouring
    year at the edges — e.g. a Dec 31 New Year's Day observance)."""
    out = {
        _observed(date(year, 1, 1)),                          # New Year's Day
        _nth_weekday(year, 1, 0, 3),                           # MLK Day - 3rd Monday of Jan
        _nth_weekday(year, 2, 0, 3),                           # Washington's Birthday
        _easter(year) - timedelta(days=2),                     # Good Friday
        _last_weekday(year, 5, 0),                             # Memorial Day
        _observed(date(year, 7, 4)),                           # Independence Day
        _nth_weekday(year, 9, 0, 1),                           # Labor Day
        _nth_weekday(year, 11, 3, 4),                          # Thanksgiving - 4th Thursday
        _observed(date(year, 12, 25)),                         # Christmas Day
    }
    if year >= 2022:                                           # Juneteenth: an NYSE holiday from 2022
        out.add(_observed(date(year, 6, 19)))
    # An observance can land in the next calendar year (New Year's Day observed Dec 31) or the
    # previous one (a look the other way is never needed in practice, but be exact): pull in the
    # neighbouring years' New Year's Day observance too, cheaply.
    out.add(_observed(date(year + 1, 1, 1)))
    return out


def is_trading_day(day: date) -> bool:
    """Weekday, and not an NYSE holiday."""
    if day.weekday() >= 5:
        return False
    return day not in nyse_holidays(day.year)


def trading_days_between(start: date, end: date) -> list:
    """Every NYSE trading day from ``start`` to ``end``, INCLUSIVE, ascending. Empty if
    ``start > end``."""
    if start > end:
        return []
    out, d = [], start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def previous_trading_day(day: date) -> date:
    """The most recent trading day strictly before ``day``."""
    d = day - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def last_completed_trading_day(now: datetime) -> date:
    """The most recent NYSE trading day whose regular session has already closed, as of
    ``now`` (any timezone; converted to New York). A trading day that has not yet reached
    its close does not count as completed — the "up to and including the last completed
    trading day" boundary GAP-BACKFILL-1 detection uses."""
    now_ny = now.astimezone(NY)
    today = now_ny.date()
    if is_trading_day(today) and now_ny.time() >= MARKET_CLOSE:
        return today
    # Not yet closed today, or today is not a trading day at all - either way, the most recent
    # trading day strictly before today is the answer (``previous_trading_day`` walks back from
    # ``today - 1`` regardless of whether ``today`` itself trades).
    return previous_trading_day(today)
