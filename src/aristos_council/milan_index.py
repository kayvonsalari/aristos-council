"""MILAN-1 - Borsa Italiana in the local market index.

WHY A SEPARATE SOURCE. The index is built from EODHD (``/exchange-symbol-list`` + ``/fundamentals``), and
EODHD does not serve Milan on this plan: ``/exchange-symbol-list/MI`` (and MTA, BIT, MIL, IT, XMIL) answer
HTTP 404, ``/fundamentals/LDO.MI`` answers "Symbol not found", and the provider's exchange list carries no
Italian exchange (re-probed 2026-10-15). Yahoo DOES serve it: the screener lists every equity on exchange
``MIL`` and each name's quote data carries the sector, industry, currency and market cap. So Milan rows are
built from Yahoo, tagged ``source = "yahoo+screener"`` so nobody mistakes them for EODHD rows, and stored in the
same table under the same ``IndexRow`` shape. The cost in EODHD units is zero.

What a Milan row does NOT carry, said rather than invented:
  * ``isin`` - Yahoo's ISIN lookup answers "-" for Italian names, so it is blank. A cross-listing of an Italian
    company on another exchange therefore links to its Milan line through its own PrimaryTicker, not an ISIN.
  * ``gics_*`` - Yahoo labels sector and industry (the same taxonomy EODHD's ``Sector`` / ``Industry`` use);
    there is no GICS sub-industry, so a cohort narrowed by GICS sub-industry cannot place a Milan name.

Currency is read from the quote (EUR for every Milan line, like Paris, Amsterdam, Madrid and Xetra); the USD
market cap goes through the SAME converter as every other row, so a EUR row converts exactly like ``.PA``.

CLI:  python -m aristos_council.market_index milan [--limit N]
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Callable, Optional

from .market_index import (SOURCE_YAHOO_LISTING, IndexRow, IndexStore, _UsdConverter, log_line)

MILAN_CODE = "MI"                  # the index's market code (EODHD's own name for Borsa Italiana)
YAHOO_EXCHANGE = "MIL"             # the screener's name for it
SUFFIX = ".MI"
PAGE = 250
_MAX_PAGES = 12                    # ~3,000 lines is far beyond Milan; a runaway pager stops here


class MilanSourceError(RuntimeError):
    pass


def listed_symbols(screen: Optional[Callable] = None, *, page: int = PAGE) -> list[dict]:
    """Every EQUITY quote on Borsa Italiana, largest market cap first.

    The screener's own ``total`` is not trusted (it reported 30,656 for this query). Yahoo files ~2,600 warrants,
    certificates and structured products under the same exchange as "EQUITY" quotes with an instrument code for a
    name (``F68736.MI``) and no market cap; they are not companies and cannot be peers, so a quote with no name or
    no market cap is not listed. The results come largest first, so paging stops at the first page with no
    capped ``.MI`` company on it."""
    if screen is None:
        import yfinance as yf
        from yfinance import EquityQuery
        query = EquityQuery("eq", ["exchange", YAHOO_EXCHANGE])

        def screen(offset: int) -> dict:                                  # noqa: E306
            return yf.screen(query, offset=offset, size=page, sortField="intradaymarketcap", sortAsc=False)

    out: list[dict] = []
    seen: set[str] = set()
    for n in range(_MAX_PAGES):
        quotes = (screen(n * page) or {}).get("quotes") or []
        milan = [q for q in quotes if str(q.get("symbol", "")).upper().endswith(SUFFIX)
                 and str(q.get("quoteType", "")).upper() == "EQUITY"
                 and (q.get("longName") or q.get("shortName"))
                 and isinstance(q.get("marketCap"), (int, float)) and q.get("marketCap") > 0]
        fresh = [q for q in milan if q["symbol"].upper() not in seen]
        if not fresh:
            break
        for q in fresh:
            seen.add(q["symbol"].upper())
        out.extend(fresh)
        if len(quotes) < page - 5:
            break
    return out


def row_from_quote(quote: dict, info: dict, *, today: date) -> IndexRow:
    """One index row from a screener quote plus the name's ``info`` (sector and industry)."""
    symbol = str(quote["symbol"]).upper()
    cap = quote.get("marketCap") or info.get("marketCap")
    cap = float(cap) if isinstance(cap, (int, float)) and not isinstance(cap, bool) and cap else None
    return IndexRow(
        ticker=symbol, yahoo_ticker=symbol,
        name=str(quote.get("longName") or quote.get("shortName") or info.get("longName") or ""),
        exchange=MILAN_CODE, market=MILAN_CODE, country="IT",
        currency=str(quote.get("currency") or info.get("currency") or ""),
        primary_ticker=symbol, isin="",
        sector=str(info.get("sector") or ""), industry=str(info.get("industry") or ""),
        market_cap=cap, fetched_at=today.isoformat(), source=SOURCE_YAHOO_LISTING)


def _info_of(symbol: str) -> dict:
    import yfinance as yf
    for attempt in range(3):
        try:
            return yf.Ticker(symbol).info or {}
        except Exception:                                                  # noqa: BLE001 - retried, then empty
            time.sleep(1.5 * (attempt + 1))
    return {}


def build_milan(*, store: Optional[IndexStore] = None, usd: Optional[_UsdConverter] = None,
                today: Optional[date] = None, limit: Optional[int] = None, screen: Optional[Callable] = None,
                info_fn: Optional[Callable[[str], dict]] = None, progress: Optional[Callable] = None,
                max_age_days: int = 30, workers: int = 4) -> dict:
    """Add or refresh the Milan rows. Rows younger than ``max_age_days`` are kept as they are. A name whose
    quote carries no market cap or no industry is still stored (honest abstention: it simply cannot be a peer),
    and counted. Returns the counts."""
    store = store or IndexStore()
    today = today or date.today()
    usd = usd if usd is not None else _UsdConverter(today=today)
    info_fn = info_fn or _info_of

    def say(msg: str) -> None:
        if progress is not None:
            progress(msg)
        log_line(store, msg)

    existing = {r.ticker: r for r in store.load()}
    say("MI: listing Borsa Italiana equities from Yahoo (no EODHD units)...")
    quotes = listed_symbols(screen)
    if limit is not None:
        quotes = quotes[:limit]
    todo = []
    kept_fresh = 0
    for q in quotes:
        sym = q["symbol"].upper()
        known = existing.get(sym)
        age = known.age_days(today) if known is not None else None
        if known is not None and known.complete and known.industry and age is not None and age < max_age_days:
            kept_fresh += 1
            continue
        todo.append(q)
    say(f"MI: {len(quotes)} equities listed, {len(todo)} to fetch, {kept_fresh} already fresh")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        infos = list(pool.map(lambda q: info_fn(q["symbol"]), todo))
    no_cap = no_industry = 0
    for q, info in zip(todo, infos):
        row = usd.apply(row_from_quote(q, info, today=today))
        no_cap += row.market_cap is None
        no_industry += not row.industry
        existing[row.ticker] = row
    store.save(list(existing.values()))
    counts = {"listed": len(quotes), "fetched": len(todo), "fresh": kept_fresh,
              "no_market_cap": no_cap, "no_industry": no_industry}
    say(f"MI: done - {counts}")
    return counts
