"""EODHD adapter stub (Phase 2 provider).

Status: STUB. Interface-complete so the composition root can switch to it the
moment a key is available, but every method raises NotImplementedError rather
than pretending to work. This keeps the swap honest — nothing silently returns
empty data.

Why EODHD is the planned upgrade from yfinance
----------------------------------------------
- Long, clean dividend history → lets us actually verify the 25-year
  consecutive-increase aristocrat criterion that yfinance can't support.
- Fundamentals endpoint exposes payout ratios, FCF, and dividend-growth data in
  one structured call.
- Stable schema (vs. yfinance's version drift).

ACTION ITEM (Kay): EODHD offers a student discount — reportedly ~50% off — via
academic verification. Worth checking whether the JHU enrolment qualifies before
committing to a paid tier. Confirm the exact current discount and eligibility
directly with EODHD; don't take the 50% figure as gospel until verified.

Implementation guidance for when the key lands
----------------------------------------------
- Read the key from env (e.g. EODHD_API_KEY); never hard-code it.
- Map EODHD's JSON into the SAME DTOs in adapter.py — do not introduce new
  return types, or you defeat the point of the seam.
- Translate HTTP errors / rate limits into DataUnavailable.
- `years_dividend_growth` should be COMPUTED from the full dividend series here
  (where the history is long enough to be trustworthy), not left None as in the
  yfinance adapter.
"""

from __future__ import annotations

import os
from datetime import date

from .adapter import (
    DataUnavailable,
    DividendEvent,
    Fundamentals,
    MarketDataAdapter,
    PriceHistory,
)

_NOT_READY = (
    "EODHDAdapter is a Phase 2 stub. Provide EODHD_API_KEY and implement the "
    "EODHD JSON -> DTO mapping before using this provider."
)


class EODHDAdapter(MarketDataAdapter):
    name = "eodhd"

    def __init__(self, api_key: str | None = None) -> None:
        # Resolve but do NOT validate against the network yet — we only store it
        # so the eventual implementation has it ready.
        self._api_key = api_key or os.environ.get("EODHD_API_KEY")

    def get_price_history(
        self, ticker: str, *, start: date, end: date
    ) -> PriceHistory:
        raise NotImplementedError(_NOT_READY)

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        raise NotImplementedError(_NOT_READY)

    def get_dividend_history(
        self, ticker: str, *, start: date, end: date
    ) -> list[DividendEvent]:
        raise NotImplementedError(_NOT_READY)

    # Small helper kept here so the future implementer has the guard rail ready.
    def _require_key(self) -> str:
        if not self._api_key:
            raise DataUnavailable("EODHD_API_KEY is not set")
        return self._api_key
