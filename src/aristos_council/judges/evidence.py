"""JUDGE-PANEL-3 - the two evidence sources the page itself does not hold: the full annual financial
statements and the recent news with snippets.

Both are FACTS, not opinions of Aristos, so they belong in the judges' pack (a headline does not
break blindness - it is what the company's world said, not what a lens concluded). Both fetchers
are plain functions handed in by the caller, so a test injects a fake and nothing here reaches a
provider in the suite. Each returns an honest ``(data, reason)``: an absence says why.

Statements come from yfinance, the source the company page's fundamentals already use, exactly as
the provider gives them - every line item of the income statement, balance sheet and cash flow
statement for as many fiscal years as it serves (four, in practice). They are day-cached under
``data/local/judges/cache/`` so a second panel on the same company the same day makes no request.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = _ROOT / "data" / "local" / "judges" / "cache"
MAX_FISCAL_YEARS = 5
NEWS_DAYS = 30
NEWS_LIMIT = 60
SNIPPET_CHARS = 400

STATEMENTS = (("income", "Income statement"), ("balance", "Balance sheet"), ("cashflow", "Cash flow statement"))


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _frame_to_dict(frame) -> dict:
    """A yfinance statement (rows = line items, columns = period ends, newest first) as
    ``{"periods": [ISO date, ...] oldest first, "rows": {line: [value|None, ...]}}``."""
    if frame is None or getattr(frame, "empty", True):
        return {"periods": [], "rows": {}}
    cols = list(frame.columns)[:MAX_FISCAL_YEARS]
    order = sorted(range(len(cols)), key=lambda i: str(cols[i])[:10])              # oldest first
    periods = [str(cols[i])[:10] for i in order]
    rows = {}
    for line in frame.index:
        vals = [_num(frame.loc[line, cols[i]]) for i in order]
        if any(v is not None for v in vals):
            rows[str(line)] = vals
    return {"periods": periods, "rows": rows}


def fetch_statements(ticker: str, *, today: Optional[date] = None, cache_dir=None, ticker_factory=None):
    """``(statements, reason)``: ``{"income": {...}, "balance": {...}, "cashflow": {...}}`` or
    ``(None, why)``. Day-cached. ``ticker_factory`` is the test seam (``yf.Ticker`` by default)."""
    today = today or date.today()
    folder = Path(cache_dir or CACHE_DIR)
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in ticker)
    path = folder / f"statements_{safe}_{today.isoformat()}.json"
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")), ""
    except (OSError, ValueError):
        pass
    try:
        if ticker_factory is None:
            import yfinance as yf
            ticker_factory = yf.Ticker
        tk = ticker_factory(ticker)
        data = {"income": _frame_to_dict(tk.financials), "balance": _frame_to_dict(tk.balance_sheet),
                "cashflow": _frame_to_dict(tk.cashflow)}
    except Exception as exc:                                  # noqa: BLE001 - the pack goes without
        return None, f"statements could not be fetched ({type(exc).__name__})"
    if not any(d["rows"] for d in data.values()):
        return None, "the provider returned no financial statements"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass                                                  # a cache that cannot be written is not an error
    return data, ""


def fetch_news(ticker: str, *, today: Optional[date] = None, days: int = NEWS_DAYS,
               limit: int = NEWS_LIMIT, api_key: Optional[str] = None, opener=None):
    """``(items, reason)``: recent EODHD news, newest first, each ``{"date", "title", "source",
    "snippet"}``. The page shows five headlines; the judges get every one the provider returns in
    the window, with the opening of the article text when the provider gives it."""
    from ..cohorts.symbols import SymbolError, eodhd_symbol
    from ..data.news_fallback import EODHD_BASE, real_url_opener
    from ..data.sentiment import clean_headline

    key = api_key if api_key is not None else os.environ.get("EODHD_API_KEY", "")
    if not (key or "").strip():
        return [], "EODHD_API_KEY is not set"
    try:
        symbol = eodhd_symbol(ticker)
    except SymbolError:
        return [], f"{ticker} has no EODHD symbol"
    today = today or date.today()
    params = {"s": symbol, "from": (today - timedelta(days=days)).isoformat(), "to": today.isoformat(),
              "api_token": key, "fmt": "json", "limit": str(limit)}
    url = f"{EODHD_BASE}/news?{urllib.parse.urlencode(params)}"
    try:
        with (opener or real_url_opener())(url, timeout=20) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return [], f"EODHD news HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return [], f"EODHD news request failed ({type(exc).__name__})"
    if not isinstance(doc, list):
        return [], "EODHD news: unexpected reply shape"
    out = []
    for row in doc:
        if not isinstance(row, dict):
            continue
        title = clean_headline(row.get("title"))
        when = str(row.get("date") or "")[:10]
        if not title or not when:
            continue
        text = " ".join(clean_headline(row.get("content")).split())
        out.append({"date": when, "title": title, "source": str(row.get("source") or "EODHD"),
                    "snippet": text[:SNIPPET_CHARS] + ("…" if len(text) > SNIPPET_CHARS else "")})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out[:limit], ""
