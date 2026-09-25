"""ANALYST-TREND-1 - which way the analysts' EPS forecast has moved, from EODHD.

Source: EODHD ``/fundamentals/{SYMBOL}?filter=Earnings::Trend`` (the Fundamentals plan). NEVER
Interactive Brokers: that data is licensed for the owner's personal, non-professional use and must
not reach client-facing output.

PROBED against the live API, 2026-09-25, before this parser was written (AAPL.US and ENR.XETRA,
10 charged units each):

  filter=Earnings::Trend
    -> a dict keyed by PERIOD-END DATE ("2027-09-30", "2026-12-31", ... back to 2017 for AAPL,
       2021 for ENR), 40 and 23 entries. Every value is a STRING, and absent ones are null.
       Each entry:
           date                              '2027-09-30'
           period                            '+1y' | '0y' | '0q' | '+1q'
           earningsEstimateAvg               '9.5815'     consensus EPS for that period
           earningsEstimateNumberOfAnalysts  '40.0000'
           earningsEstimateYearAgoEps        '8.8195'
           epsTrendCurrent                   '9.5815'     == earningsEstimateAvg
           epsTrend7daysAgo / 30 / 60 / 90daysAgo         the SAME estimate, as it stood then
           epsRevisionsUp/DownLast7/30days
       The 90-days-ago figure is served by the provider, so nothing here needs a history of its
       own: no snapshots are stored and none can drift.

  Which entry is "the current fiscal year"? NOT "the one labelled 0y": AAPL has TEN entries
  labelled '0y' (2017-09-30 ... 2026-09-30), because a past year keeps the label it had when it
  was current. The current year is the LATEST-DATED '0y' entry, and '+1y' is the single entry
  after it. (A non-US name is covered too - ENR.XETRA returned 23 entries - so "gaps outside the
  US" show up here as absences, never as errors.)

The parser is pure and tested from recorded fixtures. The fetcher NEVER raises: a missing key, a
refusal, a network failure and an unparseable payload are all "no analyst data", each with its own
stated reason, so the Company Check page abstains visibly instead of breaking.

COST. A /fundamentals request is charged 10 units whatever ``filter`` says (the same figure as
``market_index.CHARGE_FUNDAMENTALS``). ``TrendData.units_charged`` is 10 for a request that was
made - a failed one included, as the provider counts it - and 0 for a cache hit or when no
request could be made. The day-cache follows ``growth_history``: one file per symbol per day.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

BASE_URL = "https://eodhd.com/api"
TREND_FILTER = "Earnings::Trend"
SOURCE_TAG = "EODHD Earnings::Trend"
CHARGE_FUNDAMENTALS = 10

PERIOD_CURRENT_YEAR = "0y"
PERIOD_NEXT_YEAR = "+1y"

# The newest '0y' entry that ended longer ago than this is not the current fiscal year: coverage has
# lapsed or the provider stopped updating the name. Fifteen months allows a late filer.
STALE_AFTER_DAYS = 456


@dataclass(frozen=True)
class TrendPeriod:
    """One fiscal year's consensus EPS, as it stands and as it stood about 90 days ago."""

    period_end: str = ""                  # ISO date the fiscal year ends
    now: Optional[float] = None           # epsTrendCurrent (else earningsEstimateAvg)
    ago_90d: Optional[float] = None       # epsTrend90daysAgo
    analysts: Optional[int] = None        # earningsEstimateNumberOfAnalysts


@dataclass(frozen=True)
class TrendData:
    """What the fetch produced. ``note`` says WHY when there is no current-year estimate."""

    current: Optional[TrendPeriod] = None      # the latest-dated '0y'
    next_year: Optional[TrendPeriod] = None    # '+1y'
    note: str = ""
    source: str = SOURCE_TAG
    as_of: str = ""                            # ISO date the figures were fetched (the cache day)
    units_charged: int = 0
    cached: bool = False

    @property
    def available(self) -> bool:
        return self.current is not None


