"""GAP-LEDGER-1 step 4b — what the day actually did, filled in after the close.

Four readings per logged name, candidates and control group alike: the regular-session
open, the price at 10:00 ET, the price at 11:30 ET, and the regular-session close. They
are what turns a list of picks into a record that can be scored.

Three disciplines:

* **Every logged name is filled, not just the picks.** A checkpoint filled for candidates
  and skipped for the control group would produce a scorecard that compares the screen
  against nothing.
* **Same-day RAW prices.** The open, the checkpoints and the close all come from the same
  session, so no dividend or split factor differs between them and adjusting would only
  introduce a difference where there is none. (``previous_close`` in step 2 IS adjusted,
  because it is a PRIOR session's close and a corporate action between then and now would
  otherwise read as a gap.)
* **A missing reading stays missing.** An unfilled checkpoint is written empty with a note
  saying why. It is never filled with the nearest thing to hand, because the scorecard
  counts continuations and a substituted price would be counted as one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional, Sequence

from ..data.adapter import PriceBar
from .bars import IntradayBar
from .config import (CHECKPOINTS, DEFAULT_CONFIG, DEFAULT_ROOT, MARKET_CLOSE, MARKET_OPEN,
                     NY, GapConfig, at_ny, now_ny)
from .ledger import LedgerRow, et_stamp

_log = logging.getLogger(__name__)

# How far past a checkpoint a bar may start and still be that checkpoint's price. Three
# 5-minute bars: enough to survive a thin tape or a provider that skips an empty bar, small
# enough that "the price at 10:00" is still about 10:00.
CHECKPOINT_TOLERANCE = timedelta(minutes=15)


class SessionNotClosed(RuntimeError):
    """Asked to fill outcomes for a session that has not finished. Refused rather than
    filled with a part-formed close, which would be indistinguishable from a real one."""


def session_is_closed(day: date, now: datetime) -> bool:
    """Has ``day``'s regular session finished, on the market's clock?"""
    return now.astimezone(NY) >= at_ny(day, MARKET_CLOSE)


# --------------------------------------------------------------------------- #
# the readings
# --------------------------------------------------------------------------- #
def daily_reading(bars: Sequence[PriceBar], day: date) -> Optional[PriceBar]:
    """``day``'s daily bar, or None when the provider has none for that date."""
    for bar in bars:
        if bar.day == day:
            return bar
    return None


def price_at(bars: Sequence[IntradayBar], day: date, moment: time, *,
             tolerance: timedelta = CHECKPOINT_TOLERANCE) -> Optional[float]:
    """The traded price at ``moment``: the OPEN of the first bar starting at or after it.

    The open rather than the close, because "the price at 10:00" is the price at 10:00 and
    the close of the 10:00 bar is the price at 10:05. Bounded by ``tolerance`` so a thin
    name whose next print is an hour later reads as MISSING rather than as a 10:00 price
    that was nothing of the sort.
    """
    target = at_ny(day, moment)
    limit = target + tolerance
    for bar in sorted(bars, key=lambda b: b.start):
        start = bar.start.astimezone(NY)
        if start < target:
            continue
        if start >= limit:
            return None
        if MARKET_OPEN <= start.time() < MARKET_CLOSE:
            return bar.open
    return None


# --------------------------------------------------------------------------- #
# filling a row
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FillReport:
    """What one pass over a day filled. Printed by the CLI so a silent no-op is impossible."""

    day: date
    rows: int = 0
    filled: int = 0
    incomplete: int = 0
    notes: tuple[str, ...] = ()

    def sentence(self) -> str:
        head = (f"{self.day}: filled outcomes for {self.filled} of {self.rows} logged "
                f"names")
        if self.incomplete:
            head += f"; {self.incomplete} still incomplete"
        return head + "."


