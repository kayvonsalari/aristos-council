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

import http.client
import json
import os
import re
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

# PEER-SIZE-SANITY-1 - see the section of that name below. Here because ``status`` and ``peers``
# take the factor as a default argument.
DEFAULT_SIZE_FACTOR = 5.0
SIZE_SUSPECT = "size suspect"

# INDEX-CAP-RETRY-1 — how long to stop asking about a gap the provider does not have.
#
# MEASURED, 2026-09-24: 2,566 rows carry no market cap and 1,767 no classification, and EVERY
# one of them was fetched under the listing parser WITH A NAME — the provider answered, it
# simply had nothing. Probing ``/fundamentals/0052.TW`` directly returns
# ``MarketCapitalization: "NA"``: not a blank, not a timeout, an explicit "not available". A
# row like that does not fill next month either, and refetching them all cost about 15,000
# charged units per build for nothing (1,502 refetched on 2026-09-23, none filled).
#
# So a row that has been ASKED and got nothing twice is left alone for a month. Two attempts
# rather than one because the first empty answer could be a bad day at the provider; thirty
# days because a company that acquires a market cap has usually done something (an IPO, a
# re-listing) that a monthly sweep will pick up soon enough.
DEFAULT_EMPTY_RETRY_AFTER = 2
DEFAULT_EMPTY_RETRY_DAYS = 30

# MARKET-INDEX-2 - EODHD does not charge one unit per request. A /fundamentals call costs
# TEN and a listing costs one, so a build that reports "6,000 calls" has really spent
# 60,000 of the daily allowance. Every summary reports both numbers, because only one of
# them is the one you run out of.
CHARGE_FUNDAMENTALS = 10
CHARGE_LISTING = 1
DEFAULT_BUDGET = 90_000                 # charged units, not requests

# INDEX-SKIP-RETRY-1 - how long a LISTING request keeps trying when the network gives no answer.
# Three tries, waiting 30s then 90s between them (one wait per retry, so the tuple's length IS
# the retry count): about two minutes in all, which rides out a blip (the 2026-09-23 one
# skipped nine exchanges in a single second) without turning a real outage into a build that
# never ends. An HTTP 404 is NOT retried: it is an answer.
NETWORK_BACKOFF_SECONDS = (30.0, 90.0)

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
# INDEX-GBX-SCALE-1 - a USD cap converted under the corrected rule for a MINOR-UNIT currency (see
# ``MINOR_UNIT_MARKET_CAP``). Distinct from USD_COMPUTED so that the in-place repair of the rows
# built under the old rule is idempotent: only a row still carrying the old tag is rescaled.
USD_COMPUTED_MAJOR_UNIT = "computed (major unit)"
USD_ABSTAINED = "abstained"


# Why an exchange's listing was skipped (BuildOutcome.skip_kinds).
SKIP_NOT_AVAILABLE = "not_available"
SKIP_NETWORK = "network"
SKIP_OTHER = "other"


class MarketIndexError(RuntimeError):
    """The index could not be built, read or queried."""


class QuotaExhausted(MarketIndexError):
    """The provider refused on quota. A build stops cleanly rather than hammering."""


class ExchangeNotAvailable(MarketIndexError):
    """The provider answered HTTP 404: this listing does not exist on this plan.

    INDEX-SKIP-RETRY-1. A fact about the venue, not about the moment - asking again in two
    minutes gets the same answer - so it is skipped AT ONCE and reported as "not available".
    """


class NetworkUnavailable(MarketIndexError):
    """The request never got an answer (URLError, a timeout, a dropped connection, a 5xx),
    and it kept not getting one through every retry.

    INDEX-SKIP-RETRY-1. A fact about the moment, not about the venue: on 2026-09-23 a network
    blip made nine exchanges look "unavailable" inside one second, because a URLError was
    treated exactly like a 404. It is skipped only after the backoff is spent, and reported as
    "network error, will retry next build" - the venue is fine and the next build asks again.
    """


# --------------------------------------------------------------------------- #
# the row
# --------------------------------------------------------------------------- #
COLUMNS = ("ticker", "yahoo_ticker", "name", "exchange", "market", "country", "currency",
           "primary_ticker", "isin",
           "sector", "industry", "gics_sector", "gics_industry", "gics_subindustry",
           "market_cap", "market_cap_usd", "market_cap_usd_source", "fetched_at",
           "source", "empty_attempts", "last_attempt_at")

# The columns that are WHOLE NUMBERS on the way in and out. Parquet round-trips them as
# numpy types and the loader stringifies everything it is not told about, which would turn an
# attempt count into "2" and make the arithmetic below silently wrong.
_INT_COLUMNS = ("empty_attempts",)


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
    # INDEX-CAP-RETRY-1 — how many refetches left this row exactly as incomplete as it was,
    # and when the last of them happened. Not "how many times we fetched it": a fetch that
    # FILLED something resets this to zero, because that row is making progress and deserves
    # to be asked again.
    empty_attempts: int = 0
    last_attempt_at: str = ""         # ISO date of the last empty attempt

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

    @property
    def missing_fields(self) -> frozenset:
        """Which of the things a peer ladder needs this row still lacks.

        Compared before and after a refetch: an unchanged set means the attempt was EMPTY and
        counts against the backoff, while a smaller set means progress and resets it.
        """
        gaps = set()
        if self.market_cap is None:
            gaps.add("market_cap")
        if not self.classification:
            gaps.add("classification")
        if self.source != SOURCE_EODHD_LISTING:
            gaps.add("listing")
        return frozenset(gaps)

    @property
    def asked_and_got_nothing(self) -> bool:
        """Has the provider already been asked about this row and answered without filling it?

        A row fetched under the listing parser that carries a NAME is a row the provider
        ANSWERED — it returned a document, it just had no cap or no classification in it.
        (Probed 2026-09-24: ``/fundamentals/0052.TW`` returns
        ``"MarketCapitalization": "NA"``.) That is different from a row that has never been
        asked, and it is the evidence the backoff below reads for rows that predate the
        attempt counter.
        """
        return self.source == SOURCE_EODHD_LISTING and bool(self.name)

    def retry_due(self, today: Optional[date] = None, *,
                  after_attempts: int = DEFAULT_EMPTY_RETRY_AFTER,
                  every_days: int = DEFAULT_EMPTY_RETRY_DAYS) -> bool:
        """Should this incomplete row be asked about again today?

        INDEX-CAP-RETRY-1. Below the attempt threshold, always — the first empty answers may
        be a bad day at the provider. Above it, only once ``every_days`` have passed, because
        the provider has now said "nothing here" repeatedly and it costs ten charged units to
        hear it again.

        **Rows that predate the counter are not treated as unasked.** They would otherwise all
        come back DUE and the first build after this change would spend the very ~25,000 units
        the change exists to stop spending. The evidence is already ON the row: it was fetched
        under the listing parser and carries a name, so the provider answered and gave nothing
        (``asked_and_got_nothing``), and ``fetched_at`` records when. So such a row starts at
        the threshold, dated by its own last fetch. Once a real build records an attempt the
        stored numbers take over and this inference stops applying.
        """
        attempts = self.empty_attempts
        stamp = self.last_attempt_at
        if not attempts and not stamp and self.asked_and_got_nothing:
            attempts, stamp = max(0, after_attempts), self.fetched_at
        if attempts < max(0, after_attempts):
            return True
        try:
            last = date.fromisoformat(stamp)
        except (TypeError, ValueError):
            return True                       # never asked, or an unreadable date: ask once
        return ((today or date.today()) - last).days >= max(0, every_days)

    def waiting(self, today: Optional[date] = None, **kwargs) -> bool:
        """The other side of ``retry_due``, for counting."""
        return not self.retry_due(today, **kwargs)

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
                     "OL", "HE", "TO", "HK", "KO", "KQ", "AU", "TW", "SA")


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    """The tracked config, or the defaults. Same shape as ``cohorts.load_definitions``:
    a missing file is not an error, a malformed one is."""
    import yaml

    path = Path(path)
    if not path.exists():
        return {"exchanges": list(DEFAULT_EXCHANGES),
                "max_age_days": DEFAULT_MAX_AGE_DAYS, "root": str(DEFAULT_ROOT),
                "empty_retry_after": DEFAULT_EMPTY_RETRY_AFTER,
                "empty_retry_days": DEFAULT_EMPTY_RETRY_DAYS,
                "size_suspect_factor": DEFAULT_SIZE_FACTOR}
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
        # INDEX-CAP-RETRY-1 — how patient to be about a gap the provider does not have.
        "empty_retry_after": int(doc.get("empty_retry_after",
                                         DEFAULT_EMPTY_RETRY_AFTER)),
        "empty_retry_days": int(doc.get("empty_retry_days", DEFAULT_EMPTY_RETRY_DAYS)),
        # PEER-SIZE-SANITY-1 - how far a row's size may be from the company's other listings.
        "size_suspect_factor": float(doc.get("size_suspect_factor", DEFAULT_SIZE_FACTOR)),
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
            for whole in _INT_COLUMNS:
                value = clean.get(whole)
                try:
                    clean[whole] = int(value) if value is not None and value == value else 0
                except (TypeError, ValueError):
                    clean[whole] = 0
            for text in COLUMNS:
                if text in ("market_cap", "market_cap_usd") or text in _INT_COLUMNS:
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
class _RateLimited(Exception):
    """Internal: an HTTP 429, so the backoff-and-retry loop in ``_get`` can catch it."""


# What "no answer" looks like. URLError covers a failed connection or DNS lookup, and OSError
# covers the rest of the socket family (a reset, a timeout); HTTPException is a body cut off
# mid-read. HTTPError is also a URLError, which is why ``_request`` catches it first.
_NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
                   http.client.HTTPException)


class EODHDIndexSource:
    """Read-only EODHD access for the two endpoints the index is built from.

    Separate from ``data/eodhd_adapter.py`` on purpose, exactly as ``cohorts.source`` is:
    that adapter serves the ranker's per-name price/dividend/fundamentals contract, and
    exchange listings are not part of the MarketDataAdapter interface every lens depends on.
    """

    def __init__(self, api_key: str | None = None, timeout: float = 30.0,
                 sleep=time.sleep, network_backoff=NETWORK_BACKOFF_SECONDS) -> None:
        raw = api_key if api_key is not None else os.environ.get("EODHD_API_KEY")
        self._key = (raw or "").strip()
        self._timeout = timeout
        self._sleep = sleep
        self._network_backoff = tuple(network_backoff)
        # MARKET-INDEX-2 - REQUESTS and CHARGED units are different numbers and only one
        # of them is the one the daily allowance runs out of.
        self.requests = 0
        self.charged = 0

    def _get(self, path: str, charge: int = CHARGE_LISTING, *, retry_network: bool = False,
             **params) -> object:
        """One EODHD request.

        Three different answers, and they are told apart because each wants a different
        response: a 429 is a "wait" (backed off here), a 402/403 is a "stop" (quota), and a 404
        is "this does not exist" (``ExchangeNotAvailable``, no retry - it is an answer). NO
        answer at all - a URLError, a timeout, a dropped connection, a 5xx - is a fact about
        the moment, so with ``retry_network`` it is retried on ``network_backoff`` before
        ``NetworkUnavailable`` is raised. INDEX-SKIP-RETRY-1: it used to be raised as the same
        error as a 404, which skipped nine healthy exchanges in one second on 2026-09-23.
        """
        if not self._key:
            raise MarketIndexError("EODHD_API_KEY is not set — the index cannot be built")
        params.setdefault("api_token", self._key)
        params.setdefault("fmt", "json")
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
        for attempt in range(5):
            try:
                return self._request(url, path, charge, retry_network=retry_network)
            except _RateLimited:
                # Back off on 429 rather than giving up: a rate limit is a "wait", a quota is
                # a "stop", and conflating them either wastes a half-built table or hammers
                # the API.
                self._sleep(2 ** attempt)
        raise QuotaExhausted(f"EODHD {path}: rate limited five times over")

    def _request(self, url: str, path: str, charge: int, *, retry_network: bool) -> object:
        """One request, retried on a network failure when asked to."""
        waits = self._network_backoff if retry_network else ()
        for tried in range(len(waits) + 1):
            self.requests += 1
            self.charged += charge
            try:
                with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                # HTTPError is a URLError, so it is caught FIRST: an HTTP status is an answer.
                if exc.code == 429:
                    raise _RateLimited from None
                if exc.code in (402, 403):
                    raise QuotaExhausted(
                        f"EODHD refused {path} with HTTP {exc.code} — quota or plan "
                        f"limit reached") from None
                if exc.code == 404:
                    raise ExchangeNotAvailable(f"EODHD {path}: HTTP 404") from None
                if exc.code >= 500:
                    failure = f"HTTP {exc.code}"          # a server blip, not an answer
                else:
                    raise MarketIndexError(f"EODHD {path}: HTTP {exc.code}") from None
            except _NETWORK_ERRORS as exc:
                failure = type(exc).__name__
            except Exception as exc:
                raise MarketIndexError(f"EODHD {path}: {type(exc).__name__}") from None
            if tried < len(waits):
                self._sleep(waits[tried])
                continue
            raise NetworkUnavailable(f"EODHD {path}: {failure}") from None
        raise NetworkUnavailable(f"EODHD {path}: no answer")      # pragma: no cover

    def common_stocks(self, exchange: str) -> list[dict]:
        """Every COMMON STOCK listed on one exchange. Funds, ADRs, preferred lines and
        indices are dropped here rather than downstream: they are not peers of an
        operating company and fetching their fundamentals would be the bulk of the bill.

        The one call that RETRIES a network failure: a listing that fails skips its whole
        exchange (see ``build``), so it is worth two minutes to be sure it is not a blip."""
        rows = self._get(f"/exchange-symbol-list/{exchange}", retry_network=True)
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


