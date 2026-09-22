"""GAP-LEDGER-1 step 1 — who is even eligible to gap, and the pre-filter that thins them.

Two halves:

* **The candidate pool** comes from the local market index, READ-ONLY. The index's
  location is read from ``market_index.yaml`` (``root:``) rather than assumed, so a
  relocated table is still found and this screener never writes to it. US rows only, and
  only the venues the brief names.
* **The pre-filter** drops names on yesterday's daily bars: price, average volume,
  history length. Cheap, cached, and every drop carries a named reason — an excluded name
  the owner cannot account for is indistinguishable from a bug.

Market cap is NOT read. The brief is explicit and the reason is structural: a gap is a
statement about a price and a crowd, not about size, and the index's cap column is
populated best-effort — gating on it would silently drop every name whose cap the
provider happened not to serve.

ETFs are excluded by ASSET KIND, and the exclusion happens at the index's own build: the
index admits ``Type == "Common Stock"`` only (``market_index.COMMON_STOCK``), so a fund
is not in the table to begin with. ``common_stock_rows`` re-states that boundary rather
than trusting it silently — a row whose classification says fund is dropped here too,
and ``--tickers`` input (which bypasses the index entirely) is screened the same way.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from statistics import fmean
from typing import Iterable, Optional, Sequence

from ..data.adapter import PriceBar
from .. import market_index
from .config import DEFAULT_CONFIG, US_VENUES, GapConfig

_log = logging.getLogger(__name__)

# The market the brief screens. One string, so "US rows only" is a fact with one spelling.
US_MARKET = "US"

# Fund words that must never reach a single-stock screen. The index should not contain
# these at all (it filters on ``Type == "Common Stock"`` at build time); this is the
# second line of defence, and the FIRST line for a ``--tickers`` file, which has no index
# row behind it at all.
_FUND_WORDS = ("etf", "fund", "trust - ", "index tracker")

# GAP-UNIVERSE-1 — ticker SHAPES that are not common stock, whatever the index says.
#
# The market index classifies on EODHD's ``Type`` field, and EODHD calls all of these
# "Common Stock": warrants, units, rights, when-issued lines and preferreds. They are not.
# They have no pre-market tape worth screening, yfinance has no data for most of them, and
# each one used to cost a line of provider error noise on every run. 196 of 5,972 US rows
# on the 2026-09-22 index.
#
# The index is NOT changed — Aristos reads the same table, and reclassifying rows under it
# is a separate decision with its own blast radius (see docs/GAP_LEDGER.md). This is a
# Gap-Ledger-side filter on the TICKER, which is where the security type is legible.
#
# Derived from the actual suffix census of the index, not from memory. The danger in a
# filter like this is the false positive, so note what is deliberately NOT here: a bare
# trailing ``-A``/``-B``/``-C``/``-V``/``-H``/``-I`` is a genuine SHARE CLASS of common
# stock (BF-B, AKO-A, AKO-B, CIG-C, MKC-V) and must survive. Only the preferred marker
# ``P``/``PR`` before the class letter makes it a preferred (BA-P-A, PNFP-PR-A, FITB-PA).
_NOT_COMMON_STOCK: tuple[tuple[str, str], ...] = (
    # warrants: ACHR-WS, ACHR-WT, FDXF-W, NE-WTA, MBGL-WI (when-issued)
    (r"-W(S|T|I)?[A-Z]?$", "warrant"),
    # units: AIIA-U, BEBE-UN
    (r"-UN?$", "unit"),
    # preferreds, hyphenated and not: DCOM-P, BA-P-A, PNFP-PR-A, FITB-PA, AHL-PF.
    # BEFORE the rights pattern: a preferred series R (C-P-R, Citigroup's) ends in "-R"
    # and would otherwise be labelled a right. It is dropped either way, but the report
    # prints the breakdown by kind, so a wrong label is a wrong number on the page.
    (r"-P(R)?(-?[A-Z])?$", "preferred"),
    # rights: AIIA-R, MPTI-R-W, CELG-RI
    (r"-R(I|-W)?$", "right"),
)

# The NASDAQ five-letter form of a preferred: TFINP is TFIN-P, and the index carries both.
# Kept SEPARATE from the patterns above because it is the one rule that can bite a real
# company — "CHEAP" is five letters ending in P — so it is not applied on shape alone. It
# fires only when the four-letter ROOT is itself in the same pool, which is what makes
# TFINP a preferred of TFIN rather than a word. With no pool to corroborate against the
# rule does not fire at all: screening a preferred costs one wasted lookup, and dropping a
# real company costs a name nobody ever sees again.
_FIVE_LETTER_PREFERRED = re.compile(r"^([A-Z]{4})P$")

NOT_COMMON_STOCK = tuple((re.compile(pattern), label)
                         for pattern, label in _NOT_COMMON_STOCK)


# The one spelling of "the provider served nothing for this name". A constant because
# ``run`` counts it for the summary line, and counting by matching prose is how a message
# edit silently turns a count into a zero.
NO_DAILY_BARS = "no daily bars from the provider"


class UniverseUnavailable(RuntimeError):
    """The pool could not be established. Carries what the owner should do about it."""


# --------------------------------------------------------------------------- #
# the pool
# --------------------------------------------------------------------------- #
def index_root(config_path: str | Path = market_index.DEFAULT_CONFIG) -> Path:
    """Where the market index lives, ACCORDING TO ITS CONFIG — never assumed.

    ``market_index.load_config`` already treats a missing file as "use the defaults" and a
    malformed one as an error; this keeps that contract and only reaches for ``root``.
    """
    return Path(market_index.load_config(config_path)["root"])


def not_common_stock_reason(ticker: str, *, known_roots: Optional[set] = None) -> str:
    """``"warrant"`` / ``"unit"`` / ``"right"`` / ``"preferred"``, or ``""`` for a real stock.

    GAP-UNIVERSE-1. Matched on the TICKER because that is where the US market spells the
    security type; the index's own ``Type`` field calls all of these "Common Stock".

    ``known_roots`` is the rest of the pool, and it gates the five-letter preferred rule
    ONLY (see ``_FIVE_LETTER_PREFERRED``): TFINP is a preferred because TFIN is listed
    beside it, where a five-letter word ending in P is just a ticker. Omit it and that one
    rule stands down.
    """
    clean = (ticker or "").strip().upper()
    for pattern, label in NOT_COMMON_STOCK:
        if pattern.search(clean):
            return label
    match = _FIVE_LETTER_PREFERRED.match(clean)
    if match and known_roots and match.group(1) in known_roots:
        return "preferred"
    return ""


def split_common_stock(tickers) -> tuple[list[str], list[tuple[str, str]]]:
    """``(kept, [(ticker, reason), ...])``. Order is preserved so a run stays reproducible.

    Applied to EVERY pool, the index's and a ``--tickers`` file's alike. A hand-written
    list naming a warrant gets told so rather than silently screened — the drop is counted
    in the run summary, which is the opposite of silent.
    """
    roots = {(t or "").strip().upper() for t in tickers}
    kept: list[str] = []
    dropped: list[tuple[str, str]] = []
    for ticker in tickers:
        reason = not_common_stock_reason(ticker, known_roots=roots)
        if reason:
            dropped.append((ticker, reason))
        else:
            kept.append(ticker)
    return kept, dropped


def looks_like_fund(name: str, classification: str = "") -> bool:
    """Does this row's own text say it is a fund rather than an operating company?"""
    haystack = f"{name} {classification}".lower()
    return any(word in haystack for word in _FUND_WORDS)


