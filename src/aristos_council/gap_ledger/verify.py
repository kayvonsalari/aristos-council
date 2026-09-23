"""GAP-IBKR-1 step 2 — verifying a yfinance gap against Interactive Brokers.

yfinance says a name gapped. IB says whether anyone actually traded it. That is the whole
idea: yfinance serves pre-market prices but never pre-market volume, so a stray print reads
as a gap and nothing contradicts it — and the trust gate in ``screen.py`` had to infer
participation from tape density because the number itself was missing.

Two checks, in this order, because the first is one cheap request and the second is a whole
month of history:

``check 1`` today's 5-minute TRADES bars. IB's own last pre-market price, the pre-market
    volume from 04:00 ET to the run time, and the gap against the ADJUSTED previous close
    already held from yfinance. No trades, or a gap inside the threshold, and the name is
    REJECTED — ``passed is False``, not an abstention, because IB looking and finding nothing
    is a reading.

``check 2`` the twenty-session baseline, for survivors only. Relative volume is finally
    EVALUATED rather than abstained: at or above the bar it passes, below it fails.

Everything here is PURE. The requests happen in ``ibkr.py`` and the orchestration in
``run.py``; this module only turns bars into readings, so every rule below is testable
without a gateway.

**The override.** A name IB has verified takes IB's price, volume and spread, and Batch 3's
trust tests do not apply to it. Those tests exist only because the volume was missing; where
the volume is present they are a worse proxy for the thing they were standing in for, and
applying both would mean rejecting a name for a sparse yfinance tape that IB shows was
heavily traded.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from statistics import median
from typing import Optional, Sequence

from .bars import IntradayBar, Quote
from .config import DEFAULT_CONFIG, GapConfig
from .screen import (ScreenRow, baseline_volumes, gap_fraction, last_price,
                     premarket_window, relative_volume, spread_flag, spread_percent,
                     window_volume)

# The source of the numbers on a row. One spelling each, because the CSV carries it.
SOURCE_IBKR = "ibkr"
SOURCE_YFINANCE = "yfinance"

# Check 1's rejection. A READING: IB looked at the tape and there was no move there.
NO_REAL_MOVE = "IBKR: no real pre-market move"

# Item 3's banner, said once when the gateway is not there at all.
IBKR_UNAVAILABLE = "!! IBKR UNAVAILABLE: volume not checked"


@dataclass(frozen=True)
class IBKRReading:
    """What IB said about one name. ``passed`` is three-valued, as everywhere in this repo.

    ``passed is None`` means IB was never asked or could not answer — which leaves the
    yfinance row (and its trust tests) standing. True and False are both IB's verdict.
    """

    ticker: str
    passed: Optional[bool] = None
    reason: str = ""
    last_price: Optional[float] = None
    gap: Optional[float] = None
    premarket_volume: Optional[int] = None
    baseline_median: Optional[float] = None
    baseline_sessions: int = 0
    relative_volume: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None

    @property
    def verified(self) -> bool:
        """Did IB actually reach a verdict on this name, either way?"""
        return self.passed is not None

    @property
    def spread(self) -> Optional[float]:
        return spread_percent(Quote(bid=self.bid, ask=self.ask))


# --------------------------------------------------------------------------- #
# who goes to IB
# --------------------------------------------------------------------------- #
def needs_verifying(rows, *, config: GapConfig = DEFAULT_CONFIG) -> list[str]:
    """Every name yfinance says gapped, INCLUDING the ones its trust tests abstained on.

    The inclusion is the point. Batch 3 marks a thin-taped gapper NOT EVALUATED because it
    cannot tell a stray print from a real move; IB can, so sending only the names that already
    passed would leave exactly the uncertain cases unresolved forever.
    """
    out = []
    for row in rows:
        gap = getattr(row, "gap", None)
        if gap is not None and abs(gap) >= config.min_abs_gap:
            out.append(row.ticker)
    return out


# --------------------------------------------------------------------------- #
# check 1 — did it really move?
# --------------------------------------------------------------------------- #
def check_one(ticker: str, bars: Sequence[IntradayBar], *, previous_close: Optional[float],
              as_of: date, run_at: datetime,
              config: GapConfig = DEFAULT_CONFIG) -> IBKRReading:
    """IB's own price, volume and gap for today. Rejects a name that did not really move."""
    start, end = premarket_window(as_of, run_at)
    inside = [b for b in bars if start <= b.start < end]
    if not inside:
        return IBKRReading(ticker, False, NO_REAL_MOVE, premarket_volume=0)

    price = last_price(bars, start, end)
    volume = window_volume(bars, start, end)
    gap = gap_fraction(price, previous_close)
    readings = dict(last_price=price, gap=gap, premarket_volume=volume)
    if volume <= 0:
        # Bars but no traded shares: IB's grid can carry a slot with nothing in it.
        return IBKRReading(ticker, False, NO_REAL_MOVE, **readings)
    if gap is None or abs(gap) < config.min_abs_gap:
        return IBKRReading(ticker, False, NO_REAL_MOVE, **readings)
    return IBKRReading(ticker, None, "", **readings)      # survives; check 2 decides