# INDEX-GBX-SCALE-1 - minor-unit quote currencies whose MARKET CAP is served in the MAJOR unit.
#
# EODHD codes most London lines "GBX" (pence), and the code is right for the PRICE. The market cap
# from /fundamentals is in POUNDS regardless. The converter scaled it by 0.01 anyway, so every GBX
# row read about 100x too low: RR.LSE $1.6bn against $157.9bn on Xetra; Shell, AstraZeneca, HSBC,
# BHP, Unilever ... all the same. MEASURED on the built table (2026-09-25), comparing each row's
# stored USD cap with the same company's lines in other currencies: 48 of the 49 comparable GBX
# rows sit at exactly -2.0 dex (a factor of 100) and NONE at 0 (the 49th, MaxCyte, is -1.75 = a
# stale London price against a fresh US one); the GBP-coded rows (BP.LSE ...) are correct, and the
# GBPUSD implied by the GBX rows (usd / cap x 100) is 1.339, the real rate. The docs already said
# "a GBX code, which is pounds rather than pence" (Victrex, first sighting); the code disagreed.
#
# An entry here is a CLAIM ABOUT THE DATA, so it is added only where the data shows it. GBX is the
# only minor-unit code in the index today (no ZAc, ILA or KWF rows exist to measure); one that
# turns up is measured the same way before it is listed, and until then it falls through to
# ``_fetch_fx_rate`` under its own code and abstains rather than being guessed.
MINOR_UNIT_MARKET_CAP = {"GBX": "GBP", "GBP_PENCE": "GBP"}


def _major_unit_currency(currency: str) -> str:
    """The currency a market cap is actually in, given the row's quote-currency code."""
    ccy = (currency or "").strip().upper()
    return MINOR_UNIT_MARKET_CAP.get(ccy, ccy)


class _UsdConverter:
    """One FX rate per currency per build, through the repo's existing helper.

    ``factors._fetch_fx_rate`` goes via the ranker's own price path, so the rate is cached
    and replayable like every other input. A currency it cannot price abstains — the USD
    column says ``abstained`` rather than carrying a guess (the null != false discipline).

    The rate is for the currency the MARKET CAP is in, which for a minor-unit quote code
    (GBX) is the major unit - see ``MINOR_UNIT_MARKET_CAP``. No scale factor is applied.
    """

    def __init__(self, adapter=None, today: Optional[date] = None) -> None:
        self._adapter = adapter
        self._today = today or date.today()
        self._rates: dict[str, Optional[float]] = {"USD": 1.0}

    def rate(self, currency: str) -> Optional[float]:
        ccy = _major_unit_currency(currency)
        if not ccy:
            return None
        if ccy in self._rates:
            return self._rates[ccy]
        if self._adapter is None:
            self._rates[ccy] = None
            return None
        from .factors import _fetch_fx_rate
        try:
            raw = _fetch_fx_rate(self._adapter, ccy, "USD", today=self._today)
        except Exception:
            raw = None
        self._rates[ccy] = raw
        return self._rates[ccy]

    def apply(self, row: IndexRow) -> IndexRow:
        if row.market_cap is None:
            return row
        rate = self.rate(row.currency)
        if rate is None:
            row.market_cap_usd, row.market_cap_usd_source = None, USD_ABSTAINED
        else:
            row.market_cap_usd = row.market_cap * rate
            row.market_cap_usd_source = (
                USD_COMPUTED_MAJOR_UNIT
                if (row.currency or "").strip().upper() in MINOR_UNIT_MARKET_CAP
                else USD_COMPUTED)
        return row


# --------------------------------------------------------------------------- #
# INDEX-GBX-SCALE-1 - repairing the rows built under the old rule, in place
# --------------------------------------------------------------------------- #
# A GBX row's stored USD cap is ``market_cap x 0.01 x GBPUSD``: the row's OWN recorded FX rate,
# with the erroneous 0.01 in it. The corrected figure is ``market_cap x GBPUSD``, so it is the
# stored one times 100 - no new rate is introduced and nothing is refetched. A row is repaired
# only while it still carries the old tag (idempotent), only when it has a cap and a USD figure,
# and only when its implied rate is what 0.01 x a plausible GBPUSD looks like; anything else is
# skipped and counted, never rescaled on faith.
_PENCE_SCALE = 100.0
_IMPLIED_RATE_BOUNDS = (0.005, 0.03)         # 0.01 x GBPUSD for GBPUSD in 0.5 .. 3.0


@dataclass
class GbxRepair:
    repaired: int = 0
    already_ok: int = 0
    skipped: int = 0
    skipped_examples: list = field(default_factory=list)
    backup: str = ""
    dry_run: bool = False

    def lines(self) -> list[str]:
        out = [f"{'Would repair' if self.dry_run else 'Repaired'} {self.repaired} pence-scaled "
               f"row(s); {self.already_ok} already correct; {self.skipped} skipped "
               f"(no cap / no USD figure / an implied rate that does not look like 0.01 x GBPUSD)"
               + (f" e.g. {', '.join(self.skipped_examples)}" if self.skipped_examples else "")]
        if self.backup:
            out.append(f"Backup of the index as it was: {self.backup}")
        return out


def rederive_minor_unit_usd(rows) -> tuple[list["IndexRow"], GbxRepair]:
    """``(rows, report)``: the rows with the pence-scaled USD caps corrected. Pure: the input rows
    are not mutated."""
    import dataclasses

    report = GbxRepair()
    out = []
    for row in rows:
        if (row.currency or "").strip().upper() not in MINOR_UNIT_MARKET_CAP:
            out.append(row)
            continue
        if row.market_cap_usd_source == USD_COMPUTED_MAJOR_UNIT:
            report.already_ok += 1
            out.append(row)
            continue
        if (row.market_cap is None or row.market_cap <= 0 or row.market_cap_usd is None
                or row.market_cap_usd_source != USD_COMPUTED):
            if row.market_cap is not None:
                report.skipped += 1
                if len(report.skipped_examples) < EXAMPLES:
                    report.skipped_examples.append(row.ticker)
            out.append(row)
            continue
        implied = row.market_cap_usd / row.market_cap
        if not (_IMPLIED_RATE_BOUNDS[0] <= implied <= _IMPLIED_RATE_BOUNDS[1]):
            report.skipped += 1
            if len(report.skipped_examples) < EXAMPLES:
                report.skipped_examples.append(row.ticker)
            out.append(row)
            continue
        out.append(dataclasses.replace(
            row, market_cap_usd=row.market_cap_usd * _PENCE_SCALE,
            market_cap_usd_source=USD_COMPUTED_MAJOR_UNIT))
        report.repaired += 1
    return out, report


def _backup_path(store: "IndexStore", today: date) -> Path:
    """A dated copy alongside the index, never overwriting an earlier one."""
    base = store.path.with_name(f"{store.path.name}.pre-gbx-scale-{today:%Y%m%d}")
    candidate, n = base, 1
    while candidate.exists():
        n += 1
        candidate = base.with_name(f"{base.name}-{n}")
    return candidate


# A running build holds the WHOLE table in memory and rewrites the whole file on every flush, so
# a repair written while one runs is overwritten by its next flush - measured 2026-09-25, when a
# repair made between two flushes of a Korea build was reverted two minutes later - and a repair
# could equally drop rows the build had just added. The build's log is written at every flush,
# which makes its age the cheapest honest signal that someone else owns the file.
BUILD_ACTIVE_WITHIN_SECONDS = 600


def build_seems_active(store: "IndexStore", *, now: Optional[float] = None) -> bool:
    log = build_log_path(store)
    try:
        age = (now if now is not None else time.time()) - log.stat().st_mtime
    except OSError:
        return False
    return age < BUILD_ACTIVE_WITHIN_SECONDS


def fix_gbx_scale(store: Optional["IndexStore"] = None, *, today: Optional[date] = None,
                  dry_run: bool = False, even_if_build_running: bool = False,
                  now: Optional[float] = None) -> GbxRepair:
    """Repair the index in place: back it up, then rewrite it with corrected USD caps.

    No network, no build. The backup is written BEFORE the table is touched, and a dry run writes
    nothing at all. Refuses to WRITE while a build seems to be running (see
    ``BUILD_ACTIVE_WITHIN_SECONDS``); a dry run is always allowed.
    """
    import shutil

    store = store or IndexStore()
    if not dry_run and not even_if_build_running and build_seems_active(store, now=now):
        raise MarketIndexError(
            f"a build seems to be running ({build_log_path(store)} was written in the last "
            f"{BUILD_ACTIVE_WITHIN_SECONDS // 60} minutes). It rewrites the whole index on every "
            f"flush, so a repair now would be overwritten. Run this after the build finishes, or "
            f"pass --even-if-build-running if you know it has stopped.")
    rows = store.load()
    fixed, report = rederive_minor_unit_usd(rows)
    report.dry_run = dry_run
    if dry_run or not report.repaired:
        return report
    backup = _backup_path(store, today or date.today())
    shutil.copy2(store.path, backup)
    report.backup = str(backup)
    store.save(fixed)
    return report


def _record_attempt(fresh: IndexRow, previous: Optional[IndexRow], *,
                    today: date) -> IndexRow:
    """Carry the empty-attempt bookkeeping onto a freshly fetched row.

    INDEX-CAP-RETRY-1. "Empty" is judged by comparing what the row is MISSING before and
    after: an unchanged set means the provider gave nothing new, a smaller set means progress
    and resets the counter, and a complete row carries no counter at all.
    """
    if not fresh.missing_fields:
        fresh.empty_attempts, fresh.last_attempt_at = 0, ""
        return fresh
    before = previous.missing_fields if previous is not None else None
    improved = before is not None and fresh.missing_fields < before
    fresh.empty_attempts = 0 if improved else (
        (previous.empty_attempts if previous is not None else 0) + 1)
    fresh.last_attempt_at = today.isoformat()
    return fresh


