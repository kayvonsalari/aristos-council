"""MARKET-INDEX-1 — a local table of every listed common stock, built once, queried free.

WHY THIS EXISTS, measured rather than assumed (2026-09-18):

  * Finnhub ``/stock/peers`` is **403 for every non-US symbol** on this plan — ten of
    twenty-one portfolio holdings got nothing at all — and where it answers it returns at
    most 12 names, includes the subject itself, ignores size entirely (Eaton peered with
    two micro-caps) and speaks its own symbology, including tickers with spaces.
  * EODHD's ``/screener`` is **403** on this plan (found building COHORT-1).
  * S&P 500 + STOXX 600 together are 1,026 names across 132 industries — a **median of
    six** companies per industry, with only eight buckets holding twenty or more. Far too
    small to be a peer source.

So peers cannot come from an endpoint call per report. They come from a table: ticker ->
classification and size for the whole investable market, built once from the one EODHD
path that does work (``exchange-symbol-list`` + ``/fundamentals``), stored locally, and
queried with no network at all. Building it is slow and done deliberately; using it is
instant and free.

The table is also what fixes COHORT-1's thin cohorts — Integrated Oil & Gas came back at
eleven names for exactly this reason — so it pays for itself twice.

Nothing here reaches a model. The CLI:

    python -m aristos_council.market_index build [--exchanges US,XETRA,LSE]
    python -m aristos_council.market_index refresh [--older-than 30]
    python -m aristos_council.market_index peers TICKER
    python -m aristos_council.market_index status
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .cohorts.symbols import SymbolError, yahoo_symbol

# --------------------------------------------------------------------------- #
# where things live
# --------------------------------------------------------------------------- #
DEFAULT_CONFIG = Path("market_index.yaml")
DEFAULT_ROOT = Path("data/local/market_index")
INDEX_FILE = "index.parquet"
# MARKET-INDEX-2 - every progress line and every stop, appended and timestamped.
BUILD_LOG = "build.log"

BASE_URL = "https://eodhd.com/api"
COMMON_STOCK = "Common Stock"

# One `/fundamentals` call per symbol. MARKET-INDEX-2: the market cap is NOT in General -
# it lives under Highlights - so the first build returned 526 rows with a cap of None.
#
# PROBED against the live API on 2026-09-18 before this parser was written, because the
# filter changes the response SHAPE and not only its contents:
#
#   filter=General
#     -> {"Code": ..., "Name": ..., "Exchange": ...}        General is FLATTENED to the top
#
#   filter=General,Highlights::MarketCapitalization
#     -> {"General": {...37 keys...},
#         "Highlights::MarketCapitalization": 4918238773248}
#        General becomes a NESTED dict and the cap arrives at the TOP level under that
#        literal dotted key.
#
# So the parser reads both layouts: a row fetched either way is understood, which also
# means the 526 rows already on disk stay readable.
FUNDAMENTALS_FILTER = "General,Highlights::MarketCapitalization"
CAP_KEY = "Highlights::MarketCapitalization"

# A row older than this is refetched by ``refresh``. A company's industry and venue change
# on the order of years; its market cap moves daily but the peer ladder uses BANDS (a
# quarter to four times), which a month of drift does not cross.
DEFAULT_MAX_AGE_DAYS = 30

# MARKET-INDEX-2 - EODHD does not charge one unit per request. A /fundamentals call costs
# TEN and a listing costs one, so a build that reports "6,000 calls" has really spent
# 60,000 of the daily allowance. Every summary reports both numbers, because only one of
# them is the one you run out of.
CHARGE_FUNDAMENTALS = 10
CHARGE_LISTING = 1
DEFAULT_BUDGET = 90_000                 # charged units, not requests

# The venues a US listing may sit on. 17,829 US common stocks came back on 2026-09-18, of
# which 11,508 were OTC (PINK 8,644, OTCQB 1,252, OTCQX 498, OTCGREY 486, OTCCE 475,
# OTCMKTS 144, OTC 6, OTCBB 3). Fetching those cost 337 of the first build's 526 rows and
# would cost ~118,000 charged units - more than a whole day's budget - for names no lens
# should rank. Filtered on the LISTING row, before any fundamentals call, so they are free
# to exclude. An exchange absent from this map is unrestricted.
DEFAULT_VENUES = {"US": ["NYSE", "NASDAQ", "NYSE ARCA", "AMEX"]}

# Rewrite the table every this many new rows. A full rewrite of ~10k rows is well under a
# second, and a build that dies at symbol 4,000 must not lose 4,000 calls.
FLUSH_EVERY = 200

SOURCE_EODHD = "eodhd"                    # LEGACY: fetched before the listing fields
# MARKET-INDEX-3 - the parser GENERATION, not just the provider. A row tagged this way was
# fetched by a parser that ASKED for PrimaryTicker and ISIN; if it carries neither, the
# provider genuinely has neither (AMD.TO, a CDR) and refetching it every build would be a
# permanent 10-unit loop. Without the tag there is no way to tell "never asked" from
# "asked and got nothing", and the 9,018 legacy rows would look complete.
SOURCE_EODHD_LISTING = "eodhd+listing"
# Provenance for the USD column, in the repo's own ``factors`` vocabulary: a converted
# figure carries its receipt, an unconvertible one abstains rather than guessing.
USD_COMPUTED = "computed"
USD_ABSTAINED = "abstained"


class MarketIndexError(RuntimeError):
    """The index could not be built, read or queried."""


class QuotaExhausted(MarketIndexError):
    """The provider refused on quota. A build stops cleanly rather than hammering."""


# --------------------------------------------------------------------------- #
# the row
# --------------------------------------------------------------------------- #
COLUMNS = ("ticker", "yahoo_ticker", "name", "exchange", "market", "country", "currency",
           "primary_ticker", "isin",
           "sector", "industry", "gics_sector", "gics_industry", "gics_subindustry",
           "market_cap", "market_cap_usd", "market_cap_usd_source", "fetched_at",
           "source")


@dataclass
class IndexRow:
    """One listed common stock. ``market_cap`` is AS REPORTED, in ``currency``."""

    ticker: str                       # EODHD form, e.g. "XOM.US"
    yahoo_ticker: str = ""            # what the ranker resolves, e.g. "XOM"
    name: str = ""
    exchange: str = ""                # the VENUE, as EODHD reports it: NYSE, NASDAQ, ...
    # MARKET-INDEX-2 - the exchange CODE the build requested ("US"), kept apart from the
    # venue above so the venue filter has something to filter on and `status` can still
    # group by market. They were one field, which is why an OTC listing looked like a US
    # listing to every consumer.
    market: str = ""
    country: str = ""
    currency: str = ""
    # MARKET-INDEX-3 - what makes a row a HOME listing rather than a cross-listing.
    # PROBED on 2026-09-20, one real response each:
    #   AAPL.US   -> PrimaryTicker "AAPL.US",  ISIN US0378331005   ticker == primary
    #   AMD.XETRA -> PrimaryTicker "AMD.US",   ISIN US0079031078   the SAME isin as AMD.US
    # so a cross-listing names its home in PrimaryTicker and shares the home's ISIN.
    primary_ticker: str = ""
    isin: str = ""
    sector: str = ""
    industry: str = ""
    gics_sector: str = ""
    gics_industry: str = ""
    gics_subindustry: str = ""
    market_cap: Optional[float] = None
    # A SEPARATE column, never overwriting the reported one. EODHD serves the cap in the
    # local quoted currency and the unit does not always match the currency code (Victrex
    # reads 831m against a GBX code, which is pounds) — so the reported figure is kept
    # exactly as served and the conversion is an additional, tagged reading.
    market_cap_usd: Optional[float] = None
    market_cap_usd_source: str = USD_ABSTAINED
    fetched_at: str = ""              # ISO date
    source: str = SOURCE_EODHD

    @property
    def classification(self) -> str:
        """The finest classification this row actually carries."""
        return self.gics_subindustry or self.gics_industry or self.industry or ""

    @property
    def complete(self) -> bool:
        """Does this row carry everything a peer ladder needs?

        MARKET-INDEX-2: a row with no market cap cannot be banded and cannot be a peer, so
        it is NOT a row worth keeping fresh - it is a row worth refetching. The freshness
        skip therefore applies to complete rows only.
        """
        # MARKET-INDEX-3 adds the two listing fields: a row fetched before they existed
        # cannot be placed as a home listing, so it is refetched like a capless one.
        return (self.market_cap is not None
                and self.source == SOURCE_EODHD_LISTING)

    @property
    def unresolvable_listing(self) -> bool:
        """Fetched under the new parser and the provider simply has neither field.

        AMD.TO is the real example: a Canadian Depositary Receipt for which EODHD
        publishes no PrimaryTicker and no ISIN. Refetching it forever would be a loop, so
        a row that has been ASKED (it carries a name) and got nothing is left alone and
        counted as unresolved.
        """
        return (self.source == SOURCE_EODHD_LISTING
                and not self.primary_ticker and not self.isin)

    def age_days(self, today: Optional[date] = None) -> Optional[int]:
        try:
            when = date.fromisoformat(self.fetched_at)
        except (TypeError, ValueError):
            return None
        return max((today or date.today()) - when, timedelta(0)).days


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
# The venues the index tracks by default. Chosen to cover the listings a European-based
# owner actually holds plus the deep markets whose companies are the peers: North America,
# the main European books, and the Asian and Latin American venues where the comparables
# for a chip maker or a miner are listed.
DEFAULT_EXCHANGES = ("US", "XETRA", "LSE", "PA", "AS", "MC", "MI", "SW", "ST", "CO",
                     "OL", "HE", "TO", "HK", "T", "KS", "AU", "TW", "SA")


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    """The tracked config, or the defaults. Same shape as ``cohorts.load_definitions``:
    a missing file is not an error, a malformed one is."""
    import yaml

    path = Path(path)
    if not path.exists():
        return {"exchanges": list(DEFAULT_EXCHANGES),
                "max_age_days": DEFAULT_MAX_AGE_DAYS, "root": str(DEFAULT_ROOT)}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise MarketIndexError(f"{path}: expected a mapping, got {type(doc).__name__}")
    exchanges = doc.get("exchanges") or list(DEFAULT_EXCHANGES)
    if not isinstance(exchanges, list) or not all(isinstance(x, str) for x in exchanges):
        raise MarketIndexError(f"{path}: 'exchanges' must be a list of exchange codes")
    venues = doc.get("venues")
    if venues is None:
        venues = dict(DEFAULT_VENUES)
    elif not isinstance(venues, dict):
        raise MarketIndexError(f"{path}: 'venues' must be a mapping of exchange -> list")
    return {
        "exchanges": [x.strip().upper() for x in exchanges if x.strip()],
        "max_age_days": int(doc.get("max_age_days", DEFAULT_MAX_AGE_DAYS)),
        "root": str(doc.get("root", DEFAULT_ROOT)),
        # MARKET-INDEX-2 - per exchange, and an exchange that is absent is unrestricted.
        "venues": {str(k).strip().upper(): [str(v).strip().upper() for v in (vals or [])]
                   for k, vals in venues.items()},
    }


def allowed_venues(config: dict, exchange: str) -> list[str]:
    """The venues this exchange admits, or ``[]`` meaning no restriction."""
    return list((config.get("venues") or {}).get((exchange or "").upper()) or [])


def venue_allowed(venue: str, allowed) -> bool:
    """An empty allow-list admits everything; otherwise the venue must be named."""
    if not allowed:
        return True
    return (venue or "").strip().upper() in {v.upper() for v in allowed}


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #
class IndexStore:
    """The parquet table, read and written whole.

    Whole-file rewrites rather than appends because parquet is columnar and an append is a
    second file; at ten thousand rows a rewrite is milliseconds, and one file means
    ``status`` and ``peers`` can never read a half-written shard.
    """

    def __init__(self, root: str | Path = DEFAULT_ROOT) -> None:
        self.root = Path(root)
        self.path = self.root / INDEX_FILE

    # -- read ------------------------------------------------------------- #
    def load(self) -> list[IndexRow]:
        if not self.path.exists():
            return []
        try:
            import pandas as pd
        except ImportError as exc:                       # pragma: no cover
            raise MarketIndexError(
                "pandas is required to read the market index — pip install pandas pyarrow"
            ) from exc
        frame = pd.read_parquet(self.path)
        rows: list[IndexRow] = []
        for record in frame.to_dict("records"):
            clean = {k: record.get(k) for k in COLUMNS if k in record}
            for money in ("market_cap", "market_cap_usd"):
                value = clean.get(money)
                clean[money] = None if value is None or value != value else float(value)
            for text in COLUMNS:
                if text in ("market_cap", "market_cap_usd"):
                    continue
                clean[text] = "" if clean.get(text) is None else str(clean.get(text))
            rows.append(IndexRow(**clean))
        return rows

    # -- write ------------------------------------------------------------ #
    def save(self, rows: list[IndexRow]) -> Path:
        try:
            import pandas as pd
        except ImportError as exc:                       # pragma: no cover
            raise MarketIndexError(
                "pandas is required to write the market index — pip install pandas pyarrow"
            ) from exc
        self.root.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame([{k: getattr(r, k) for k in COLUMNS}
                              for r in sorted(rows, key=lambda r: r.ticker)],
                             columns=list(COLUMNS))
        # A temp file then a replace: a build interrupted mid-write must never leave a
        # truncated table where a readable one used to be.
        tmp = self.path.with_suffix(".parquet.tmp")
        frame.to_parquet(tmp, index=False)
        tmp.replace(self.path)
        return self.path


# --------------------------------------------------------------------------- #
# the provider
# --------------------------------------------------------------------------- #
class EODHDIndexSource:
    """Read-only EODHD access for the two endpoints the index is built from.

    Separate from ``data/eodhd_adapter.py`` on purpose, exactly as ``cohorts.source`` is:
    that adapter serves the ranker's per-name price/dividend/fundamentals contract, and
    exchange listings are not part of the MarketDataAdapter interface every lens depends on.
    """

    def __init__(self, api_key: str | None = None, timeout: float = 30.0,
                 sleep=time.sleep) -> None:
        raw = api_key if api_key is not None else os.environ.get("EODHD_API_KEY")
        self._key = (raw or "").strip()
        self._timeout = timeout
        self._sleep = sleep
        # MARKET-INDEX-2 - REQUESTS and CHARGED units are different numbers and only one
        # of them is the one the daily allowance runs out of.
        self.requests = 0
        self.charged = 0

    def _get(self, path: str, charge: int = CHARGE_LISTING, **params) -> object:
        if not self._key:
            raise MarketIndexError("EODHD_API_KEY is not set — the index cannot be built")
        params.setdefault("api_token", self._key)
        params.setdefault("fmt", "json")
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
        # Back off on 429 rather than giving up: a rate limit is a "wait", a quota is a
        # "stop", and conflating them either wastes a half-built table or hammers the API.
        for attempt in range(5):
            self.requests += 1
            self.charged += charge
            try:
                with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    self._sleep(2 ** attempt)
                    continue
                if exc.code in (402, 403):
                    raise QuotaExhausted(
                        f"EODHD refused {path} with HTTP {exc.code} — quota or plan "
                        f"limit reached") from None
                raise MarketIndexError(f"EODHD {path}: HTTP {exc.code}") from None
            except Exception as exc:
                raise MarketIndexError(f"EODHD {path}: {type(exc).__name__}") from None
        raise QuotaExhausted(f"EODHD {path}: rate limited five times over")

    def common_stocks(self, exchange: str) -> list[dict]:
        """Every COMMON STOCK listed on one exchange. Funds, ADRs, preferred lines and
        indices are dropped here rather than downstream: they are not peers of an
        operating company and fetching their fundamentals would be the bulk of the bill."""
        rows = self._get(f"/exchange-symbol-list/{exchange}")
        out = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and str(row.get("Type", "")) == COMMON_STOCK:
                out.append(row)
        return out

    def general(self, symbol: str) -> dict:
        doc = self._get(f"/fundamentals/{urllib.parse.quote(symbol)}",
                        charge=CHARGE_FUNDAMENTALS, filter=FUNDAMENTALS_FILTER)
        return doc if isinstance(doc, dict) else {}


# --------------------------------------------------------------------------- #
# building
# --------------------------------------------------------------------------- #
def read_market_cap(doc: dict) -> Optional[float]:
    """The cap, from wherever it actually arrived.

    Three layouts are possible and all three are read, because rows already on disk were
    fetched under the old filter:
      * top level ``Highlights::MarketCapitalization`` (the combined filter - see the
        module docstring for the probed response);
      * nested ``Highlights.MarketCapitalization`` (filter=Highlights);
      * top level ``MarketCapitalization`` (filter=Highlights flattened).
    A cap that is absent, zero or not a number is None - a real abstention, counted by
    ``status`` as "no market cap" rather than quietly stored as a figure.
    """
    highlights = doc.get("Highlights")
    for candidate in (doc.get(CAP_KEY),
                      highlights.get("MarketCapitalization") if isinstance(highlights, dict) else None,
                      doc.get("MarketCapitalization")):
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool) and candidate:
            return float(candidate)
    return None


def _general_block(doc: dict) -> dict:
    """The General fields, whichever layout they arrived in.

    ``filter=General`` flattens them to the top level; ``filter=General,<nested>`` puts
    them under a "General" key. Probed, not assumed.
    """
    nested = doc.get("General")
    return nested if isinstance(nested, dict) else doc


def _row_from_general(symbol: str, exchange: str, doc: dict, *, today: date) -> IndexRow:
    cap = read_market_cap(doc)
    doc = _general_block(doc)
    try:
        yahoo = yahoo_symbol(symbol)
    except SymbolError:
        yahoo = ""                       # an untranslatable venue is recorded, not guessed
    return IndexRow(
        ticker=symbol, yahoo_ticker=yahoo,
        name=str(doc.get("Name") or ""),
        exchange=str(doc.get("Exchange") or exchange),
        market=exchange,
        country=str(doc.get("CountryISO") or doc.get("CountryName") or ""),
        currency=str(doc.get("CurrencyCode") or ""),
        primary_ticker=str(doc.get("PrimaryTicker") or ""),
        isin=str(doc.get("ISIN") or ""),
        sector=str(doc.get("Sector") or ""),
        industry=str(doc.get("Industry") or ""),
        gics_sector=str(doc.get("GicSector") or ""),
        gics_industry=str(doc.get("GicIndustry") or ""),
        gics_subindustry=str(doc.get("GicSubIndustry") or ""),
        market_cap=cap,
        fetched_at=today.isoformat(), source=SOURCE_EODHD_LISTING)


class _UsdConverter:
    """One FX rate per currency per build, through the repo's existing helper.

    ``factors._fetch_fx_rate`` goes via the ranker's own price path, so the rate is cached
    and replayable like every other input. A currency it cannot price abstains — the USD
    column says ``abstained`` rather than carrying a guess (the null != false discipline).
    """

    def __init__(self, adapter=None, today: Optional[date] = None) -> None:
        self._adapter = adapter
        self._today = today or date.today()
        self._rates: dict[str, Optional[float]] = {"USD": 1.0}

    def rate(self, currency: str) -> Optional[float]:
        ccy = (currency or "").strip().upper()
        if not ccy:
            return None
        # GBX is pence, not a currency: 100 pence to the pound, then pounds to dollars.
        if ccy in self._rates:
            return self._rates[ccy]
        if self._adapter is None:
            self._rates[ccy] = None
            return None
        from .factors import _fetch_fx_rate
        base, scale = ("GBP", 0.01) if ccy in ("GBX", "GBP_PENCE") else (ccy, 1.0)
        try:
            raw = _fetch_fx_rate(self._adapter, base, "USD", today=self._today)
        except Exception:
            raw = None
        self._rates[ccy] = None if raw is None else raw * scale
        return self._rates[ccy]

    def apply(self, row: IndexRow) -> IndexRow:
        if row.market_cap is None:
            return row
        rate = self.rate(row.currency)
        if rate is None:
            row.market_cap_usd, row.market_cap_usd_source = None, USD_ABSTAINED
        else:
            row.market_cap_usd = row.market_cap * rate
            row.market_cap_usd_source = USD_COMPUTED
        return row


@dataclass
class BuildOutcome:
    exchanges: list[str] = field(default_factory=list)
    listed: int = 0                  # common stocks the listing returned
    eligible: int = 0                # ...after the venue filter
    fetched: int = 0
    skipped_fresh: int = 0
    refetched_incomplete: int = 0    # capless rows refetched regardless of age
    dropped_venue: int = 0           # rows removed from the store: venue not allowed
    failed: int = 0
    requests: int = 0
    charged: int = 0
    budget: int = DEFAULT_BUDGET
    venues_seen: dict = field(default_factory=dict)
    stopped: str = ""

    def summary(self) -> str:
        head = (f"{self.fetched} row(s) fetched ({self.refetched_incomplete} refetched "
                f"for a missing cap), {self.skipped_fresh} still fresh, "
                f"{self.dropped_venue} dropped on venue, {self.failed} failed; "
                f"{self.requests} request(s) = {self.charged:,} charged of "
                f"{self.budget:,}")
        return head + (f" - STOPPED: {self.stopped}" if self.stopped else ".")

    def venue_lines(self) -> list[str]:
        """Every venue string seen, with a count - once per build, so a venue nobody
        allowed for is visible rather than silently dropped."""
        if not self.venues_seen:
            return []
        out = ["Venues seen in the listings (kept and dropped):"]
        for venue, n in sorted(self.venues_seen.items(), key=lambda kv: -kv[1]):
            out.append(f"    {venue or '(blank)':16s} {n:6d}")
        return out


def build_log_path(store: "IndexStore") -> "Path":
    return store.root / BUILD_LOG


def log_line(store: "IndexStore", message: str) -> None:
    """One timestamped line, appended. Never raises - a logger that can break a build is
    worse than no logger."""
    try:
        store.root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with build_log_path(store).open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {message}\n")
    except Exception:
        pass


def build(*, exchanges: Optional[list[str]] = None, store: Optional[IndexStore] = None,
          source: Optional[EODHDIndexSource] = None, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
          usd: Optional[_UsdConverter] = None, today: Optional[date] = None,
          limit: Optional[int] = None, budget: int = DEFAULT_BUDGET,
          venues: Optional[dict] = None, progress=None) -> BuildOutcome:
    """Fill or top up the index. Resumable, budgeted, and it never exits silently.

    Three reasons a symbol is not fetched, and they are counted separately because they
    mean different things: it is already COMPLETE and fresh; its venue is not allowed; or
    the budget ran out. A row that is present but has NO MARKET CAP is refetched whatever
    its age - it was never usable, so keeping it fresh is keeping a hole fresh.
    """
    store = store or IndexStore()
    source = source or EODHDIndexSource()
    today = today or date.today()
    usd = usd if usd is not None else _UsdConverter(today=today)
    venues = venues if venues is not None else dict(DEFAULT_VENUES)

    def say(message: str) -> None:
        if progress is not None:
            progress(message)
        log_line(store, message)

    existing = {r.ticker: r for r in store.load()}
    outcome = BuildOutcome(exchanges=list(exchanges or DEFAULT_EXCHANGES), budget=budget)
    since_flush = 0

    # MARKET-INDEX-3 - say up front what this build owes. Rows fetched before
    # PrimaryTicker and ISIN existed cannot be placed as home listings, so they are
    # refetched like capless ones, and on a 9,018-row index that is most of a day's
    # budget. Better known before the build than discovered during it.
    stale = [r for r in existing.values() if not r.complete]
    if stale:
        say(f"{len(stale)} existing row(s) are incomplete (no market cap, or fetched "
            f"before PrimaryTicker/ISIN were stored) and will be refetched regardless of "
            f"age - about {len(stale) * CHARGE_FUNDAMENTALS:,} charged units")

    try:
        for exchange in outcome.exchanges:
            allowed = [str(v).upper() for v in (venues.get(exchange.upper()) or [])]
            say(f"{exchange}: listing common stocks...")
            listings = source.common_stocks(exchange)
            outcome.listed += len(listings)

            # Every venue string seen, counted - including the ones about to be dropped,
            # so a venue nobody thought to allow is visible instead of silently missing.
            for listing in listings:
                venue = str(listing.get("Exchange") or "").strip()
                outcome.venues_seen[venue] = outcome.venues_seen.get(venue, 0) + 1

            eligible = [l for l in listings
                        if venue_allowed(str(l.get("Exchange") or ""), allowed)]
            outcome.eligible += len(eligible)
            if allowed:
                say(f"{exchange}: {len(listings)} common stock(s) listed, "
                    f"{len(eligible)} on {', '.join(allowed)} - "
                    f"{len(listings) - len(eligible)} other venues skipped before any "
                    f"fundamentals call")
            else:
                say(f"{exchange}: {len(listings)} common stock(s) listed, no venue "
                    f"restriction")

            # Rows already in the store whose venue is NOT allowed are removed. They were
            # fetched before the filter existed and no peer ladder should ever see them.
            if allowed:
                for ticker, row in list(existing.items()):
                    if row.market != exchange and not ticker.endswith(f".{exchange}"):
                        continue
                    if row.exchange and not venue_allowed(row.exchange, allowed):
                        del existing[ticker]
                        outcome.dropped_venue += 1

            for i, listing in enumerate(eligible, 1):
                code = str(listing.get("Code") or "").strip()
                if not code:
                    continue
                symbol = f"{code}.{exchange}"
                known = existing.get(symbol)
                age = known.age_days(today) if known else None
                if known is not None and known.complete and age is not None \
                        and age < max_age_days:
                    outcome.skipped_fresh += 1
                    continue
                if known is not None and not known.complete:
                    outcome.refetched_incomplete += 1

                if source.charged + CHARGE_FUNDAMENTALS > budget:
                    outcome.stopped = (
                        f"budget reached: {source.charged:,} of {budget:,} charged units "
                        f"used; the next request would cost {CHARGE_FUNDAMENTALS}")
                    raise _BudgetReached

                try:
                    doc = source.general(symbol)
                except QuotaExhausted:
                    raise
                except MarketIndexError:
                    outcome.failed += 1
                    continue
                row = usd.apply(_row_from_general(symbol, exchange, doc, today=today))
                existing[symbol] = row
                outcome.fetched += 1
                since_flush += 1
                if since_flush >= FLUSH_EVERY:
                    store.save(list(existing.values()))
                    since_flush = 0
                    say(f"{exchange}: {i}/{len(eligible)} - {outcome.fetched} fetched, "
                        f"{source.requests} requests / {source.charged:,} charged")
                if limit is not None and outcome.fetched >= limit:
                    outcome.stopped = f"--limit {limit} reached"
                    raise _BuildLimit
    except _BuildLimit:
        pass
    except _BudgetReached:
        pass
    except QuotaExhausted as exc:
        outcome.stopped = str(exc)
    except KeyboardInterrupt:
        outcome.stopped = "interrupted"
    except BaseException as exc:
        # NO SILENT EXITS. Anything at all - a bad payload, a disk error, an import that
        # fails halfway - ends with the store flushed and the reason written down. A build
        # that dies quietly after 4,000 fetches loses 40,000 charged units and tells
        # nobody why.
        import traceback
        outcome.stopped = f"{type(exc).__name__}: {exc}"
        log_line(store, f"STOPPED: {outcome.stopped}")
        log_line(store, traceback.format_exc())

    store.save(list(existing.values()))
    outcome.requests = source.requests
    outcome.charged = source.charged
    log_line(store, outcome.summary())
    return outcome


class _BudgetReached(Exception):
    """Internal: the --budget stop, so the flush-and-report path is shared."""


class _BuildLimit(Exception):
    """Internal: the --limit stop, so the flush-and-report path is shared."""


def refresh(*, older_than: int = DEFAULT_MAX_AGE_DAYS, **kwargs) -> BuildOutcome:
    """A build that only refetches STALE rows. Same code path; the age is the whole
    difference, which is why it is one flag rather than a second function."""
    return build(max_age_days=older_than, **kwargs)


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
@dataclass
class IndexStatus:
    rows: int = 0
    per_exchange: dict = field(default_factory=dict)
    oldest: str = ""
    missing_classification: int = 0
    missing_cap: int = 0
    missing_usd: int = 0
    cross_listings: int = 0
    unresolved: int = 0
    needs_refetch: int = 0
    complete: int = 0
    path: str = ""

    def lines(self) -> list[str]:
        if not self.rows:
            return [f"Market index: EMPTY ({self.path} does not exist yet).",
                    "Build it with:  python -m aristos_council.market_index build"]
        out = [f"Market index: {self.rows} row(s) in {self.path}",
               f"  complete (usable as peers): {self.complete}",
               f"  no market cap:              {self.missing_cap}",
               f"  missing classification:     {self.missing_classification}",
               f"  no USD conversion:          {self.missing_usd}",
               f"  {self.cross_listings} cross-listing(s), excluded from peer groups",
               f"  {self.unresolved} row(s) with neither PrimaryTicker nor ISIN "
               f"(unresolved; treated as home listings)",
               f"  {self.needs_refetch} row(s) will be REFETCHED by the next build",
               f"  oldest row: {self.oldest or 'unknown'}",
               "  per exchange:"]
        if self.missing_cap:
            # MARKET-INDEX-2 - the first build produced 526 rows and every one of them had
            # no cap, because the request asked for General and the cap lives under
            # Highlights. A capless row is refetched by the next build whatever its age.
            out.insert(1, f"  {self.missing_cap} row(s) carry NO MARKET CAP and cannot be "
                          f"peers; the next build refetches them.")
        for exchange, n in sorted(self.per_exchange.items(), key=lambda kv: -kv[1]):
            out.append(f"    {exchange:8s} {n:6d}")
        return out


def status(store: Optional[IndexStore] = None) -> IndexStatus:
    """What is in the table. Works on an EMPTY index without raising — the first thing
    anyone runs is ``status``, and it must answer rather than fail."""
    store = store or IndexStore()
    rows = store.load()
    out = IndexStatus(rows=len(rows), path=str(store.path))
    for row in rows:
        out.per_exchange[row.exchange or "?"] = out.per_exchange.get(row.exchange or "?", 0) + 1
        if not row.classification:
            out.missing_classification += 1
        if row.market_cap is None:
            out.missing_cap += 1
        if row.market_cap is not None and row.market_cap_usd is None:
            out.missing_usd += 1
        if not is_home_listing(row):
            out.cross_listings += 1
        if row.unresolvable_listing:
            out.unresolved += 1
        if row.complete:
            out.complete += 1
        else:
            out.needs_refetch += 1
    stamps = sorted(r.fetched_at for r in rows if r.fetched_at)
    out.oldest = stamps[0] if stamps else ""
    return out


# --------------------------------------------------------------------------- #
# MARKET-INDEX-3 - one row per COMPANY in a peer group
# --------------------------------------------------------------------------- #
# The 9,018-row build put AMD.US, AMD.TO and AMD.XETRA in one peer group, and NVDA.US
# beside NVD.XETRA. They are one company each. A peer group that counts a company three
# times is not a comparison, it is a weighted average nobody asked for.
#
# PROBED, 2026-09-20 - PrimaryTicker names the home listing and a cross-listing shares the
# home's ISIN:
#     AAPL.US    PrimaryTicker "AAPL.US"    ISIN US0378331005     home
#     AMD.XETRA  PrimaryTicker "AMD.US"     ISIN US0079031078     cross-listing of AMD.US
#
# But a STRICT "ticker == PrimaryTicker" pool filter loses companies, which is the same
# defect wearing a different hat. Of ten German blue chips probed the same day, FOUR name
# a primary this index does not track: SAP -> SAP.F, Mercedes-Benz -> MBG.F,
# Rheinmetall -> RHM.F, and VOW3 -> VOW.XETRA (a different share class). Dropping every
# non-home row would delete SAP from every software peer group.
#
# So the pool is DEDUPLICATED BY COMPANY and the home listing is merely PREFERRED. One row
# per company always survives; which one is decided, in order, by: it is the home listing;
# its country matches the company's; then the ticker, so the result never depends on the
# order rows came back in.
def normalise_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def is_home_listing(row: "IndexRow") -> bool:
    """Is this row the company's own primary line?

    ``PrimaryTicker`` decides it when present. When it is absent the ISIN fallback runs
    in ``company_rows``, which can see the other rows sharing that ISIN; a row judged
    alone and carrying neither field is treated as a home listing and counted as
    unresolved, because refusing to place it would silently drop the company.
    """
    primary = normalise_symbol(row.primary_ticker)
    if not primary:
        return True                     # nothing to contradict it; see `unresolved`
    return normalise_symbol(row.ticker) == primary


def company_key(row: "IndexRow") -> str:
    """What makes two rows the same company.

    ISIN first: AMD.US and AMD.XETRA share US0079031078, which is the fact that makes them
    one company. Then the primary ticker, which links a cross-listing to its home even
    when the ISIN is absent. Then the ticker itself, so an unlinkable row is its own
    company rather than being merged with a stranger.
    """
    if row.isin:
        return f"isin:{row.isin.strip().upper()}"
    if row.primary_ticker:
        return f"primary:{normalise_symbol(row.primary_ticker)}"
    return f"ticker:{normalise_symbol(row.ticker)}"


def _listing_rank(row: "IndexRow") -> tuple:
    """Which row of a company is the one to keep. Lower is better."""
    home = 0 if is_home_listing(row) else 1
    # The ISIN fallback: the home listing is the one whose venue country matches the
    # company's own country. ISINs carry the issuer country in their first two letters.
    country_match = 1
    if row.country and row.isin and len(row.isin) >= 2:
        country_match = 0 if row.isin[:2].upper() == row.country.strip().upper() else 1
    return (home, country_match, normalise_symbol(row.ticker))


def one_row_per_company(rows) -> tuple[list, int]:
    """``(kept, dropped)`` - the pool, deduplicated by company."""
    groups: dict = {}
    for row in rows:
        groups.setdefault(company_key(row), []).append(row)
    kept = []
    dropped = 0
    for group in groups.values():
        group.sort(key=_listing_rank)
        kept.append(group[0])
        dropped += len(group) - 1
    return sorted(kept, key=lambda r: r.ticker), dropped


# --------------------------------------------------------------------------- #
# peers
# --------------------------------------------------------------------------- #
RUNG_SUBINDUSTRY_TIGHT = "sub-industry, 1/4x–4x"
RUNG_SUBINDUSTRY_WIDE = "sub-industry, 1/10x–10x"
RUNG_INDUSTRY_WIDE = "industry, 1/10x–10x"
RUNG_NONE = "none"

DEFAULT_FLOOR = 12
DEFAULT_CAP = 40

_FINANCIAL_SECTORS = ("financial services", "financials", "financial")
_FINANCIAL_INDUSTRY_PREFIXES = ("bank", "insurance", "capital markets",
                                "asset management", "credit services", "mortgage finance",
                                "financial data", "financial conglomerates")


@dataclass
class PeerGroup:
    subject: Optional[IndexRow] = None
    members: list[IndexRow] = field(default_factory=list)
    rung: str = RUNG_NONE
    band: str = ""
    reasons: list[str] = field(default_factory=list)
    snapshot: str = ""

    @property
    def available(self) -> bool:
        return bool(self.members)

    @property
    def thin(self) -> bool:
        return 0 < len(self.members) < DEFAULT_FLOOR

    def sentence(self) -> str:
        if not self.members:
            return "; ".join(self.reasons) or "no peer group could be formed"
        return (f"{len(self.members)} peers at {self.rung} ({self.band}), "
                f"index snapshot {self.snapshot or 'unknown'}")


def is_financial(row: IndexRow) -> bool:
    """Banks and insurers, by sector then by industry prefix.

    The same judgement ``cohorts.excluded_by`` makes and for the same reason: a bank's
    balance sheet is its product, so leverage and EV-based measures do not mean for it
    what they mean for an operating company. A financial subject gets financial peers; a
    non-financial subject never does.
    """
    if (row.sector or "").strip().lower() in _FINANCIAL_SECTORS:
        return True
    industry = (row.industry or "").strip().lower()
    return any(industry.startswith(p) for p in _FINANCIAL_INDUSTRY_PREFIXES)


def _classification(row: IndexRow, level: str) -> str:
    """The key a rung compares on, falling back when GICS is absent."""
    if level == "subindustry":
        return row.gics_subindustry or row.industry
    return row.gics_industry or row.industry


def _within(cap: Optional[float], subject: float, low: float, high: float) -> bool:
    return cap is not None and subject * low <= cap <= subject * high


def _log_distance(cap: Optional[float], subject: float) -> float:
    """Distance on a LOG scale, so 'half the size' and 'twice the size' are equally near.

    On a linear scale a $400bn peer of a $200bn subject is 200bn away and a $1bn peer is
    199bn away — which would keep the giant and drop the near-match.
    """
    import math
    if not cap or cap <= 0 or subject <= 0:
        return float("inf")
    return abs(math.log(cap / subject))


def peers(ticker: str, *, floor: int = DEFAULT_FLOOR, cap: int = DEFAULT_CAP,
          store: Optional[IndexStore] = None, rows: Optional[list[IndexRow]] = None,
          ) -> PeerGroup:
    """The peer group for one company, by ladder, from the local table only.

    Deterministic for a given snapshot: every rung is a filter over the same rows and the
    trim is by log-distance then ticker, so the same index always yields the same group.
    """
    universe = rows if rows is not None else (store or IndexStore()).load()
    group = PeerGroup()
    if not universe:
        group.reasons.append("the market index is empty — run "
                             "`python -m aristos_council.market_index build`")
        return group

    wanted = (ticker or "").strip().upper()
    by_ticker = {r.ticker.upper(): r for r in universe}
    by_yahoo = {r.yahoo_ticker.upper(): r for r in universe if r.yahoo_ticker}
    subject = by_ticker.get(wanted) or by_yahoo.get(wanted)
    if subject is None:
        group.reasons.append(f"{ticker} is not in the market index")
        return group

    # MARKET-INDEX-3 - a cross-listing or an ADR is looked up by the symbol the reader
    # has, and answered for the company. NVO.US is a US line of NOVO-B.CO; its peers are
    # Novo Nordisk's peers, in Novo Nordisk's own size.
    if not is_home_listing(subject):
        home = by_ticker.get(normalise_symbol(subject.primary_ticker))
        if home is not None:
            group.reasons.append(
                f"{subject.ticker} is a {subject.exchange or subject.market} listing of "
                f"{home.ticker}; peers computed for the home listing")
            subject = home
        else:
            group.reasons.append(
                f"{subject.ticker} is a listing of {subject.primary_ticker}, which is not "
                f"in the index; peers computed for {subject.ticker} as it stands")

    group.subject = subject
    stamps = sorted(r.fetched_at for r in universe if r.fetched_at)
    group.snapshot = stamps[-1] if stamps else ""

    # MARKET-INDEX-3 - the bands compare USD, never the local figure. Tokyo and Korea
    # join the index next, and a 900bn JPY company is about 6bn USD: banded against a 9bn
    # USD subject in local units it would look a hundred times too big and be excluded,
    # or worse, included for the wrong reason.
    if subject.market_cap_usd is None:
        if subject.market_cap is None:
            group.reasons.append(f"{subject.ticker} has no market cap in the index, so no "
                                 "size band can be applied")
        else:
            group.reasons.append(
                f"{subject.ticker} has a market cap in {subject.currency or 'its own '
                'currency'} but no USD conversion, so it cannot be size-banded against "
                f"the rest of the index")
        return group
    if not subject.classification:
        group.reasons.append(f"{subject.ticker} has no industry classification in the "
                             "index")
        return group

    subject_financial = is_financial(subject)
    if not subject.gics_subindustry:
        group.reasons.append("subject has no GICS sub-industry; fell back to the EODHD "
                             "industry field")

    # Everything that is eligible to be a peer at all, before any rung.
    candidates, no_cap, no_usd = [], 0, 0
    for row in universe:
        if row.ticker.upper() == subject.ticker.upper():
            continue                                   # never its own peer
        if is_financial(row) != subject_financial:
            continue                                   # financials only with financials
        if row.market_cap is None:
            no_cap += 1
            continue
        if row.market_cap_usd is None:
            # Counted SEPARATELY from "no cap": this one has a figure, we just cannot
            # compare it. Folding the two together would hide a broken FX rate behind
            # what looks like missing data.
            no_usd += 1
            continue
        candidates.append(row)

    # ONE ROW PER COMPANY. AMD.US, AMD.TO and AMD.XETRA were three peers in the 9,018-row
    # build; they are one company.
    pool, cross_listings = one_row_per_company(candidates)

    if no_cap:
        group.reasons.append(f"{no_cap} candidate(s) skipped: no market cap in the index")
    if no_usd:
        group.reasons.append(f"{no_usd} candidate(s) skipped: a local market cap with no "
                             f"USD conversion, so not comparable in size")
    if cross_listings:
        group.reasons.append(f"{cross_listings} cross-listing(s) collapsed into their "
                             f"home listing")

    ladder = (
        (RUNG_SUBINDUSTRY_TIGHT, "subindustry", 0.25, 4.0),
        (RUNG_SUBINDUSTRY_WIDE, "subindustry", 0.10, 10.0),
        (RUNG_INDUSTRY_WIDE, "industry", 0.10, 10.0),
    )
    subject_cap = subject.market_cap_usd
    for rung, level, low, high in ladder:
        key = _classification(subject, level)
        if not key:
            continue
        matched = [r for r in pool
                   if _classification(r, level) == key
                   and _within(r.market_cap_usd, subject_cap, low, high)]
        if len(matched) >= floor:
            matched.sort(key=lambda r: (_log_distance(r.market_cap_usd, subject_cap),
                                        r.ticker))
            trimmed = matched[:max(int(cap), 0)]
            if len(matched) > len(trimmed):
                group.reasons.append(
                    f"{len(matched)} matched at this rung; trimmed to {len(trimmed)} "
                    f"nearest in size")
            group.members = sorted(trimmed, key=lambda r: r.ticker)
            group.rung, group.band = rung, f"{low:g}x-{high:g}x market cap (USD)"
            return group
        group.reasons.append(f"{rung}: only {len(matched)} comparable companies found")

    group.reasons.append(
        f"only {len(pool)} comparable companies found in the index for "
        f"{subject.classification or 'this classification'} — no peer group")
    return group


def peer_snapshot(group: PeerGroup) -> dict:
    """What a report saves so a rerun is reproducible: the members and the snapshot date.

    The same discipline as ``universe.member_hash`` and COHORT-1's frozen members.csv — a
    verdict that depends on a peer list is unreadable later unless the list is recorded.
    """
    return {
        "subject": group.subject.ticker if group.subject else "",
        "snapshot": group.snapshot,
        "rung": group.rung,
        "band": group.band,
        "members": [r.ticker for r in group.members],
        "yahoo_members": [r.yahoo_ticker for r in group.members if r.yahoo_ticker],
        "reasons": list(group.reasons),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _say(message: str) -> None:
    try:
        print(message, flush=True)
    except UnicodeEncodeError:                          # a cp1252 console
        encoding = getattr(sys.stdout, "encoding", "") or "ascii"
        print(message.encode(encoding, "replace").decode(encoding, "replace"), flush=True)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except Exception:
        pass


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m aristos_council.market_index",
        description="Build and query the local market index. No model is ever called.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--root", default=None, help="where index.parquet lives")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="fill or top up the index")
    p_build.add_argument("--exchanges", default="", help="comma-separated, overrides config")
    p_build.add_argument("--limit", type=int, default=None,
                         help="stop after N fetched rows (for a trial run)")
    p_build.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                         help=f"CHARGED units, not requests (default {DEFAULT_BUDGET:,}; "
                              f"a /fundamentals call costs {CHARGE_FUNDAMENTALS})")
    p_build.set_defaults(func=_cmd_build, refresh_only=False)

    p_refresh = sub.add_parser("refresh", help="refetch only rows older than N days")
    p_refresh.add_argument("--older-than", type=int, default=DEFAULT_MAX_AGE_DAYS)
    p_refresh.add_argument("--exchanges", default="")
    p_refresh.add_argument("--limit", type=int, default=None)
    p_refresh.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    p_refresh.set_defaults(func=_cmd_build, refresh_only=True)

    p_peers = sub.add_parser("peers", help="the peer group for one ticker")
    p_peers.add_argument("ticker")
    p_peers.add_argument("--floor", type=int, default=DEFAULT_FLOOR)
    p_peers.add_argument("--cap", type=int, default=DEFAULT_CAP)
    p_peers.set_defaults(func=_cmd_peers)

    p_status = sub.add_parser("status", help="what is in the index")
    p_status.set_defaults(func=_cmd_status)
    return parser


def _store_for(args) -> IndexStore:
    config = load_config(args.config)
    return IndexStore(args.root or config["root"])


def _cmd_build(args) -> int:
    config = load_config(args.config)
    exchanges = ([x.strip().upper() for x in args.exchanges.split(",") if x.strip()]
                 or config["exchanges"])
    max_age = getattr(args, "older_than", config["max_age_days"])
    _say(f"Exchanges: {', '.join(exchanges)}")
    _say(f"Rows younger than {max_age} day(s) are skipped without a call.")
    from .data.provider import select_market_adapter
    try:
        adapter = select_market_adapter("yfinance")
    except Exception:
        adapter = None                       # the USD column abstains; the build goes on
    store = _store_for(args)
    outcome = build(exchanges=exchanges, store=store, max_age_days=max_age,
                    usd=_UsdConverter(adapter), limit=args.limit,
                    budget=args.budget, venues=config.get("venues"), progress=_say)
    for line in outcome.venue_lines():
        _say("  " + line if not line.endswith(":") else line)
    _say(outcome.summary())
    if outcome.stopped and "limit" not in outcome.stopped:
        # The exact command that picks up where this one stopped - a resume instruction a
        # reader has to reconstruct is one they will get wrong at 4,000 rows in.
        _say("Resume with:")
        _say(f"  python -m aristos_council.market_index build "
             f"--exchanges {','.join(exchanges)} --budget {args.budget}")
        _say(f"Log: {build_log_path(store)}")
        return 1
    return 0


def _cmd_peers(args) -> int:
    group = peers(args.ticker, floor=args.floor, cap=args.cap, store=_store_for(args))
    if not group.available:
        _say(f"No peer group for {args.ticker}.")
        for reason in group.reasons:
            _say(f"  - {reason}")
        return 1
    _say(group.sentence())
    _say(f"{'ticker':16s} {'name':28s} {'exch':6s} {'local cap':>20s} "
         f"{'USD cap':>16s}  sub-industry")
    for row in group.members:
        local = ("-" if row.market_cap is None
                 else f"{row.market_cap:,.0f} {row.currency}")
        usd = "-" if row.market_cap_usd is None else f"{row.market_cap_usd:,.0f}"
        _say(f"{row.ticker:16s} {row.name[:28]:28s} {row.exchange:6s} {local:>20s} "
             f"{usd:>16s}  {row.classification}")
    for reason in group.reasons:
        _say(f"  note: {reason}")
    return 0


def _cmd_status(args) -> int:
    for line in status(_store_for(args)).lines():
        _say(line)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _load_env()
    try:
        return args.func(args)
    except MarketIndexError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(main())