def common_stock_rows(rows: Iterable[market_index.IndexRow], *,
                      venues: Sequence[str] = US_VENUES) -> list[market_index.IndexRow]:
    """The US common-stock rows on the admitted venues, one per ticker.

    The venue check uses the index's own ``venue_allowed`` helper so this screener and the
    index agree on what "NYSE" means, and the row's ``market`` (the exchange CODE the
    build requested) is what identifies a US listing — MARKET-INDEX-2 split that from the
    venue precisely so an OTC row could stop looking like a US row.
    """
    out: list[market_index.IndexRow] = []
    for row in rows:
        if (row.market or "").strip().upper() != US_MARKET:
            continue
        if not market_index.venue_allowed(row.exchange, list(venues)):
            continue
        if looks_like_fund(row.name, row.classification):
            continue
        if not (row.yahoo_ticker or row.ticker):
            continue
        out.append(row)
    return out


def pool_from_index(*, config_path: str | Path = market_index.DEFAULT_CONFIG,
                    venues: Sequence[str] = US_VENUES) -> list[str]:
    """Every screenable US common stock, as the tickers yfinance resolves.

    Raises ``UniverseUnavailable`` when there is no index — with the two ways forward the
    brief asks for (build it, or hand over a ``--tickers`` file) rather than a traceback.
    """
    root = index_root(config_path)
    store = market_index.IndexStore(root)
    if not store.path.exists():
        raise UniverseUnavailable(
            f"no market index at {store.path} — build it with "
            f"`python -m aristos_council.market_index build`, or pass "
            f"`--tickers <file>` (one ticker per line) to screen a list instead")
    rows = store.load()
    if not rows:
        raise UniverseUnavailable(
            f"the market index at {store.path} is empty — rebuild it with "
            f"`python -m aristos_council.market_index build`, or pass `--tickers <file>`")
    kept = common_stock_rows(rows, venues=venues)
    if not kept:
        raise UniverseUnavailable(
            f"the market index at {store.path} holds {len(rows)} rows but none is a US "
            f"common stock on {', '.join(venues)} — rebuild it, or pass `--tickers <file>`")
    return sorted({(row.yahoo_ticker or row.ticker).strip().upper() for row in kept})