@dataclass
class BuildOutcome:
    exchanges: list[str] = field(default_factory=list)
    listed: int = 0                  # common stocks the listing returned
    eligible: int = 0                # ...after the venue filter
    fetched: int = 0
    skipped_fresh: int = 0
    refetched_incomplete: int = 0    # capless rows refetched regardless of age
    # INDEX-CAP-RETRY-1 — incomplete rows NOT refetched because the provider has already said
    # "nothing here" twice and the month is not up. The whole point of the change, so it is
    # counted and reported rather than showing up as an unexplained drop in requests.
    waiting_on_backoff: int = 0
    dropped_venue: int = 0           # rows removed from the store: venue not allowed
    failed: int = 0
    requests: int = 0
    charged: int = 0
    budget: int = DEFAULT_BUDGET
    venues_seen: dict = field(default_factory=dict)
    # MARKET-INDEX-SKIP-1 - exchanges whose LISTING could not be fetched, with the reason.
    # One unlistable exchange used to end the whole build: on 2026-09-22 the European run
    # died at Milan (EODHD /exchange-symbol-list/MI answered HTTP 404) and never attempted
    # the exchanges after it. A listing that fails is a fact about one venue, not about the
    # build.
    skipped_exchanges: list = field(default_factory=list)
    # INDEX-SKIP-RETRY-1 - WHY each was skipped, by exchange code: SKIP_NOT_AVAILABLE (HTTP 404,
    # a fact about the venue), SKIP_NETWORK (no answer after every retry, a fact about the
    # moment) or SKIP_OTHER. They mean different things to the owner - one is "this plan does not
    # have it", the other is "run it again" - so they are never reported as one number.
    skip_kinds: dict = field(default_factory=dict)
    stopped: str = ""

    def summary(self) -> str:
        head = (f"{self.fetched} row(s) fetched ({self.refetched_incomplete} refetched "
                f"for a missing cap), {self.skipped_fresh} still fresh, "
                f"{self.waiting_on_backoff} waiting on the empty-gap backoff, "
                f"{self.dropped_venue} dropped on venue, {self.failed} failed; "
                f"{self.requests} request(s) = {self.charged:,} charged of "
                f"{self.budget:,}")
        if self.skipped_exchanges:
            head += f". {self.skipped_sentence()}"
        return head + (f" - STOPPED: {self.stopped}" if self.stopped else ".")

    def skipped_codes(self, kind: str) -> list[str]:
        """The exchange codes skipped for ``kind``, in build order."""
        return [code for code, _reason in self.skipped_exchanges
                if self.skip_kinds.get(code, SKIP_OTHER) == kind]

    @property
    def network_skipped(self) -> list[str]:
        """The exchanges to run again: skipped because nothing answered, not because the
        venue is missing."""
        return self.skipped_codes(SKIP_NETWORK)

    def skipped_sentence(self) -> str:
        """``2 exchanges skipped - not available (404): MI; network error, will retry next
        build: XETRA (URLError)`` - named and separated by cause, so a missing market is never
        inferred from a short table and a blip is never mistaken for a missing venue."""
        if not self.skipped_exchanges:
            return ""
        reasons = dict(self.skipped_exchanges)
        groups = []
        missing = self.skipped_codes(SKIP_NOT_AVAILABLE)
        if missing:
            groups.append(f"not available (404): {', '.join(missing)}")
        blipped = self.skipped_codes(SKIP_NETWORK)
        if blipped:
            groups.append("network error, will retry next build: "
                          + ", ".join(f"{c} ({reasons[c]})" for c in blipped))
        other = self.skipped_codes(SKIP_OTHER)
        if other:
            groups.append("failed: " + ", ".join(f"{c} ({reasons[c]})" for c in other))
        word = "exchange" if len(self.skipped_exchanges) == 1 else "exchanges"
        return f"{len(self.skipped_exchanges)} {word} skipped - " + "; ".join(groups)

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
          venues: Optional[dict] = None,
          empty_retry_after: int = DEFAULT_EMPTY_RETRY_AFTER,
          empty_retry_days: int = DEFAULT_EMPTY_RETRY_DAYS, progress=None) -> BuildOutcome:
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
    backoff = dict(after_attempts=empty_retry_after, every_days=empty_retry_days)
    stale = [r for r in existing.values() if not r.complete]
    due = [r for r in stale if r.retry_due(today, **backoff)]
    waiting = len(stale) - len(due)
    if stale:
        say(f"{len(stale)} existing row(s) are incomplete (no market cap, or fetched "
            f"before PrimaryTicker/ISIN were stored); {len(due)} are due a refetch - about "
            f"{len(due) * CHARGE_FUNDAMENTALS:,} charged units")
    if waiting:
        # INDEX-CAP-RETRY-1 — said up front, because "the build got cheaper" should never be
        # something the owner has to infer from the bill.
        say(f"{waiting} incomplete row(s) are waiting on the empty-gap backoff "
            f"({empty_retry_after} empty attempts, retried every {empty_retry_days} days) - "
            f"about {waiting * CHARGE_FUNDAMENTALS:,} charged units NOT spent")

    try:
        for exchange in outcome.exchanges:
            allowed = [str(v).upper() for v in (venues.get(exchange.upper()) or [])]
            say(f"{exchange}: listing common stocks...")
            try:
                listings = source.common_stocks(exchange)
            except QuotaExhausted:
                # A quota is a STOP, not a skip: continuing would burn the remaining
                # exchanges against an allowance that is already gone.
                raise
            except MarketIndexError as exc:
                # MARKET-INDEX-SKIP-1 - this ONE venue could not be listed. Record it,
                # name it, and carry on with the rest: the alternative cost an entire
                # European build on 2026-09-22 because Milan answered 404.
                reason = str(exc).split(": ", 1)[-1] or type(exc).__name__
                outcome.skipped_exchanges.append((exchange, reason))
                # INDEX-SKIP-RETRY-1 - said by CAUSE. By now a network failure has already
                # been retried on the backoff, so "will retry next build" is the true remedy.
                if isinstance(exc, ExchangeNotAvailable):
                    kind, why = SKIP_NOT_AVAILABLE, "not available on this plan"
                elif isinstance(exc, NetworkUnavailable):
                    kind, why = SKIP_NETWORK, "network error after retries, will retry next build"
                else:
                    kind, why = SKIP_OTHER, "listing failed"
                outcome.skip_kinds[exchange] = kind
                say(f"{exchange}: SKIPPED - {why} ({reason}); continuing with "
                    f"the remaining exchanges")
                continue
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
                    if not known.retry_due(today, **backoff):
                        # The provider has already said "nothing here" and the month is not
                        # up. Skipping is the change; the row keeps its own bookkeeping.
                        outcome.waiting_on_backoff += 1
                        continue
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
                # INDEX-CAP-RETRY-1 — did this attempt actually fill anything? An unchanged
                # set of gaps is an EMPTY attempt and counts against the backoff; a smaller
                # one is progress and resets the count, because a row that is improving
                # deserves to be asked again next build.
                row = _record_attempt(row, known, today=today)
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
EXAMPLES = 5                       # how many tickers status names per kind of excluded row


@dataclass
class IndexStatus:
    rows: int = 0
    per_exchange: dict = field(default_factory=dict)
    oldest: str = ""
    missing_classification: int = 0
    missing_cap: int = 0
    # INDEX-CAP-RETRY-1 — the gap-less rows split by whether the next build will ASK again.
    # Without the split, "2,566 rows carry no market cap" reads as 2,566 rows about to cost
    # ten units each, which after this change is not what it means.
    cap_due: int = 0
    cap_waiting: int = 0
    classification_due: int = 0
    classification_waiting: int = 0
    missing_usd: int = 0
    cross_listings: int = 0
    unresolved: int = 0
    needs_refetch: int = 0
    complete: int = 0
    path: str = ""
    # INDEX-CLASS-SANITY-1 - rows kept in the table but never used as peers, and which ones, so
    # a wrong call can be checked rather than trusted.
    funds: int = 0
    suspect: int = 0
    fund_examples: list = field(default_factory=list)
    suspect_examples: list = field(default_factory=list)
    # PEER-RECEIPTS-1 - secondary trading lines, by kind, and how many of them are the ONLY line
    # their company has in the index (so the company is absent from every peer group).
    receipts: dict = field(default_factory=dict)
    receipt_examples: dict = field(default_factory=dict)
    receipts_sole: int = 0
    # PEER-SIZE-SANITY-1 - rows whose size the company's other lines refute; counted apart from
    # the receipts, funds and suspect labels above (a row is counted under the first that fits).
    size_suspect: int = 0
    size_suspect_examples: list = field(default_factory=list)
    # ...and the companies whose lines disagree where no row could be blamed (two lines cannot
    # say which is wrong), used as they stand.
    size_disputed: int = 0
    size_disputed_examples: list = field(default_factory=list)
    # PEER-ADR-ALIAS-1 - rows an identity alias applies to, and the ADR/ADS rows nothing links to a
    # home (so the next orphan is visible rather than found by accident).
    aliased: int = 0
    aliased_examples: list = field(default_factory=list)
    orphan_adrs: int = 0
    orphan_adr_examples: list = field(default_factory=list)

    def lines(self) -> list[str]:
        if not self.rows:
            return [f"Market index: EMPTY ({self.path} does not exist yet).",
                    "Build it with:  python -m aristos_council.market_index build"]
        out = [f"Market index: {self.rows} row(s) in {self.path}",
               f"  complete (usable as peers): {self.complete}",
               f"  no market cap:              {self.missing_cap}"
               f"  ({self.cap_due} due, {self.cap_waiting} waiting)",
               f"  missing classification:     {self.missing_classification}"
               f"  ({self.classification_due} due, "
               f"{self.classification_waiting} waiting)",
               f"  no USD conversion:          {self.missing_usd}",
               f"  {self.cross_listings} cross-listing(s), excluded from peer groups",
               f"  {self.unresolved} row(s) with neither PrimaryTicker nor ISIN "
               f"(unresolved; treated as home listings)",
               f"  {self.needs_refetch} row(s) will be REFETCHED by the next build",
               f"  {self.funds} row(s) are {FUND_NOT_A_COMPANY}, excluded from peer groups"
               + (f" (e.g. {', '.join(self.fund_examples)})" if self.fund_examples else ""),
               f"  {self.suspect} row(s) {CLASSIFICATION_SUSPECT} - kept, excluded from "
               f"peer groups" + (f" (e.g. {', '.join(self.suspect_examples)})"
                                 if self.suspect_examples else ""),
               f"  oldest row: {self.oldest or 'unknown'}",
               "  per exchange:"]
        if self.aliased or self.orphan_adrs:
            out.insert(out.index("  per exchange:"), (
                f"  {self.aliased} row(s) carry an identity alias (data/identity_aliases.yaml)"
                + (f": {', '.join(self.aliased_examples)}" if self.aliased_examples else "")))
            out.insert(out.index("  per exchange:"), (
                f"  {self.orphan_adrs} US ADR/ADS row(s) name no home and share their name with "
                f"no other row - unlinked, may count a company twice"
                + (f" (e.g. {', '.join(self.orphan_adr_examples)})"
                   if self.orphan_adr_examples else "")))
        if self.size_disputed:
            out.insert(out.index("  per exchange:"),
                       f"  {self.size_disputed} company(ies) whose lines differ in size by more "
                       f"than the limit and cannot be adjudicated - sizes used as they stand"
                       + (f" (e.g. {', '.join(self.size_disputed_examples)})"
                          if self.size_disputed_examples else ""))
        if self.size_suspect:
            out.insert(out.index("  per exchange:"),
                       f"  {self.size_suspect} row(s) {SIZE_SUSPECT} - kept, excluded from peer "
                       f"groups" + (f" (e.g. {', '.join(self.size_suspect_examples)})"
                                    if self.size_suspect_examples else ""))
        if self.receipts:
            at = out.index("  per exchange:")
            out.insert(at, f"  {sum(self.receipts.values())} secondary trading line(s) (receipts "
                           f"and foreign lines) - kept, excluded from peer groups; "
                           f"{self.receipts_sole} of them are the ONLY line of their company, so "
                           f"that company is absent from every peer group")
            for kind, n in sorted(self.receipts.items()):
                examples = self.receipt_examples.get(kind, [])
                at += 1
                out.insert(at + 0, f"    {kind}: {n}" + (f" (e.g. {', '.join(examples)})"
                                                         if examples else ""))
        if self.missing_cap:
            # MARKET-INDEX-2 - the first build produced 526 rows and every one of them had
            # no cap, because the request asked for General and the cap lives under
            # Highlights.
            #
            # INDEX-CAP-RETRY-1 - and they are no longer all refetched. The provider answers
            # "MarketCapitalization": "NA" for these and it does not change, so a row that has
            # come back empty twice waits a month. "Due" is what the next build will spend.
            out.insert(1, f"  {self.missing_cap} row(s) carry NO MARKET CAP and cannot be "
                          f"peers; the next build refetches {self.cap_due} of them "
                          f"(~{self.cap_due * CHARGE_FUNDAMENTALS:,} charged units), "
                          f"{self.cap_waiting} are waiting on the backoff.")
        for exchange, n in sorted(self.per_exchange.items(), key=lambda kv: -kv[1]):
            out.append(f"    {exchange:8s} {n:6d}")
        return out


