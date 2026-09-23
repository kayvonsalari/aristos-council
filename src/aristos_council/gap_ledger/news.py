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
import re
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
    # GAP-NEWS-MATCH-1 — every symbol the provider tagged, first one first. EODHD tags an
    # article with every ticker it thinks is involved, which is why one AXT Inc. story
    # arrived as "news" for AXTI, CPRI and DK at once.
    symbols: tuple[str, ...] = ()

    @property
    def published_ny(self) -> str:
        if self.published_at is None:
            return ""
        return self.published_at.astimezone(NY).isoformat(timespec="minutes")

    @property
    def primary_symbol(self) -> str:
        """The symbol the provider put first — its notion of what the story is ABOUT."""
        return base_symbol(self.symbols[0]) if self.symbols else ""


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


def base_symbol(symbol: str) -> str:
    """``AAPL.US`` -> ``AAPL``. The exchange suffix is not part of the identity here."""
    return (symbol or "").strip().upper().split(".", 1)[0]


# --------------------------------------------------------------------------- #
# GAP-NEWS-MATCH-1 — does this story belong to this name?
# --------------------------------------------------------------------------- #
# Live, 2026-09-22: one AXT Inc. headline was attached to AXTI, CPRI and DK; ONON got a
# Quest/Labcorp article; JAZZ got an Iambic story; BMRN got Travere; VCYT got DGX; and ALNY,
# up 28%, showed no news at all. The cause is that EODHD's ``symbols`` is a LOOSE tag list,
# and a windowed query on one ticker returns anything tagged with it.
#
# So a story counts as this name's news only on positive evidence: the provider itself says
# the story is primarily about this symbol, or the name is legible in the headline. Anything
# else is kept in the record as RELATED and never printed as the name's news — a wrong
# reason beside a real gap is worse than no reason, because it gets believed.
MATCH_PRIMARY = "primary symbol"
MATCH_TICKER = "ticker in headline"
MATCH_NAME = "company name in headline"
RELATED = "related, not matched"

# A one- or two-letter ticker (T, F, KO) matches almost any sentence, so the
# ticker-in-headline test needs a floor. Those names are not left out: they still match on
# the provider's primary symbol and on their company name, which is the stronger signal
# anyway ("Ford recalls..." for F).
MIN_TICKER_IN_HEADLINE = 3

# Legal furniture that is never what a headline calls a company. Stripped from the index
# name before looking for it, so "AXT Inc." is sought as "axt" and Deutsche Bank AG as
# "deutsche bank".
_NAME_NOISE = (
    "incorporated", "inc", "corporation", "corp", "company", "co", "limited", "ltd",
    "plc", "holdings", "holding", "group", "the", "class", "common", "stock", "shares",
    "ag", "nv", "sa", "se", "ab", "as", "oyj", "spa", "p l c", "l p", "lp", "llc",
    "trust", "reit", "n v", "s a",
)
# A core shorter than this is too generic to look for in prose.
MIN_NAME_CORE = 3

# A headline calls a company by its SHORT name: "Alnylam reports...", "Ford recalls...",
# never "Alnylam Pharmaceuticals, Inc. reports...". So the full core is not enough to match
# on — the leading words are tried too (see ``name_forms``).
#
# A one-word form is where a false positive would come from, so it needs two guards: length,
# and this list of leading words that are ordinary English before they are company names. A
# name whose first word is here still matches on its two-word form ("capital one" for Capital
# One), which is the point — the guard narrows the test, it does not switch it off.
_GENERIC_FIRST_WORDS = frozenset({
    "american", "general", "national", "international", "united", "first", "global",
    "new", "world", "pacific", "atlantic", "northern", "southern", "eastern", "western",
    "central", "standard", "premier", "capital", "federal", "allied", "great", "prime",
    "core", "next", "open", "main", "summit", "union", "public", "peoples", "community",
    "liberty", "independence", "enterprise", "advance", "advanced", "select", "value",
    "quality", "service", "services", "industries", "international", "business",
})
# A single-word form shorter than this is not distinctive enough to risk.
MIN_SINGLE_WORD_FORM = 4


def name_core(name: str) -> str:
    """The part of a company name a headline would actually use, lowercased.

    ``"AXT Inc."`` -> ``"axt"``; ``"Alnylam Pharmaceuticals, Inc."`` -> ``"alnylam
    pharmaceuticals"``. Returns ``""`` when nothing usable is left, which switches the
    company-name test off rather than matching on a fragment.
    """
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    words = [w for w in cleaned.split() if w and w not in _NAME_NOISE]
    core = " ".join(words).strip()
    return core if len(core) >= MIN_NAME_CORE else ""


