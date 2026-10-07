"""HTML-ENTITIES-1 - provider headlines are unescaped once, at ingest (JPM, 2026-10-06 showed
"S&amp;P 500" and "JPMorgan Chase &amp; Co." in the Markdown and HTML reports)."""
from datetime import date

from aristos_council.data.news_fallback import fetch_eodhd_news, fetch_yfinance_news
from aristos_council.data.sentiment import clean_headline
from tests.test_sentiment_fallback import _FakeResponse

RAW = "JPMorgan says a blue wave could slam AI stocks now 45% of the S&amp;P 500"
CLEAN = "JPMorgan says a blue wave could slam AI stocks now 45% of the S&P 500"


def test_clean_headline_decodes_entities_once():
    assert clean_headline(RAW) == CLEAN
    assert clean_headline("JPMorgan Chase &amp; Co. &#39;beats&#39;") == "JPMorgan Chase & Co. 'beats'"
    assert clean_headline("a &amp;amp; b") == "a &amp; b"        # once: never a second pass
    assert clean_headline(None) == ""


def test_eodhd_headlines_are_unescaped():
    items, _ = fetch_eodhd_news("RIO.AX", start=date(2026, 9, 1), end=date(2026, 9, 30), api_key="k",
                                opener=lambda url, timeout: _FakeResponse(
                                    [{"date": "2026-09-30", "title": RAW, "source": "x"}]))
    assert items[0].headline == CLEAN


def test_yfinance_headlines_are_unescaped():
    raw = [{"content": {"title": RAW, "pubDate": "2026-09-30T08:00:00Z",
                        "provider": {"displayName": "Yahoo"}}}]
    items, _ = fetch_yfinance_news("JPM", fetcher=lambda sym: raw)
    assert items[0].headline == CLEAN