def status(store: Optional[IndexStore] = None, *, today: Optional[date] = None,
           empty_retry_after: int = DEFAULT_EMPTY_RETRY_AFTER,
           empty_retry_days: int = DEFAULT_EMPTY_RETRY_DAYS,
           size_factor: float = DEFAULT_SIZE_FACTOR, aliases=...) -> IndexStatus:
    """What is in the table. Works on an EMPTY index without raising — the first thing
    anyone runs is ``status``, and it must answer rather than fail."""
    store = store or IndexStore()
    rows = store.load()
    today = today or date.today()
    backoff = dict(after_attempts=empty_retry_after, every_days=empty_retry_days)
    out = IndexStatus(rows=len(rows), path=str(store.path))
    for row in rows:
        out.per_exchange[row.exchange or "?"] = out.per_exchange.get(row.exchange or "?", 0) + 1
        if not row.classification:
            out.missing_classification += 1
        if row.market_cap is None:
            out.missing_cap += 1
            # INDEX-CAP-RETRY-1 — split by whether the next build will actually ASK again.
            if row.retry_due(today, **backoff):
                out.cap_due += 1
            else:
                out.cap_waiting += 1
        if not row.classification:
            if row.retry_due(today, **backoff):
                out.classification_due += 1
            else:
                out.classification_waiting += 1
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
        # A fund is counted as a fund and never also as suspect.
        if is_fund(row):
            out.funds += 1
            if len(out.fund_examples) < EXAMPLES:
                out.fund_examples.append(row.ticker)
        elif suspect_reason(row):
            out.suspect += 1
            if len(out.suspect_examples) < EXAMPLES:
                out.suspect_examples.append(row.ticker)
    secondary = secondary_lines(rows)
    if secondary:
        own_line_sized = set()      # rows of a company that has a non-receipt line with a size
        for company in company_groups(rows):
            if any(normalise_symbol(r.ticker) not in secondary and r.market_cap_usd is not None
                   for r in company):
                own_line_sized.update(normalise_symbol(r.ticker) for r in company)
        named_own = {_name_key(r.name) for r in rows
                     if normalise_symbol(r.ticker) not in secondary and _has_identity(r)
                     and r.market_cap_usd is not None}
        for row in rows:
            kind = secondary.get(normalise_symbol(row.ticker))
            if not kind:
                continue
            out.receipts[kind] = out.receipts.get(kind, 0) + 1
            examples = out.receipt_examples.setdefault(kind, [])
            if len(examples) < EXAMPLES:
                examples.append(row.ticker)
            if (normalise_symbol(row.ticker) not in own_line_sized
                    and _name_key(row.name) not in named_own):
                out.receipts_sole += 1
    for ticker in sorted(size_suspects(rows, size_factor)):
        row = next(r for r in rows if normalise_symbol(r.ticker) == ticker)
        if ticker in secondary or is_fund(row) or suspect_reason(row):
            continue                                   # already counted under its first reason
        out.size_suspect += 1
        if len(out.size_suspect_examples) < EXAMPLES:
            out.size_suspect_examples.append(row.ticker)
    if aliases is ...:
        aliases = load_identity_aliases()
    present = {normalise_symbol(r.ticker) for r in rows}
    aliased = [a.ticker for a in aliases or () if a.ticker in present]
    out.aliased = len(aliased)
    out.aliased_examples = aliased[:EXAMPLES]
    orphans = orphan_depositary_rows(rows, aliased)
    out.orphan_adrs = len(orphans)
    out.orphan_adr_examples = [r.ticker for r in orphans[:EXAMPLES]]
    for tickers in size_disputes(rows, size_factor):
        out.size_disputed += 1
        if len(out.size_disputed_examples) < EXAMPLES:
            out.size_disputed_examples.append("/".join(tickers))
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
    """The identifier a row itself offers for its company, strongest first.

    PEER-DEDUP-1: PrimaryTicker FIRST, then ISIN, then the ticker. It used to be ISIN first, and
    a US ADR carries its OWN ISIN while naming its home line in PrimaryTicker (probed 2026-09-25:
    TSM.US -> PrimaryTicker "2330.TW", ISIN US8740391003; 2330.TW has TW0003...), so the two
    were kept apart and TSMC was its own peer. The primary ticker is the fact that says "this is
    a line of THAT company"; an ISIN only says it is the same security.

    This is the key for ONE row. Companies are built by ``company_groups``, which links rows
    transitively over all three, because a company's rows do not all carry the same field.
    """
    if row.primary_ticker and row.primary_ticker.strip():
        return f"primary:{normalise_symbol(row.primary_ticker)}"
    if row.isin and row.isin.strip():
        return f"isin:{row.isin.strip().upper()}"
    return f"ticker:{normalise_symbol(row.ticker)}"


def _identity_nodes(row: "IndexRow") -> list[str]:
    """Every handle a row gives for its company: its own ticker, its primary, its ISIN."""
    nodes = [f"t:{normalise_symbol(row.ticker)}"]
    if row.primary_ticker and row.primary_ticker.strip():
        nodes.append(f"t:{normalise_symbol(row.primary_ticker)}")
    if row.isin and row.isin.strip():
        nodes.append(f"i:{row.isin.strip().upper()}")
    return nodes


