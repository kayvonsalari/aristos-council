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
        row = (PreFilterRow(ticker, False, "no daily bars from the provider")
               if not bars else pre_filter_one(ticker, bars, as_of=as_of, config=config))
        (result.passed if row.passed else result.excluded).append(row)
    return result
