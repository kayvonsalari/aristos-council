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
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

BASE_URL = "https://eodhd.com/api"
# ANALYST-RATINGS-1 - the ratings ride on the SAME request as the forecasts: EODHD answers a
# comma-separated filter in ONE /fundamentals call (charged once, 10 units, whatever the filter),
# and the reply is keyed by block name. ``General::CurrencyCode`` comes with it because the target
# price block states no currency of its own - a target whose currency cannot be established is never
# compared with a price.
TREND_FILTER = "Earnings::Trend,AnalystRatings,General::CurrencyCode"
RATINGS_ONLY_FILTER = "AnalystRatings,General::CurrencyCode"
SOURCE_TAG = "EODHD"
MIN_RATING_ANALYSTS = 3           # fewer opinions than this is not a consensus
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
class AnalystRatings:
    """The analysts' buy/hold/sell counts and average price target for one LISTING.

    Probed live 2026-09-26 (10 units each): a US line answers
    ``{"Rating": 4.04, "TargetPrice": 328.22, "StrongBuy": 23, "Buy": 7, "Hold": 16, "Sell": 1,
    "StrongSell": 1}`` (AAPL.US, AZN.US, MU.US); every London, Hong Kong, Taiwan and Swiss line probed
    (AZN, SHEL, GSK, BP .LSE, 0700.HK, 2330.TW, NESN.SW) answers the string ``"NA"``. The block carries
    NO currency, so ``currency`` is the listing's own ``General::CurrencyCode``."""

    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0
    target_price: Optional[float] = None
    rating: Optional[float] = None
    currency: str = ""                          # of the target: the listing's currency code
    symbol: str = ""                            # the EODHD listing these ratings describe

    @property
    def total(self) -> int:
        return self.strong_buy + self.buy + self.hold + self.sell + self.strong_sell


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
    # ANALYST-RATINGS-1: the ratings block of the same response, or why there is none.
    ratings: Optional[AnalystRatings] = None
    ratings_note: str = ""

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


def _int(value) -> int:
    number = _as_float(value)
    return int(number) if number is not None else 0


def parse_ratings(block, currency, *, symbol: str = "") -> tuple[Optional[AnalystRatings], str]:
    """``(ratings, "")`` or ``(None, why)`` from an ``AnalystRatings`` block. Pure.

    EODHD answers the string ``"NA"`` for a listing it has no ratings for (every non-US line probed).
    Fewer than ``MIN_RATING_ANALYSTS`` opinions is not a consensus, and a missing count is not a zero."""
    where = f" for {symbol}" if symbol else ""
    if not isinstance(block, dict) or not block:
        return None, f"EODHD has no analyst ratings{where}"
    counts = {k: block.get(name) for k, name in (
        ("strong_buy", "StrongBuy"), ("buy", "Buy"), ("hold", "Hold"), ("sell", "Sell"),
        ("strong_sell", "StrongSell"))}
    if all(_as_float(v) is None for v in counts.values()):
        return None, f"EODHD's analyst ratings{where} carry no counts"
    ratings = AnalystRatings(
        **{k: _int(v) for k, v in counts.items()}, target_price=_as_float(block.get("TargetPrice")),
        rating=_as_float(block.get("Rating")), currency=str(currency or "").strip(), symbol=symbol)
    if ratings.total < MIN_RATING_ANALYSTS:
        return None, (f"only {ratings.total} analyst(s) rate it{where}; a consensus needs at least "
                      f"{MIN_RATING_ANALYSTS}")
    return ratings, ""


def split_response(doc) -> tuple[object, object, object, bool]:
    """``(trend block, ratings block, currency, combined)`` from a /fundamentals reply.

    A reply to the combined filter is keyed by block name; an older cached reply IS the trend block
    (keyed by period-end dates) and has no ratings, which ``combined=False`` says."""
    if isinstance(doc, dict) and "Earnings::Trend" in doc:
        return (doc.get("Earnings::Trend"), doc.get("AnalystRatings"),
                doc.get("General::CurrencyCode"), True)
    return doc, None, None, False


def parse_response(doc, *, today: date, symbol: str = "") -> TrendData:
    """The whole combined reply -> forecasts AND ratings."""
    trend_block, ratings_block, currency, combined = split_response(doc)
    data = parse_trend(trend_block, today=today)
    if not combined:
        return replace(data, ratings_note="EODHD's cached reply predates the ratings request")
    ratings, note = parse_ratings(ratings_block, currency, symbol=symbol)
    return replace(data, ratings=ratings, ratings_note=note)


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


def _cache_path(cache_dir, symbol: str, today: date, kind: str = "trend") -> Path:
    """The day-cache convention of ``growth_history``: provider, symbol, date, kind."""
    safe = symbol.replace("/", "_")
    return Path(cache_dir) / f"eodhd_{safe}_{today:%Y_%m_%d}_{kind}.json"


