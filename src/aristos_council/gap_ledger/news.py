"""GAP-LEDGER-1 step 3a — the headlines behind a gap, from EODHD News.

Read-only, one call per candidate, over the last ``news_lookback_hours`` (18 by default).
Only candidates: the screen has already thinned thousands of names to a handful, and news
is the one charged call in the run.

Separate from ``data/eodhd_adapter.py`` on purpose, exactly as ``cohorts.source`` and
``market_index.EODHDIndexSource`` are: that adapter serves the ranker's per-name
price/dividend/fundamentals contract, and headlines are not part of the
``MarketDataAdapter`` interface every lens depends on.

Two honesty rules:

* **"no news found" is a MARK, never a drop.** A stock up 9% on 6x volume with no
  headline in the feed is a genuine and interesting reading — it is the case where the
  reason is not public yet — and dropping it would hide exactly the names worth looking at.
* **The publisher is DERIVED, and says so.** EODHD's news rows carry ``date``, ``title``,
  ``link``, ``content``, ``symbols`` and ``tags``; there is no publisher field. Rather
  than invent one, ``source`` is the link's host (``reuters.com``), which is a fact about
  the link and nothing more.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol, Sequence

from .config import DEFAULT_CONFIG, NY, GapConfig

_log = logging.getLogger(__name__)

BASE_URL = "https://eodhd.com/api"
NEWS_PATH = "news"
# Enough to see whether a morning is one headline or a storm, small enough to stay cheap.
DEFAULT_LIMIT = 20


class NewsUnavailable(RuntimeError):
    """News could not be fetched at all (no key, provider refused). Never a per-name state:
    a name the provider simply has nothing for is "no news found", which is not an error."""


@dataclass(frozen=True)
class Headline:
    """One story. ``published_at`` is timezone-aware; the record renders it in ET."""

    title: str
    link: str
    published_at: Optional[datetime] = None
    source: str = ""

    @property
    def published_ny(self) -> str:
        if self.published_at is None:
            return ""
        return self.published_at.astimezone(NY).isoformat(timespec="minutes")


class NewsSource(Protocol):
    def headlines(self, ticker: str, *, since: datetime,
                  until: datetime) -> list[Headline]:
        """Stories for one ticker in [since, until], newest first. Empty is a valid answer."""


# --------------------------------------------------------------------------- #
# helpers — pure, so the parsing is testable without a network
# --------------------------------------------------------------------------- #
def eodhd_symbol(ticker: str) -> str:
    """``AAPL`` -> ``AAPL.US``. A ticker that already carries an exchange is left alone."""
    clean = (ticker or "").strip().upper()
    return clean if "." in clean else f"{clean}.US"


def publisher_from_link(link: str) -> str:
    """The host, minus a leading ``www.``. Derived from the link, never claimed as a field."""
    try:
        host = urllib.parse.urlparse(link or "").netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def parse_timestamp(raw: object) -> Optional[datetime]:
    """EODHD stamps are ISO 8601 with an offset. An unparseable one becomes None rather
    than a guess — a headline with an unknown time is still a headline."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def headlines_from_rows(rows: object, *, since: datetime,
                        until: datetime) -> list[Headline]:
    """The provider's payload, filtered to the window and sorted newest first.

    A row whose timestamp could not be parsed is KEPT: the provider returned it for a
    windowed query, so dropping it for a formatting fault of ours would lose a real story.
    Rows with a timestamp outside the window are dropped — the query is windowed, but a
    provider that widens it should not widen our record.
    """
    if not isinstance(rows, list):
        return []
    out: list[Headline] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        link = str(row.get("link") or "").strip()
        if not title:
            continue
        when = parse_timestamp(row.get("date"))
        if when is not None and not (since <= when <= until):
            continue
        out.append(Headline(title=title, link=link, published_at=when,
                            source=publisher_from_link(link)))
    out.sort(key=lambda h: h.published_at or datetime.min.replace(tzinfo=timezone.utc),
             reverse=True)
    return out


def news_window(run_at: datetime, config: GapConfig = DEFAULT_CONFIG) -> tuple[datetime,
                                                                              datetime]:
    """``(since, until)`` — the lookback ending at the run time."""
    return run_at - timedelta(hours=config.news_lookback_hours), run_at


# --------------------------------------------------------------------------- #
# the provider
# --------------------------------------------------------------------------- #
class EODHDNews:
    """EODHD ``/news``, one windowed call per ticker.

    ``urllib`` rather than ``requests``, matching every other outbound call in this repo
    (``cohorts.source``, ``market_index``) — nothing here adds a dependency.
    """

    name = "eodhd"

    def __init__(self, api_key: Optional[str] = None, *, timeout: float = 15.0,
                 limit: int = DEFAULT_LIMIT) -> None:
        self._key = api_key
        self.timeout = timeout
        self.limit = limit

    def _require_key(self) -> str:
        key = self._key or os.environ.get("EODHD_API_KEY", "")
        if not key.strip():
            raise NewsUnavailable(
                "EODHD_API_KEY is not set — news needs it. Run with `--no-news` to "
                "screen without headlines (every name is then marked 'no news found').")
        return key.strip()

    def headlines(self, ticker: str, *, since: datetime,
                  until: datetime) -> list[Headline]:
        params = urllib.parse.urlencode({
            "s": eodhd_symbol(ticker),
            "from": since.astimezone(timezone.utc).date().isoformat(),
            "to": until.astimezone(timezone.utc).date().isoformat(),
            "limit": self.limit,
            "offset": 0,
            "api_token": self._require_key(),
            "fmt": "json",
        })
        url = f"{BASE_URL}/{NEWS_PATH}?{params}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # A per-name refusal is a per-name absence, not a dead run: the screen has
            # already earned this name its place, and "no news found" is a legal mark.
            _log.warning("gap_ledger: news HTTP %s for %s", exc.code, ticker)
            return []
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            _log.warning("gap_ledger: news failed for %s: %s", ticker, exc)
            return []
        return headlines_from_rows(payload, since=since, until=until)


# --------------------------------------------------------------------------- #
# the step
# --------------------------------------------------------------------------- #
def gather_news(tickers: Sequence[str], *, source: Optional[NewsSource], run_at: datetime,
                config: GapConfig = DEFAULT_CONFIG) -> dict[str, list[Headline]]:
    """Headlines per candidate. ``source=None`` means news was switched off, and every
    name comes back with an empty list — the same shape "the provider had nothing" takes,
    because the record distinguishes them by the run's own ``news`` flag rather than by
    guessing from an empty list."""
    if source is None:
        return {ticker: [] for ticker in tickers}
    since, until = news_window(run_at, config)
    return {ticker: source.headlines(ticker, since=since, until=until)
            for ticker in tickers}
