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

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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
        """"Germany — XETRA", or just the exchange when the index carries no country."""
        return f"{self.country} — {self.exchange}" if self.country else (self.exchange or "—")


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
    """Up to ``limit`` matches for ``query`` against a company's full name, EODHD ticker or
    Yahoo ticker — case- and accent-insensitive substring matching, home listings first, then
    by name. Empty/whitespace-only query -> no matches. Every call after the first reuses the
    cached pool and cohort membership; neither is re-read. ``rows`` (tests only) injects a
    fixed set of ``IndexRow`` directly, bypassing the local index file and its cache."""
    from .market_index import is_home_listing

    needle = _fold(query.strip())
    if not needle:
        return SearchResult(matches=(), cohorts_known=True)
    pool = _clean_pool(root, rows=rows)
    membership, any_built = _cohort_membership(cohorts_root)
    hits = [row for row in pool.rows
           if needle in _fold(row.name) or needle in _fold(row.ticker)
           or needle in _fold(row.yahoo_ticker)]
    hits.sort(key=lambda r: (not is_home_listing(r), _fold(r.name)))
    matches = tuple(
        CompanyMatch(ticker=row.ticker, name=row.name, exchange=row.exchange,
                    market=row.market, country=row.country,
                    market_cap_usd=row.market_cap_usd, is_home=is_home_listing(row),
                    cohorts=tuple(membership.get(row.ticker, ())))
        for row in hits[:max(int(limit), 0)])
    return SearchResult(matches=matches, cohorts_known=any_built)
