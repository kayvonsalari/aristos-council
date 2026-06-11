"""Deterministic sentiment aggregation.

Same hard rule as every tool: pure functions, all counting here, the LLM only
reasons about the result. The snapshot deliberately carries raw headlines too —
reading text IS the Sentiment specialist's job; what it may not do is count.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..data.sentiment import NewsItem, RecommendationTrend

MAX_HEADLINES = 15


@dataclass(frozen=True)
class SentimentSnapshot:
    news_count: int
    headlines: list[str]                  # most recent first, capped
    latest_trend_period: str | None
    analysts_total: int | None
    bullish_count: int | None             # strong_buy + buy
    bearish_count: int | None             # sell + strong_sell
    hold_count: int | None
    bullish_ratio: float | None           # bullish / total
    notes: list[str] = field(default_factory=list)


def sentiment_snapshot(
    news: list[NewsItem],
    trends: list[RecommendationTrend],
) -> SentimentSnapshot:
    notes: list[str] = []

    ordered = sorted(news, key=lambda n: n.published, reverse=True)
    headlines = [
        f"{n.published.isoformat()} [{n.source}] {n.headline}"
        for n in ordered[:MAX_HEADLINES] if n.headline
    ]
    if len(ordered) > MAX_HEADLINES:
        notes.append(
            f"headlines truncated to most recent {MAX_HEADLINES} "
            f"of {len(ordered)}"
        )

    latest = None
    for t in sorted(trends, key=lambda t: t.period, reverse=True):
        if t.total > 0:
            latest = t
            break
    if latest is None:
        notes.append("no analyst recommendation trend with coverage available")
        return SentimentSnapshot(
            news_count=len(news), headlines=headlines,
            latest_trend_period=None, analysts_total=None,
            bullish_count=None, bearish_count=None, hold_count=None,
            bullish_ratio=None, notes=notes,
        )

    bullish = latest.strong_buy + latest.buy
    bearish = latest.sell + latest.strong_sell
    return SentimentSnapshot(
        news_count=len(news),
        headlines=headlines,
        latest_trend_period=latest.period,
        analysts_total=latest.total,
        bullish_count=bullish,
        bearish_count=bearish,
        hold_count=latest.hold,
        bullish_ratio=round(bullish / latest.total, 4),
        notes=notes,
    )