def fill_row(row: LedgerRow, *, daily: Sequence[PriceBar], intraday: Sequence[IntradayBar],
             day: date, now: datetime, config: GapConfig = DEFAULT_CONFIG) -> LedgerRow:
    """One row, filled where the data allows. Returns a NEW row; the input is untouched."""
    bar = daily_reading(daily, day)
    missing: list[str] = []
    open_price = bar.open if bar is not None else None
    close_price = bar.close if bar is not None else None
    if bar is None:
        missing.append("no daily bar for the session")

    prices: list[Optional[float]] = []
    for moment in CHECKPOINTS:
        value = price_at(intraday, day, moment) if intraday else None
        if value is None:
            missing.append(f"no print within {int(CHECKPOINT_TOLERANCE.total_seconds() // 60)}"
                           f" minutes of {moment.strftime('%H:%M')} ET")
        prices.append(value)
    while len(prices) < 2:                               # a configured third checkpoint
        prices.append(None)                              # would need a column of its own

    filled = LedgerRow(**{**row.__dict__})
    filled.open_price = open_price
    filled.price_1000 = prices[0]
    filled.price_1130 = prices[1]
    filled.close_price = close_price
    # The diagnostic is derived from the row, so it is filled last and cannot disagree with
    # the columns it is computed from.
    filled.premarket_vs_open = premarket_vs_open(filled)
    filled.outcome_note = "; ".join(missing)
    filled.outcomes_filled_at_et = et_stamp(now)
    return filled


def premarket_vs_open(row: LedgerRow) -> Optional[float]:
    """The pre-market price against the 09:30 open, as a fraction — the tuning diagnostic.

    GAP-PRICE-TRUST-1. This is the number that says how often the pre-market print was junk:
    a real gap opens near where it printed (AMD, 2026-09-21: 579.36 pre-market, 583.88 open,
    +0.78%), and a stray print does not (ALNY, 2026-09-22: 21% away). Without it the trust
    thresholds would be tuned on impressions.

    Signed so the direction is legible: POSITIVE means the pre-market print was ABOVE where
    the session actually opened. None when either side is missing — never 0.0, which would
    read as a perfect agreement that was never measured.
    """
    if row.premarket_price is None or row.open_price is None or row.open_price <= 0:
        return None
    return (row.premarket_price - row.open_price) / row.open_price


def is_complete(row: LedgerRow) -> bool:
    """Does this row carry all four readings? Used to count, and to skip a refill."""
    return None not in (row.open_price, row.price_1000, row.price_1130, row.close_price)


# --------------------------------------------------------------------------- #
# a whole day
# --------------------------------------------------------------------------- #
def fill_day(day: date, *, daily, intraday, root: str | Path = DEFAULT_ROOT,
             now: Optional[datetime] = None, config: GapConfig = DEFAULT_CONFIG,
             write: bool = True) -> FillReport:
    """Fill every logged name for ``day``, candidates and control group alike.

    Refuses a session that has not closed (``SessionNotClosed``) rather than writing a
    part-formed close, and refuses nothing else: a day with no CSV simply reports zero rows.
    """
    from .ledger import read_day, write_day

    now = (now or now_ny()).astimezone(NY)
    if not session_is_closed(day, now):
        raise SessionNotClosed(
            f"{day} has not closed yet (it is {now.strftime('%H:%M')} ET; the session ends "
            f"at {MARKET_CLOSE.strftime('%H:%M')} ET). Outcomes are filled after the close.")
    rows = read_day(day, root)
    if not rows:
        return FillReport(day=day, notes=(f"no ledger for {day}",))

    tickers = sorted({row.ticker for row in rows if row.ticker})
    daily_bars = daily.daily_bars(tickers, start=day, end=day + timedelta(days=1))
    intraday_bars = intraday.intraday_bars(tickers, start=day, end=day + timedelta(days=1))

    filled = [fill_row(row, daily=daily_bars.get(row.ticker, []),
                       intraday=intraday_bars.get(row.ticker, []), day=day, now=now,
                       config=config) for row in rows]
    if write:
        write_day(day, filled, root=root)
    complete = [row for row in filled if is_complete(row)]
    notes = tuple(sorted({row.outcome_note for row in filled if row.outcome_note}))
    return FillReport(day=day, rows=len(filled), filled=len(complete),
                      incomplete=len(filled) - len(complete), notes=notes)
