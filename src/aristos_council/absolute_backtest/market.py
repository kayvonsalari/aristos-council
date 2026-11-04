"""Prices, the bond yield on a past date, the index and cash legs.

``Tape`` is one ticker's closes with bisect lookups (raw close for the valuation and the size estimate, adjusted close for
returns). ``HistoricalBonds`` is the hurdle's risk-free leg on the ROUND date (EODHD ``.GBOND`` series, fetched once per
symbol with its whole history). ``cash_return`` compounds the US 13-week bill yield over a window.
"""
from __future__ import annotations

import bisect
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

from ..backtest import PRICE_TOLERANCE_DAYS, add_months
from ..data.bond_yield import BASE_URL, STALENESS_DAYS, BondResult, _fetch_with_retry, _usable, _as_float, \
    load_series_map, major_currency


class Tape:
    """(day, raw close, adjusted close) for one ticker."""

    def __init__(self, bars) -> None:
        pts = sorted((b.day, b.close, b.adj_close) for b in bars if b.close is not None or b.adj_close is not None)
        self._days = [p[0] for p in pts]
        self._raw = [p[1] for p in pts]
        self._adj = [p[2] for p in pts]

    def _at(self, values, d: date) -> Optional[float]:
        i = bisect.bisect_right(self._days, d)
        if i == 0 or (d - self._days[i - 1]).days > PRICE_TOLERANCE_DAYS:
            return None
        v = values[i - 1]
        return v if v is not None and v > 0 else None

    def raw(self, d: date) -> Optional[float]:
        return self._at(self._raw, d)

    def adj(self, d: date) -> Optional[float]:
        return self._at(self._adj, d)

    def raw_latest(self) -> Optional[float]:
        return next((v for v in reversed(self._raw) if v), None)

    def total_return(self, d0: date, d1: date) -> Optional[float]:
        a, b = self.adj(d0), self.adj(d1)
        return None if a is None or b is None else b / a - 1.0

    def last_day(self) -> Optional[date]:
        return self._days[-1] if self._days else None


def annualise(total: Optional[float], years: float) -> Optional[float]:
    """A total return over ``years`` as a yearly rate. A loss of everything is -100%."""
    if total is None:
        return None
    if total <= -1.0:
        return -1.0
    return (1.0 + total) ** (1.0 / years) - 1.0


def cash_return(irx: Optional[Tape], d0: date, d1: date) -> Optional[float]:
    """The US 13-week bill compounded month by month from ``d0`` to ``d1`` at the yield (a percent) of each month
    start. None when the yield series is not there."""
    if irx is None:
        return None
    growth, cur = 1.0, d0
    while cur < d1:
        nxt = min(add_months(cur, 1), d1)
        y = irx.raw(cur)
        if y is None:
            return None
        growth *= 1.0 + (y / 100.0) * (nxt - cur).days / 365.0
        cur = nxt
    return growth - 1.0


# --------------------------------------------------------------------------- #
# the hurdle on a past date
# --------------------------------------------------------------------------- #
def parse_rows(rows) -> list[tuple[date, float]]:
    """Ascending ``(date, fraction)`` from an end-of-day reply (the series quote a percent)."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            d = date.fromisoformat(str(r.get("date") or "")[:10])
        except ValueError:
            continue
        c = _as_float(r.get("close"))
        if c is None or c <= 0:
            continue
        out.append((d, c / 100.0))
    return sorted(out)


class HistoricalBonds:
    """``bonds(currency, today=d) -> BondResult``: the ten-year yield of the reporting currency on round date ``d``.

    A currency with no mapped series has no hurdle, said as such (the caller counts it). The row used is the last on or
    before ``d``; older than ``STALENESS_DAYS`` it has lapsed, as on the live page. Each symbol is fetched once with
    its whole history (one EODHD unit) and kept for the life of the object; ``units`` counts the fetches."""

    def __init__(self, *, series: Optional[dict] = None, rows_fetcher: Optional[Callable] = None,
                 api_key: Optional[str] = None, cache_dir=None, since: date = date(2005, 1, 1),
                 today: Optional[date] = None, opener=None, sleep: Callable[[float], None] = time.sleep) -> None:
        self._series = series if series is not None else load_series_map()
        self._fetch = rows_fetcher
        self._key = api_key
        self._dir = Path(cache_dir) if cache_dir is not None else None
        self._since = since
        self._today = today or date.today()
        self._opener = opener
        self._sleep = sleep
        self._rows: dict[str, list] = {}
        self.units = 0

    def _load(self, symbol: str):
        if symbol in self._rows:
            return self._rows[symbol]
        rows = None
        path = (self._dir / f"absolute_backtest_bond_{symbol}_{self._today:%Y_%m_%d}.json") if self._dir else None
        if path is not None and path.exists():
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except Exception:                                     # noqa: BLE001 - refetched
                rows = None
        if rows is None:
            if self._fetch is not None:
                rows = self._fetch(symbol, self._since)
            else:
                key = self._key if self._key is not None else os.environ.get("EODHD_API_KEY", "")
                if not (key or "").strip():
                    self._rows[symbol] = None
                    return None
                url = (f"{BASE_URL}/eod/{urllib.parse.quote(symbol)}.GBOND?"
                       + urllib.parse.urlencode({"api_token": key, "fmt": "json", "from": self._since.isoformat()}))
                open_url = self._opener or urllib.request.urlopen

                def go():
                    with open_url(url, timeout=60) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                try:
                    rows = _fetch_with_retry(go, sleep=self._sleep)
                except Exception:                                 # noqa: BLE001 - no series, said as such
                    self._rows[symbol] = None
                    return None
                self.units += 1
                if path is not None:
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(json.dumps(rows), encoding="utf-8")
                    except Exception:                             # noqa: BLE001
                        pass
        self._rows[symbol] = parse_rows(rows)
        return self._rows[symbol]

    def __call__(self, currency: str, *, today: date) -> BondResult:
        ccy = major_currency(currency)
        if not ccy:
            return BondResult("", reason="the reporting currency is not known")
        entry = self._series.get(ccy)
        if not entry:
            return BondResult(ccy, reason=f"no government bond series is mapped for {ccy}")
        symbol = str(entry["symbol"])
        rows = self._load(symbol)
        if not rows:
            return BondResult(ccy, reason=f"the {symbol} history could not be read")
        i = bisect.bisect_right([r[0] for r in rows], today)
        if i == 0:
            return BondResult(ccy, reason=f"the {symbol} series has no row on or before {today.isoformat()}")
        when, value = rows[i - 1]
        age = (today - when).days
        if age > STALENESS_DAYS:
            return BondResult(ccy, reason=(f"the last {symbol} yield on {today.isoformat()} is dated "
                                           f"{when.isoformat()}, more than {STALENESS_DAYS} days old"))
        return _usable(ccy, entry, symbol, when, value, age)
