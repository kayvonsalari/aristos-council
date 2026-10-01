"""PAPER-TRADE-1 — which days NYSE/Nasdaq trade.

Deliberately NOT imported from ``aristos_council.gap_ledger.nyse_calendar`` — this package
imports nothing from ``gap_ledger`` at all (see the package docstring). The rule this
implements is the same well-known, public one Gap Ledger's own calendar states: the standard
permanent closures (New Year's Day, MLK Day, Washington's Birthday, Good Friday, Memorial
Day, Juneteenth since 2022, Independence Day, Labor Day, Thanksgiving, Christmas), with the
"falls on Saturday -> observed Friday, falls on Sunday -> observed Monday" rule. Pure
arithmetic, no dependency, no one-off closures (none fall in this project's window).
"""
from __future__ import annotations

from datetime import date, timedelta


def _easter(year: int) -> date:
    """Easter Sunday, Gregorian calendar (the Anonymous/Meeus algorithm)."""
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
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    if day.weekday() == 5:              # Saturday -> preceding Friday
        return day - timedelta(days=1)
    if day.weekday() == 6:               # Sunday -> following Monday
        return day + timedelta(days=1)
    return day


def nyse_holidays(year: int) -> set:
    out = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),                           # MLK Day
        _nth_weekday(year, 2, 0, 3),                           # Washington's Birthday
        _easter(year) - timedelta(days=2),                     # Good Friday
        _last_weekday(year, 5, 0),                             # Memorial Day
        _observed(date(year, 7, 4)),                           # Independence Day
        _nth_weekday(year, 9, 0, 1),                           # Labor Day
        _nth_weekday(year, 11, 3, 4),                          # Thanksgiving
        _observed(date(year, 12, 25)),                         # Christmas Day
    }
    if year >= 2022:
        out.add(_observed(date(year, 6, 19)))                 # Juneteenth
    out.add(_observed(date(year + 1, 1, 1)))                  # a Dec-31 observance
    return out


def is_trading_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    return day not in nyse_holidays(day.year)
