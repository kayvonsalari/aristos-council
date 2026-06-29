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
from .tools.technical import technical_snapshot, total_return, _TD_6M, _TD_12M

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
}


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
        annualized_volatility=snap.annualized_volatility if snap else None)
