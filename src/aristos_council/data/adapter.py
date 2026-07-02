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
import logging
from dataclasses import dataclass, field
from datetime import date

_log = logging.getLogger(__name__)


def sane_dividend_yield(v: float | None) -> float | None:
    """Defensive backstop enforcing that dividend_yield is a DECIMAL (0.0289 = 2.89%).

    A real equity yield never exceeds ~100%, so a value > 1.0 is a PERCENT that
    slipped through a provider unit change (yfinance's dividendYield became a percent
    number: 2.89) -> divide by 100 and warn. Applied AFTER each adapter's per-source
    normalization, so it catches FUTURE drift rather than doing the per-source
    conversion itself (a low percent like 0.5%==0.5 is < 1.0 and must be normalised at
    the source, not here — see each adapter)."""
    if v is not None and v > 1.0:
        _log.warning("dividend_yield %.4g > 1.0 (>100%%): treating as a percent "
                     "that slipped through, dividing by 100", v)
        return v / 100.0
    return v


# --------------------------------------------------------------------------- #
# Ticker normalization (provider-neutral, applied at INPUT)
# --------------------------------------------------------------------------- #
def normalize_ticker(raw: str) -> str:
    """Canonical ticker form: trim whitespace, upper-case, strip stray trailing
    dots.

    A dangling trailing '.' (e.g. ``000660.KS.`` pasted from prose or a sentence
    boundary) silently broke retrieval on the SK Hynix run — the symbol must end
    at its exchange suffix (``.KS``), never a dotted tail. Internal dots
    (``BRK.B``, ``NESN.SW``) are preserved; only trailing dots are removed.
    Applied at the input edge so the cleaned symbol also names the persisted
    verdict/report files, not just the provider call.
    """
    return (raw or "").strip().upper().rstrip(".")


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
    # GICS-style sector (yfinance info 'sector'; EODHD General::Sector). Used by the
    # rank engine's universe exclusions — e.g. Magic Formula EXCLUDES financials,
    # because ROIC is a meaningless calculation artifact on a bank's balance sheet.
    # None = provider didn't report it; callers must NOT exclude on unknown (only on
    # a confirmed sector match), so missing data never silently drops a name.
    sector: str | None = None
    # Listing/price currency (yfinance info 'currency') and the financial-
    # statements currency ('financialCurrency'). market_cap, last_close, and
    # dividend_per_share are all denominated in `currency`. A non-USD `currency`
    # makes USD-denominated absolute thresholds (min_market_cap) meaningless, so
    # those criteria honestly ABSTAIN rather than convert (no FX). None means the
    # provider didn't report it — treated as USD/unknown (evaluate normally), so
    # records predating this field are unaffected.
    currency: str | None = None
    financial_currency: str | None = None
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

    #: DATA-SHAPE DECLARATION (not logic): which dividend-streak computation method
    #: matches this provider's data shape. The streak is the one criterion whose
    #: correct computation depends on the provider's shape (ex-date timing noise vs
    #: split-adjusted annual totals), so the adapter STATES its shape and
    #: ``screening.streak_by_method`` owns the math. "per_payment_median" is the
    #: default (yfinance's shape); EODHD overrides to "calendar_year_sum".
    dividend_streak_method: str = "per_payment_median"

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

    def provider_for(self, data_kind: str) -> str:
        """Which provider actually produced a given data kind.

        ``data_kind`` is one of ``"dividends"``, ``"fundamentals"``, ``"prices"``.
        Single-source adapters answer with their own ``name``; ``HybridAdapter``
        overrides so a MIXED-source run records the real producer per data kind in
        the ledger — honest provenance, never flattened to a single ``"hybrid"``.
        """
        return self.name
