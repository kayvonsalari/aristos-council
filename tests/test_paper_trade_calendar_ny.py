"""PAPER-TRADE-1 — the standalone NYSE holiday calendar (independent of
``gap_ledger.nyse_calendar`` — see the package docstring)."""
from __future__ import annotations

from datetime import date

from aristos_council.paper_trade.calendar_ny import is_trading_day, nyse_holidays


def test_thanksgiving_2026_is_closed_the_days_around_it_are_not():
    assert is_trading_day(date(2026, 11, 26)) is False      # Thanksgiving
    assert is_trading_day(date(2026, 11, 25)) is True
    assert is_trading_day(date(2026, 11, 27)) is True


def test_a_weekend_is_never_a_trading_day():
    assert is_trading_day(date(2026, 9, 26)) is False       # a Saturday
    assert is_trading_day(date(2026, 9, 27)) is False       # a Sunday


def test_an_ordinary_weekday_trades():
    assert is_trading_day(date(2026, 9, 29)) is True


def test_a_saturday_holiday_observes_the_preceding_friday():
    # Independence Day 2026 falls on a Saturday; NYSE observes it Friday 3 July.
    assert date(2026, 7, 4).weekday() == 5                 # confirms the fixture's premise
    assert date(2026, 7, 3) in nyse_holidays(2026)
    assert is_trading_day(date(2026, 7, 3)) is False        # the observed holiday (a Friday)
    assert is_trading_day(date(2026, 7, 2)) is True         # the ordinary Thursday before it
