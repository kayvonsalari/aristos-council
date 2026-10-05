"""SENT-FALLBACK-1 — when Finnhub has no news for a name (non-US, on this plan — see
FINNHUB-SKIP-1), two more sources are tried before the Sentiment specialist abstains:
EODHD's news endpoint, then yfinance's. Each fetcher is pure and NEVER raises: a missing
key, a network failure or an empty reply is "no items from this source", with a reason,
exactly like every other optional data source in this repo.

EODHD's shape here is built from its PUBLIC, documented ``/news`` endpoint (date, title,
content, symbols) — NOT live-probed against the real API the way ``analyst_trend.py``'s
endpoint was (no ``EODHD_API_KEY`` was available to probe with). The parser is
correspondingly defensive: an unexpected field shape degrades to "no items", never a
crash, so a format surprise on first real use costs nothing worse than an abstention the
fallback chain already covers (yfinance). Confirmed LIVE and working here: yfinance's
``Ticker.news`` served 10 real items for RIO.AX with no key at all — the check in this
module's own test suite verifies the PARSER only, with a fixture frozen from that probe.

Ticker form: EODHD's endpoint wants its own form (``RIO.AX`` -> ``RIO.AU``); yfinance
wants its own (``RIO.AU`` -> ``RIO.AX``). Both fetchers translate internally via
``cohorts.symbols``, so a caller passes whichever form it already has.
"""
from __future__ import annotations

from aristos_council.plurals import plural

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from .sentiment import NewsItem

EODHD_BASE = "https://eodhd.com/api"
# Finnhub's own cap (MAX_NEWS_LOGGED in agents/nodes.py) sizes the prompt; fallbacks fetch
# at most this many so a high-coverage name cannot blow the same budget from a different
# source.
MAX_FALLBACK_ITEMS = 60


# --------------------------------------------------------------------------- #
# TEST-ISOLATION-1 — the two seams a real network call actually goes through, each a
# patchable module attribute (not inlined at the call site) so tests/conftest.py can guard
# them exactly like every other live-provider factory in this repo (select_market_adapter,
# YFinanceBars, IBKRBars, PaperIBKR, ...). Neither is reached when a caller supplies its
# own ``opener``/``fetcher`` — which every test does.
# --------------------------------------------------------------------------- #
def real_url_opener():
    return urllib.request.urlopen


def real_yfinance_news():
    def _fetch(symbol):
        import yfinance as yf
        return yf.Ticker(symbol).news
    return _fetch


@dataclass(frozen=True)
class NewsFetchResult:
    """``items`` is the final list (possibly empty); ``source`` names which source actually
    answered ("EODHD news" / "yfinance news" / ""), empty when none did; ``tried`` lists
    every source attempted, each with why it came back empty, so an abstention can say
    exactly what was checked (item 1c/1d)."""

    items: tuple = ()
    source: str = ""
    tried: tuple = ()                  # tuple[str] — "Finnhub: <reason>", "EODHD news: ...", ...

    @property
    def available(self) -> bool:
        return bool(self.items)


def _as_date(value) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        # EODHD dates look like "2026-09-30 14:11:46" or "2026-09-30T14:11:46+00:00".
        return datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def fetch_eodhd_news(ticker: str, *, start: date, end: date, api_key: Optional[str] = None,
                     opener=None, limit: int = MAX_FALLBACK_ITEMS) -> tuple[list[NewsItem], str]:
    """``(items, reason)`` — ``reason`` is "" on success (even zero items — a quiet window
    is a real finding), else why nothing was asked for or returned. ``ticker`` is accepted
    in EITHER form (EODHD's or Yahoo's); translated to EODHD's via ``cohorts.symbols``."""
    import os

    from ..cohorts.symbols import SymbolError, eodhd_symbol

    key = api_key if api_key is not None else os.environ.get("EODHD_API_KEY", "")
    if not (key or "").strip():
        return [], "EODHD_API_KEY is not set"
    try:
        symbol = eodhd_symbol(ticker)
    except SymbolError:
        return [], f"{ticker} has no EODHD symbol"
    params = {"s": symbol, "from": start.isoformat(), "to": end.isoformat(),
             "api_token": key, "fmt": "json", "limit": str(max(int(limit), 0))}
    url = f"{EODHD_BASE}/news?{urllib.parse.urlencode(params)}"
    # TEST-ISOLATION-1: the DEFAULT opener is a separate, patchable module attribute
    # (``real_url_opener``, guarded in tests/conftest.py exactly like every other live
    # provider factory) — never reached when a caller passes its own ``opener`` (every
    # test in this repo does).
    open_url = opener or real_url_opener()
    try:
        with open_url(url, timeout=20) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return [], f"EODHD news HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return [], f"EODHD news request failed ({type(exc).__name__})"
    if not isinstance(doc, list):
        return [], "EODHD news: unexpected reply shape"
    items: list[NewsItem] = []
    for row in doc:
        if not isinstance(row, dict):
            continue
        published = _as_date(row.get("date"))
        title = str(row.get("title") or "").strip()
        if published is None or not title:
            continue                                    # malformed row, skip, keep the rest
        items.append(NewsItem(published=published, headline=title,
                              source=str(row.get("source") or "EODHD")))
    return items[:limit], ""