def company_groups(rows, *, link_by_name: bool = False) -> list[list["IndexRow"]]:
    """The rows, grouped so that each group is ONE company.

    Two rows are the same company when they share ANY handle - a primary ticker (one names the
    other's ticker, or both name the same home) or an ISIN. Linked transitively, PrimaryTicker
    and ISIN together, because neither alone is enough. Measured on the 22,209-row table
    (2026-09-25): grouping by PrimaryTicker-else-ISIN alone would leave 727 companies split
    across two or more groups - Hutchmed's Hong Kong lines ``0013.HK`` and ``13.HK`` (own
    primary each, one ISIN), its US ADR (primary 0013.HK, its own ISIN) and its London line;
    Nordea's five lines. ISIN-first, the old rule, left TSMC's ADR standing apart.

    ``link_by_name`` adds a third, GUARDED link for lines the provider gives no common handle:
    see ``_link_groups_by_name``.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    materialised = list(rows)
    for row in materialised:
        first, *rest = _identity_nodes(row)
        find(first)
        for node in rest:
            parent[find(node)] = find(first)
    groups: dict[str, list] = {}
    for row in materialised:
        groups.setdefault(find(_identity_nodes(row)[0]), []).append(row)
    linked = list(groups.values())
    return _link_groups_by_name(linked) if link_by_name else linked


# Two lines of one company, in one currency, differ by FX and timing - a few per cent; the
# widest legitimate gap seen in the table is a Hong Kong RMB counter against the HKD one, at 24%.
NAME_LINK_TOLERANCE = 1.25


def _link_groups_by_name(groups: list) -> list:
    """Merge groups that share a company NAME and a size, for lines no handle links.

    Measured 2026-09-25: 385 reduced company names span more than one group in the eligible pool,
    and most are one company - Alphabet x4, ArcelorMittal x4, Alibaba x4, ASML x3 (``ASML.AS``
    and ``ASML.US`` each name THEMSELVES as primary, with different ISINs), Atlas Copco's A and B
    shares, Illinois Tool Works' Xetra line. PrimaryTicker and ISIN cannot link those, and left
    apart they were counted as several peers - the defect this batch exists to remove.

    A name alone is not enough, and the guard is what makes this safe: the two rows must ALSO
    carry USD caps within ``NAME_LINK_TOLERANCE``. Different companies do share a short name
    (APA Corp in the US at $15.7bn and APA Group in Australia at $10.2bn, 1.54x; Argan Inc and
    Argan SA, 2.5x), and they are not merged. A row with no size is never linked - null is not
    a match. It is used for the peer POOL and the subject's own lines, not for the size-sanity
    test, which needs the lines this would merge to still be separate.
    """
    parent = list(range(len(groups)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    buckets: dict[str, list] = {}
    for number, members in enumerate(groups):
        for row in members:
            key = _name_key(row.name)
            if len(key) >= 3 and row.market_cap_usd is not None and row.market_cap_usd > 0:
                buckets.setdefault(key, []).append((number, row))
    for entries in buckets.values():
        for i, (first, a) in enumerate(entries):
            for second, b in entries[i + 1:]:
                if (find(first) != find(second)
                        and not _far_apart(a.market_cap_usd, b.market_cap_usd,
                                           NAME_LINK_TOLERANCE)):
                    parent[find(second)] = find(first)
    merged: dict[int, list] = {}
    for number, members in enumerate(groups):
        merged.setdefault(find(number), []).extend(members)
    return list(merged.values())


def _listing_rank(row: "IndexRow") -> tuple:
    """Which row of a company is the one to keep. Lower is better."""
    home = 0 if is_home_listing(row) else 1
    # The ISIN fallback: the home listing is the one whose venue country matches the
    # company's own country. ISINs carry the issuer country in their first two letters.
    country_match = 1
    if row.country and row.isin and len(row.isin) >= 2:
        country_match = 0 if row.isin[:2].upper() == row.country.strip().upper() else 1
    return (home, country_match, normalise_symbol(row.ticker))


def company_pool(rows, *, link_by_name: bool = False) -> tuple[list, int, dict]:
    """``(kept, dropped, absorbed)``: one row per company, how many were dropped, and
    ``{dropped ticker: the ticker that kept its place}`` so a caller can say who stood in for whom.
    """
    kept = []
    dropped = 0
    absorbed: dict[str, str] = {}
    for group in company_groups(rows, link_by_name=link_by_name):
        group.sort(key=_listing_rank)
        kept.append(group[0])
        dropped += len(group) - 1
        for other in group[1:]:
            absorbed[normalise_symbol(other.ticker)] = normalise_symbol(group[0].ticker)
    return sorted(kept, key=lambda r: r.ticker), dropped, absorbed


def one_row_per_company(rows, *, link_by_name: bool = False) -> tuple[list, int]:
    """``(kept, dropped)`` - the pool, deduplicated by company."""
    kept, dropped, _ = company_pool(rows, link_by_name=link_by_name)
    return kept, dropped


# --------------------------------------------------------------------------- #
# INDEX-CLASS-SANITY-1 - a fund is not a company, and a label can contradict its own name
# --------------------------------------------------------------------------- #
# MEASURED on the built table (21,181 rows), because the peer ladder trusts every row:
#
#   0052.TW   "Fubon Taiwan Technology"               GicSubIndustry "Pharmaceuticals"
#   00939.TW  "China Construction Bank Corp Class H"  GicSubIndustry "Semiconductor Materials"
#   00941.TW  "China Mobile Ltd"                      GicSubIndustry "Semiconductor Materials"
#
# All three are Taiwan ETFs (the 00xx code range) that EODHD lists as "Common Stock", so they
# passed the listing filter, and the provider's classification for them is nonsense. A row like
# that becomes a "pharmaceutical peer" or a "semiconductor-equipment peer" of a real company.
#
# Two DIFFERENT problems, kept apart because they are handled differently:
#
#   FUND        the row is not an operating company at all. Recognised by name and by the code
#               shape each exchange gives its funds. Kept in the table (it is a faithful mirror
#               of the provider, the way cross-listings are), excluded from peer groups, and
#               counted in ``status`` as "fund, not a company".
#
#   SUSPECT     the row may well be a company, but its classification contradicts what its own
#               name says (a "Bank" filed under semiconductors). Kept, excluded from peer
#               groups, counted. Only a POSITIVE contradiction flags: a row with no
#               classification at all contradicts nothing (null is not false).
#
# Everything here is a pure function of the row. No network, no model.
_FUND_NAME_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:etf|etn|etp)s?\b",                 # "... ETF", "VanEck Sui ETN A"
    r"\bexchange[- ]traded\b",
    r"\bucits\b", r"\bsicav\b",
    r"\bfundos?\b",                          # Brazilian funds: "... Fundo De Indice"
    r"\b(?:leveraged|inverse) product\b",    # HK: "CSOP CSI 300 Index Daily (2x) Leveraged Product"
    r"\bfunds? (?:series )?trust\b",         # US ETF shells: "Listed Funds Trust"
    r"\bseries trust\b",
    r"\bclosed[- ](?:end )?fund\b", r"\bclosed[-]end\b",
    # UK closed-end funds. NOT a Real Estate / Realty / Mortgage / Property Investment Trust:
    # those are REITs, operating property companies and legitimate peers (Link REIT, Federal
    # Realty, PennyMac Mortgage Investment Trust, Allied Properties). Found by surveying the
    # built table: the unqualified pattern swallowed 60-odd of them.
    r"(?<!estate )(?<!realty )(?<!mortgage )(?<!property )\binvestment trusts?\b",
    r"\bindex solutions\b",
    r"\b(?:ishares|spdr|proshares|xtrackers|lyxor|direxion|vaneck|global x)\b",
))
# A fund word is only a FUND when the row does not read as an operating business: Chemtrade
# Logistics Income Fund is a chemicals company and Rural Funds Group is a REIT. So these count
# only with corroboration - the provider gave the row neither a classification nor a cap - or
# with an asset-management classification.
_FUND_WORD = re.compile(r"\b(?:funds?|index)\b", re.IGNORECASE)
_FUND_WORD_STRICT = re.compile(r"\bfunds?\b", re.IGNORECASE)
_ASSET_MANAGEMENT = re.compile(r"asset management", re.IGNORECASE)

# The code shape each exchange gives its funds. Per exchange, because the shapes do not
# overlap and a pattern that is right for one is a false positive on another.
#   TW   the 00xx series is ETFs (0050, 0052, 00939 ...); companies start at 1101.
#   LSE  "0P" + eight characters is a Morningstar fund id, not a ticker.
_FUND_CODE_PATTERNS = {
    "TW": re.compile(r"^00\d{2,5}[A-Z]?$"),
    "LSE": re.compile(r"^0P[0-9A-Z]{8}$"),
}

FUND_NOT_A_COMPANY = "fund, not a company"


def _code_and_market(row: "IndexRow") -> tuple[str, str]:
    code, _, suffix = (row.ticker or "").upper().rpartition(".")
    if not code:                                    # no dot: the whole string is the code
        code, suffix = suffix, ""
    return code, (row.market or suffix or "").upper()


def fund_reason(row: "IndexRow") -> str:
    """Why this row is a fund rather than a company, or "" when it is not.

    Four tests, strongest first, and the reason names which fired so a wrong call is
    diagnosable from the row. A row is a fund on a strong name pattern, on its exchange's fund
    code shape, on a fund word plus an asset-management classification, or on a fund word with
    NOTHING to say otherwise (no classification and no cap).
    """
    name = row.name or ""
    for pattern in _FUND_NAME_PATTERNS:
        if pattern.search(name):
            return f"{FUND_NOT_A_COMPANY} (name: {pattern.search(name).group(0).lower()})"
    code, market = _code_and_market(row)
    shape = _FUND_CODE_PATTERNS.get(market)
    if shape is not None and shape.search(code):
        return f"{FUND_NOT_A_COMPANY} ({market} fund code {code})"
    classification = _classification_text(row)
    if _FUND_WORD_STRICT.search(name) and _ASSET_MANAGEMENT.search(classification):
        return f"{FUND_NOT_A_COMPANY} (fund in an asset-management classification)"
    if _FUND_WORD.search(name) and not classification.strip() and row.market_cap is None:
        return f"{FUND_NOT_A_COMPANY} (fund word, and no classification or cap)"
    return ""


def is_fund(row: "IndexRow") -> bool:
    return bool(fund_reason(row))


def _classification_text(row: "IndexRow") -> str:
    """Every classification field the row carries, as one lowercase string.

    A field that says "Other" is no classification at all (Granite REIT's US line reads
    "Other"), so it is dropped: it must not be read as a label that contradicts the name.
    """
    return " ".join(x for x in (row.gics_sector, row.gics_industry, row.gics_subindustry,
                                row.sector, row.industry)
                    if x and x.strip().lower() != "other").strip().lower()


# The name words that say what KIND of business a row is, and the classification text that is
# CONSISTENT with each. A row is suspect only when the name says one thing and EVERY
# classification field says something outside the family - one field agreeing is enough to
# leave it alone, because a false flag deletes a real peer.
#
# "Trust" and "Fund" are deliberately not here: Canadian income trusts and royalty funds are
# ordinary operating businesses with ordinary classifications (Chemtrade -> Commodity
# Chemicals), so a contradiction test would remove real peers. Funds are handled above.
_FINANCIAL_FAMILY = re.compile(
    r"bank|financ|insur|capital markets|mortgage|thrift|credit|lending|loan|asset management|"
    r"broker|invest|saving|reit|real estate|holding|conglomerate|diversified", re.IGNORECASE)
_REAL_ESTATE_FAMILY = re.compile(
    r"reit|real estate|propert|mortgage|financ|hotel|land|realty|trust", re.IGNORECASE)
_NAME_EXPECTS = (
    ("bank", re.compile(r"\b(?:bank|banks|banco|bancorp|banca|bankshares)\b", re.IGNORECASE),
     _FINANCIAL_FAMILY),
    ("insurance", re.compile(r"\b(?:insurance|insurer|insurers|assurance|reinsurance)\b",
                             re.IGNORECASE), _FINANCIAL_FAMILY),
    ("REIT", re.compile(r"\breits?\b|\b(?:real estate|realty|mortgage|property) investment "
                        r"trusts?\b", re.IGNORECASE), _REAL_ESTATE_FAMILY),
)

CLASSIFICATION_SUSPECT = "classification suspect"


def suspect_reason(row: "IndexRow") -> str:
    """Why this row's classification contradicts its own name, or "" when it does not.

    A row with no classification at all is NEVER suspect: nothing contradicts anything, and the
    peer ladder already cannot place it. A fund is not reported here either - it has its own
    reason and is counted once.
    """
    classification = _classification_text(row)
    if not classification:
        return ""
    for word, pattern, family in _NAME_EXPECTS:
        if pattern.search(row.name or "") and not family.search(classification):
            return (f"{CLASSIFICATION_SUSPECT} (named like a {word}, filed under "
                    f"{row.classification or classification})")
    return ""


def is_suspect(row: "IndexRow") -> bool:
    return not is_fund(row) and bool(suspect_reason(row))


# --------------------------------------------------------------------------- #
# PEER-RECEIPTS-1 - a receipt is a trading line, not a company
# --------------------------------------------------------------------------- #
# MEASURED on the built table (22,209 rows, 2026-09-25). EODHD serves depository receipts and
# foreign trading lines as ordinary "Common Stock" rows, usually with NEITHER PrimaryTicker nor
# ISIN (4,825 rows carry no identity at all), so nothing links them to the company they mirror
# and each one stood in a peer group as a company of its own:
#
#   E1TN34.SA   Eaton Corporation plc          a Brazilian BDR, stood in for Eaton itself
#   AMD.TO      Advanced Micro Devices CDR     a Canadian CDR, beside AMD.US
#   NVDA.SW     NVIDIA Corporation             a Swiss line, no identity
#   0NMK.LSE    Vestas Wind Systems A/S        a London "0xxx" line: $5.2bn there against the
#                                              $31.5bn its home line reports, and filed under
#                                              "Coal & Consumable Fuels"
#
# The London "0xxx" block alone is 2,371 of 3,834 LSE rows; almost all carry a wrong label and
# a wrong size. So a SECONDARY LINE is kept in the table (it is a faithful mirror of the
# provider, like a cross-listing), excluded from peer pools, and counted in ``status`` by kind.
#
# The cost is stated rather than hidden: a company whose ONLY line in the index is a receipt
# leaves every peer pool. ``status`` counts those ("the only line of their company").
RECEIPT_BDR = "Brazilian receipt (BDR)"
RECEIPT_BR_FRACTIONAL = "Brazilian fractional-lot line"
RECEIPT_CDR = "Canadian receipt (CDR)"
RECEIPT_LSE_LINE = "London 0xxx line of a foreign company"
RECEIPT_LSE_GDR = "London depositary receipt (GDR)"
RECEIPT_SWISS_LINE = "Swiss line of a foreign company"

# Sao Paulo tickers: a BDR is <4 characters>3<2..9> (A1MD34, AVGO34, E1TN34, TSMC34, NVDC34);
# no Brazilian company code has that shape (they end 3, 4, 5, 6 or 11), and the one 3x-ending
# code in the table, TF533, is five characters. A fractional-lot line is the company's code plus F.
_BDR_CODE = re.compile(r"^[A-Z0-9]{4}3[2-9]$")
_BDR_ISIN = re.compile(r"^BR[A-Z0-9]{4}BDR[A-Z0-9]{3}$")
_BR_FRACTIONAL_CODE = re.compile(r"^[A-Z0-9]{4}\d{1,2}F$")
_CDR_NAME = re.compile(r"\bCDR\b")
# London's "0xxx" lines: a zero and three characters (0QMI, 0A0D, 0NMK). UK companies do not
# start with a zero, and the 10-character "0P" Morningstar fund ids are handled as funds above.
_LSE_FOREIGN_CODE = re.compile(r"^0[A-Z0-9]{3}$")
_GDR_NAME = re.compile(r"\bGDR\b|global depositary", re.IGNORECASE)


def receipt_kind(row: "IndexRow") -> str:
    """What kind of secondary trading line this row is, or "" when it is a company's own line.

    A pure function of the row: exchange, code shape, name, ISIN and PrimaryTicker. Nothing here
    needs the rest of the table; the one case that does (a Swiss line with no identity at all)
    is ``secondary_lines``.
    """
    code, market = _code_and_market(row)
    name = row.name or ""
    if market == "SA":
        if _BDR_CODE.match(code) or _BDR_ISIN.match((row.isin or "").upper()):
            return RECEIPT_BDR
        if _BR_FRACTIONAL_CODE.match(code):
            return RECEIPT_BR_FRACTIONAL
    elif market == "TO":
        if _CDR_NAME.search(name):
            return RECEIPT_CDR
    elif market == "LSE":
        if _LSE_FOREIGN_CODE.match(code):
            return RECEIPT_LSE_LINE
        if _GDR_NAME.search(name):
            return RECEIPT_LSE_GDR
    elif market == "SW":
        primary = normalise_symbol(row.primary_ticker)
        if primary and not primary.endswith(".SW"):
            return RECEIPT_SWISS_LINE      # LLY.SW -> LLY.US, NIBEB.SW -> NIBE-B.ST
    return ""


# The words that do not identify a company in a name: legal forms, share-class, receipt and
# preference wording. "Eaton Corporation plc" and "Eaton Corporation PLC" must read as one; "Vestas
# Wind Systems A/S" must not read as anything else.
#
# PEER-ADR-ALIAS-1 added the depositary and preference words. "American" is NOT noise on its own
# ("American Express", "American Airlines"); only the PHRASE "american depositary shares/receipts"
# is stripped. ``shs`` / ``pfd`` / ``prf`` are the abbreviations the provider actually uses for a
# preference share ("Doosan Pref Shs", "Chinhung International Inc. Pfd. S...", "Nexen Tire Prf 1").
_NAME_NOISE = frozenset((
    "inc", "corp", "corporation", "plc", "ag", "sa", "nv", "se", "ltd", "limited", "co",
    "company", "holding", "holdings", "group", "ab", "oyj", "asa", "as", "spa", "llc", "the",
    "n", "a", "b", "class", "series", "ordinary", "share", "shares", "sponsored", "adr", "cdr",
    "cad", "hedged", "drn", "dr", "incorporated", "kgaa", "sab", "cv",
    "ads", "gdr", "depositary", "receipt", "receipts",
    "pref", "preferred", "preference", "pfd", "prf", "shs"))
_DEPOSITARY_PHRASE = re.compile(
    r"\b(?:american|global) depositary (?:shares?|receipts?)\b")


def _name_key(name: str) -> str:
    """A company name reduced to what identifies it, or "" when nothing is left."""
    import unicodedata
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    text = re.sub(r"\(.*?\)", " ", text.lower())
    if "fully paid" in text:
        return ""     # ASX deferred-settlement placeholders ("Ordinary Fully Paid Deferred ...")
    text = _DEPOSITARY_PHRASE.sub(" ", text)
    text = re.sub(r"[./]", "", text)                 # N.V. -> nv, S.A. -> sa, A/S -> as
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(tok for tok in text.split() if tok not in _NAME_NOISE)


def _has_identity(row: "IndexRow") -> bool:
    return bool((row.primary_ticker or "").strip() or (row.isin or "").strip())


def secondary_lines(rows) -> dict[str, str]:
    """``{normalised ticker: kind}`` for every secondary trading line in ``rows``.

    ``receipt_kind`` for each row, plus the one case a single row cannot show: a SIX row with
    neither PrimaryTicker nor ISIN (NVDA.SW, INTC.SW, ASML.SW) whose name is listed elsewhere
    WITH an identity. A Swiss row that has no identity and no such twin is left alone - CENTIEL
    and INFRACORE are real Swiss companies.
    """
    rows = list(rows)
    out = {normalise_symbol(r.ticker): kind for r in rows if (kind := receipt_kind(r))}
    elsewhere: dict[str, list] = {}
    for row in rows:
        if normalise_symbol(row.ticker) not in out and _has_identity(row):
            elsewhere.setdefault(_name_key(row.name), []).append(row)
    for row in rows:
        code, market = _code_and_market(row)
        if market != "SW" or _has_identity(row) or normalise_symbol(row.ticker) in out:
            continue
        key = _name_key(row.name)
        if key and any(_code_and_market(twin)[1] != "SW" for twin in elsewhere.get(key, ())):
            out[normalise_symbol(row.ticker)] = RECEIPT_SWISS_LINE
    return out


def _receipt_home(row: "IndexRow", rows, secondary: dict) -> Optional["IndexRow"]:
    """The company's own line for a receipt that names no home, found by NAME, or None.

    Used to answer for the company when the reader looks a receipt up (``E1TN34.SA`` is Eaton).
    Only a row that is not itself a secondary line, has an identity and carries a size is a
    candidate, and the usual home-listing preference decides between several.
    """
    key = _name_key(row.name)
    if not key:
        return None
    twins = [r for r in rows
             if normalise_symbol(r.ticker) != normalise_symbol(row.ticker)
             and normalise_symbol(r.ticker) not in secondary
             and _has_identity(r) and r.market_cap_usd is not None
             and _name_key(r.name) == key]
    return min(twins, key=_listing_rank) if twins else None


# --------------------------------------------------------------------------- #
# PEER-SIZE-SANITY-1 - a size that no other line of the company supports
# --------------------------------------------------------------------------- #
# The peer ladder bands on USD market cap, so a row whose cap is wrong lands in the wrong band or,
# worse, in the right one for the wrong reason. Measured 2026-09-25: Ming Yang's London GDR reads
# $999.6bn (it is a wind-turbine maker worth a few billion), and Vestas' London line reads $5.2bn
# against $31.5bn on its home line and $31.0bn on Xetra.
#
# A row is SIZE SUSPECT when its USD cap is more than ``factor`` times away from the same
# company's other listings (default 5x). It stays in the table, is excluded from peer groups and
# is counted. The rule is deliberately unwilling to guess:
#
#   * three or more sized listings: a row is flagged only when it disagrees with EVERY other
#     listing AND those others form a consensus (two of them agree). Figures that all disagree
#     with each other say nothing about which is wrong, so nothing is flagged;
#   * exactly TWO sized listings: the NON-HOME line is flagged against an EXPLICIT home line (one
#     that names itself as its own primary). If neither or both are home (0013.HK and 13.HK each
#     name themselves) it abstains, because "which of the two is wrong" is not something the data
#     can answer;
#   * one sized listing: nothing to compare against - a company with a single line is never
#     flagged by this test (null is not false).
#
# HISTORY. The two-line rule was first written as above, then SWITCHED OFF in batch 6 because,
# measured on the built table, it flagged the CORRECT row for every UK company: RR.LSE (home,
# GBX) read $1.6bn and RRU.XETRA $157.9bn, and the home line was the wrong one because of the
# pence scaling of a pounds figure (INDEX-GBX-SCALE-1, ``MINOR_UNIT_MARKET_CAP``). With that
# defect fixed the anchor is sound again and the rule is back on.
#
# A company whose lines disagree and cannot be adjudicated is not flagged and not silent:
# ``size_disputes`` names it, and ``status`` counts it, so the disagreement is visible.
#
# Secondary lines are neither judged nor allowed to vote: they are already excluded from every
# pool and their sizes are the least trustworthy in the table.
def _far_apart(a: float, b: float, factor: float) -> bool:
    return max(a, b) / min(a, b) > factor


def _sized_own_lines(company, secondary: dict) -> list:
    return [r for r in company if r.market_cap_usd is not None and r.market_cap_usd > 0
            and normalise_symbol(r.ticker) not in secondary]


def size_suspects(rows, factor: float = DEFAULT_SIZE_FACTOR) -> dict[str, str]:
    """``{normalised ticker: reason}`` for every row whose size the company's other lines refute."""
    out: dict[str, str] = {}
    secondary = secondary_lines(rows)
    for company in company_groups(rows):
        sized = _sized_own_lines(company, secondary)
        if len(sized) < 2:
            continue                            # one line has nothing to compare against
        if len(sized) == 2:
            explicit_home = [r for r in sized if r.primary_ticker and is_home_listing(r)]
            if len(explicit_home) != 1:
                continue                        # cannot say which of the two is wrong
            anchor = explicit_home[0]
            other = next(r for r in sized if r is not anchor)
            if _far_apart(other.market_cap_usd, anchor.market_cap_usd, factor):
                out[normalise_symbol(other.ticker)] = _size_reason(
                    other, [anchor.market_cap_usd], factor)
            continue
        for row in sized:
            others = [r.market_cap_usd for r in sized if r is not row]
            if not all(_far_apart(row.market_cap_usd, cap, factor) for cap in others):
                continue
            if not any(not _far_apart(a, b, factor)
                       for i, a in enumerate(others) for b in others[i + 1:]):
                continue                        # no consensus among the others: abstain
            out[normalise_symbol(row.ticker)] = _size_reason(row, others, factor)
    return out


