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


class TransientFetchError(Exception):
    """A fetch that FAILED for a transient reason (timeout / 429 / 5xx / connection)
    and did NOT recover after retries. Distinct from ``DataUnavailable`` (ABSENT data —
    a clean 404 or an empty result) so the pipeline can ABORT the name with a
    'fetch failed — rerun' status instead of mislabelling live data as UNRATEABLE.
    Raised by ``data.retry.RetryAdapter`` after its attempts are exhausted."""


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
    # Cash-flow-statement lines for the FCF-basis payout criterion (max_payout_ratio_fcf).
    # dividends_paid is the ABSOLUTE cash paid in dividends; free cash flow is used
    # directly when present, else derived operating_cash_flow + capital_expenditure
    # (yfinance CapEx is negative). All None when the provider omits the cash-flow
    # statement -> the criterion falls back to the EPS payout basis (marked).
    dividends_paid: float | None = None
    operating_cash_flow: float | None = None
    capital_expenditure: float | None = None
    # Consecutive years of dividend increases — the defining aristocrat test.
    # Most free providers DON'T supply this; see adapter notes.
    years_dividend_growth: int | None = None

    # --- Defensive-risk signals derivable from FREE data (yield-trap separators) --- #
    # dividend_streak_years: consecutive YoY dividend INCREASES ending at the latest
    # complete year (derived from the payment history, screening.dividend_streak).
    # last_dividend_reduction_year: the most recent actual CUT (a FLAT year ends the
    # growth streak but is NOT a cut). total_debt / debt_to_equity: balance-sheet
    # leverage — d/e is UNDEFINED for negative-equity (heavy-buyback) names like MCD,
    # so leverage gates use a total_debt-vs-market-cap measure, never excluding on a
    # None d/e. All None when the provider/history can't supply them (honest abstain).
    dividend_streak_years: int | None = None
    last_dividend_reduction_year: int | None = None
    total_debt: float | None = None
    debt_to_equity: float | None = None          # yfinance percent-ish; may be None
    # Cash & short-term investments (yfinance info 'totalCash'). With total_debt +
    # market_cap this gives enterprise value (EV = market cap + total debt − cash) for
    # the EBIT/EV earnings-yield factor. None when the provider omits it -> the factor
    # falls back to EBIT/market_cap (see factors.enterprise_value).
    total_cash: float | None = None

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


@dataclass(frozen=True)
class StreetConsensus:
    """Sell-side analyst consensus snapshot (provider 'info'-style fields).

    Every field is Optional: a provider that doesn't report one records ``None`` —
    the same abstain-not-guess discipline the rest of the system uses, applied to
    analyst data. ``recommendation_mean`` is on yfinance's 1=StrongBuy .. 5=Sell
    scale (LOWER = more bullish); the prospective scoreboard buckets it by RELATIVE
    terciles, never absolute bands (see ``scoreboard`` — absolute bands are
    structurally all-BUY on the observed universes).
    """

    ticker: str
    recommendation_mean: float | None = None     # 1=StrongBuy .. 5=Sell
    n_analysts: int | None = None
    target_mean_price: float | None = None
    current_price: float | None = None


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

    def get_street_consensus(self, ticker: str) -> StreetConsensus:
        """Sell-side analyst consensus for a ticker.

        DEFAULT: an all-null abstention — a provider without analyst data records
        nulls rather than guessing, and every existing adapter/fake keeps working
        without change. Concrete adapters that HAVE the data (yfinance) override.
        Deliberately NOT abstract: this is a read-only add-on for the prospective
        scoreboard, orthogonal to the council's data path.
        """
        return StreetConsensus(ticker=ticker)

    def provider_for(self, data_kind: str) -> str:
        """Which provider actually produced a given data kind.

        ``data_kind`` is one of ``"dividends"``, ``"fundamentals"``, ``"prices"``.
        Single-source adapters answer with their own ``name``; ``HybridAdapter``
        overrides so a MIXED-source run records the real producer per data kind in
        the ledger — honest provenance, never flattened to a single ``"hybrid"``.
        """
        return self.name
