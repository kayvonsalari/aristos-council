"""HybridAdapter — EODHD dividends + yfinance fundamentals/prices.

Composition, NOT reimplementation. It wraps one ``EODHDAdapter`` and one
``YFinanceAdapter`` and delegates each method to the right source:

    get_dividend_history -> EODHD     (deep, clean, adjusted; the verified 45yr KO streak)
    get_fundamentals     -> yfinance  (free; adequate for US dividend names)
    get_price_history    -> yfinance

Why this exists: EODHD's ``/fundamentals`` endpoint sits behind a paid tier that
403s on the current key, but its ``/div`` history is exactly what makes the
multi-decade aristocrat streak verifiable. The hybrid takes the best of each and
unblocks a clean ``dividend_aristocrats_v1`` run without paying for EODHD
fundamentals — wrapping the two adapters unchanged, never modifying them.

CRITICAL — ``dividend_streak_method``: the streak is computed FROM THE DIVIDENDS,
and the dividends come from EODHD, so the method MUST be EODHD's shape
(``calendar_year_sum``), NOT the yfinance default (``per_payment_median``). The
yfinance method would silently false-break the streak on a cadence change
(annual -> Interim/Final). Set explicitly below and asserted in the tests.
"""

from __future__ import annotations

from datetime import date

from .adapter import (
    DividendEvent,
    Fundamentals,
    MarketDataAdapter,
    PriceHistory,
    StreetConsensus,
)


class HybridAdapter(MarketDataAdapter):
    name = "hybrid"
    # Dividends (hence the streak) come from EODHD -> EODHD's calendar-year method.
    # NOT the inherited yfinance default; getting this wrong false-breaks the streak.
    dividend_streak_method = "calendar_year_sum"

    def __init__(self, eodhd=None, yfinance=None) -> None:
        # Lazy construction: EODHD validates its key only at first fetch, and the
        # yfinance import is heavy — so build each only when not injected (tests
        # inject fakes for both).
        if eodhd is None:
            from .eodhd_adapter import EODHDAdapter
            eodhd = EODHDAdapter()
        if yfinance is None:
            from .yfinance_adapter import YFinanceAdapter
            yfinance = YFinanceAdapter()
        self._eodhd = eodhd
        self._yf = yfinance

    # --- delegation (no logic of its own) ----------------------------------- #
    def get_dividend_history(
        self, ticker: str, *, start: date, end: date
    ) -> list[DividendEvent]:
        return self._eodhd.get_dividend_history(ticker, start=start, end=end)

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        return self._yf.get_fundamentals(ticker)

    def get_price_history(
        self, ticker: str, *, start: date, end: date
    ) -> PriceHistory:
        return self._yf.get_price_history(ticker, start=start, end=end)

    def get_street_consensus(self, ticker: str) -> StreetConsensus:
        # Consensus rides with fundamentals/prices -> yfinance (EODHD /fundamentals
        # is paywalled; its default would abstain). Same source as get_fundamentals.
        return self._yf.get_street_consensus(ticker)

    # --- honest per-source provenance --------------------------------------- #
    def provider_for(self, data_kind: str) -> str:
        """Dividends are sourced to EODHD, fundamentals/prices to yfinance — so the
        ledger records the REAL producer per figure, never flattened to 'hybrid'."""
        return self._eodhd.name if data_kind == "dividends" else self._yf.name
