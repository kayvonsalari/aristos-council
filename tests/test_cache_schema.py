"""Schema-marked cache (cleanup ITEM 2): a cache entry written under an OLD DTO shape is
detected as stale on read and refetched — never silently deserialised with new fields as
None (tonight's root cause: fundamentals cached pre-total_cash read all day as EBIT/mcap).
"""

from __future__ import annotations

import json
from datetime import date

from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceHistory,
)
from aristos_council.data.cache import CachingAdapter

TODAY = date(2026, 7, 5)


class _Counting(MarketDataAdapter):
    name = "fake"

    def __init__(self):
        self.calls = 0

    def get_fundamentals(self, ticker):
        self.calls += 1
        return Fundamentals(ticker=ticker, market_cap=1e10, total_cash=2e9)

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker)

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _write(cad, ticker, payload):
    p = cad._path(ticker, "fundamentals")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(payload, encoding="utf-8")
    return p


def test_matching_schema_is_a_hit(tmp_path):
    inner = _Counting()
    cad = CachingAdapter(inner, cache_dir=tmp_path, today=TODAY)
    cad.get_fundamentals("AAPL")             # writes schema-stamped
    cad.get_fundamentals("AAPL")             # matching marker -> HIT
    assert inner.calls == 1


def test_stale_schema_marker_is_a_miss_and_rewrites(tmp_path):
    inner = _Counting()
    cad = CachingAdapter(inner, cache_dir=tmp_path, today=TODAY)
    _write(cad, "AAPL", json.dumps(
        {"_schema": "an,old,shape", "data": {"ticker": "AAPL", "market_cap": 5e9}}))
    f = cad.get_fundamentals("AAPL")
    assert inner.calls == 1                  # stale marker -> refetch
    assert f.market_cap == 1e10              # the FRESH value, not the stale 5e9
    cad.get_fundamentals("AAPL")             # rewritten under current schema -> HIT
    assert inner.calls == 1


def test_pre_marker_bare_file_is_a_miss(tmp_path):
    # a pre-ITEM-2 file: the bare serialized dict, no _schema wrapper
    inner = _Counting()
    cad = CachingAdapter(inner, cache_dir=tmp_path, today=TODAY)
    _write(cad, "AAPL", json.dumps({"ticker": "AAPL", "market_cap": 5e9}))
    cad.get_fundamentals("AAPL")
    assert inner.calls == 1                  # missing marker -> refetch


def test_corrupted_file_is_a_miss_not_a_crash(tmp_path):
    inner = _Counting()
    cad = CachingAdapter(inner, cache_dir=tmp_path, today=TODAY)
    _write(cad, "AAPL", "{ not valid json at all")
    f = cad.get_fundamentals("AAPL")         # must not raise
    assert inner.calls == 1 and f.market_cap == 1e10
