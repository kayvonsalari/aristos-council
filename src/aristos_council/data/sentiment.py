"""Provider-agnostic sentiment/news data adapter.

Same philosophy as adapter.py: specialists and tools see only these DTOs and
this interface, never a vendor SDK. Finnhub is the Phase 3 implementation; if
a better source appears later, it slots in here without touching the council.

A council built WITHOUT a sentiment adapter must behave exactly as before:
the Sentiment specialist finds no sentiment evidence and abstains.
"""

from __future__ import annotations

import abc
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
