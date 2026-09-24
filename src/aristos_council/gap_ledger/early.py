"""GAP-EARLY-CHECKPOINT-1 — was the move already visible before the 09:00 list?

The screen runs at 09:00 ET and the list is read after that. The question this module lets the
record answer is whether acting EARLIER in the pre-market is worth anything, and to answer it
the run has to log WHEN each name's move first showed. That needs two things the run already
holds for an IBKR-verified candidate: today's 5-minute bars from 04:00 ET, and the
twenty-session history fetched for the relative-volume check.

**"Visible" means two things at once**, on one 5-minute bar:

* its price is already at or beyond the gap threshold (3%) in the gap's direction; and
* its volume is at least ``early_volume_multiple`` (3x) the MEDIAN volume of the same
  time-of-day bar over the prior twenty sessions — a price that moved on nothing is the stray
  print the trust tests exist to catch, not a signal.

**Time and price are the bar's CLOSE.** A bar stamped 05:30 is not known to be a signal until
05:35, so the signal time is 05:35 and the price is that bar's close. Reporting the bar's start
would let the record "act" on information that did not yet exist, which would flatter exactly
the comparison this feature is for. A bar still forming at the run time is not used either.

**A usual volume of zero is NOT-EVALUATED, not a pass.** Many pre-market slots have a median
of 0 over twenty sessions (nobody usually trades at 04:20), and 3 x 0 = 0 would let any print
at all qualify — a single 100-share trade would become the "first signal" and drag the early
times earlier for no reason. Null is not false and it is not true either (project rule 3): such
a bar is skipped and COUNTED, and the count goes in the row's note.

Everything here is pure, over bars. Nothing here reaches a provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Optional, Sequence

from .bars import IntradayBar
from .config import DEFAULT_CONFIG, MARKET_OPEN, NY, PATH_MOMENTS, GapConfig, at_ny
from .outcomes import CHECKPOINT_TOLERANCE
from .screen import gap_fraction, premarket_window, session_dates

BAR_LENGTH = timedelta(minutes=5)

# Why a verified candidate has no first signal. Constants because the tests and the viewer read
# them, and matching prose is how an edit silently turns a check into a no-op.
NO_DIRECTION = "no gap direction to measure a signal against"
NO_HISTORY = "no prior sessions to compare a bar's volume against"


@dataclass(frozen=True)
class EarlyReading:
    """One candidate's pre-market timeline. Every field is None/empty when it was not measured.

    ``path`` is aligned to ``config.PATH_MOMENTS``: the traded price at 04:00, 06:00, 07:00,
    08:00 and 09:00, or None where the name did not print at that moment.
    """

    signal_time: Optional[datetime] = None
    signal_price: Optional[float] = None
    path: tuple = ()
    note: str = ""


def _volume_lookup(bars: Sequence[IntradayBar]) -> dict:
    """``{(date, time_of_day): volume}`` for every bar, in New York time.

    Keyed by slot so the baseline for "05:30 on each prior session" is one dict read. A slot with
    no bar is absent from the dict, and is READ as 0 — the provider omits a slot in which nothing
    traded, so absence is the true reading, the same convention ``screen.baseline_volumes`` uses.
    """
    out: dict = {}
    for bar in bars:
        start = bar.start.astimezone(NY)
        key = (start.date(), start.time())
        out[key] = out.get(key, 0) + int(bar.volume)
    return out


def usual_volume(lookup: dict, sessions: Sequence[date], slot: time) -> float:
    """The median volume of ``slot`` across ``sessions`` (0 for a session with no bar there)."""
    return float(median([lookup.get((day, slot), 0) for day in sessions]))


def path_prices(bars: Sequence[IntradayBar], day: date, run_at: datetime, *,
                tolerance: timedelta = CHECKPOINT_TOLERANCE) -> tuple:
    """The traded price at each of ``PATH_MOMENTS``: the OPEN of the first bar starting at or
    after the moment, within ``tolerance`` (the same rule ``outcomes.price_at`` uses).

    Bounded by the run time — a bar that starts after the screen ran did not exist when it ran —
    and by the open bell, so a pre-market moment can never read a regular-session price. None where
    the name did not print, never the nearest thing to hand.
    """
    known = sorted((b for b in bars
                    if b.start.astimezone(NY).date() == day and b.start <= run_at),
                   key=lambda b: b.start)
    out = []
    for moment in PATH_MOMENTS:
        target = at_ny(day, moment)
        limit = target + tolerance
        price = None
        for bar in known:
            start = bar.start.astimezone(NY)
            if start < target:
                continue
            if start >= limit:
                break
            if start.time() < MARKET_OPEN:
                price = bar.open
            break
        out.append(price)
    return tuple(out)


def first_signal(bars: Sequence[IntradayBar], *, as_of: date, run_at: datetime,
                 previous_close: Optional[float], gap: Optional[float],
                 config: GapConfig = DEFAULT_CONFIG) -> tuple:
    """``(time, price, note)`` — when the move first showed, or ``(None, None, why)``.

    ``bars`` is today PLUS the prior sessions (the twenty-session request the relative-volume
    check already made), so the usual volume for each slot comes from data already in hand.
    """
    if gap is None or gap == 0:
        return None, None, NO_DIRECTION
    direction = 1 if gap > 0 else -1
    sessions = session_dates(bars, before=as_of)[:config.relative_volume_days]
    if not sessions:
        return None, None, NO_HISTORY

    start, end = premarket_window(as_of, run_at)
    today = sorted((b for b in bars if start <= b.start < end), key=lambda b: b.start)
    lookup = _volume_lookup(bars)
    at_the_gap = unmeasurable = 0
    for bar in today:
        closed = bar.start + BAR_LENGTH
        if closed > run_at:                            # still forming when the screen ran
            break
        move = gap_fraction(bar.close, previous_close)
        if move is None or move * direction < config.min_abs_gap:
            continue
        at_the_gap += 1
        usual = usual_volume(lookup, sessions, bar.start.astimezone(NY).time())
        if usual <= 0:
            unmeasurable += 1                          # NOT-EVALUATED: nothing to compare to
            continue
        if bar.volume >= config.early_volume_multiple * usual:
            return closed.astimezone(NY), bar.close, ""

    if at_the_gap == 0:
        return None, None, (f"no 5-minute bar reached the {config.min_abs_gap * 100:.0f}% "
                            f"gap before the run")
    note = (f"{at_the_gap} bar(s) reached the gap but none had volume "
            f"{config.early_volume_multiple:.0f}x the usual for its time of day")
    if unmeasurable:
        note += (f"; {unmeasurable} of them had a usual volume of zero and could not be "
                 f"evaluated")
    return None, None, note


def early_reading(bars: Sequence[IntradayBar], *, as_of: date, run_at: datetime,
                  previous_close: Optional[float], gap: Optional[float],
                  config: GapConfig = DEFAULT_CONFIG) -> EarlyReading:
    """The whole pre-market timeline for one IBKR-verified candidate."""
    when, price, note = first_signal(bars, as_of=as_of, run_at=run_at,
                                     previous_close=previous_close, gap=gap, config=config)
    return EarlyReading(signal_time=when, signal_price=price,
                        path=path_prices(bars, as_of, run_at), note=note)
