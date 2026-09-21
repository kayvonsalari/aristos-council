"""ABS-READINGS-3 - the long annual history the growth record needs.

yfinance returns FOUR annual periods, so Coca-Cola - a company with a century of
published accounts - reported a three-year compound rate. EODHD carries far more, and
that plan is already paid for.

PROBED against the live API, KO.US, 2026-09-21, before this parser was written:

  filter=Financials::Income_Statement::yearly
    -> a dict keyed by period-end date ("1985-12-31" ... "2025-12-31"), 41 ANNUAL
       PERIODS, 34 fields each. The fields this module needs:
           totalRevenue   '47941000000.00'    <- a STRING, not a number
           netIncome      '13107000000.00'    <- likewise
           ebit           '17652000000.00'
       There is NO diluted-EPS field and NO share count in this block:
       commonStockSharesOutstanding, weightedAverageShsOutDil and weightedAverageShsOut
       are all absent.

  filter=outstandingShares::annual
    -> a dict keyed by an index string ("0", "1", ...), 42 entries covering 1985-2026,
       each {"date": "2025", "dateFormatted": "2025-12-31", "shares": 4313000000}.

So revenue is read directly and EPS is DERIVED as netIncome / shares, matched by fiscal
year - the same derivation, and the same disclosure, that the yfinance path already uses
when the reported EPS line is missing. Every value is coerced from a string.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

BASE_URL = "https://eodhd.com/api"
GROWTH_FILTER = "Financials::Income_Statement::yearly,outstandingShares::annual"
INCOME_KEY = "Financials::Income_Statement::yearly"
SHARES_KEY = "outstandingShares::annual"

SOURCE_EODHD = "EODHD"
SOURCE_YFINANCE = "yfinance"


@dataclass(frozen=True)
class GrowthHistory:
    """Revenue and EPS by fiscal year, NEWEST FIRST, with where they came from."""

    years: tuple = ()
    revenue: tuple = ()
    eps: tuple = ()
    source: str = SOURCE_YFINANCE
    reports: int = 0

    @property
    def available(self) -> bool:
        return bool(self.years)

    def tag(self) -> str:
        """"source: EODHD, 41 annual reports" - printed on the page."""
        if not self.available:
            return ""
        plural = "s" if self.reports != 1 else ""
        return f"source: {self.source}, {self.reports} annual report{plural}"


def _as_float(value) -> Optional[float]:
    """EODHD sends every statement figure as a STRING. None for anything unusable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _year_of(text) -> str:
    raw = str(text or "").strip()
    return f"FY{raw[:4]}" if raw[:4].isdigit() else ""


def parse_growth_history(doc: dict) -> GrowthHistory:
    """The two probed blocks -> one history. Pure, so the parser is tested without a call."""
    income = doc.get(INCOME_KEY) if isinstance(doc, dict) else None
    if not isinstance(income, dict) or not income:
        return GrowthHistory()

    shares_by_year: dict = {}
    raw_shares = (doc.get(SHARES_KEY) if isinstance(doc, dict) else None) or {}
    entries = raw_shares.values() if isinstance(raw_shares, dict) else raw_shares
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        year = _year_of(entry.get("dateFormatted") or entry.get("date"))
        count = _as_float(entry.get("shares"))
        if year and count:
            shares_by_year.setdefault(year, count)

    years, revenue, eps = [], [], []
    for period in sorted(income, reverse=True):          # NEWEST FIRST
        row = income.get(period) or {}
        year = _year_of(row.get("date") or period)
        if not year:
            continue
        years.append(year)
        revenue.append(_as_float(row.get("totalRevenue")))
        profit = _as_float(row.get("netIncome"))
        count = shares_by_year.get(year)
        eps.append(profit / count if profit is not None and count else None)

    return GrowthHistory(years=tuple(years), revenue=tuple(revenue), eps=tuple(eps),
                         source=SOURCE_EODHD,
                         reports=sum(1 for v in revenue if v is not None))


def _cache_path(cache_dir, symbol: str, today: date) -> Path:
    """The day-cache convention: provider, symbol, date, kind - one file, one day."""
    safe = symbol.replace("/", "_")
    return Path(cache_dir) / f"eodhd_{safe}_{today:%Y_%m_%d}_growth.json"


def fetch_growth_history(yahoo_ticker: str, *, api_key: Optional[str] = None,
                         cache_dir=None, today: Optional[date] = None,
                         opener=None) -> GrowthHistory:
    """One cached call. An empty history whenever anything at all is unavailable.

    NEVER raises: the growth record falls back to yfinance, which is a worse answer but
    an answer. A missing key, a quota refusal and an unparseable payload are all the same
    thing from here - no EODHD history - and each leaves the caller's fallback intact.
    """
    import os

    from .cohorts.symbols import SymbolError, eodhd_symbol

    key = api_key if api_key is not None else os.environ.get("EODHD_API_KEY", "")
    if not (key or "").strip():
        return GrowthHistory()
    try:
        symbol = eodhd_symbol(yahoo_ticker)
    except SymbolError:
        return GrowthHistory()

    today = today or date.today()
    cache_dir = cache_dir if cache_dir is not None else _default_cache_dir()
    path = _cache_path(cache_dir, symbol, today)
    if path.exists():
        try:
            return parse_growth_history(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass                                   # a corrupt entry is refetched, not fatal

    url = (f"{BASE_URL}/fundamentals/{urllib.parse.quote(symbol)}?"
           + urllib.parse.urlencode({"api_token": key, "fmt": "json",
                                     "filter": GROWTH_FILTER}))
    try:
        open_url = opener or urllib.request.urlopen
        with open_url(url, timeout=40) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return GrowthHistory()

    try:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
    except Exception:
        pass                                       # an uncacheable answer is still an answer
    return parse_growth_history(doc)


def _default_cache_dir():
    from .data.cache import DEFAULT_CACHE_DIR
    return DEFAULT_CACHE_DIR
