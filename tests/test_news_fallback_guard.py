"""SENT-FALLBACK-1 — the TEST-ISOLATION-1 guard covers the news-fallback fetchers too.

Its own module, deliberately (same convention as ``test_gap_ledger_ibkr_guard.py`` /
``test_paper_trade_ibkr_paper_guard.py``): these two factories are the ONLY seam a test
could use to accidentally reach a real yfinance/EODHD call from ``fetch_eodhd_news`` /
``fetch_yfinance_news`` when it forgets to pass its own ``opener``/``fetcher`` — unlike
Finnhub and EODHD's OTHER endpoints, neither is naturally key-gated into silence
(yfinance needs no key at all), so the guard is the only thing standing between an
unconfigured test and a live call. Also pins that ``fetch_yfinance_news`` does NOT
swallow the guard's own AssertionError in its "an optional fallback, never fatal"
handler — a real regression caught while building this feature (Batch 12 item 1b): the
guard fired, but the broad ``except Exception`` quietly turned it into an ordinary-
looking "no news found" instead of a loud test failure.
"""
from __future__ import annotations

import pytest

from aristos_council.data import news_fallback


def test_the_suite_refuses_the_real_eodhd_news_opener():
    with pytest.raises(AssertionError) as caught:
        news_fallback.real_url_opener()
    assert "inject a fake" in str(caught.value)
    assert "data.news_fallback.real_url_opener" in str(caught.value)


def test_the_suite_refuses_the_real_yfinance_news_fetcher():
    with pytest.raises(AssertionError) as caught:
        news_fallback.real_yfinance_news()
    assert "inject a fake" in str(caught.value)
    assert "data.news_fallback.real_yfinance_news" in str(caught.value)


def test_fetch_yfinance_news_does_not_swallow_the_guards_own_assertion():
    """The regression: a broad ``except Exception`` around the actual fetch call used to
    also catch the TEST-ISOLATION-1 guard's AssertionError (raised one line earlier, by
    just calling the guarded factory), silently reporting "no news found" instead of
    failing the test loudly. The guard is resolved OUTSIDE that try block now."""
    with pytest.raises(AssertionError):
        news_fallback.fetch_yfinance_news("RIO.AX")
