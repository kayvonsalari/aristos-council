"""FIND-COMPANY-1 — search the LOCAL market index by company name or ticker, for the Company
Check tab's "Find a company" box.

Read-only and offline: no network call, no fetch, nothing written. Two things are read, each
ONCE per process and cached in memory thereafter (``search_companies`` re-reads neither on a
later call):

- the cleaned market index (``market_index.clean_pool`` — the SAME one-row-per-company,
  receipts/funds/suspects-out pool the peer engine itself builds from);
- every BUILT cohort's own frozen ``members.csv`` (never ``data/local/cohorts/watch.yaml``,
  which is personal and holds no bearing on what a company IS).

No holdings information appears anywhere here — this module has no notion of a holding at
all, only of companies, their listings and their cohort membership.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .exchange_names import exchange_name

_ROOT = Path(__file__).resolve().parents[2]
MAX_MATCHES = 15


@dataclass(frozen=True)
class CompanyMatch:
    """One search result. ``ticker`` is what fills the Company Check ticker box (EODHD form,
    e.g. ``"SIE.F"``) — the same form ``normalize_ticker``/``run_company_check`` already
    accept. ``cohorts`` is empty both when the company belongs to none of the 57 and when no
    cohort has been built at all; callers tell the two apart with ``cohorts_known`` on the
    search result set (``SearchResult`` below), not on the match itself."""

    ticker: str
    name: str
    exchange: str
    market: str
    country: str
    market_cap_usd: Optional[float]
    is_home: bool
    cohorts: tuple = ()

    @property
    def market_cap_display(self) -> str:
        from .tools.price_context import format_money
        return format_money(self.market_cap_usd, "USD", abbreviate=True)

    @property
    def where(self) -> str:
        """"Germany — XETRA" names the country in full; the exchange is appended only when it
        differs from the bare code the index stores for country (Taiwan's own exchange code
        IS "TW", so that pair reads just "Taiwan", never "Taiwan — TW" — FIND-COMPANY-2 item
        3). Just the exchange when the index carries no country at all."""
        if not self.country:
            return self.exchange or "—"
        name = _COUNTRY_NAMES.get(self.country, self.country)
        if not self.exchange or self.exchange == self.country:
            return name
        # EXCHANGE-NAMES-1: a readable exchange ("Copenhagen", not "CO"); dropped when it only
        # repeats the country ("Hong Kong — Hong Kong", "Taiwan — Taiwan").
        venue = exchange_name(self.exchange)
        if _fold(venue) == _fold(name):
            return name
        return f"{name} — {venue}"


@dataclass(frozen=True)
class SearchResult:
    matches: tuple = ()
    cohorts_known: bool = True     # False when NO cohort has been built at all (item 4's fallback)


def _fold(text: str) -> str:
    """Case- and accent-insensitive folding — "Nestlé" and "nestle" compare equal, "Ørsted"
    and "orsted" compare equal. Combining marks (the accents) are stripped after NFKD
    decomposition, then the rest is case-folded."""
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()


# FIND-COMPANY-2 — common short names that are neither the company's own name nor its ticker,
# so plain substring matching never finds them (e.g. "TSMC" appears in neither "Taiwan
# Semiconductor Manufacturing Co. Ltd." nor "2330.TW"). Each alias maps to a fragment that DOES
# appear in that company's name in the local index. Small and hand-picked, not a general
# initials-matcher — the two tried (BMW, ASML, LVMH) already match by ticker or name substring,
# so they are harmless rather than load-bearing here, but kept for when the index's naming
# drifts (e.g. a legal-suffix change).
_ALIASES: dict[str, str] = {
    "tsmc": "taiwan semiconductor",
    "vw": "volkswagen",
    "bmw": "bayerische motoren werke",
    "lvmh": "lvmh",
    "asml": "asml",
    "ge": "ge aerospace",
    "j&j": "johnson & johnson",
}

# The 18 exchange-code countries MARKET-INDEX-1 covers (docs/MARKET_INDEX.md). The index
# stores the bare ISO-ish code ("TW", "HK") as `country`, which is also often identical to
# `exchange` for a single-exchange market — showing the raw code twice ("TW — TW") is FIND-
# COMPANY-2 item 3. A code missing here (the index gains a 19th market) falls back to showing
# itself, never a blank.
_COUNTRY_NAMES: dict[str, str] = {
    "AU": "Australia", "BR": "Brazil", "CA": "Canada", "CH": "Switzerland",
    "DE": "Germany", "DK": "Denmark", "ES": "Spain", "FI": "Finland",
    "FR": "France", "GB": "United Kingdom", "HK": "Hong Kong", "KR": "South Korea",
    "NA": "Namibia", "NL": "Netherlands", "NO": "Norway", "SE": "Sweden",
    "TW": "Taiwan", "US": "United States",
}


def _ticker_base(ticker: str) -> str:
    """"2330.TW" -> "2330" — the exchange suffix stripped, so a ticker search ranks a company
    by its OWN identity rather than by which exchange happened to list it."""
    return ticker.rpartition(".")[0] or ticker


def _name_start_rank(name_folded: str, needle: str) -> Optional[int]:
    """SEARCH-RANK-1 — 0 when the NAME starts with the query, 1 when a later WORD of it does
    ("Boyd" for "BYD" does not: only a real prefix counts), else None. Word starts split on
    anything that is not a letter or digit, so "AT&T" and "Rolls-Royce" have words."""
    if name_folded.startswith(needle):
        return 0
    words = re.split(r"[^0-9a-z]+", name_folded)
    return 1 if any(w.startswith(needle) for w in words[1:] if w) else None


def _ticker_rank(row, needle: str, alias_needle: Optional[str] = None) -> int:
    """0 = exact ticker match ignoring the exchange suffix, OR a curated alias match (hand-
    picked for exactly one company, so it is just as confident as an exact ticker — without
    this, "VW" ranked a dozen tickers that merely CONTAIN the letters "vw" — VWS.CO (Vestas),
    CVW.AU — ahead of the aliased Volkswagen itself, which sits at a different ticker, VOW.DE,
    entirely); 1 = ticker starts with the query; 2 = ticker contains the query; 3 = matched by
    plain name only. Checked against both the EODHD and Yahoo ticker forms, since the two
    disagree on suffix style (``AAPL.US`` vs ``AAPL``)."""
    name_f = _fold(row.name)
    if alias_needle and alias_needle in name_f:
        return 0
    # SEARCH-RANK-1 (live: "byd" listed Boyd Gaming, whose TICKER is BYD, first and BYD Company
    # Limited, whose NAME starts with BYD, fourth): a name that starts with the query is at least
    # as good a match as a ticker that equals it, and a name with a later word that starts with it
    # is as good as a ticker that starts with it. Ties inside a tier still go to market cap.
    by_name = _name_start_rank(name_f, needle)
    if by_name == 0:
        return 0
    ticker_f, yahoo_f = _fold(row.ticker), _fold(row.yahoo_ticker)
    base_f, ybase_f = _fold(_ticker_base(row.ticker)), _fold(_ticker_base(row.yahoo_ticker))
    if needle == base_f or needle == ybase_f:
        return 0
    if ticker_f.startswith(needle) or yahoo_f.startswith(needle) or by_name == 1:
        return 1
    if needle in ticker_f or needle in yahoo_f:
        return 2
    return 3


# --------------------------------------------------------------------------- #
# the two caches — each filled once, keyed by the root that produced it
# --------------------------------------------------------------------------- #
_POOL_CACHE: dict = {}
_COHORT_CACHE: dict = {}


def _clean_pool(root=None, rows=None):
    from .market_index import IndexStore, clean_pool, load_config

    if rows is not None:
        return clean_pool(rows=rows)          # test injection — never cached, always fresh
    key = str(root) if root else "__default__"
    if key not in _POOL_CACHE:
        index_root = Path(root) if root else Path(load_config()["root"])
        _POOL_CACHE[key] = clean_pool(store=IndexStore(index_root))
    return _POOL_CACHE[key]


def _cohort_membership(cohorts_root=None) -> tuple[dict, bool]:
    """``({ticker: (cohort display name, ...)}, any_built)`` — read once, cached. Walks the
    57 definitions in ``data/cohort_definitions.yaml`` for their display names, and for each
    one that has been BUILT, reads that cohort's own frozen ``members.csv`` (never
    ``data/local/cohorts/watch.yaml``, which this function does not know exists)."""
    from .cohorts.builder import DEFAULT_ROOT
    from .cohorts.definitions import load_definitions
    from .cohorts.freeze import MEMBERS_FILE, current_version, read_members, version_dir

    root = Path(cohorts_root) if cohorts_root else DEFAULT_ROOT
    key = str(root)
    if key in _COHORT_CACHE:
        return _COHORT_CACHE[key]
    try:
        defs = load_definitions(_ROOT / "data" / "cohort_definitions.yaml")
    except Exception:                                     # noqa: BLE001 — named, not fatal
        defs = []
    membership: dict[str, list[str]] = {}
    any_built = False
    for defn in defs:
        version = current_version(root, defn.slug)
        if version is None:
            continue
        any_built = True
        for cand in read_members(version_dir(root, defn.slug, version) / MEMBERS_FILE):
            names = membership.setdefault(cand.ticker, [])
            if defn.name not in names:                    # a company in two cohorts: both kept
                names.append(defn.name)
    result = (membership, any_built)
    _COHORT_CACHE[key] = result
    return result


def search_companies(query: str, *, limit: int = MAX_MATCHES, root=None,
                     cohorts_root=None, rows=None) -> SearchResult:
    """Up to ``limit`` matches for ``query`` against a company's full name, EODHD ticker,
    Yahoo ticker or a small hand-picked alias (``_ALIASES``) — case- and accent-insensitive.
    Ranked exact-ticker-ignoring-suffix first, then ticker-starts-with, then ticker-contains,
    then name/alias matches; ties within a tier break by market cap, largest first (FIND-
    COMPANY-2 item 2) — so the top result is always the one worth preselecting. Empty/
    whitespace-only query -> no matches. Every call after the first reuses the cached pool and
    cohort membership; neither is re-read. ``rows`` (tests only) injects a fixed set of
    ``IndexRow`` directly, bypassing the local index file and its cache."""
    from .market_index import is_home_listing

    needle = _fold(query.strip())
    if not needle:
        return SearchResult(matches=(), cohorts_known=True)
    pool = _clean_pool(root, rows=rows)
    membership, any_built = _cohort_membership(cohorts_root)
    alias_needle = _ALIASES.get(needle)
    hits = [row for row in pool.rows
           if needle in _fold(row.name) or needle in _fold(row.ticker)
           or needle in _fold(row.yahoo_ticker)
           or (alias_needle and alias_needle in _fold(row.name))]
    hits.sort(key=lambda r: (_ticker_rank(r, needle, alias_needle),
                             -(r.market_cap_usd or 0.0), _fold(r.name)))
    matches = tuple(
        CompanyMatch(ticker=row.ticker, name=row.name, exchange=row.exchange,
                    market=row.market, country=row.country,
                    market_cap_usd=row.market_cap_usd, is_home=is_home_listing(row),
                    cohorts=tuple(membership.get(row.ticker, ())))
        for row in hits[:max(int(limit), 0)])
    return SearchResult(matches=matches, cohorts_known=any_built)
