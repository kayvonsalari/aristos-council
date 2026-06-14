"""Provider-agnostic market-data adapter.

Why this exists
---------------
Phase 1 develops against yfinance (free, no key) but the council should never
depend on yfinance's quirks. Every specialist and tool talks to the
`MarketDataAdapter` interface and to the normalized DTOs below — NOT to a
provider SDK. Swapping to EODHD later is then a one-line change at the
composition root, with no edits to tools or specialists.

The DTOs are deliberately small and provider-neutral. Each adapter is
responsible for mapping its provider's raw response into these shapes and for
raising `DataUnavailable` (not provider-specific exceptions) on failure, so the
data-quality veto trigger has a single error type to reason about.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class DataUnavailable(Exception):
    """Raised when an adapter cannot supply requested data.

    Adapters must translate provider-specific failures (rate limits, empty
    frames, missing tickers, network errors) into this single exception so the
    rest of the system has one thing to catch and one signal to map onto the
    DATA_QUALITY veto trigger.
    """


# --------------------------------------------------------------------------- #
# Normalized DTOs (provider-neutral)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PriceBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: int


@dataclass(frozen=True)
class PriceHistory:
    ticker: str
    bars: list[PriceBar] = field(default_factory=list)

    @property
    def closes(self) -> list[float]:
        return [b.adj_close for b in self.bars]


@dataclass(frozen=True)
class Fundamentals:
    """The fundamental snapshot the dividend-aristocrat screen needs.

    Fields are Optional-by-convention: a provider may not return all of them.
    Tools must handle None explicitly rather than assuming presence, and a None
    where the strategy needs a value should surface as a caveat / data-quality
    flag, never a silent zero.
    """

    ticker: str
    name: str | None = None
    market_cap: float | None = None
    dividend_yield: float | None = None          # decimal, e.g. 0.038 = 3.8%
    dividend_per_share: float | None = None
    payout_ratio: float | None = None            # decimal
    eps: float | None = None
    pe_ratio: float | None = None
    free_cash_flow: float | None = None
    # Consecutive years of dividend increases — the defining aristocrat test.
    # Most free providers DON'T supply this; see adapter notes.
    years_dividend_growth: int | None = None

    # --- Annual income-statement & balance-sheet series (Sprint 4B) --------- #
    # NEWEST-FIRST lists of clean annual values (NaN years and the trailing
    # empty column dropped by the adapter). Empty when the provider has none —
    # a criterion with insufficient series returns NOT-EVAL, never crashes.
    # Growth/quality criteria read these (revenue CAGR, ROIC, PEG).
    total_revenue: list[float] = field(default_factory=list)
    operating_income: list[float] = field(default_factory=list)
    ebit: list[float] = field(default_factory=list)
    tax_provision: list[float] = field(default_factory=list)
    pretax_income: list[float] = field(default_factory=list)
    invested_capital: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class DividendEvent:
    ex_date: date
    amount: float


# --------------------------------------------------------------------------- #
# Adapter interface
# --------------------------------------------------------------------------- #
class MarketDataAdapter(abc.ABC):
    """Contract every market-data provider implementation must satisfy."""

    #: Short identifier recorded in tool provenance, e.g. "yfinance".
    name: str = "abstract"

    @abc.abstractmethod
    def get_price_history(
        self, ticker: str, *, start: date, end: date
    ) -> PriceHistory:
        ...

    @abc.abstractmethod
    def get_fundamentals(self, ticker: str) -> Fundamentals:
        ...

    @abc.abstractmethod
    def get_dividend_history(
        self, ticker: str, *, start: date, end: date
    ) -> list[DividendEvent]:
        ...
