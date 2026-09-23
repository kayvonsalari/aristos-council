"""GAP-LEDGER-1 step 2 — the gap, the relative pre-market volume, and the spread.

Every number in this module is computed HERE, deterministically, from bars. No model ever
sees a formula and no model ever produces a figure: an LLM in this system may write one
sentence of prose about headlines someone else fetched (``explain.py``) and nothing else.
That is the repo's first hard rule and it applies to a pre-market screener exactly as it
applies to the council.

Three readings, and the discipline that separates them:

``gap``
    ``(last pre-market price - previous close) / previous close``, where the previous
    close is the DIVIDEND- AND SPLIT-ADJUSTED regular-session close (see
    ``universe.pre_filter_one`` for why the adjusted reading is the only honest one).

``relative pre-market volume``
    today's volume in the window 04:00 ET → run time, divided by the MEDIAN of the same
    clock window over the prior 20 sessions. Median, not mean: one earnings morning in the
    baseline would otherwise raise the bar for the next month.

``spread``
    recorded when the provider quotes both sides, and only then. A missing spread is
    "spread unknown" and the name is KEPT; a wide one is MARKED and the name is kept.
    Neither is ever a reason to drop, because neither is a statement about the move.

Three-valued readings, the ``passed is None`` discipline the rest of the repo runs on: a
number that could not be computed is NOT a failing number. A name whose relative volume is
not computable is excluded with that as its stated reason — it is never quietly treated as
0x (which would read as a confirmed fail) and never quietly treated as passing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Iterable, Optional, Sequence

from .bars import IntradayBar, Quote
from .config import DEFAULT_CONFIG, MARKET_CLOSE, MARKET_OPEN, PREMARKET_OPEN, GapConfig
from .config import at_ny


# --------------------------------------------------------------------------- #
# the window
# --------------------------------------------------------------------------- #
def premarket_window(day: date, run_at: datetime) -> tuple[datetime, datetime]:
    """The pre-market volume window for ``day``: 04:00 ET up to ``run_at``, CLAMPED at 09:30.

    The clamp is the point. The brief says "04:00 ET to run time" and the default run is
    09:00, comfortably inside pre-market — but a run launched at 11:00 would otherwise
    count ninety minutes of regular-session volume as PRE-market volume, which is both
    wrong and flattering (the numerator inflates against a baseline that never contained
    regular-session trade). Past the open the window simply ends at the open, and the
    record stamps ``window_end`` so the reader can see it did.
    """
    start = at_ny(day, PREMARKET_OPEN)
    open_bell = at_ny(day, MARKET_OPEN)
    end = min(run_at.astimezone(start.tzinfo), open_bell)
    return start, max(start, end)


def bars_in(bars: Iterable[IntradayBar], start: datetime,
            end: datetime) -> list[IntradayBar]:
    """Bars whose stamp lies in [start, end). Half-open: a bar stamped 09:30 belongs to
    the regular session, not to a pre-market window that ends at 09:30."""
    return [b for b in bars if start <= b.start < end]


def window_volume(bars: Iterable[IntradayBar], start: datetime, end: datetime) -> int:
    """Shares traded in [start, end). An empty window is 0 — a real reading (nobody
    traded), not a missing one."""
    return sum(int(b.volume) for b in bars_in(bars, start, end))


def last_price(bars: Iterable[IntradayBar], start: datetime,
               end: datetime) -> Optional[float]:
    """The last trade price in the window, or None when the window holds no bars.

    None means "this name did not trade pre-market", which is a legitimate answer and the
    reason ``gap`` is Optional rather than defaulting to zero.
    """
    inside = bars_in(bars, start, end)
    return inside[-1].close if inside else None


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #
def session_dates(bars: Iterable[IntradayBar], *, before: date) -> list[date]:
    """The dates before ``before`` on which the REGULAR session actually traded, newest first.

    Regular-session presence is what makes a date a session: a holiday serves no bars at
    all, but a partial feed can serve a stray pre-market bar on a day the market never
    opened, and counting that as a session would pull a zero into the baseline median.
    """
    seen: set[date] = set()
    for bar in bars:
        day = bar.start.date()
        if day >= before:
            continue
        if MARKET_OPEN <= bar.start.time() < MARKET_CLOSE:
            seen.add(day)
    return sorted(seen, reverse=True)


def baseline_volumes(bars: Sequence[IntradayBar], *, as_of: date, window_end: time,
                     sessions: int) -> list[int]:
    """The same clock window on each of the ``sessions`` most recent prior sessions.

    The window is the same CLOCK window, not the same number of bars, so a run at 09:00
    is compared against 04:00–09:00 on each prior day. A prior session with no pre-market
    trade contributes 0, which is a true reading and belongs in the median.
    """
    out: list[int] = []
    for day in session_dates(bars, before=as_of)[:sessions]:
        start = at_ny(day, PREMARKET_OPEN)
        end = at_ny(day, window_end)
        out.append(window_volume(bars, start, max(start, end)))
    return out


# --------------------------------------------------------------------------- #
# the three readings
# --------------------------------------------------------------------------- #
def gap_fraction(premarket_price: Optional[float],
                 previous_close: Optional[float]) -> Optional[float]:
    """``(premarket - previous) / previous``, or None when either side is unusable.

    A non-positive previous close is unusable rather than a division to be attempted.
    """
    if premarket_price is None or previous_close is None or previous_close <= 0:
        return None
    return (premarket_price - previous_close) / previous_close


def relative_volume(today: Optional[int],
                    baseline: Sequence[int]) -> tuple[Optional[float], str]:
    """``(ratio, note)``. The ratio is None when it cannot be computed, and the note says why.

    Two ways it cannot be computed, and neither is a failing 0x:

    * no baseline sessions at all — nothing to divide by;
    * a baseline median of zero — this name has not traded pre-market in twenty sessions.
      An infinite ratio is not a number, and calling it 0 would read as a confirmed fail
      on a name that may be the most interesting on the screen. It is excluded WITH THIS
      REASON so it is visible in the day's record rather than silently absent.
    """
    if today is None:
        return None, "no pre-market bars today"
    if not baseline:
        return None, "no prior sessions to compare against"
    mid = float(median(baseline))
    if mid <= 0:
        return None, ("pre-market baseline is zero over "
                      f"{len(baseline)} sessions — ratio not computable")
    return today / mid, ""


def spread_percent(quote: Optional[Quote]) -> Optional[float]:
    """``(ask - bid) / midpoint``, or None when the provider did not quote both sides.

    A crossed or zero-width book (ask <= bid) is NOT reported as a 0% spread: a zero there
    is a stale or synthetic quote, not a free round trip, and reporting it as tight would
    be the one reading that misleads in the expensive direction.
    """
    if quote is None or quote.bid is None or quote.ask is None:
        return None
    if quote.bid <= 0 or quote.ask <= quote.bid:
        return None
    return (quote.ask - quote.bid) / ((quote.ask + quote.bid) / 2.0)


SPREAD_UNKNOWN = "spread unknown"
# A book is a snapshot of NOW. A run for a past date cannot read the spread that stood that
# morning, and a live quote fetched today would be an anachronism presented as a reading —
# live, on 2026-09-22, a backfill of 2026-09-21 marked RIOT "wide spread 52.42%" from a
# pre-dawn book that had nothing to do with the morning being screened.
SPREAD_HISTORICAL = "spread unknown — historical run, a book cannot be read retroactively"


# --------------------------------------------------------------------------- #
# GAP-PRICE-TRUST-1 — is the pre-market price worth believing at all?
# --------------------------------------------------------------------------- #
# The first full run produced 98 candidates and roughly 80 were junk: XEL +11%, LNT +12% on
# no news, dozens of spreads at 40-57%. With no pre-market volume published, ONE odd print
# reads as a gap and nothing contradicts it.
#
# The spread turned out NOT to be the test. Measured on 2026-09-22: ALNY showed a 1.99%
# spread and was junk (it opened 21% away from its pre-market price), while VKTX showed 7.28%
# and was the day's most genuine mover. So the spread is a loose backstop and these two are
# the decisive readings:
#
# What separates them, MEASURED on the twelve named names against the live 2026-09-22
# tape, is TAPE DENSITY. yfinance omits a five-minute slot in which nothing traded — it
# does not forward-fill — so the number of bars in a window IS the number of printed
# intervals:
#
#             bars in the final 30 min      bars in the 5h window
#   junk      1, 1, 1, 1, 1, 2, 3           2 - 13
#   genuine   6, 6, 6, 6, 6                 55 - 60
#
# Six is the most a 30-minute window can hold, so every genuine mover printed in EVERY
# slot of the final half hour. This is effectively a volume proxy — the leg the provider
# will not serve.
#
#   (a) more than one print in the window at all.
#   (b) the last print CONFIRMED: enough prints around it to confirm anything, and
#       agreement with them. The density half is the decisive one — drift alone does not
#       discriminate, because a one-bar window agrees with itself perfectly (five of the
#       seven junk names scored 0.000% drift).
#
# Each failure gets its OWN reason, so the CSV says which test fired. And every one of them
# is a NOT-EVALUATED marking, never a rejection: an untrustworthy price is a missing reading
# about the name, not a finding about it, so it may not enter the control group either.
# The two spellings of "we have no pre-market print for this name": the provider served no
# bars for today at all, or it served bars but none inside the window. Constants because
# ``run`` counts them for the summary, and counting by matching prose is how a message edit
# silently turns a count into a zero.
#
# They are counted TOGETHER. From the owner's side they are one phenomenon — 234 names on
# 2026-09-22, including AIG, AMT and AFL, which is suspicious for companies that size — and
# the open question is about the provider's extended-hours coverage, not about which of two
# ways it declined. Separating them in the summary would hide the number behind a detail.
NO_INTRADAY_BARS = "no intraday bars from the provider"
NO_PREMARKET_TRADE = "no pre-market trade in the window"
NO_PREMARKET_PRICE_REASONS = frozenset({NO_INTRADAY_BARS, NO_PREMARKET_TRADE})

PRICE_SINGLE_PRINT = "single pre-market print"
PRICE_NOT_CONFIRMED = "last print not confirmed"
PRICE_WIDE_SPREAD = "spread above limit"

# The trust abstentions, as a set, because a caller needs to distinguish THEM from the other
# reasons a row can carry ``passed is None``. Specifically: the first fetch pass has only
# today's bars, so it cannot judge relative volume and abstains on it by construction —
# treating that as "untrustworthy" would stop every name reaching the second pass.
PRICE_TRUST_REASONS = frozenset({PRICE_SINGLE_PRINT, PRICE_NOT_CONFIRMED,
                                 PRICE_WIDE_SPREAD})


def is_price_untrusted(row) -> bool:
    """Did the TRUST gate abstain on this row (as opposed to anything else abstaining)?"""
    return getattr(row, "reason", "") in PRICE_TRUST_REASONS


def premarket_prints(bars: Iterable[IntradayBar], start: datetime, end: datetime) -> int:
    """How many five-minute intervals actually PRINTED in the window.

    Bars, not distinct prices. The first cut of this counted distinct prices, on the theory
    that a quiet tape is forward-filled into many bars carrying one number — which is not
    what this provider does. It OMITS a slot in which nothing traded, so XEL's stray +11%
    arrived as five bars over five hours with five different prices, and counting prices
    called that five prints and trusted it.
    """
    return len(bars_in(bars, start, end))


def confirm_prints(bars: Iterable[IntradayBar], end: datetime, *,
                   minutes: int = DEFAULT_CONFIG.confirm_window_minutes) -> int:
    """How many intervals printed in the final ``minutes`` of the window.

    The decisive reading: six is the ceiling for a 30-minute window, every genuine mover in
    the sample hit six, and no junk name exceeded three.
    """
    return len(bars_in(bars, end - timedelta(minutes=minutes), end))


def confirmation_average(bars: Sequence[IntradayBar], end: datetime, *,
                         minutes: int = DEFAULT_CONFIG.confirm_window_minutes
                         ) -> Optional[float]:
    """The average price over the last ``minutes`` of the window, or None when it is empty.

    Volume-weighted where the provider serves volume, and a simple mean where it does not —
    which on yfinance is always, since extended-hours volume comes back as zero. The
    fallback is explicit rather than incidental: a VWAP with a zero denominator is not a
    small number, it is not a number.
    """
    inside = bars_in(bars, end - timedelta(minutes=minutes), end)
    if not inside:
        return None
    volume = sum(int(b.volume) for b in inside)
    if volume > 0:
        return sum(float(b.close) * int(b.volume) for b in inside) / volume
    return sum(float(b.close) for b in inside) / len(inside)


def confirmed(price: Optional[float], average: Optional[float], *,
              tolerance: float = DEFAULT_CONFIG.max_confirm_drift) -> Optional[bool]:
    """Does the last print agree with the average around it? None when it cannot be asked."""
    if price is None or average is None or average <= 0:
        return None
    return abs(price / average - 1.0) <= tolerance


@dataclass(frozen=True)
class PriceTrust:
    """Whether the pre-market price is worth believing, and the readings behind the answer."""

    reason: str = ""                     # "" when trusted
    prints: Optional[int] = None
    confirming: Optional[int] = None
    average: Optional[float] = None

    @property
    def trusted(self) -> bool:
        return not self.reason


def price_trust(bars: Sequence[IntradayBar], *, price: Optional[float], start: datetime,
                end: datetime, spread: Optional[float],
                config: GapConfig = DEFAULT_CONFIG) -> PriceTrust:
    """Is this pre-market price believable? Each failure carries its OWN reason, so the
    record says which test fired.

    A spread the provider did not give is NOT a failure (the same never-drop-on-unknown
    discipline as everywhere else); only a spread that is present and over the limit is.
    """
    prints = premarket_prints(bars, start, end)
    confirming = confirm_prints(bars, end, minutes=config.confirm_window_minutes)
    average = confirmation_average(bars, end, minutes=config.confirm_window_minutes)
    readings = dict(prints=prints, confirming=confirming, average=average)
    if prints < config.min_premarket_prints:
        return PriceTrust(PRICE_SINGLE_PRINT, **readings)
    # Too few prints around the last one to confirm it. This is the junk signature, and the
    # reason a drift test on its own is worthless: one bar agrees with itself perfectly.
    if confirming < config.min_confirm_prints:
        return PriceTrust(PRICE_NOT_CONFIRMED, **readings)
    if confirmed(price, average, tolerance=config.max_confirm_drift) is False:
        return PriceTrust(PRICE_NOT_CONFIRMED, **readings)
    if spread is not None and spread > config.max_trusted_spread:
        return PriceTrust(PRICE_WIDE_SPREAD, **readings)
    return PriceTrust("", **readings)


def spread_flag(spread: Optional[float], config: GapConfig = DEFAULT_CONFIG, *,
                unknown_note: str = SPREAD_UNKNOWN) -> str:
    """The mark that goes in the record: unknown, wide, or tight. Never a drop."""
    if spread is None:
        return unknown_note
    if spread > config.wide_spread:
        return f"wide spread {spread * 100:.2f}%"
    return "spread ok"


# --------------------------------------------------------------------------- #
# one name
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ScreenRow:
    """One name after step 2. ``passed`` is three-valued the way the repo's screens are:
    True kept, False evaluated-and-rejected, None NOT EVALUATED (data was missing)."""

    ticker: str
    passed: Optional[bool]
    reason: str = ""
    previous_close: Optional[float] = None
    premarket_price: Optional[float] = None
    gap: Optional[float] = None
    premarket_volume: Optional[int] = None
    baseline_median_volume: Optional[float] = None
    baseline_sessions: int = 0
    relative_volume: Optional[float] = None
    # Why the ratio is absent, on a row that was kept anyway. Empty when the ratio was
    # computed — so a candidate carrying this mark was selected on its GAP ALONE, and the
    # mark is what stops that being mistaken for a gap-and-volume selection.
    relative_volume_note: str = ""
    # GAP-PRICE-TRUST-1 — the readings that decide whether the gap is believable.
    premarket_prints: Optional[int] = None
    confirm_prints: Optional[int] = None
    confirm_average: Optional[float] = None
    spread: Optional[float] = None
    spread_note: str = ""
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None

    @property
    def direction(self) -> int:
        """+1 gap up, -1 gap down, 0 when there is no gap to have a direction."""
        if self.gap is None or self.gap == 0:
            return 0
        return 1 if self.gap > 0 else -1


def screen_one(ticker: str, *, bars: Sequence[IntradayBar], previous_close: Optional[float],
               as_of: date, run_at: datetime, quote: Optional[Quote] = None,
               spread_unknown_note: str = SPREAD_UNKNOWN,
               config: GapConfig = DEFAULT_CONFIG) -> ScreenRow:
    """The whole of step 2 for one name.

    Order matters. The pre-market price is established first, then TRUSTED or not
    (GAP-PRICE-TRUST-1) — an untrustworthy price makes every threshold below it moot, so it
    abstains there and the twenty-session volume history is never fetched for it. Then the
    gap, which is the cheaper and more decisive threshold: a name that did not gap needs no
    volume baseline at all.

    The spread is read early because the trust gate uses it as a loose backstop, but it
    still never rejects on its own account: it is recorded on every row that got that far,
    including rejected ones, because "it gapped 8% on 1.2x volume with a 4% spread" is
    exactly the sort of thing worth being able to look back at.
    """
    start, end = premarket_window(as_of, run_at)
    spread = spread_percent(quote)
    note = spread_flag(spread, config, unknown_note=spread_unknown_note)
    common = dict(previous_close=previous_close, spread=spread, spread_note=note,
                  window_start=start, window_end=end)

    if end <= start:
        return ScreenRow(ticker, None, "run time is before the 04:00 ET pre-market open",
                         **common)
    if not bars:
        return ScreenRow(ticker, None, NO_INTRADAY_BARS, **common)

    price = last_price(bars, start, end)
    if price is None:
        return ScreenRow(ticker, None, NO_PREMARKET_TRADE, **common)
    gap = gap_fraction(price, previous_close)
    if gap is None:
        return ScreenRow(ticker, None, "no usable previous close", premarket_price=price,
                         **common)

    # GAP-PRICE-TRUST-1 — a gap is EVALUATED only when the price behind it is worth
    # believing. This runs before the thresholds, and before the twenty-session volume
    # history is ever fetched, because an untrustworthy price makes both moot. The gap is
    # still RECORDED on the row: it is what the outcomes diagnostic measures against.
    verdict = price_trust(bars, price=price, start=start, end=end, spread=spread,
                          config=config)
    trust = dict(premarket_prints=verdict.prints, confirm_prints=verdict.confirming,
                 confirm_average=verdict.average)
    if not verdict.trusted:
        return ScreenRow(ticker, None, verdict.reason, premarket_price=price, gap=gap,
                         **trust, **common)

    volume = window_volume(bars, start, end)
    baseline = baseline_volumes(bars, as_of=as_of, window_end=end.time(),
                                sessions=config.relative_volume_days)
    ratio, ratio_note = relative_volume(volume, baseline)
    mid = float(median(baseline)) if baseline else None
    measured = dict(premarket_price=price, gap=gap, premarket_volume=volume,
                    baseline_median_volume=mid, baseline_sessions=len(baseline),
                    relative_volume=ratio, **trust, **common)

    if abs(gap) < config.min_abs_gap:
        return ScreenRow(ticker, False,
                         f"gap {gap * 100:+.2f}% inside "
                         f"±{config.min_abs_gap * 100:.1f}%", **measured)
    if ratio is None:
        # Rule 3 at the sharp end. A ratio that could not be computed is NOT a ratio of
        # zero, so it may not act as a confirmed fail. Under the default config the name is
        # kept and MARKED (``relative_volume_note``); set ``require_relative_volume`` to
        # demand the reading instead, which abstains. See ``GapConfig``.
        if config.require_relative_volume:
            return ScreenRow(ticker, None, ratio_note, **measured)
        return ScreenRow(ticker, True, "", relative_volume_note=ratio_note, **measured)
    if ratio < config.min_relative_volume:
        return ScreenRow(ticker, False,
                         f"relative pre-market volume {ratio:.2f}x below "
                         f"{config.min_relative_volume:.1f}x", **measured)
    return ScreenRow(ticker, True, "", **measured)
