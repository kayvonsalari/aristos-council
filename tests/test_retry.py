"""Adapter armor (hardening ITEM 5): a transient failure is retried and, if it does not
recover, aborts the name with a FETCH-ERROR status — never mislabelled UNRATEABLE. A
clean 404 stays ABSENT -> UNRATEABLE. The cache (reused CachingAdapter) is consulted
before fetching. No network.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aristos_council.data.adapter import (
    DataUnavailable,
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
    TransientFetchError,
)
from aristos_council.data.cache import CachingAdapter
from aristos_council.data.retry import RetryAdapter, is_transient
from aristos_council.pipeline import run_rank_pipeline

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
_NO_SLEEP = lambda _s: None                                     # noqa: E731


# --------------------------------------------------------------------------- #
# Classification + retry/backoff
# --------------------------------------------------------------------------- #
def test_is_transient_classification():
    assert is_transient(DataUnavailable("429 Too Many Requests")) is True
    assert is_transient(DataUnavailable("Read timed out")) is True
    assert is_transient(DataUnavailable("503 Service Unavailable")) is True
    assert is_transient(DataUnavailable("No fundamentals for XYZ")) is False   # ABSENT
    assert is_transient(DataUnavailable("No price history in range")) is False


class _Flaky(MarketDataAdapter):
    name = "fake"

    def __init__(self, exc_seq):
        self._seq = list(exc_seq)          # exceptions to raise, then succeed
        self.calls = 0

    def get_fundamentals(self, ticker):
        self.calls += 1
        if self._seq:
            raise self._seq.pop(0)
        return Fundamentals(ticker=ticker, market_cap=1e10)

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker)

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_transient_retried_then_raises_transient_fetch_error():
    inner = _Flaky([DataUnavailable("429")] * 5)               # always throttled
    sleeps = []
    r = RetryAdapter(inner, attempts=3, sleep=sleeps.append)
    with pytest.raises(TransientFetchError):
        r.get_fundamentals("AAPL")
    assert inner.calls == 3                                     # 3 attempts
    assert len(sleeps) == 2                                     # backoff between attempts
    assert sleeps == [0.5, 1.0]                                 # exponential


def test_transient_that_recovers_returns_the_value():
    inner = _Flaky([DataUnavailable("timeout")])               # fails once, then ok
    r = RetryAdapter(inner, attempts=3, sleep=_NO_SLEEP)
    assert r.get_fundamentals("AAPL").market_cap == 1e10
    assert inner.calls == 2


def test_absent_error_is_not_retried():
    inner = _Flaky([DataUnavailable("No fundamentals for AAPL")])   # clean absent
    r = RetryAdapter(inner, attempts=3, sleep=_NO_SLEEP)
    with pytest.raises(DataUnavailable):                        # re-raised as-is
        r.get_fundamentals("AAPL")
    assert inner.calls == 1                                     # NOT retried


# --------------------------------------------------------------------------- #
# Pipeline: transient -> FETCH_ERROR (rerun); clean 404 -> UNRATEABLE
# --------------------------------------------------------------------------- #
_FUND = {
    "A": dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0] * 4, tax_provision=[600.0] * 4,
              pretax_income=[2900.0] * 4, invested_capital=[5000.0] * 4),
    "B": dict(market_cap=2e10, sector="Technology", ebit=[1500.0], pe_ratio=20.0,
              operating_income=[1500.0] * 4, tax_provision=[300.0] * 4,
              pretax_income=[1450.0] * 4, invested_capital=[5000.0] * 4),
}


class _MixedAdapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        if ticker == "THROTTLED":
            raise DataUnavailable("429 Too Many Requests")     # transient
        if ticker == "DEAD":
            return Fundamentals(ticker="DEAD")                 # shell (absent)
        return Fundamentals(ticker=ticker, name=ticker, **_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        if ticker == "THROTTLED":
            raise DataUnavailable("429 Too Many Requests")
        if ticker == "DEAD":
            raise RuntimeError("no timezone found, symbol may be delisted")
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_throttled_name_is_fetch_error_not_unrateable():
    adapter = RetryAdapter(_MixedAdapter(), attempts=3, sleep=_NO_SLEEP)
    result = run_rank_pipeline(
        ["A", "B", "THROTTLED", "DEAD"], "magic_formula_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=adapter, today=date(2026, 6, 30))

    fetch = {t for t, _ in result.fetch_errors}
    unrate = {t for t, _ in result.unrateable}
    assert "THROTTLED" in fetch and "THROTTLED" not in unrate     # transient -> rerun
    assert "DEAD" in unrate and "DEAD" not in fetch               # absent -> UNRATEABLE
    assert {r.ticker for r in result.ranked} == {"A", "B"}        # healthy names ranked
    assert result.meta["fetch_error_count"] == 1
    # THROTTLED never became a (worst-ranked) verdict
    assert "THROTTLED" not in {r.ticker for r in result.ranked}


# --------------------------------------------------------------------------- #
# Cache (reused CachingAdapter) — consulted before fetching; daily TTL
# --------------------------------------------------------------------------- #
class _Counting(MarketDataAdapter):
    name = "fake"

    def __init__(self):
        self.calls = 0

    def get_fundamentals(self, ticker):
        self.calls += 1
        return Fundamentals(ticker=ticker, market_cap=1e10)

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker)

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_cache_hit_avoids_a_second_fetch(tmp_path):
    inner = _Counting()
    cad = CachingAdapter(inner, cache_dir=tmp_path, today=date(2026, 7, 5))
    cad.get_fundamentals("AAPL")
    cad.get_fundamentals("AAPL")
    assert inner.calls == 1                                      # 2nd served from cache


def test_cache_ttl_refetches_on_a_new_day(tmp_path):
    inner = _Counting()
    CachingAdapter(inner, cache_dir=tmp_path,
                   today=date(2026, 7, 5)).get_fundamentals("AAPL")
    CachingAdapter(inner, cache_dir=tmp_path,
                   today=date(2026, 7, 6)).get_fundamentals("AAPL")   # next day
    assert inner.calls == 2                                      # TTL expired -> refetch