def _default_cache_dir():
    from .cache import DEFAULT_CACHE_DIR
    return DEFAULT_CACHE_DIR


def _stamped(data: TrendData, *, today: date, units: int, cached: bool) -> TrendData:
    return replace(data, as_of=today.isoformat(), units_charged=units, cached=cached)


def _request(symbol: str, filter_: str, key: str, opener):
    """One /fundamentals request, or an exception. The exception text is never shown: it can carry
    the URL, and the URL carries the key."""
    url = (f"{BASE_URL}/fundamentals/{urllib.parse.quote(symbol)}?"
           + urllib.parse.urlencode({"api_token": key, "fmt": "json", "filter": filter_}))
    open_url = opener or urllib.request.urlopen
    with open_url(url, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _write_cache(path: Path, doc) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
    except Exception:
        pass                                       # an uncacheable answer is still an answer


def _read_cache(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None                                # a corrupt entry is refetched, not fatal


def fetch_analyst_trend(yahoo_ticker: str, *, api_key: Optional[str] = None, cache_dir=None,
                        today: Optional[date] = None, opener=None,
                        ratings_fallback_symbol: Optional[str] = None) -> TrendData:
    """One cached call for the forecasts AND the ratings. NEVER raises; every way of having no data
    says why in ``note`` / ``ratings_note``.

    ANALYST-RATINGS-1: the ratings come from the SAME request as the forecasts - one call, one charge.
    When the listing has no ratings (EODHD answers "NA" for every non-US line probed) and the caller
    names a ``ratings_fallback_symbol`` (the same company's US line), the ratings alone are asked of
    THAT symbol - a second request, made only in that case, and stated on the result
    (``ratings.symbol``) so the page can say whose ratings they are."""
    import os

    from ..cohorts.symbols import SymbolError, eodhd_symbol

    today = today or date.today()
    key = api_key if api_key is not None else os.environ.get("EODHD_API_KEY", "")
    if not (key or "").strip():
        return _stamped(TrendData(note="EODHD_API_KEY is not set, so no analyst data was asked "
                                       "for", ratings_note="EODHD_API_KEY is not set"),
                        today=today, units=0, cached=False)
    try:
        symbol = eodhd_symbol(yahoo_ticker)
    except SymbolError:
        return _stamped(TrendData(note=f"{yahoo_ticker} has no EODHD symbol",
                                  ratings_note=f"{yahoo_ticker} has no EODHD symbol"),
                        today=today, units=0, cached=False)

    cache_dir = cache_dir if cache_dir is not None else _default_cache_dir()
    path = _cache_path(cache_dir, symbol, today)
    units, cached = 0, True
    doc = _read_cache(path)
    if doc is None or not split_response(doc)[3]:
        # a miss, a corrupt file, or a reply cached before the ratings existed (it has none to give)
        try:
            doc = _request(symbol, TREND_FILTER, key, opener)
        except Exception as exc:
            return _stamped(TrendData(note=f"the EODHD request failed ({type(exc).__name__})",
                                      ratings_note=f"the EODHD request failed "
                                                   f"({type(exc).__name__})"),
                            today=today, units=CHARGE_FUNDAMENTALS, cached=False)
        _write_cache(path, doc)
        units, cached = CHARGE_FUNDAMENTALS, False
    data = parse_response(doc, today=today, symbol=symbol)

    if data.ratings is None and ratings_fallback_symbol and ratings_fallback_symbol != symbol:
        fb = _fallback_ratings(ratings_fallback_symbol, key, cache_dir, today, opener)
        units += fb[2]
        cached = cached and fb[2] == 0
        if fb[0] is not None:
            data = replace(data, ratings=fb[0], ratings_note="")
        else:
            data = replace(data, ratings_note=f"{data.ratings_note}; {fb[1]}".strip("; "))
    return _stamped(data, today=today, units=units, cached=cached)


def _fallback_ratings(symbol: str, key: str, cache_dir, today: date, opener):
    """``(ratings, why not, units charged)`` for the company's US line, cached for the day."""
    path = _cache_path(cache_dir, symbol, today, "ratings")
    doc = _read_cache(path)
    units = 0
    if doc is None:
        try:
            doc = _request(symbol, RATINGS_ONLY_FILTER, key, opener)
        except Exception as exc:
            return None, f"the EODHD request for {symbol} failed ({type(exc).__name__})", \
                CHARGE_FUNDAMENTALS
        _write_cache(path, doc)
        units = CHARGE_FUNDAMENTALS
    block = doc.get("AnalystRatings") if isinstance(doc, dict) else None
    currency = doc.get("General::CurrencyCode") if isinstance(doc, dict) else None
    ratings, why = parse_ratings(block, currency, symbol=symbol)
    return ratings, why, units
