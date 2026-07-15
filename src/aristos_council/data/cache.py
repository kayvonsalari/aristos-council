"""Tiny on-disk DATA cache for a market-data adapter.

Re-running the same ticker re-fetches market data every time (slow, and the user
re-runs constantly). ``CachingAdapter`` wraps any ``MarketDataAdapter`` and caches
each raw fetch as JSON, keyed by ``{provider}:{ticker}:{YYYY-MM-DD}:{kind}`` so it
auto-refreshes daily. ``refresh=True`` forces a fresh fetch.

This speeds up the DATA step ONLY — it never caches LLM calls (verdicts must stay
live), so it helps most on the screen-only ranking path where data is the main cost.

Each entry carries a ``_schema`` marker (adapter-version token + DTO field-name set,
see ``_schema_marker`` / ``ADAPTER_SCHEMA_VERSION``); an entry whose marker no longer
matches is bypassed and refetched, so an adapter change automatically invalidates
stale entries — manual cache deletion is never the remedy.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, fields
from datetime import date
from pathlib import Path

_log = logging.getLogger(__name__)

from .adapter import (
    DividendEvent,
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
    StreetConsensus,
)

_FUND_FIELDS = {f.name for f in fields(Fundamentals)}
_CONSENSUS_FIELDS = {f.name for f in fields(StreetConsensus)}


def _ser_consensus(c: StreetConsensus) -> dict:
    return asdict(c)


def _deser_consensus(d: dict) -> StreetConsensus:
    return StreetConsensus(**{k: v for k, v in d.items() if k in _CONSENSUS_FIELDS})


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


# --------------------------------------------------------------------------- #
# ADAPTER SCHEMA VERSION — bump this integer whenever an adapter changes the
# SHAPE or SEMANTICS of a cached payload WITHOUT changing the DTO field-name set.
#
# WHY a hand-bumped token (not just the field-name marker below): the field-set
# marker only changes when a field is added/removed. An adapter fix that changes
# how an EXISTING field is populated leaves the field set untouched, so the marker
# would still match and a stale entry would keep being served. That is exactly the
# ETFCHK-4 incident: pre-fix code cached ETF ``dividend_yield`` as None (rendered
# 0 [computed]); two merged fixes changed how the field is DERIVED but not the
# field set, so cached entries kept serving 0 until the cache dir was manually
# deleted. Folding this version into the schema marker forces every pre-bump entry
# to be detected as stale on read and refetched — manual deletion is never again
# the remedy.
#
# BUMP CONVENTION: increment by 1 and add a one-line ``# vN: <what changed>`` entry
# to the log below, whenever an adapter's normalization/derivation changes a cached
# value's meaning without changing its DTO's fields. (Field ADD/REMOVE is already
# caught by the field-name marker — no bump needed for those.)
#   v1: initial field-name marker only (pre-ETFCHK-4).
#   v2: ETF dividend_yield derivation fix (ETFCHK-4) — same field set, new value.
ADAPTER_SCHEMA_VERSION = 2


def _schema_marker(cls) -> str:
    """A stable marker for a DTO's shape: an adapter-version token + its sorted
    field-name set. The field-name set catches a field ADD/REMOVE (e.g.
    Fundamentals.total_cash); the leading ``vN`` token (``ADAPTER_SCHEMA_VERSION``)
    catches a shape/semantics change to an EXISTING field that leaves the field set
    unchanged (ETFCHK-4: ETF dividend_yield re-derived — same fields, new value).
    Either kind of change makes the marker differ, so an entry written under the OLD
    shape is detected as stale on read and refetched, never silently deserialised."""
    return f"v{ADAPTER_SCHEMA_VERSION}:" + ",".join(sorted(f.name for f in fields(cls)))


# Schema marker per cache KIND — keyed by the DTO whose shape drift would silently
# introduce None fields on read.
_SCHEMAS = {
    "fundamentals": _schema_marker(Fundamentals),
    "prices": _schema_marker(PriceBar),
    "dividends": _schema_marker(DividendEvent),
    "consensus": _schema_marker(StreetConsensus),
}


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
        schema = _SCHEMAS.get(kind, "")
        if not self._refresh and path.exists():
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(doc, dict) and doc.get("_schema") == schema:
                    return deser(doc["data"])          # HIT: shape matches
                # marker mismatch (schema drift), a pre-marker file, or a corrupted
                # marker -> treat as MISS. Logged once so the event is VISIBLE, then
                # refetched + rewritten under the current schema.
                _log.info("cache schema stale for %s (%s) — refetching", ticker, kind)
            except Exception:
                pass   # corrupt/unparseable cache -> fall through and refetch (no crash)
        obj = fetch()
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"_schema": schema, "data": ser(obj)}),
                        encoding="utf-8")
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

    def get_street_consensus(self, ticker):
        # Delegate to the inner adapter (the base default would return an all-null
        # abstention and silently drop real analyst data), cached daily like the rest.
        return self._cached(ticker, "consensus",
                            lambda: self._inner.get_street_consensus(ticker),
                            _ser_consensus, _deser_consensus)