def fetch_yfinance_news(ticker: str, *, fetcher=None,
                        limit: int = MAX_FALLBACK_ITEMS) -> tuple[list[NewsItem], str]:
    """``(items, reason)``. ``ticker`` is accepted in either form; translated to Yahoo's.
    ``fetcher`` (tests only) replaces the live ``yfinance.Ticker(symbol).news`` call —
    takes the translated symbol, returns yfinance's own raw ``.news`` list."""
    from ..cohorts.symbols import SymbolError, yahoo_symbol

    try:
        symbol = yahoo_symbol(ticker)
    except SymbolError:
        symbol = ticker                     # already bare/Yahoo-form, or no translation needed
    # TEST-ISOLATION-1: resolved OUTSIDE the try below, on purpose. In a test run with no
    # ``fetcher`` injected, this is the GUARDED factory (conftest.py) and its AssertionError
    # must reach the test as a loud failure — catching it in the broad "an optional fallback
    # never fatal" handler below would silently turn "you reached a live adapter" into an
    # ordinary-looking "no news found", exactly the silent gap TEST-ISOLATION-1 exists to
    # prevent. A genuine runtime failure from the ACTUAL fetch (the call made 2 lines down)
    # is still caught, same as ever.
    live_fetch = fetcher or real_yfinance_news()
    try:
        raw = live_fetch(symbol)
    except Exception as exc:                # noqa: BLE001 — an optional fallback, never fatal
        return [], f"yfinance news request failed ({type(exc).__name__})"
    if not isinstance(raw, list):
        return [], "yfinance news: unexpected reply shape"
    items: list[NewsItem] = []
    for row in raw:
        content = row.get("content") if isinstance(row, dict) else None
        if not isinstance(content, dict):
            content = row if isinstance(row, dict) else {}
        title = str(content.get("title") or "").strip()
        published = _as_date(content.get("pubDate") or content.get("displayTime"))
        if published is None or not title:
            continue
        provider = content.get("provider")
        source = (provider or {}).get("displayName", "") if isinstance(provider, dict) else ""
        items.append(NewsItem(published=published, headline=title, source=source or "Yahoo"))
    return items[:limit], ""


def gather_news_with_fallback(ticker: str, *, start: date, end: date, finnhub_items,
                              finnhub_reason: str, eodhd_fetcher=fetch_eodhd_news,
                              yfinance_fetcher=fetch_yfinance_news) -> NewsFetchResult:
    """Finnhub first (already attempted by the caller — its own items/reason are passed
    in, so this never re-requests it); EODHD news next; yfinance news last. The first
    source to answer with at least one item wins; ``tried`` records every attempt and why
    it did not, so "not assessed" (when every source comes back empty) can name them all
    (item 1c/1d)."""
    tried: list[str] = []
    if finnhub_items:
        return NewsFetchResult(items=tuple(finnhub_items), source="Finnhub",
                               tried=(f"Finnhub: {plural(len(finnhub_items), 'item')}",))
    tried.append(f"Finnhub: {finnhub_reason or 'no items'}")

    eodhd_items, eodhd_reason = eodhd_fetcher(ticker, start=start, end=end)
    if eodhd_items:
        return NewsFetchResult(items=tuple(eodhd_items), source="EODHD news",
                               tried=(*tried, f"EODHD news: {plural(len(eodhd_items), 'item')}"))
    tried.append(f"EODHD news: {eodhd_reason or 'no items in the window'}")

    yf_items, yf_reason = yfinance_fetcher(ticker)
    if yf_items:
        return NewsFetchResult(items=tuple(yf_items), source="yfinance news",
                               tried=(*tried, f"yfinance news: {plural(len(yf_items), 'item')}"))
    tried.append(f"yfinance news: {yf_reason or 'no items'}")

    return NewsFetchResult(items=(), source="", tried=tuple(tried))