def size_disputes(rows, factor: float = DEFAULT_SIZE_FACTOR) -> list[list[str]]:
    """Companies whose lines disagree by more than ``factor`` and where NO row could be flagged.

    Each entry is the tickers of one company. Used to say so, not to exclude anything: the sizes
    of these companies are used as they stand.
    """
    flagged = size_suspects(rows, factor)
    secondary = secondary_lines(rows)
    out = []
    for company in company_groups(rows):
        sized = _sized_own_lines(company, secondary)
        if len(sized) < 2 or any(normalise_symbol(r.ticker) in flagged for r in sized):
            continue
        caps = [r.market_cap_usd for r in sized]
        if any(_far_apart(a, b, factor) for i, a in enumerate(caps) for b in caps[i + 1:]):
            out.append(sorted(r.ticker for r in sized))
    return out


def _size_reason(row: "IndexRow", others: list, factor: float) -> str:
    reference = sorted(others)[len(others) // 2]
    ratio = max(row.market_cap_usd, reference) / min(row.market_cap_usd, reference)
    return (f"{SIZE_SUSPECT} (${row.market_cap_usd / 1e9:,.1f}bn here against "
            f"${reference / 1e9:,.1f}bn on the company's other listings, {ratio:.1f}x apart; "
            f"the limit is {factor:g}x)")


# --------------------------------------------------------------------------- #
# peers
# --------------------------------------------------------------------------- #
RUNG_SUBINDUSTRY_TIGHT = "sub-industry, 1/4x–4x"
RUNG_SUBINDUSTRY_WIDE = "sub-industry, 1/10x–10x"
RUNG_INDUSTRY_WIDE = "industry, 1/10x–10x"
RUNG_NONE = "none"

DEFAULT_FLOOR = 12
DEFAULT_CAP = 40
LADDER_STEPS = 3                   # the three rungs above, numbered 1-3 in the cohort report

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
    # PEER-LABEL-MATCH-1 - which step of the ladder (1, 2 or 3) found the cohort, how many
    # DISTINCT companies it holds (counted by company identity, not by ticker), and which label
    # system matched each member ("GICS" / "EODHD").
    step: int = 0
    distinct_companies: int = 0
    matched_on: dict = field(default_factory=dict)
    # PEER-LABEL-RECALL-1 - every corrected label this group actually used (the subject's or a
    # member's), so a reader can tell a provider label from a corrected one.
    overridden: list = field(default_factory=list)
    # PEER-ADR-ALIAS-1 - every identity alias this group actually used, with the role it played
    # (the line the reader looked up, another line of the subject, a duplicate collapsed into its
    # home, or a member).
    aliased: list = field(default_factory=list)

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
                f"index snapshot {self.snapshot or 'unknown'} - found at step {self.step} of "
                f"{LADDER_STEPS}, {self.distinct_companies} distinct companies")


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


# --------------------------------------------------------------------------- #
# PEER-ADR-ALIAS-1 - a small file of identity CORRECTIONS
# --------------------------------------------------------------------------- #
# PrimaryTicker / ISIN / a shared name and size link most lines of one company. Some ADRs escape
# all three. Measured 2026-09-25 on the live index:
#
#   GSK.US   "GlaxoSmithKline PLC ADR"   names ITSELF as PrimaryTicker, has its own ISIN, and its
#                                        name key is "glaxosmithkline" against "gsk": no link fires,
#                                        so AZN.LSE's cohort held GSK twice ($102.5bn and $100.6bn)
#   SKHY.US  "SK Hynix Inc. ADS"         no PrimaryTicker, its own ISIN, and 1.37x from 000660.KO,
#                                        beyond the 1.25x name-link tolerance: SK Hynix twice in
#                                        TSMC's cohort
#
# ``data/identity_aliases.yaml`` supplies the PrimaryTicker the provider left out or got wrong:
#
#     aliases:
#       - ticker: GSK.US
#         primary: GSK.LSE
#         date: 2026-09-25
#         reason: why this line is the same company as the primary
#
# DATA CORRECTIONS ONLY, the same discipline as the label overrides: it changes ONE field
# (``primary_ticker``) on the rows a query reads, never a size, a label or a peer directly. The
# table on disk is not rewritten. The consequences follow from rules that already exist - the two
# lines fall in one company group, so the home line wins ``_listing_rank`` and the ADR's size is
# never used - and every alias a group actually used is printed as "identity aliased".
DEFAULT_ALIASES = Path(__file__).resolve().parents[2] / "data" / "identity_aliases.yaml"


@dataclass(frozen=True)
class IdentityAlias:
    ticker: str
    primary: str
    date: str
    reason: str