def name_forms(name: str) -> tuple[str, ...]:
    """The spellings of this company a headline might use, longest first.

    ``"Alnylam Pharmaceuticals, Inc."`` -> ``("alnylam pharmaceuticals", "alnylam")``;
    ``"Ford Motor Company"`` -> ``("ford motor", "ford")``. This is what the live run needed
    and the full core alone did not give: "Alnylam reports positive Phase 3 data" contains
    neither "Alnylam Pharmaceuticals" nor the ticker, and it is unmistakably an ALNY story.

    The one-word form is dropped when it is short or ordinary (``_GENERIC_FIRST_WORDS``), so
    "Capital One" is sought as "capital one" and never as "capital".
    """
    core = name_core(name)
    if not core:
        return ()
    words = core.split()
    forms = [core]
    for count in range(len(words) - 1, 0, -1):
        form = " ".join(words[:count])
        if count == 1 and (len(form) < MIN_SINGLE_WORD_FORM
                           or form in _GENERIC_FIRST_WORDS):
            continue
        if form not in forms:
            forms.append(form)
    return tuple(forms)


def _mentions(haystack: str, needle: str) -> bool:
    """Is ``needle`` in ``haystack`` as a whole word (or phrase)?

    Word-bounded so "AXT" does not match inside "AXTI", and "ON" does not match "Monday".
    """
    if not needle:
        return False
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])",
                     haystack, re.IGNORECASE) is not None


def match_reason(headline: Headline, ticker: str, company_name: str = "") -> str:
    """How this story belongs to this ticker, or ``""`` when it does not.

    Three ways in, in order of how much they prove:

    1. the provider's own PRIMARY symbol is this ticker;
    2. the ticker appears in the headline (three characters or more);
    3. the company's name, as the index spells it, appears in the headline.
    """
    clean = base_symbol(ticker)
    if not clean:
        return ""
    if headline.primary_symbol and headline.primary_symbol == clean:
        return MATCH_PRIMARY
    title = headline.title or ""
    if len(clean) >= MIN_TICKER_IN_HEADLINE and _mentions(title, clean):
        return MATCH_TICKER
    for form in name_forms(company_name):
        if _mentions(title, form):
            return MATCH_NAME
    return ""


@dataclass(frozen=True)
class MatchedNews:
    """One name's news, split into what is about it and what merely mentions it."""

    matched: tuple[Headline, ...] = ()
    related: tuple[Headline, ...] = ()
    how: str = ""                        # how ``matched[0]`` matched; "" when none did

    @property
    def found(self) -> str:
        """The mark for the record. Three-valued in the same spirit as the screens: a story
        that could not be attributed is NOT "no news" and NOT "news found"."""
        if self.matched:
            return "news found"
        if self.related:
            return RELATED
        return "no news found"


def match_news(headlines: Sequence[Headline], ticker: str,
               company_name: str = "") -> MatchedNews:
    """Split one name's returned stories into matched and merely-related, newest first."""
    matched: list[Headline] = []
    related: list[Headline] = []
    how = ""
    for headline in headlines:
        reason = match_reason(headline, ticker, company_name)
        if reason:
            if not matched:
                how = reason
            matched.append(headline)
        else:
            related.append(headline)
    return MatchedNews(matched=tuple(matched), related=tuple(related), how=how)


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
        raw_symbols = row.get("symbols")
        symbols = tuple(str(x) for x in raw_symbols
                        if isinstance(x, (str, int))) if isinstance(raw_symbols, list) else ()
        out.append(Headline(title=title, link=link, published_at=when,
                            source=publisher_from_link(link), symbols=symbols))
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
                config: GapConfig = DEFAULT_CONFIG,
                company_names: Optional[dict] = None) -> dict[str, MatchedNews]:
    """Per candidate, the stories that are ABOUT it and the ones that merely mention it.

    ``source=None`` means news was switched off, and every name comes back empty — the
    record distinguishes that from "the provider had nothing" by the run's own ``news``
    flag rather than by guessing from an empty result.

    ``company_names`` maps ticker -> the index's name for it, which is what makes the
    company-name test possible. Absent (a ``--tickers`` file has no index behind it) the
    match falls back to the provider's primary symbol and the ticker in the headline.
    """
    if source is None:
        return {ticker: MatchedNews() for ticker in tickers}
    since, until = news_window(run_at, config)
    names = company_names or {}
    return {ticker: match_news(source.headlines(ticker, since=since, until=until),
                               ticker, names.get(ticker, ""))
            for ticker in tickers}
