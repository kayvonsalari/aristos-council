"""The data a lens backtest reads (BACKTEST-1): EODHD accounts WITH their fiscal-period dates,
yfinance prices and dividends, and one in-process memo so ten years of monthly rounds cost one
request per company, not one per round.

Two small pieces, both plain ``MarketDataAdapter``s so ``AsOfAdapter`` and the rank pipeline see
nothing unusual:

``BacktestFeed``   accounts from EODHD ``/fundamentals`` (the only source here that dates each yearly
                   statement), prices and dividends from yfinance. The fundamentals cache key is its
                   own (``provider_for("fundamentals")`` -> ``eodhd-dated``): a dated read carries
                   more than a normal EODHD read, and sharing one cache file with it would hand a
                   dated-period consumer an undated payload (or the other way round).
``MemoAdapter``    remembers every answer for the life of the process. Prices and dividends are
                   fetched ONCE per company over the whole backtest window and sliced per request;
                   a failure is remembered too, so 108 rounds do not retry a dead ticker 108 times.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from .adapter import DividendEvent, Fundamentals, MarketDataAdapter, PriceHistory

FUNDAMENTALS_KIND = "eodhd-dated"


class BacktestFeed(MarketDataAdapter):
    name = "backtest-feed"

    def __init__(self, fundamentals: MarketDataAdapter | None = None,
                 market: MarketDataAdapter | None = None) -> None:
        # Built lazily, like HybridAdapter: EODHD checks its key at first fetch and yfinance is a
        # heavy import, so tests inject fakes for both.
        if fundamentals is None:
            from .eodhd_adapter import EODHDAdapter
            fundamentals = EODHDAdapter(with_periods=True)
        if market is None:
            from .yfinance_adapter import YFinanceAdapter
            market = YFinanceAdapter()
        self._fund = fundamentals
        self._market = market

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        return self._fund.get_fundamentals(ticker)

    def get_price_history(self, ticker: str, *, start: date, end: date) -> PriceHistory:
        return self._market.get_price_history(ticker, start=start, end=end)

    def get_dividend_history(self, ticker: str, *, start: date, end: date) -> list[DividendEvent]:
        return self._market.get_dividend_history(ticker, start=start, end=end)

    def provider_for(self, data_kind: str) -> str:
        return FUNDAMENTALS_KIND if data_kind == "fundamentals" else self._market.name


class MemoAdapter(MarketDataAdapter):
    """Process-lifetime memo over ``inner``; see the module docstring.

    ``window`` is the (start, end) the backtest will need; the first price or dividend request for
    a ticker fetches that whole span (widened to include the request) and every later request is a
    slice of it. A request that reaches outside what was fetched widens it and fetches again."""

    def __init__(self, inner: MarketDataAdapter, *, start: date, end: date) -> None:
        self._inner = inner
        self._start, self._end = start, end
        self.name = getattr(inner, "name", "memo")
        self.dividend_streak_method = getattr(inner, "dividend_streak_method", "per_payment_median")
        self._fund: dict[str, object] = {}
        self._prices: dict[str, tuple[date, date, object]] = {}
        self._divs: dict[str, tuple[date, date, object]] = {}
        self.fetches = {"fundamentals": 0, "prices": 0, "dividends": 0}

    def provider_for(self, data_kind: str) -> str:
        return self._inner.provider_for(data_kind)

    @staticmethod
    def _recall(entry):
        if isinstance(entry, BaseException):
            raise entry
        return entry

    def _wide(self, store: dict, ticker: str, start: date, end: date, kind: str, fetch):
        held = store.get(ticker)
        if held is None or start < held[0] or end > held[1]:
            lo, hi = min(start, self._start), max(end, self._end)
            if held is not None:
                lo, hi = min(lo, held[0]), max(hi, held[1])
            try:
                got: object = fetch(lo, hi)
            except Exception as exc:                        # noqa: BLE001 - remembered, re-raised
                got = exc
            self.fetches[kind] += 1
            held = store[ticker] = (lo, hi, got)
        return self._recall(held[2])

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        if ticker not in self._fund:
            try:
                self._fund[ticker] = self._inner.get_fundamentals(ticker)
            except Exception as exc:                        # noqa: BLE001 - remembered, re-raised
                self._fund[ticker] = exc
            self.fetches["fundamentals"] += 1
        return self._recall(self._fund[ticker])

    def get_price_history(self, ticker: str, *, start: date, end: date) -> PriceHistory:
        history = self._wide(self._prices, ticker, start, end, "prices",
                             lambda lo, hi: self._inner.get_price_history(ticker, start=lo, end=hi))
        return replace(history, bars=[b for b in history.bars if start <= b.day <= end])

    def get_dividend_history(self, ticker: str, *, start: date, end: date) -> list[DividendEvent]:
        events = self._wide(self._divs, ticker, start, end, "dividends",
                            lambda lo, hi: self._inner.get_dividend_history(ticker, start=lo, end=hi))
        return [e for e in events if start <= e.ex_date <= end]


def lookback_start(first_as_of: date, days: int = 800) -> date:
    """Earliest price a round beginning at ``first_as_of`` can ask for (the factor windows are
    ~400 days; the margin is for a longer lens)."""
    return first_as_of - timedelta(days=days)
