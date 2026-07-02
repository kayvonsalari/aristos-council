"""Factor registry for the rank-based multi-factor decision core (Aristos v2).

Grounding: Schwartz & Hanauer, "Do Simple Stock-Picking Formulas Still Work?"
(2024, evaluated 1963-2022; Piotroski / Greenblatt Magic Formula / Carlisle
Acquirer's Multiple / van Vliet-Blitz Conservative). All four earn significant
risk-adjusted returns by giving efficient exposure to the SAME established factors —
VALUE, PROFITABILITY (quality), MOMENTUM (and low-vol). The methods RANK and combine
ranks; they do NOT assign magic point-weights. This module computes those factors
from DETERMINISTIC market/fundamental data already fetched — NEVER from the (wobbly)
LLM specialists (rejected: their per-agent instability would re-poison the verdict).

Each factor returns a float or None (NOT-EVAL — missing/insufficient data). The
rank engine (rank_engine.py) does the ranking; this module only extracts values and
declares each factor's NATURAL direction (is higher or lower better?).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from .data.adapter import DataUnavailable, Fundamentals
from .tools.screening import revenue_cagr, through_cycle_roic
from .tools.technical import (
    _TD_6M,
    _TD_12M,
    annualized_volatility,
    technical_snapshot,
    total_return,
)

_ROIC_WINDOW = 4   # through-cycle window (matches the screen's ROIC window intent)


@dataclass
class FactorInputs:
    """The deterministic data a factor may read, per ticker — assembled from the
    SAME adapter the council uses (fundamentals + price-derived returns/vol)."""

    ticker: str
    fundamentals: Optional[Fundamentals] = None
    return_6m: Optional[float] = None
    return_12m: Optional[float] = None
    annualized_volatility: Optional[float] = None
    last_close: Optional[float] = None     # for the screen-as-prefilter (yield etc.)


# --- factor functions (pure; None == NOT-EVAL) ---------------------------- #
def _earnings_yield(fi: FactorInputs) -> Optional[float]:
    """Greenblatt's value leg. Proper form is EBIT/EV, but enterprise value (market
    cap + net debt) needs a balance-sheet debt/cash line free fundamentals don't
    reliably give — so use EBIT/market_cap as the available proxy, falling back to
    1/PE. Higher is cheaper/better."""
    f = fi.fundamentals
    if f is None:
        return None
    ebit = f.ebit[0] if f.ebit else None
    if ebit is not None and f.market_cap and f.market_cap > 0:
        return ebit / f.market_cap
    if f.pe_ratio and f.pe_ratio > 0:
        return 1.0 / f.pe_ratio
    return None


def _return_on_capital(fi: FactorInputs) -> Optional[float]:
    """Greenblatt's quality leg — through-cycle ROIC off the PROVIDED invested
    capital (negative-equity-safe). Higher is better."""
    f = fi.fundamentals
    if f is None:
        return None
    roic, _ = through_cycle_roic(f.operating_income, f.tax_provision,
                                 f.pretax_income, f.invested_capital,
                                 window=_ROIC_WINDOW)
    return roic


def _momentum_12m(fi: FactorInputs) -> Optional[float]:
    return fi.return_12m


def _momentum_6m(fi: FactorInputs) -> Optional[float]:
    return fi.return_6m


def _low_volatility(fi: FactorInputs) -> Optional[float]:
    """Annualized volatility — direction LOW (lower vol ranks better). The
    Conservative-Formula leg that, with momentum, structurally avoids falling
    knives (a crashing name is high-vol AND negative-momentum)."""
    return fi.annualized_volatility


def _net_payout_yield(fi: FactorInputs) -> Optional[float]:
    """Net payout = dividends + buybacks / market cap. Buyback data isn't on free
    fundamentals, so this falls back to DIVIDEND YIELD (an under-count for big
    repurchasers — documented). Higher is better."""
    f = fi.fundamentals
    return f.dividend_yield if f is not None else None


def _revenue_growth(fi: FactorInputs) -> Optional[float]:
    f = fi.fundamentals
    if f is None:
        return None
    cagr, _ = revenue_cagr(f.total_revenue, 3)
    return cagr


def _dividend_streak(fi: FactorInputs) -> Optional[float]:
    """Consecutive years of dividend increases (adapter-derived) — a durable-income
    quality signal; higher is better. None when the streak couldn't be derived."""
    f = fi.fundamentals
    s = f.dividend_streak_years if f is not None else None
    return float(s) if s is not None else None


@dataclass(frozen=True)
class FactorDef:
    name: str
    fn: Callable[[FactorInputs], Optional[float]]
    direction: str        # "high" = higher is better, "low" = lower is better
    label: str
    fallback_note: str = ""


FACTOR_REGISTRY: dict[str, FactorDef] = {
    "earnings_yield": FactorDef(
        "earnings_yield", _earnings_yield, "high", "Earnings yield (EBIT/EV proxy)",
        "EBIT/market_cap proxy; 1/PE fallback (no enterprise-value debt line)"),
    "roic": FactorDef(
        "roic", _return_on_capital, "high", "Return on invested capital"),
    "momentum_12m": FactorDef(
        "momentum_12m", _momentum_12m, "high", "12-month price momentum"),
    "momentum_6m": FactorDef(
        "momentum_6m", _momentum_6m, "high", "6-month price momentum"),
    "low_volatility": FactorDef(
        "low_volatility", _low_volatility, "low", "Annualized volatility (low best)"),
    "net_payout_yield": FactorDef(
        "net_payout_yield", _net_payout_yield, "high", "Net payout yield",
        "dividend-yield fallback (buybacks unavailable on free fundamentals)"),
    "revenue_growth": FactorDef(
        "revenue_growth", _revenue_growth, "high", "Revenue CAGR (3y)"),
    "dividend_streak": FactorDef(
        "dividend_streak", _dividend_streak, "high", "Dividend-growth streak (years)"),
}


