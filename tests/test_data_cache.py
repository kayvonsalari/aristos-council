"""Date-stamped data cache — round-trip, daily key, --refresh bypass, miss-fetches.

Deterministic: a fake adapter counts fetches; the cache is a tmp dir; today is
injected (no real date)."""

from __future__ import annotations

from datetime import date

from aristos_council.data.adapter import (
    DividendEvent,
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.data.cache import CachingAdapter


class _CountingAdapter(MarketDataAdapter):
    name = "fake"

    def __init__(self):
        self.fund_calls = 0
        self.price_calls = 0
        self.div_calls = 0

    def get_fundamentals(self, ticker):
        self.fund_calls += 1
        return Fundamentals(ticker=ticker, name="F", market_cap=5e10, pe_ratio=20.0,
                            total_revenue=[180.0, 150.0, 125.0, 100.0])

    def get_price_history(self, ticker, *, start, end):
        self.price_calls += 1
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 2), open=1, high=2, low=0.5, close=1.5,
                     adj_close=1.5, volume=10)])

    def get_dividend_history(self, ticker, *, start, end):
        self.div_calls += 1
        return [DividendEvent(ex_date=date(2025, 6, 1), amount=1.25)]


_TODAY = date(2026, 6, 29)


def _cache(inner, tmp, *, today=_TODAY, refresh=False):
    return CachingAdapter(inner, cache_dir=tmp, today=today, refresh=refresh)


def test_fundamentals_round_trip_and_second_read_skips_fetch(tmp_path):
    inner = _CountingAdapter()
    c = _cache(inner, tmp_path)
    first = c.get_fundamentals("NVDA")
    second = c.get_fundamentals("NVDA")
    assert inner.fund_calls == 1                     # second served from cache
    assert first == second                           # round-trips identically
    assert first.total_revenue == [180.0, 150.0, 125.0, 100.0]


def test_prices_and_dividends_round_trip(tmp_path):
    inner = _CountingAdapter()
    c = _cache(inner, tmp_path)
    p1 = c.get_price_history("NVDA", start=date(2025, 1, 1), end=_TODAY)
    p2 = c.get_price_history("NVDA", start=date(2025, 1, 1), end=_TODAY)
    assert inner.price_calls == 1 and p1 == p2
    assert p1.bars[0].day == date(2026, 1, 2)        # date survived the round-trip
    d1 = c.get_dividend_history("NVDA", start=date(2020, 1, 1), end=_TODAY)
    d2 = c.get_dividend_history("NVDA", start=date(2020, 1, 1), end=_TODAY)
    assert inner.div_calls == 1 and d1 == d2
    assert d1[0].ex_date == date(2025, 6, 1)


def test_key_is_date_stamped_so_a_new_day_refetches(tmp_path):
    inner = _CountingAdapter()
    _cache(inner, tmp_path, today=date(2026, 6, 29)).get_fundamentals("NVDA")
    assert inner.fund_calls == 1
    # same ticker, NEXT day -> different key -> a fresh fetch
    _cache(inner, tmp_path, today=date(2026, 6, 30)).get_fundamentals("NVDA")
    assert inner.fund_calls == 2
    key = _cache(inner, tmp_path).cache_key("NVDA", "fundamentals")
    assert key == "fake:NVDA:2026-06-29:fundamentals"   # provider:ticker:date:kind


def test_refresh_bypasses_the_cache(tmp_path):
    inner = _CountingAdapter()
    _cache(inner, tmp_path).get_fundamentals("NVDA")      # warm the cache
    assert inner.fund_calls == 1
    _cache(inner, tmp_path, refresh=True).get_fundamentals("NVDA")  # forced refetch
    assert inner.fund_calls == 2


def test_miss_triggers_a_fetch_for_a_new_ticker(tmp_path):
    inner = _CountingAdapter()
    c = _cache(inner, tmp_path)
    c.get_fundamentals("NVDA")
    c.get_fundamentals("AMD")                        # different ticker -> miss
    assert inner.fund_calls == 2


def test_caching_adapter_mirrors_inner_identity(tmp_path):
    inner = _CountingAdapter()
    c = _cache(inner, tmp_path)
    assert c.name == inner.name
    assert c.dividend_streak_method == inner.dividend_streak_method
