"""Tiny on-disk DATA cache for a market-data adapter.

Re-running the same ticker re-fetches market data every time (slow, and the user
re-runs constantly). ``CachingAdapter`` wraps any ``MarketDataAdapter`` and caches
each raw fetch as JSON, keyed by ``{provider}:{ticker}:{YYYY-MM-DD}:{kind}`` so it
auto-refreshes daily. ``refresh=True`` forces a fresh fetch.

This speeds up the DATA step ONLY — it never caches LLM calls (verdicts must stay
live), so it helps most on the screen-only ranking path where data is the main cost.
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from datetime import date
from pathlib import Path

from .adapter import (
    DividendEvent,
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)

_FUND_FIELDS = {f.name for f in fields(Fundamentals)}


# --- per-type (de)serialisers: dataclasses with date fields aren't JSON-native --- #
def _ser_fundamentals(f: Fundamentals) -> dict:
    return asdict(f)


def _deser_fundamentals(d: dict) -> Fundamentals:
    # Tolerate schema drift: only feed known fields (date-stamped key already bounds
    # staleness to one day, but be defensive about a mid-day code change).
    return Fundamentals(**{k: v for k, v in d.items() if k in _FUND_FIELDS})


def _ser_prices(p: PriceHistory) -> dict:
    return {"ticker": p.ticker,
            "bars": [{"day": b.day.isoformat(), "open": b.open, "high": b.high,
                      "low": b.low, "close": b.close, "adj_close": b.adj_close,
                      "volume": b.volume} for b in p.bars]}


def _deser_prices(d: dict) -> PriceHistory:
    return PriceHistory(ticker=d["ticker"], bars=[
        PriceBar(day=date.fromisoformat(b["day"]), open=b["open"], high=b["high"],
                 low=b["low"], close=b["close"], adj_close=b["adj_close"],
                 volume=b["volume"]) for b in d["bars"]])


def _ser_dividends(evs: list) -> list:
    return [{"ex_date": e.ex_date.isoformat(), "amount": e.amount} for e in evs]


def _deser_dividends(d: list) -> list:
    return [DividendEvent(ex_date=date.fromisoformat(x["ex_date"]),
                          amount=x["amount"]) for x in d]


DEFAULT_CACHE_DIR = ".aristos_cache"


class CachingAdapter(MarketDataAdapter):
    """Wraps an adapter; caches raw fetches to dated JSON files. Delegates
    provider/streak metadata to the inner adapter so provenance is unchanged."""

    def __init__(self, inner: MarketDataAdapter, *, cache_dir: str | Path,
                 today: date, refresh: bool = False):
        self._inner = inner
        self._dir = Path(cache_dir)
        self._today = today
        self._refresh = refresh
        # Mirror the inner adapter's identity so downstream code is none the wiser.
        self.name = inner.name
        self.dividend_streak_method = inner.dividend_streak_method

    def provider_for(self, data_kind: str) -> str:
        return self._inner.provider_for(data_kind)

    # --- cache plumbing --- #
    def cache_key(self, ticker: str, kind: str) -> str:
        return f"{self._inner.provider_for(kind)}:{ticker}:{self._today.isoformat()}:{kind}"

    def _path(self, ticker: str, kind: str) -> Path:
        safe = "".join(ch if ch.isalnum() else "_"
                       for ch in self.cache_key(ticker, kind))
        return self._dir / f"{safe}.json"

    def _cached(self, ticker, kind, fetch, ser, deser):
        path = self._path(ticker, kind)
        if not self._refresh and path.exists():
            try:
                return deser(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass   # corrupt/incompatible cache -> fall through and refetch
        obj = fetch()
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ser(obj)), encoding="utf-8")
        return obj

    # --- MarketDataAdapter interface --- #
    def get_fundamentals(self, ticker):
        return self._cached(ticker, "fundamentals",
                            lambda: self._inner.get_fundamentals(ticker),
                            _ser_fundamentals, _deser_fundamentals)

    def get_price_history(self, ticker, *, start, end):
        return self._cached(ticker, "prices",
                            lambda: self._inner.get_price_history(
                                ticker, start=start, end=end),
                            _ser_prices, _deser_prices)

    def get_dividend_history(self, ticker, *, start, end):
        return self._cached(ticker, "dividends",
                            lambda: self._inner.get_dividend_history(
                                ticker, start=start, end=end),
                            _ser_dividends, _deser_dividends)