def read_ticker_file(path: str | Path) -> list[str]:
    """One ticker per line. Blank lines and ``#`` comments are ignored.

    Comment-tolerant to match ``universe_editor.parse_ticker_lines``, which is what the
    Aristos side uses for hand-written lists.
    """
    path = Path(path)
    if not path.exists():
        raise UniverseUnavailable(f"no such ticker file: {path}")
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip().upper()
        if text and text not in out:
            out.append(text)
    if not out:
        raise UniverseUnavailable(f"{path} contains no tickers")
    return out


# --------------------------------------------------------------------------- #
# the pre-filter
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PreFilterRow:
    """What the pre-filter learned about one name. ``reason`` is empty when it passed."""

    ticker: str
    passed: bool
    reason: str = ""
    previous_close: Optional[float] = None
    previous_session: Optional[date] = None
    average_volume: Optional[float] = None
    history_days: Optional[int] = None


@dataclass
class PreFilterResult:
    """The pool, split. ``excluded`` is ordered as the pool was, so a run is reproducible."""

    passed: list[PreFilterRow] = field(default_factory=list)
    excluded: list[PreFilterRow] = field(default_factory=list)

    @property
    def tickers(self) -> list[str]:
        return [row.ticker for row in self.passed]

    def by_ticker(self) -> dict[str, PreFilterRow]:
        return {row.ticker: row for row in self.passed}


def completed_sessions(bars: Sequence[PriceBar], *, as_of: date) -> list[PriceBar]:
    """The bars for sessions that have CLOSED, oldest first.

    ``as_of`` is today on the market's clock, and today's bar — which yfinance serves
    during the session, part-formed — is excluded. Yesterday's close must be yesterday's,
    not a half-finished today.
    """
    return sorted((b for b in bars if b.day < as_of), key=lambda b: b.day)


def pre_filter_one(ticker: str, bars: Sequence[PriceBar], *, as_of: date,
                   config: GapConfig = DEFAULT_CONFIG) -> PreFilterRow:
    """The three brief thresholds on one name, in the order that names the cheapest fault.

    Previous close is the DIVIDEND- AND SPLIT-ADJUSTED close (``adj_close``). That is the
    brief's wording and it is the only reading that does not invent a gap: with
    ``auto_adjust=False`` yfinance serves ``Close`` split-adjusted but NOT
    dividend-adjusted, so on an ex-dividend morning the raw close sits a dividend above
    where the stock actually reopens, and a 2% payer would read as a 2% gap down on no
    news at all.
    """
    closed = completed_sessions(bars, as_of=as_of)
    if not closed:
        return PreFilterRow(ticker, False, "no completed daily session before today")
    history = len(closed)
    if history < config.min_history_days:
        return PreFilterRow(ticker, False,
                            f"only {history} trading days of history "
                            f"(needs {config.min_history_days})",
                            history_days=history)
    last = closed[-1]
    if last.adj_close < config.min_price:
        return PreFilterRow(ticker, False,
                            f"previous close ${last.adj_close:,.2f} below "
                            f"${config.min_price:,.2f}",
                            previous_close=last.adj_close, previous_session=last.day,
                            history_days=history)
    window = closed[-config.avg_volume_days:]
    average = fmean(float(b.volume) for b in window)
    if average < config.min_avg_volume:
        return PreFilterRow(ticker, False,
                            f"{config.avg_volume_days}-session average volume "
                            f"{average:,.0f} below {config.min_avg_volume:,.0f}",
                            previous_close=last.adj_close, previous_session=last.day,
                            average_volume=average, history_days=history)
    return PreFilterRow(ticker, True, "", previous_close=last.adj_close,
                        previous_session=last.day, average_volume=average,
                        history_days=history)


def pre_filter(tickers: Sequence[str], bars_by_ticker: dict[str, Sequence[PriceBar]], *,
               as_of: date, config: GapConfig = DEFAULT_CONFIG) -> PreFilterResult:
    """Run the pre-filter over the pool. A name the provider served no bars for is
    EXCLUDED with that as its reason, never silently missing."""
    result = PreFilterResult()
    for ticker in tickers:
        bars = bars_by_ticker.get(ticker)
        row = (PreFilterRow(ticker, False, NO_DAILY_BARS)
               if not bars else pre_filter_one(ticker, bars, as_of=as_of, config=config))
        (result.passed if row.passed else result.excluded).append(row)
    return result
