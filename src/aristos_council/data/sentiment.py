"""Provider-agnostic sentiment/news data adapter.

Same philosophy as adapter.py: specialists and tools see only these DTOs and
this interface, never a vendor SDK. Finnhub is the Phase 3 implementation; if
a better source appears later, it slots in here without touching the council.

A council built WITHOUT a sentiment adapter must behave exactly as before:
the Sentiment specialist finds no sentiment evidence and abstains.
"""

from __future__ import annotations

import abc
import os
from dataclasses import dataclass, field
from datetime import date


class SentimentDataUnavailable(Exception):
    """Single failure type for sentiment providers (rate limit, bad key,
    empty response) — maps onto the DATA_QUALITY veto trigger."""


@dataclass(frozen=True)
class NewsItem:
    published: date
    headline: str
    source: str = ""


@dataclass(frozen=True)
class RecommendationTrend:
    """One month of aggregated analyst recommendations."""

    period: str            # e.g. "2026-06-01"
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0

    @property
    def total(self) -> int:
        return (self.strong_buy + self.buy + self.hold
                + self.sell + self.strong_sell)


class SentimentAdapter(abc.ABC):
    """Contract every sentiment/news provider must satisfy."""

    name: str = "abstract"

    @abc.abstractmethod
    def get_company_news(
        self, ticker: str, *, start: date, end: date
    ) -> list[NewsItem]:
        ...

    @abc.abstractmethod
    def get_recommendation_trends(
        self, ticker: str
    ) -> list[RecommendationTrend]:
        ...


# --------------------------------------------------------------------------- #
# SENT-WIRE-1 — ONE construction, used by every entry point
# --------------------------------------------------------------------------- #
def build_sentiment_adapter(*, provider: str = "finnhub"):
    """``(adapter, missing_key, error)`` — the sentiment provider, or an honest absence.

    Extracted from ``examples/run_council.py``, which was the ONLY place that ever built
    one. ``run_rank_pipeline`` accepts a ``sentiment_adapter`` and threads it all the way
    to the agents, but no caller ever constructed one — so on every universe run the
    Sentiment specialist abstained no matter what the key said, and the abstention blamed
    a missing key that was present. Both entry points now call this, so a future entry
    point cannot re-introduce the gap by forgetting to.

    THREE distinguishable outcomes, because they have three different fixes:
      * ``(None, True, "")``      no key — the abstention says so;
      * ``(None, False, "...")``  a key was present and construction FAILED, carrying the
                                  provider's own message (never a "no key" story);
      * ``(adapter, False, "")``  wired.

    ``error`` is a separate signal rather than something inferred from ``adapter is None``:
    a caller that simply does not configure sentiment (most tests, the upstream harness)
    passes neither flag and must NOT be reported as a broken tool.
    """
    if not (os.environ.get("FINNHUB_API_KEY") or "").strip():
        return None, True, ""
    try:
        from .finnhub_adapter import FinnhubAdapter

        return FinnhubAdapter(), False, ""
    except Exception as exc:                # construction failed despite a key present
        return None, False, str(exc) or exc.__class__.__name__