def load_identity_aliases(path: str | Path = DEFAULT_ALIASES) -> list[IdentityAlias]:
    """The alias file, or an empty list when there is none. A malformed one is an error: a
    correction that silently fails to apply is worse than one that is refused."""
    import yaml

    path = Path(path)
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = doc.get("aliases") if isinstance(doc, dict) else None
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise MarketIndexError(f"{path}: 'aliases' must be a list")
    out, seen = [], set()
    for i, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise MarketIndexError(f"{path}: alias {i} must be a mapping")
        missing = [k for k in ("ticker", "primary", "date", "reason")
                   if not str(entry.get(k) or "").strip()]
        if missing:
            raise MarketIndexError(f"{path}: alias {i} is missing {', '.join(missing)} - every "
                                   f"alias carries its ticker, the primary it belongs to, a "
                                   f"date and a reason")
        ticker = normalise_symbol(str(entry["ticker"]))
        primary = normalise_symbol(str(entry["primary"]))
        if ticker == primary:
            raise MarketIndexError(f"{path}: alias {i} maps {ticker} to itself")
        if ticker in seen:
            raise MarketIndexError(f"{path}: alias {i} repeats {ticker}")
        seen.add(ticker)
        out.append(IdentityAlias(ticker=ticker, primary=primary,
                                 date=str(entry["date"]).strip(),
                                 reason=str(entry["reason"]).strip()))
    return out


def apply_identity_aliases(rows, aliases) -> tuple[list["IndexRow"], dict]:
    """``(rows, applied)``: the rows with the aliased PrimaryTicker, and ``{ticker: alias}``.

    Rows are copied, never mutated. Applied BEFORE ``company_groups`` so every rule that groups
    by company sees the corrected identity.
    """
    import dataclasses

    rows = list(rows)
    wanted = {a.ticker: a for a in aliases or ()}
    if not wanted:
        return rows, {}
    out, applied = [], {}
    for row in rows:
        alias = wanted.get(normalise_symbol(row.ticker))
        if alias is None:
            out.append(row)
            continue
        applied[normalise_symbol(row.ticker)] = alias
        out.append(dataclasses.replace(row, primary_ticker=alias.primary))
    return out, applied


def _alias_line(alias: IdentityAlias) -> str:
    return f"identity aliased: {alias.ticker} -> {alias.primary} ({alias.date}: {alias.reason})"


# ADR / ADS / depositary wording in a row's name.
_DEPOSITARY_ROW = re.compile(r"\b(?:ADR|ADS|GDR)\b|depositary", re.IGNORECASE)


def orphan_depositary_rows(rows, aliased=()) -> list["IndexRow"]:
    """US rows named like an ADR/ADS that nothing links to a home line, so the NEXT GSK.US is
    visible instead of found by accident.

    An orphan names no home (no PrimaryTicker, or one that names the row itself), is not covered by
    an alias, and shares its cleaned name with no other row - so neither a handle nor the name link
    can ever merge it with the company it mirrors.
    """
    rows = list(rows)
    aliased = {normalise_symbol(t) for t in aliased}
    keys: dict[str, int] = {}
    for row in rows:
        key = _name_key(row.name)
        if key:
            keys[key] = keys.get(key, 0) + 1
    out = []
    for row in rows:
        code, market = _code_and_market(row)
        if market != "US" or normalise_symbol(row.ticker) in aliased:
            continue
        if not _DEPOSITARY_ROW.search(row.name or ""):
            continue
        primary = normalise_symbol(row.primary_ticker)
        if primary and primary != normalise_symbol(row.ticker):
            continue                                    # it names a home: not an orphan
        key = _name_key(row.name)
        if key and keys.get(key, 0) > 1:
            continue                                    # a name twin exists for the link to use
        out.append(row)
    return sorted(out, key=lambda r: r.ticker)


# --------------------------------------------------------------------------- #
# PEER-LABEL-RECALL-1 - a small file of label CORRECTIONS
# --------------------------------------------------------------------------- #
# The index mirrors the provider's classification (see docs/MARKET_INDEX.md, "Whose
# classification is this?") and does not maintain a private taxonomy. But a provider label that is
# plainly wrong hides a rival from every cohort it belongs to: Siemens Energy and Schneider
# Electric are filed as Industrial Machinery and Micron's US line as Semiconductor Materials &
# Equipment. ``data/label_overrides.yaml`` is the narrow, dated, reasoned escape hatch:
#
#     overrides:
#       - ticker: ENR.XETRA
#         gics_subindustry: Heavy Electrical Equipment
#         date: 2026-09-25
#         reason: why the provider's label is wrong
#         gics_industry: Electrical Equipment      # optional
#
# DATA CORRECTIONS ONLY: it changes a label, never a size, a listing or a peer. The table on disk
# is never rewritten - the correction is applied to the rows a query reads, and every use is
# shown in the cohort report as "label overridden". The corrected GICS INDUSTRY is taken from the
# file when given, otherwise from the industry the index's own rows already carry for that
# sub-industry, so a corrected row also sits in the right industry for the wide rung.
DEFAULT_OVERRIDES = Path(__file__).resolve().parents[2] / "data" / "label_overrides.yaml"


@dataclass(frozen=True)
class LabelOverride:
    ticker: str
    gics_subindustry: str
    date: str
    reason: str
    gics_industry: str = ""


def load_label_overrides(path: str | Path = DEFAULT_OVERRIDES) -> list[LabelOverride]:
    """The override file, or an empty list when there is none. A malformed one is an error:
    a correction that silently fails to apply is worse than one that is refused."""
    import yaml

    path = Path(path)
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = doc.get("overrides") if isinstance(doc, dict) else None
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise MarketIndexError(f"{path}: 'overrides' must be a list")
    out = []
    for i, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise MarketIndexError(f"{path}: override {i} must be a mapping")
        missing = [k for k in ("ticker", "gics_subindustry", "date", "reason")
                   if not str(entry.get(k) or "").strip()]
        if missing:
            raise MarketIndexError(f"{path}: override {i} is missing {', '.join(missing)} - "
                                   f"every correction carries its ticker, the corrected label, "
                                   f"a date and a reason")
        out.append(LabelOverride(
            ticker=normalise_symbol(str(entry["ticker"])),
            gics_subindustry=str(entry["gics_subindustry"]).strip(),
            date=str(entry["date"]).strip(), reason=str(entry["reason"]).strip(),
            gics_industry=str(entry.get("gics_industry") or "").strip()))
    return out


def apply_label_overrides(rows, overrides) -> tuple[list["IndexRow"], dict]:
    """``(rows, applied)``: the rows with corrected labels, and ``{ticker: (was, override)}``.

    Rows are copied, never mutated, so the caller's table (and the index on disk) is untouched.
    """
    import dataclasses
    from collections import Counter

    rows = list(rows)
    wanted = {o.ticker: o for o in overrides or ()}
    if not wanted:
        return rows, {}
    industry_of: dict[str, Counter] = {}
    for row in rows:
        if row.gics_subindustry and row.gics_industry:
            industry_of.setdefault(row.gics_subindustry, Counter())[row.gics_industry] += 1
    out, applied = [], {}
    for row in rows:
        override = wanted.get(normalise_symbol(row.ticker))
        if override is None:
            out.append(row)
            continue
        industry = override.gics_industry
        if not industry and industry_of.get(override.gics_subindustry):
            counts = industry_of[override.gics_subindustry]
            industry = sorted(counts, key=lambda k: (-counts[k], k))[0]
        applied[normalise_symbol(row.ticker)] = (
            row.gics_subindustry or row.industry or "unlabelled", override)
        out.append(dataclasses.replace(
            row, gics_subindustry=override.gics_subindustry,
            gics_industry=industry or row.gics_industry))
    return out, applied


def _override_line(ticker: str, was: str, override: LabelOverride) -> str:
    return (f"label overridden: {ticker} {was} -> {override.gics_subindustry} "
            f"({override.date}: {override.reason})")


# PEER-LABEL-MATCH-1 - like with like. A row carries two label SYSTEMS that merely share words:
# the GICS fields (sub-industry, industry) and EODHD's own ``industry``. The ladder used to fall
# back from one to the other, so a row with NO GICS label was matched against GICS names on the
# strength of its EODHD wording ("Semiconductors" is both an EODHD industry and a GICS
# sub-industry). Now GICS is compared with GICS and EODHD with EODHD, and nothing else.
LABEL_GICS = "GICS"
LABEL_EODHD = "EODHD"

# A provider's stand-in for "no label" is not a label: 'Other' is EODHD's industry for 1,400-odd
# unrelated rows, and matching them on it would be matching on nothing.
_NON_LABELS = frozenset(("", "other", "n/a", "na", "unknown", "none", "-", "--", "nan", "null"))


def _label(value) -> str:
    text = (value or "").strip().lower()
    return "" if text in _NON_LABELS else text


def _gics_label(row: IndexRow, level: str) -> str:
    return _label(row.gics_subindustry if level == "subindustry" else row.gics_industry)


def _eodhd_label(row: IndexRow) -> str:
    return _label(row.industry)


def _subject_systems(subject: IndexRow, level: str) -> tuple:
    """The label systems the subject can be matched in at this level: every one it has a label in.

    PEER-LABEL-RECALL-1: a peer qualifies on EITHER system, so a wrong label in one does not hide
    a rival. Measured 2026-09-25: Siemens Energy and Schneider Electric are filed by GICS as
    "Industrial Machinery" while their rivals (GE Vernova, Vestas, Eaton) are "Heavy Electrical
    Equipment" or "Electrical Components" - but EODHD files them all under one industry, so the
    second system finds what the first hides. Each system still compares only with itself.
    """
    systems = []
    if _gics_label(subject, level):
        systems.append(LABEL_GICS)
    if _eodhd_label(subject):
        systems.append(LABEL_EODHD)
    return tuple(systems)


