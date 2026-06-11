from datetime import date

from aristos_council.data.sentiment import NewsItem, RecommendationTrend
from aristos_council.tools.sentiment_tools import (
    MAX_HEADLINES,
    sentiment_snapshot,
)


def _news(n):
    return [NewsItem(published=date(2026, 6, 1 + i % 9), headline=f"h{i}",
                     source="src") for i in range(n)]


def test_snapshot_counts_and_ratio():
    trends = [RecommendationTrend(period="2026-06-01", strong_buy=4, buy=6,
                                  hold=8, sell=1, strong_sell=1)]
    snap = sentiment_snapshot(_news(3), trends)
    assert snap.news_count == 3
    assert snap.analysts_total == 20
    assert snap.bullish_count == 10
    assert snap.bearish_count == 2
    assert snap.bullish_ratio == 0.5


def test_snapshot_picks_latest_nonempty_trend():
    trends = [
        RecommendationTrend(period="2026-06-01"),                  # empty
        RecommendationTrend(period="2026-05-01", buy=5, hold=5),   # has data
    ]
    snap = sentiment_snapshot([], trends)
    assert snap.latest_trend_period == "2026-05-01"
    assert snap.bullish_ratio == 0.5


def test_snapshot_no_trends_degrades_with_note():
    snap = sentiment_snapshot(_news(2), [])
    assert snap.bullish_ratio is None
    assert any("no analyst recommendation" in n for n in snap.notes)
    assert snap.news_count == 2


def test_headlines_capped_with_note():
    snap = sentiment_snapshot(_news(MAX_HEADLINES + 10), [])
    assert len(snap.headlines) == MAX_HEADLINES
    assert any("truncated" in n for n in snap.notes)
