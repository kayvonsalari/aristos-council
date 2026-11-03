"""ABSOLUTE-VALUE-1 - the risk-free leg of the hurdle: the ten-year government bond yield of a currency.

Source: EODHD's government-bond series through the END-OF-DAY endpoint,
``/api/eod/<SYMBOL>.GBOND`` (one unit a call, whatever the history length). Which series answers for
which currency is DATA, ``data/bond_series.yaml``; a currency with no entry has no hurdle and says so.

Same armour as every other fetch: a day-cache (one file per symbol per day, like ``growth_history``),
the retry rule of ``data.retry`` (transient failures back off and retry, a clean 404 does not), and a
fetch that NEVER raises - every way of having no yield is a stated reason, not a broken page.

The last good yield is used for ``STALENESS_DAYS`` (7) after its date, then it lapses: a stale rate is
not a rate. If today's request fails, the newest cached answer for the symbol is tried before giving up.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

import yaml

from .retry import is_transient

BASE_URL = "https://eodhd.com/api"
STALENESS_DAYS = 7
LOOKBACK_DAYS = 30                # the request asks for this much history; only the last row is used
_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SERIES_FILE = _ROOT / "data" / "bond_series.yaml"

_MINOR_TO_MAJOR = {"GBX": "GBP", "GBp": "GBP", "ZAc": "ZAR", "ILA": "ILS", "KWF": "KWD"}


@dataclass(frozen=True)
class BondYield:
    """A usable yield: ``value`` is a fraction (0.0493 == 4.93%), dated ``as_of``."""

    currency: str
    symbol: str
    name: str
    value: float
    as_of: str                      # ISO date of the row used
    age_days: int = 0
    note: str = ""                  # e.g. the peg note

    def percent(self) -> str:
        return f"{100.0 * self.value:.2f}%"


@dataclass(frozen=True)
class BondResult:
    """``yield_`` when there is a usable one; otherwise ``reason`` says why there is no hurdle."""

    currency: str
    yield_: Optional[BondYield] = None
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.yield_ is not None


def load_series_map(path: Path | str | None = None) -> dict:
    """``{currency: {"symbol", "name", ...}}`` from the data file. An unreadable file is an empty map."""
    try:
        doc = yaml.safe_load(Path(path or DEFAULT_SERIES_FILE).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    series = doc.get("series") if isinstance(doc, dict) else None
    return {str(k).upper(): dict(v) for k, v in (series or {}).items() if isinstance(v, dict) and v.get("symbol")}


def major_currency(code: str) -> str:
    code = (code or "").strip()
    return _MINOR_TO_MAJOR.get(code, code).upper()


def _as_float(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def latest_row(rows) -> Optional[tuple[date, float]]:
    """The newest ``(date, close-as-fraction)`` in an end-of-day reply. The series quote a percent
    (4.927 == 4.927%), so the fraction is close / 100. A non-positive or unreadable close is skipped."""
    best: Optional[tuple[date, float]] = None
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            d = date.fromisoformat(str(r.get("date") or "")[:10])
        except ValueError:
            continue
        close = _as_float(r.get("close"))
        if close is None or close <= 0:
            continue
        if best is None or d > best[0]:
            best = (d, close / 100.0)
    return best


def _cache_file(cache_dir, symbol: str, today: date) -> Path:
    return Path(cache_dir) / f"eodhd_{symbol}_{today:%Y_%m_%d}_gbond.json"


def _default_cache_dir():
    from .cache import DEFAULT_CACHE_DIR
    return DEFAULT_CACHE_DIR


def _request(symbol: str, key: str, today: date, opener):
    url = (f"{BASE_URL}/eod/{urllib.parse.quote(symbol)}.GBOND?"
           + urllib.parse.urlencode({"api_token": key, "fmt": "json",
                                     "from": (today - timedelta(days=LOOKBACK_DAYS)).isoformat()}))
    open_url = opener or urllib.request.urlopen
    with open_url(url, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_with_retry(fetch: Callable[[], object], *, attempts: int = 3, base_delay: float = 0.5,
                      sleep: Callable[[float], None] = time.sleep):
    """``data.retry`` rule: a transient failure backs off and retries; anything else raises at once."""
    last: Optional[BaseException] = None
    for n in range(max(1, attempts)):
        try:
            return fetch()
        except Exception as exc:                            # noqa: BLE001 - classify, then act
            if not is_transient(exc):
                raise
            last = exc
            if n < attempts - 1:
                sleep(base_delay * (2 ** n))
    raise RuntimeError(f"{attempts} attempts failed transiently")  # never carries the URL (it holds the key)


def _newest_cached_rows(cache_dir, symbol: str):
    files = sorted(Path(cache_dir).glob(f"eodhd_{symbol}_*_gbond.json"), reverse=True)
    for p in files:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                   # noqa: BLE001
            continue
    return None


def bond_yield(currency: str, *, today: Optional[date] = None, series: Optional[dict] = None,
               rows_fetcher: Optional[Callable] = None, api_key: Optional[str] = None,
               cache_dir=None, opener=None, sleep: Callable[[float], None] = time.sleep) -> BondResult:
    """The ten-year yield of ``currency`` as a ``BondResult``. NEVER raises.

    ``rows_fetcher(symbol, today) -> list[dict]`` replaces the network call (tests inject a fake;
    nothing in the suite reaches EODHD). The three honest outcomes besides a yield: no series for the
    currency, no key / no answer, and a yield older than ``STALENESS_DAYS``."""
    import os

    today = today or date.today()
    ccy = major_currency(currency)
    if not ccy:
        return BondResult("", reason="the reporting currency is not known")
    table = series if series is not None else load_series_map()
    entry = table.get(ccy)
    if not entry:
        return BondResult(ccy, reason=f"no government bond series is mapped for {ccy}")
    symbol = str(entry["symbol"])

    key = api_key if api_key is not None else os.environ.get("EODHD_API_KEY", "")
    if rows_fetcher is None and not (key or "").strip():
        # no key: nothing is asked for and no cache is read, so an offline run never depends on a stray file
        return BondResult(ccy, reason=f"EODHD_API_KEY is not set, so the {symbol} yield was not asked for")

    rows = None
    cache_dir = cache_dir if cache_dir is not None else _default_cache_dir()
    path = _cache_file(cache_dir, symbol, today)
    if rows_fetcher is None and path.exists():
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                                   # noqa: BLE001 - refetched, not fatal
            rows = None
    if rows is None:
        try:
            if rows_fetcher is not None:
                rows = rows_fetcher(symbol, today)
            else:
                rows = _fetch_with_retry(lambda: _request(symbol, key, today, opener), sleep=sleep)
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(rows), encoding="utf-8")
                except Exception:                           # noqa: BLE001 - an uncacheable answer is still one
                    pass
        except Exception:                                   # noqa: BLE001 - fall back to the last good answer
            rows = _newest_cached_rows(cache_dir, symbol) if rows_fetcher is None else None
            if rows is None:
                return BondResult(ccy, reason=f"the {symbol} yield could not be fetched")

    latest = latest_row(rows)
    if latest is None:
        return BondResult(ccy, reason=f"the {symbol} series returned no usable yield")
    when, value = latest
    age = (today - when).days
    if age > STALENESS_DAYS:
        return BondResult(ccy, reason=(f"the last {symbol} yield is dated {when.isoformat()}, more than "
                                       f"{STALENESS_DAYS} days old, so it has lapsed"))
    return _usable(ccy, entry, symbol, when, value, age)


def _usable(ccy, entry, symbol, when, value, age) -> BondResult:
    return BondResult(ccy, BondYield(currency=ccy, symbol=symbol,
                                     name=str(entry.get("name") or symbol), value=value,
                                     as_of=when.isoformat(), age_days=age,
                                     note=str(entry.get("note") or "")))
