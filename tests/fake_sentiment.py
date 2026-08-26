"""A deterministic sentiment provider for tests whose subject IS the sentiment channel.

SENT-ISOLATE-1 made an empty sentiment channel abstain WITHOUT invoking the model, which
is the point — "bullish 0.72" reasoned from rank-sums is now structurally impossible.
That also means a test that wants the Sentiment specialist to actually RUN must give it
something to read. Passing this fake is the honest fix: those tests are about the
specialist's behaviour, not about the channel being dark.
"""

from __future__ import annotations

from datetime import date

from aristos_council.data.sentiment import (
    NewsItem,
    RecommendationTrend,
    SentimentAdapter,
    SentimentDataUnavailable,
)


class FakeSentiment(SentimentAdapter):
    """Two headlines and one analyst-trend row — enough for a live, non-empty channel."""

    name = "fake-sentiment"

    def __init__(self, *, news: int = 2):
        self._news = news

    def get_company_news(self, ticker: str, *, start: date, end: date):
        return [
            NewsItem(published=date(2026, 8, 20),
                     headline=f"{ticker} headline {i}", source="test")
            for i in range(self._news)
        ]

    def get_recommendation_trends(self, ticker: str):
        return [RecommendationTrend(period="2026-08", strong_buy=3, buy=5, hold=2,
                                    sell=1, strong_sell=0)]


class EmptySentiment(FakeSentiment):
    """Wired, reachable, and it returned nothing — a real finding about the name rather
    than a broken tool. Distinct from having no adapter at all."""

    name = "empty-sentiment"

    def get_company_news(self, ticker: str, *, start: date, end: date):
        return []

    def get_recommendation_trends(self, ticker: str):
        return []


class FailingSentiment(FakeSentiment):
    """Wired and REFUSED — the SK hynix case: Finnhub answers 403 for some listings.
    The run must degrade to honest abstention carrying the provider's own message."""

    name = "failing-sentiment"

    def __init__(self, message: str = "Finnhub /company-news HTTP 403"):
        super().__init__()
        self.message = message

    def get_company_news(self, ticker: str, *, start: date, end: date):
        raise SentimentDataUnavailable(self.message)

    def get_recommendation_trends(self, ticker: str):
        raise SentimentDataUnavailable(
            self.message.replace("/company-news", "/stock/recommendation"))