def _label_hits(row: IndexRow, subject: IndexRow, level: str, systems: tuple) -> tuple:
    """The label systems in which ``row`` matches the subject, in ``systems`` only."""
    hits = []
    if LABEL_GICS in systems and _gics_label(row, level) == _gics_label(subject, level):
        hits.append(LABEL_GICS)
    if LABEL_EODHD in systems and _eodhd_label(row) == _eodhd_label(subject):
        hits.append(LABEL_EODHD)
    return tuple(hits)


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
          size_factor: float = DEFAULT_SIZE_FACTOR, overrides=..., aliases=...) -> PeerGroup:
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

    # PEER-LABEL-RECALL-1 - corrected labels are applied to the rows this query reads, before the
    # subject is looked up, so the subject, its other lines and every candidate see them. Left at
    # its default the shipped file is read when the REAL index is (``store``/no ``rows``) and
    # nothing is applied to rows handed in directly, so a fabricated table never meets a real
    # correction by accident.
    if overrides is ...:
        overrides = load_label_overrides() if rows is None else []
    universe, applied = apply_label_overrides(universe, overrides)

    # PEER-ADR-ALIAS-1 - the same rule for identity: the shipped alias file is read with the REAL
    # index and never applied to rows handed in directly. Applied before anything groups by
    # company.
    if aliases is ...:
        aliases = load_identity_aliases() if rows is None else []
    universe, alias_applied = apply_identity_aliases(universe, aliases)

    wanted = (ticker or "").strip().upper()
    by_ticker = {r.ticker.upper(): r for r in universe}
    by_yahoo = {r.yahoo_ticker.upper(): r for r in universe if r.yahoo_ticker}
    subject = by_ticker.get(wanted) or by_yahoo.get(wanted)
    if subject is None:
        group.reasons.append(f"{ticker} is not in the market index")
        return group

    alias_used: dict[str, str] = {}
    if normalise_symbol(subject.ticker) in alias_applied:
        alias_used[normalise_symbol(subject.ticker)] = "line the reader looked up"

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

    # PEER-RECEIPTS-1 - a receipt looked up by the symbol the reader has is answered for the
    # company it mirrors, found by name because the provider gives it no identity.
    secondary = secondary_lines(universe)
    subject_kind = secondary.get(normalise_symbol(subject.ticker), "")
    if subject_kind:
        home = _receipt_home(subject, universe, secondary)
        if home is not None:
            group.reasons.append(f"{subject.ticker} is a {subject_kind}; peers computed for "
                                 f"{home.ticker}, the company's own line")
            subject = home
        else:
            group.reasons.append(f"{subject.ticker} is a {subject_kind} and the company's own "
                                 f"line is not in the index; its label and size are the "
                                 f"receipt's own and may be unreliable")

    group.subject = subject
    if normalise_symbol(subject.ticker) in applied:
        was, override = applied[normalise_symbol(subject.ticker)]
        group.overridden.append({"ticker": subject.ticker, "role": "subject", "was": was,
                                 "now": override.gics_subindustry, "date": override.date,
                                 "reason": override.reason})
        group.reasons.append(_override_line(subject.ticker, was, override))
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
            where = subject.currency or "its own currency"
            group.reasons.append(
                f"{subject.ticker} has a market cap in {where} but no USD conversion, "
                f"so it cannot be size-banded against the rest of the index")
        return group
    if not subject.classification:
        group.reasons.append(f"{subject.ticker} has no industry classification in the "
                             "index")
        return group

    # INDEX-CLASS-SANITY-1 - a subject that is not a company, or whose label contradicts its own
    # name, has no peers to give: the classification the ladder would key on is not to be
    # believed.
    if is_fund(subject):
        group.reasons.append(f"{subject.ticker}: {fund_reason(subject)}, so no peer group is "
                             f"formed")
        return group
    if suspect_reason(subject):
        group.reasons.append(f"{subject.ticker}: {suspect_reason(subject)}, so no peer group "
                             f"is formed")
        return group

    # PEER-SIZE-SANITY-1 - the subject is kept (it is the company asked about) but its size is
    # said to be doubtful, because every band below is a multiple of it.
    size_flags = size_suspects(universe, size_factor)
    if normalise_symbol(subject.ticker) in size_flags:
        group.reasons.append(f"{subject.ticker}: {size_flags[normalise_symbol(subject.ticker)]} "
                             f"- the size bands below are built on it as it stands")

    subject_financial = is_financial(subject)
    if not subject.gics_subindustry:
        group.reasons.append("subject has no GICS sub-industry; matched on its EODHD industry "
                             "label only, and only against other EODHD industry labels")
    elif not _eodhd_label(subject):
        group.reasons.append("subject has no EODHD industry label; matched on GICS only")

    # PEER-DEDUP-1 - a company is never its own peer, and "its own" means every LINE of it,
    # not just the identical ticker string. TSM.US (an ADR that names 2330.TW as home) stood in
    # TSMC's own cohort because only the ticker was compared.
    company_of = {}
    for number, members in enumerate(company_groups(universe, link_by_name=True)):
        for member in members:
            company_of[normalise_symbol(member.ticker)] = number
    subject_company = company_of.get(normalise_symbol(subject.ticker))

    # Everything that is eligible to be a peer at all, before any rung.
    candidates, no_cap, no_usd, funds, suspect, own_lines = [], 0, 0, 0, 0, 0
    receipts: dict[str, int] = {}
    size_suspect = 0
    for row in universe:
        if normalise_symbol(row.ticker) == normalise_symbol(subject.ticker):
            continue                                   # never its own peer
        if company_of.get(normalise_symbol(row.ticker)) == subject_company:
            own_lines += 1                             # ...nor another line of itself
            if normalise_symbol(row.ticker) in alias_applied:
                alias_used.setdefault(normalise_symbol(row.ticker),
                                      "another line of the subject's company")
            continue
        kind = secondary.get(normalise_symbol(row.ticker))
        if kind:
            receipts[kind] = receipts.get(kind, 0) + 1   # kept in the table, never a peer
            continue
        if is_fund(row):
            funds += 1
            continue
        if suspect_reason(row):
            suspect += 1
            continue
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
        if normalise_symbol(row.ticker) in size_flags:
            size_suspect += 1                          # kept in the table, never a peer
            continue
        candidates.append(row)

    # ONE ROW PER COMPANY. AMD.US, AMD.TO and AMD.XETRA were three peers in the 9,018-row
    # build; they are one company.
    pool, cross_listings, absorbed = company_pool(candidates, link_by_name=True)

    def report_aliases(members) -> None:
        """PEER-ADR-ALIAS-1 - say which aliases THIS group used, and only those. An alias that
        collapsed a duplicate elsewhere in the table but whose home line is not in this cohort
        played no part in it and is not mentioned."""
        member_keys = {normalise_symbol(m.ticker) for m in members}
        used = dict(alias_used)
        for key, alias in alias_applied.items():
            if key in used:
                continue
            if absorbed.get(key) in member_keys:
                used[key] = f"duplicate collapsed into {absorbed[key]}"
            elif key in member_keys:
                used[key] = "member"
        for key in sorted(used):
            alias = alias_applied[key]
            group.aliased.append({"ticker": alias.ticker, "primary": alias.primary,
                                  "role": used[key], "date": alias.date,
                                  "reason": alias.reason})
            group.reasons.append(_alias_line(alias))

    if own_lines:
        group.reasons.append(f"{own_lines} other line(s) of {subject.name or subject.ticker} "
                             f"left out: a company is never its own peer")
    if receipts:
        group.reasons.append(
            f"{sum(receipts.values())} candidate(s) skipped: secondary trading line, not a "
            f"company (" + ", ".join(f"{kind} {n}" for kind, n in sorted(receipts.items()))
            + ")")
    if size_suspect:
        group.reasons.append(f"{size_suspect} candidate(s) skipped: {SIZE_SUSPECT} (a market "
                             f"cap more than {size_factor:g}x from the same company's other "
                             f"listings)")
    if funds:
        group.reasons.append(f"{funds} candidate(s) skipped: {FUND_NOT_A_COMPANY}")
    if suspect:
        group.reasons.append(f"{suspect} candidate(s) skipped: {CLASSIFICATION_SUSPECT} "
                             f"(the label contradicts the name)")
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
    tried: list = []
    for step, (rung, level, low, high) in enumerate(ladder, start=1):
        systems = _subject_systems(subject, level)
        if not systems:
            continue
        matched, how = [], {}
        for r in pool:
            if not _within(r.market_cap_usd, subject_cap, low, high):
                continue
            hits = _label_hits(r, subject, level, systems)
            if hits:
                matched.append(r)
                how[r.ticker] = "+".join(hits)
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
            group.step = step
            group.matched_on = {r.ticker: how[r.ticker] for r in group.members}
            tally = {}
            for how_matched in group.matched_on.values():
                tally[how_matched] = tally.get(how_matched, 0) + 1
            if len(systems) > 1:
                group.reasons.append(
                    f"matched on: GICS only {tally.get(LABEL_GICS, 0)}, EODHD label only "
                    f"{tally.get(LABEL_EODHD, 0)}, both {tally.get(f'{LABEL_GICS}+{LABEL_EODHD}', 0)}")
            group.distinct_companies = len({company_of.get(normalise_symbol(r.ticker), r.ticker)
                                            for r in group.members})
            report_aliases(group.members)
            for member in group.members:
                if normalise_symbol(member.ticker) in applied:
                    was, override = applied[normalise_symbol(member.ticker)]
                    group.overridden.append({
                        "ticker": member.ticker, "role": "member", "was": was,
                        "now": override.gics_subindustry, "date": override.date,
                        "reason": override.reason})
                    group.reasons.append(_override_line(member.ticker, was, override))
            return group
        tried.append((rung, len(matched)))
        group.reasons.append(f"{rung}: only {len(matched)} comparable companies found")

    report_aliases([])
    # ABS-READINGS-2 — the count at the WIDEST rung actually tried, and its name.
    # This line used to report len(pool), the size of the whole eligible pool: for NFLX it
    # said "only 6451 comparable companies found" directly under three rungs that had
    # correctly reported 3, 5 and 9. The number a reader needs is how close the LAST and
    # most generous attempt came.
    if tried:
        widest_rung, widest_count = tried[-1]
        group.reasons.append(
            f"no peer group: the widest rung tried ({widest_rung}) found only "
            f"{widest_count} comparable companies for "
            f"{subject.classification or 'this classification'}")
    else:
        group.reasons.append(
            f"no peer group: no rung could be tried for "
            f"{subject.classification or 'this classification'}")
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
        "step": group.step,
        "distinct_companies": group.distinct_companies,
        "label_overrides": list(group.overridden),
        "identity_aliases": list(group.aliased),
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
    p_peers.add_argument("--size-factor", type=float, default=None,
                         help="a row whose USD cap is more than this many times away from the "
                              "company's other listings is size suspect (default: the config's "
                              f"size_suspect_factor, {DEFAULT_SIZE_FACTOR:g})")
    p_peers.set_defaults(func=_cmd_peers)

    p_gbx = sub.add_parser(
        "fix-gbx-scale",
        help="repair London (GBX) USD market caps that were scaled as pence, in place, from the "
             "stored figures (no network, no build; backs the index up first)")
    p_gbx.add_argument("--dry-run", action="store_true",
                       help="count what would change and write nothing")
    p_gbx.add_argument("--even-if-build-running", action="store_true",
                       help="write even though a build log was touched in the last 10 minutes")
    p_gbx.set_defaults(func=_cmd_fix_gbx_scale)

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
                    budget=args.budget, venues=config.get("venues"),
                    empty_retry_after=config["empty_retry_after"],
                    empty_retry_days=config["empty_retry_days"], progress=_say)
    for line in outcome.venue_lines():
        _say("  " + line if not line.endswith(":") else line)
    _say(outcome.summary())
    if outcome.skipped_exchanges:
        # Said again, on its own line: the summary is one line at the end of a log that can
        # run to thousands, and a market silently missing from the table is the kind of
        # thing that gets noticed months later.
        _say(f"NOTE: {outcome.skipped_sentence()}")
    if outcome.network_skipped:
        # Only the ones that were a blip: a 404 will 404 again, so it is not worth a command.
        _say("Run the network-skipped exchanges again with:")
        _say(f"  python -m aristos_council.market_index build "
             f"--exchanges {','.join(outcome.network_skipped)} --budget {args.budget}")
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
    factor = args.size_factor
    if factor is None:
        factor = load_config(args.config).get("size_suspect_factor", DEFAULT_SIZE_FACTOR)
    group = peers(args.ticker, floor=args.floor, cap=args.cap, store=_store_for(args),
                  size_factor=factor)
    if not group.available:
        _say(f"No peer group for {args.ticker}.")
        for reason in group.reasons:
            _say(f"  - {reason}")
        return 1
    _say(group.sentence())
    _say(f"{'ticker':16s} {'name':28s} {'exch':6s} {'local cap':>20s} "
         f"{'USD cap':>16s}  {'matched':10s} sub-industry")
    for row in group.members:
        local = ("-" if row.market_cap is None
                 else f"{row.market_cap:,.0f} {row.currency}")
        usd = "-" if row.market_cap_usd is None else f"{row.market_cap_usd:,.0f}"
        _say(f"{row.ticker:16s} {row.name[:28]:28s} {row.exchange:6s} {local:>20s} "
             f"{usd:>16s}  {group.matched_on.get(row.ticker, ''):10s} {row.classification}")
    for reason in group.reasons:
        _say(f"  note: {reason}")
    return 0


def _cmd_fix_gbx_scale(args) -> int:
    try:
        report = fix_gbx_scale(_store_for(args), dry_run=args.dry_run,
                               even_if_build_running=args.even_if_build_running)
    except MarketIndexError as exc:
        _say(str(exc))
        return 1
    for line in report.lines():
        _say(line)
    return 0


def _cmd_status(args) -> int:
    # The same backoff the build will use, so "due" is what the next build will actually spend.
    config = load_config(args.config)
    for line in status(_store_for(args),
                       empty_retry_after=config["empty_retry_after"],
                       empty_retry_days=config["empty_retry_days"],
                       size_factor=config["size_suspect_factor"]).lines():
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