# Factors derivable from PRICE CLOSES ALONE — these are the only ones a free-data
# backtest can compute POINT-IN-TIME (historical prices are available as-of; historical
# point-in-time FUNDAMENTALS are not). The backtest validates this sleeve honestly and
# FLAGS the rest as data-limited (see backtest.py).
PRICE_DERIVED_FACTORS: frozenset[str] = frozenset(
    {"momentum_6m", "momentum_12m", "low_volatility"})


def price_factors_from_closes(closes: list[float], names) -> dict[str, Optional[float]]:
    """Compute the PRICE-DERIVED factors among ``names`` from a close series — the
    point-in-time-safe sleeve for backtesting. Non-price factors map to None (they
    cannot be computed point-in-time from free data)."""
    out: dict[str, Optional[float]] = {}
    for name in names:
        if name == "momentum_6m":
            out[name] = total_return(closes, _TD_6M)
        elif name == "momentum_12m":
            out[name] = total_return(closes, _TD_12M)
        elif name == "low_volatility":
            out[name] = annualized_volatility(closes)
        else:
            out[name] = None
    return out


def is_sector_excluded(sector: Optional[str], exclude_sectors) -> bool:
    """Case-insensitive, CONFIRMED-ONLY sector exclusion. True only when ``sector``
    is PRESENT and matches an entry — a missing/None sector is NEVER excluded, so
    absent provider data can't silently drop a name (the rank engine's universe
    filter, e.g. Magic Formula dropping financials where ROIC is invalid)."""
    if not sector or not exclude_sectors:
        return False
    return sector.strip().lower() in {s.strip().lower() for s in exclude_sectors}


def is_payout_uncovered(payout_ratio: Optional[float],
                        max_payout: Optional[float]) -> bool:
    """CONFIRMED-ONLY payout-coverage gate. True only when payout_ratio is PRESENT and
    EXCEEDS max_payout — a dividend the company can't afford is a coming cut, so it is
    DISQUALIFYING for a defensive income holding (the income-strategy analogue of the
    falling-knife guard). A missing/None payout (a non-dividend name has no payout to
    be uncovered) is NEVER excluded — same principle as the sector gate. No gate set
    (max_payout None) excludes nothing."""
    if max_payout is None or payout_ratio is None:
        return False
    return payout_ratio > max_payout


def compute_factors(fi: FactorInputs, names) -> dict[str, Optional[float]]:
    """The factor values for one ticker, for the named factors. Unknown names raise
    (the rank-strategy loader validates names up front)."""
    out: dict[str, Optional[float]] = {}
    for name in names:
        fdef = FACTOR_REGISTRY.get(name)
        if fdef is None:
            raise KeyError(f"unknown factor '{name}'")
        out[name] = fdef.fn(fi)
    return out


def gather_factor_inputs(adapter, ticker: str, *, today: date) -> FactorInputs:
    """Fetch the deterministic inputs one ticker needs for factor ranking — the same
    adapter the council uses. Per-source DataUnavailable is swallowed (partial inputs
    -> NOT-EVAL factors), so one flaky name never aborts a universe ranking."""
    fundamentals = None
    try:
        fundamentals = adapter.get_fundamentals(ticker)
    except DataUnavailable:
        pass
    closes: list[float] = []
    try:
        prices = adapter.get_price_history(
            ticker, start=today - timedelta(days=400), end=today)
        closes = prices.closes if prices and prices.closes else []
    except DataUnavailable:
        pass
    snap = technical_snapshot(closes) if closes else None
    return FactorInputs(
        ticker=ticker, fundamentals=fundamentals,
        return_6m=total_return(closes, _TD_6M) if closes else None,
        return_12m=total_return(closes, _TD_12M) if closes else None,
        annualized_volatility=snap.annualized_volatility if snap else None,
        last_close=closes[-1] if closes else None)


def screen_prefilter_fail(screen_criteria, fi: FactorInputs) -> Optional[str]:
    """Run a SCREEN's criteria on one name; return the NAMED reason for the first
    CONFIRMED FAIL (passed is False), or None if it passes or only ABSTAINS.

    This is the screen-as-prefilter: for a strategy defined by HARD REQUIREMENTS (a
    defensive holding MUST have covered, real income and intact trend), the screen
    says WHO QUALIFIES and the ranking orders what's left — ranking-and-combining
    alone can't enforce a floor. SAFETY: a criterion that ABSTAINS (passed is None,
    e.g. missing data) does NOT exclude — abstention != failure, never drop on a data
    gap. (Requiring income IS the strategy's intent here, so a genuine non-payer
    failing min_dividend_yield is CORRECT — distinct from the growth-factor rule that
    never punishes a non-dividend name.)"""
    from .tools.criteria.registry import Evidence, run_screen
    if fi.fundamentals is None:
        return None
    ev = Evidence(fundamentals=fi.fundamentals, last_close=fi.last_close,
                  return_6m=fi.return_6m, return_12m=fi.return_12m, dividends=[])
    for c in run_screen(screen_criteria, ev, ticker=fi.ticker).criteria:
        if c.passed is False:               # confirmed fail only (None abstains)
            obs = (f"{c.observed:.4g}" if isinstance(c.observed, (int, float))
                   else "n/a")
            return f"screen: {c.name} (observed {obs} vs threshold {c.threshold})"
    return None