def _as_float(value) -> Optional[float]:
    """EODHD sends every figure as a STRING; null / "NA" / NaN are absences."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _as_date(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def _period_of(entry: dict, end: date) -> TrendPeriod:
    analysts = _as_float(entry.get("earningsEstimateNumberOfAnalysts"))
    now = _as_float(entry.get("epsTrendCurrent"))
    if now is None:
        now = _as_float(entry.get("earningsEstimateAvg"))
    return TrendPeriod(period_end=end.isoformat(), now=now,
                       ago_90d=_as_float(entry.get("epsTrend90daysAgo")),
                       analysts=int(analysts) if analysts is not None else None)


def parse_trend(doc, *, today: date) -> TrendData:
    """The probed ``Earnings::Trend`` block -> current and next fiscal year. Pure."""
    if not isinstance(doc, dict) or not doc:
        return TrendData(note="EODHD has no Earnings::Trend block for this name")

    dated: list[tuple[date, str, dict]] = []
    for key, entry in doc.items():
        if not isinstance(entry, dict):
            continue
        end = _as_date(entry.get("date") or key)
        if end is None:
            continue
        dated.append((end, str(entry.get("period") or "").strip().lower(), entry))

    current_rows = [row for row in dated if row[1] == PERIOD_CURRENT_YEAR]
    if not current_rows:
        return TrendData(note="EODHD's Earnings::Trend block has no current-year (0y) estimate")
    end, _, entry = max(current_rows, key=lambda row: row[0])
    if (today - end).days > STALE_AFTER_DAYS:
        return TrendData(note=f"the latest current-year estimate is for the fiscal year that ended "
                              f"{end.isoformat()}, so it is stale")
    current = _period_of(entry, end)

    following = [row for row in dated if row[1] == PERIOD_NEXT_YEAR and row[0] > end]
    next_year = None
    if following:
        n_end, _, n_entry = min(following, key=lambda row: row[0])
        next_year = _period_of(n_entry, n_end)
    return TrendData(current=current, next_year=next_year)


def _cache_path(cache_dir, symbol: str, today: date) -> Path:
    """The day-cache convention of ``growth_history``: provider, symbol, date, kind."""
    safe = symbol.replace("/", "_")
    return Path(cache_dir) / f"eodhd_{safe}_{today:%Y_%m_%d}_trend.json"


def _default_cache_dir():
    from .cache import DEFAULT_CACHE_DIR
    return DEFAULT_CACHE_DIR


def _stamped(data: TrendData, *, today: date, units: int, cached: bool) -> TrendData:
    from dataclasses import replace
    return replace(data, as_of=today.isoformat(), units_charged=units, cached=cached)


def fetch_analyst_trend(yahoo_ticker: str, *, api_key: Optional[str] = None, cache_dir=None,
                        today: Optional[date] = None, opener=None) -> TrendData:
    """One cached call. NEVER raises; every way of having no data says why in ``note``."""
    import os

    from ..cohorts.symbols import SymbolError, eodhd_symbol

    today = today or date.today()
    key = api_key if api_key is not None else os.environ.get("EODHD_API_KEY", "")
    if not (key or "").strip():
        return _stamped(TrendData(note="EODHD_API_KEY is not set, so no analyst data was asked "
                                       "for"), today=today, units=0, cached=False)
    try:
        symbol = eodhd_symbol(yahoo_ticker)
    except SymbolError:
        return _stamped(TrendData(note=f"{yahoo_ticker} has no EODHD symbol"), today=today,
                        units=0, cached=False)

    cache_dir = cache_dir if cache_dir is not None else _default_cache_dir()
    path = _cache_path(cache_dir, symbol, today)
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            return _stamped(parse_trend(doc, today=today), today=today, units=0, cached=True)
        except Exception:
            pass                                   # a corrupt entry is refetched, not fatal

    url = (f"{BASE_URL}/fundamentals/{urllib.parse.quote(symbol)}?"
           + urllib.parse.urlencode({"api_token": key, "fmt": "json", "filter": TREND_FILTER}))
    try:
        open_url = opener or urllib.request.urlopen
        with open_url(url, timeout=40) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        # The request WAS made, so it was charged; the exception text is not shown (it can carry
        # the URL, and the URL carries the key).
        return _stamped(TrendData(note=f"the EODHD request failed ({type(exc).__name__})"),
                        today=today, units=CHARGE_FUNDAMENTALS, cached=False)

    try:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
    except Exception:
        pass                                       # an uncacheable answer is still an answer
    return _stamped(parse_trend(doc, today=today), today=today, units=CHARGE_FUNDAMENTALS,
                    cached=False)