# --------------------------------------------------------------------------- #
# check 2 — was anyone there?
# --------------------------------------------------------------------------- #
def check_two(reading: IBKRReading, bars: Sequence[IntradayBar], *, as_of: date,
              run_at: datetime, config: GapConfig = DEFAULT_CONFIG) -> IBKRReading:
    """The twenty-session baseline, and the relative volume it makes possible.

    Same clock window and same median rule as the yfinance path (``screen.baseline_volumes``)
    — the only difference is that the numbers are not all zero, so the answer is a verdict
    instead of an abstention.
    """
    _start, end = premarket_window(as_of, run_at)
    baseline = baseline_volumes(bars, as_of=as_of, window_end=end.time(),
                               sessions=config.relative_volume_days)
    ratio, note = relative_volume(reading.premarket_volume, baseline)
    mid = float(median(baseline)) if baseline else None
    scored = replace(reading, baseline_median=mid, baseline_sessions=len(baseline),
                     relative_volume=ratio)
    if ratio is None:
        # IB has the volume but not enough history to compare it against — a genuine
        # NOT-EVALUATED, so the yfinance row and its trust tests stand rather than this
        # becoming a silent pass.
        return replace(scored, passed=None, reason=f"IBKR: {note}")
    if ratio < config.min_relative_volume:
        return replace(scored, passed=False,
                       reason=f"IBKR: relative pre-market volume {ratio:.2f}x below "
                              f"{config.min_relative_volume:.1f}x")
    return replace(scored, passed=True, reason="")


# --------------------------------------------------------------------------- #
# the override
# --------------------------------------------------------------------------- #
def apply_reading(row: ScreenRow, reading: IBKRReading, *,
                  spread_unknown_note: str,
                  config: GapConfig = DEFAULT_CONFIG) -> ScreenRow:
    """The yfinance row, replaced by what IB measured.

    IB's price, volume and spread override yfinance's, and Batch 3's trust readings are
    CLEARED rather than carried: they are an inference about participation, and where the
    participation is measured the inference is not evidence about anything. Leaving
    ``premarket_prints`` on the row would invite a reader to apply both.

    A reading IB could not reach (``passed is None``) leaves the row exactly as it was.
    """
    if not reading.verified:
        return row
    spread = reading.spread
    return replace(
        row,
        passed=reading.passed,
        reason=reading.reason,
        premarket_price=reading.last_price,
        gap=reading.gap,
        premarket_volume=reading.premarket_volume,
        baseline_median_volume=reading.baseline_median,
        baseline_sessions=reading.baseline_sessions,
        relative_volume=reading.relative_volume,
        relative_volume_note="",                          # measured, so nothing to excuse
        premarket_prints=None,                            # the trust inference does not apply
        confirm_prints=None,
        confirm_average=None,
        spread=spread,
        spread_note=spread_flag(spread, config, unknown_note=spread_unknown_note),
    )
