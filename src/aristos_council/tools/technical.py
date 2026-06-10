"""Deterministic technical-analysis tools.

Same hard rule as screening.py: pure functions, no LLM, no network. These give
the Technical specialist real numbers to reason over instead of letting a model
hallucinate chart talk.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TechnicalSnapshot:
    last_close: float | None
    sma_50: float | None
    sma_200: float | None
    pct_off_52w_high: float | None      # negative = below high; -0.12 == 12% off
    annualized_volatility: float | None  # decimal, e.g. 0.22 == 22%
    notes: list[str]


def sma(closes: list[float], window: int) -> float | None:
    """Simple moving average of the LAST `window` closes; None if too short."""
    if window <= 0 or len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def pct_off_high(closes: list[float], lookback: int = 252) -> float | None:
    """Last close relative to the highest close in the lookback window.

    Returns (last / high) - 1, so 0.0 means at the high, -0.15 means 15% below.
    """
    if not closes:
        return None
    window = closes[-lookback:] if len(closes) > lookback else closes
    high = max(window)
    if high <= 0:
        return None
    return closes[-1] / high - 1.0


def annualized_volatility(closes: list[float], trading_days: int = 252) -> float | None:
    """Annualized stdev of daily log returns. None if <2 closes."""
    if len(closes) < 2:
        return None
    rets = []
    for prev, cur in zip(closes[:-1], closes[1:]):
        if prev <= 0 or cur <= 0:
            return None
        rets.append(math.log(cur / prev))
    n = len(rets)
    if n < 2:
        return None
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return math.sqrt(var) * math.sqrt(trading_days)


def technical_snapshot(closes: list[float]) -> TechnicalSnapshot:
    """Bundle the primitives; record which metrics were uncomputable and why."""
    notes: list[str] = []
    s50 = sma(closes, 50)
    s200 = sma(closes, 200)
    if s50 is None:
        notes.append("sma_50 unavailable: <50 closes")
    if s200 is None:
        notes.append("sma_200 unavailable: <200 closes")
    return TechnicalSnapshot(
        last_close=closes[-1] if closes else None,
        sma_50=s50,
        sma_200=s200,
        pct_off_52w_high=pct_off_high(closes),
        annualized_volatility=annualized_volatility(closes),
        notes=notes,
    )
