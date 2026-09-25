"""COHORT-1 — where the names come from, and the honest record of which path was used.

The brief's primary source is EODHD's ``/screener``, filtered by industry, exchange and
market cap. Its fallback is index constituents filtered by the fundamentals' industry
field. Which one runs is not a decision taken here on a hunch: ``probe`` asks the API and
writes down the answer, every cohort records the path that built it, and the report says
so in a sentence.

On the subscription this was written against, ``/screener`` answers 403 and the fallback
is what runs. That is a fact about a plan, not about the code — a key that can screen will
take the primary path with no change here.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

BASE_URL = "https://eodhd.com/api"

PATH_SCREENER = "screener"
PATH_CONSTITUENTS = "constituents"
PATH_YFINANCE = "yfinance"
# COHORT-3 - the names come from the local market index, through the same cleaned pool the peer
# groups use. No request is made to build the pool.
PATH_INDEX = "index"

# The index constituent lists the fallback pulls. S&P 500 for the US leg and STOXX 600 for
# Europe, which between them cover every exchange the shipped definitions name.
DEFAULT_INDEXES: tuple[str, ...] = ("GSPC.INDX", "STOXX.INDX")

# The fundamentals fields the cleanup rules need, requested in ONE filtered call per name
# rather than pulling a whole fundamentals document (which is megabytes).
FUNDAMENTALS_FIELDS = (
    "General::Code,General::Name,General::Type,General::Exchange,"
    "General::CurrencyCode,General::CountryISO,General::ISIN,General::PrimaryTicker,"
    "General::Industry,General::Sector,General::GicSubIndustry,General::IPODate,"
    "Highlights::MarketCapitalization"
)


class SourceError(RuntimeError):
    """The source could not be reached or answered with something unusable."""


# --------------------------------------------------------------------------- #
# what the API will actually do for us
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SourceProbe:
    """What the endpoint honoured, recorded at startup and printed in every report."""

    screener_available: bool
    honoured: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()
    note: str = ""

    @property
    def path(self) -> str:
        return PATH_SCREENER if self.screener_available else PATH_CONSTITUENTS

    def sentence(self) -> str:
        if self.screener_available:
            return (f"Source: EODHD screener, honouring {', '.join(self.honoured)}"
                    + (f"; it ignored {', '.join(self.refused)}." if self.refused else "."))
        return (f"Source: EODHD index constituents ({', '.join(DEFAULT_INDEXES)}) filtered "
                f"by the fundamentals industry field — the screener was not available "
                f"({self.note}).")


@dataclass
class Candidate:
    """One name on the way in. Every field carries where it came from."""

    ticker: str                       # EODHD symbol, e.g. "XOM.US"
    exchange: str = ""
    name: str = ""
    industry: str = ""
    sector: str = ""
    market_cap: float | None = None
    currency: str = ""
    isin: str = ""
    primary_ticker: str = ""
    security_type: str = ""
    history_years: float | None = None
    source: str = PATH_CONSTITUENTS
    # COHORT-3 - the market cap converted to USD by the market index, kept beside the one in the
    # name's own currency. ``None`` for a name that did not come from the index.
    market_cap_usd: float | None = None
    # Per-field source tags for anything that had to be filled from a second provider.
    filled: dict[str, str] = field(default_factory=dict)

    @property
    def code(self) -> str:
        """The bare symbol without the exchange suffix."""
        return self.ticker.split(".")[0]


# --------------------------------------------------------------------------- #
# the client
# --------------------------------------------------------------------------- #
class EODHDSource:
    """A thin, read-only EODHD client. No retries, no cache — the CLI is not a hot path.

    Kept separate from ``data/eodhd_adapter.py`` on purpose: that adapter serves the
    ranker's per-name price/dividend/fundamentals contract, and widening it with
    cohort-construction endpoints would put index and screener concerns inside the
    MarketDataAdapter interface every lens depends on.
    """

    def __init__(self, api_key: str | None = None, timeout: float = 30.0,
                 indexes: tuple[str, ...] = DEFAULT_INDEXES) -> None:
        self._key = api_key if api_key is not None else os.environ.get("EODHD_API_KEY", "")
        self._timeout = timeout
        self.indexes = indexes
        self.calls = 0
        # One pull of the constituent lists per process. The builder reads them twice —
        # once to match the rule, once to count what a wider exchange list WOULD have
        # matched for the "too thin" suggestion — and a suggestion is not worth a second
        # 1,000-row download.
        self._constituents: list[dict] | None = None

    # -- transport ---------------------------------------------------------- #
    def _get(self, path: str, **params) -> object:
        if not self._key:
            raise SourceError("EODHD_API_KEY is not set — the cohort builder cannot reach "
                              "a source. Put it in the environment or a local .env.")
        params.setdefault("api_token", self._key)
        params.setdefault("fmt", "json")
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
        self.calls += 1
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise SourceError(f"HTTP {exc.code} from {path}") from None
        except Exception as exc:                       # network, DNS, malformed JSON
            raise SourceError(f"{type(exc).__name__} from {path}: {exc}") from None

    # -- the probe ---------------------------------------------------------- #
    def probe(self) -> SourceProbe:
        """Ask the screener for one row and record exactly what came back.

        A filter set that returns rows is HONOURED only if the rows respect it — an
        endpoint that accepts a filter and ignores it is worse than one that refuses,
        because it answers confidently with the wrong universe. So the probe checks the
        returned row against the industry it asked for.
        """
        filters = json.dumps([["industry", "=", "Oil & Gas Integrated"],
                              ["exchange", "=", "US"],
                              ["market_capitalization", ">", 1_000_000_000]])
        try:
            rows = self._get("/screener", filters=filters, limit=5,
                             sort="market_capitalization.desc")
        except SourceError as exc:
            return SourceProbe(False, note=str(exc))

        data = rows.get("data") if isinstance(rows, dict) else rows
        if not isinstance(data, list) or not data:
            return SourceProbe(False, note="screener returned no rows for a filter that "
                                           "should match several")
        honoured, refused = [], []
        for label, key, want in (("industry", "industry", "Oil & Gas Integrated"),
                                 ("exchange", "exchange", "US")):
            seen = {str(r.get(key, "")) for r in data if isinstance(r, dict)}
            (honoured if seen == {want} else refused).append(label)
        caps = [r.get("market_capitalization") for r in data if isinstance(r, dict)]
        ok_caps = [c for c in caps if isinstance(c, (int, float))]
        (honoured if ok_caps and min(ok_caps) > 1_000_000_000
         else refused).append("market_capitalization")
        # Industry filtering is the one the whole primary path rests on. If the endpoint
        # ignored it, the brief's own instruction applies: treat it as unreliable and fall
        # back, rather than build ten cohorts out of an unfiltered list.
        if "industry" in refused:
            return SourceProbe(False, honoured=tuple(honoured), refused=tuple(refused),
                               note="screener ignored the industry filter")
        return SourceProbe(True, honoured=tuple(honoured), refused=tuple(refused))

    # -- the two paths ------------------------------------------------------ #
    def screener_rows(self, industries: tuple[str, ...], exchange_codes: tuple[str, ...],
                      min_market_cap: float, limit: int = 500) -> list[dict]:
        out: list[dict] = []
        for industry in industries:
            filters = json.dumps([["industry", "=", industry],
                                  ["market_capitalization", ">", float(min_market_cap)]])
            rows = self._get("/screener", filters=filters, limit=limit,
                             sort="market_capitalization.desc")
            data = rows.get("data") if isinstance(rows, dict) else rows
            for r in data or []:
                if isinstance(r, dict) and str(r.get("exchange", "")) in exchange_codes:
                    out.append(r)
        return out

    def index_components(self, index: str) -> list[dict]:
        """One index's constituents as a list. EODHD returns them keyed by position."""
        doc = self._get(f"/fundamentals/{index}", filter="Components")
        if isinstance(doc, dict):
            return [v for v in doc.values() if isinstance(v, dict)]
        return [v for v in (doc or []) if isinstance(v, dict)]

    def constituents(self) -> list[dict]:
        if self._constituents is None:
            rows: list[dict] = []
            for index in self.indexes:
                for row in self.index_components(index):
                    rows.append({**row, "_index": index})
            self._constituents = rows
        return self._constituents

    def fundamentals(self, symbol: str) -> dict:
        doc = self._get(f"/fundamentals/{symbol}", filter=FUNDAMENTALS_FIELDS)
        return doc if isinstance(doc, dict) else {}


# --------------------------------------------------------------------------- #
# building the pool
# --------------------------------------------------------------------------- #
def _candidate_from_fundamentals(symbol: str, doc: dict, source: str) -> Candidate:
    cap = doc.get("Highlights::MarketCapitalization")
    return Candidate(
        ticker=symbol,
        exchange=str(doc.get("General::Exchange") or symbol.split(".")[-1]),
        name=str(doc.get("General::Name") or ""),
        industry=str(doc.get("General::Industry") or ""),
        sector=str(doc.get("General::Sector") or ""),
        market_cap=float(cap) if isinstance(cap, (int, float)) else None,
        currency=str(doc.get("General::CurrencyCode") or ""),
        isin=str(doc.get("General::ISIN") or ""),
        primary_ticker=str(doc.get("General::PrimaryTicker") or ""),
        security_type=str(doc.get("General::Type") or ""),
        source=source,
    )


def build_pool(defn, source: EODHDSource, probe: SourceProbe,
               progress=None) -> tuple[list[Candidate], str, list[str]]:
    """``(candidates, path_used, log)`` — every name the rule could possibly admit.

    No cleanup happens here. This function's only job is to produce the widest honest
    pool for the rule, tagged with where each name came from, so that every later removal
    is a RULE removing it rather than a source quietly never offering it.
    """
    log: list[str] = [probe.sentence()]
    wanted_industries = set(defn.industry)
    codes = set(defn.exchange_codes)

    if probe.screener_available:
        rows = source.screener_rows(defn.industry, tuple(codes), defn.min_market_cap)
        symbols = [f"{r.get('code')}.{r.get('exchange')}" for r in rows if r.get("code")]
        log.append(f"Screener returned {len(symbols)} name(s) for "
                   f"{len(defn.industry)} industry code(s).")
        path = PATH_SCREENER
    else:
        rows = source.constituents()
        log.append(f"Pulled {len(rows)} index constituent(s) from "
                   f"{', '.join(source.indexes)}.")
        matched = [r for r in rows
                   if str(r.get("Industry") or "") in wanted_industries
                   and str(r.get("Exchange") or "") in codes]
        symbols = [f"{r.get('Code')}.{r.get('Exchange')}" for r in matched if r.get("Code")]
        log.append(f"{len(matched)} matched the industry code(s) "
                   f"{', '.join(sorted(wanted_industries))} on "
                   f"{', '.join(sorted(codes))}.")
        path = PATH_CONSTITUENTS

    # De-dupe before spending a call per name: the same company can appear in both
    # indexes, and an index list can carry the same code twice.
    symbols = list(dict.fromkeys(symbols))

    out: list[Candidate] = []
    for i, symbol in enumerate(symbols, 1):
        if progress is not None and i % 10 == 0:
            progress(f"  fundamentals {i}/{len(symbols)}")
        try:
            doc = source.fundamentals(symbol)
        except SourceError as exc:
            log.append(f"{symbol}: no fundamentals ({exc}) — left for rule 4 to drop.")
            out.append(Candidate(ticker=symbol, source=path))
            continue
        out.append(_candidate_from_fundamentals(symbol, doc, path))
    return out, path, log


# A preference share is filed by the provider as "Common Stock" but is a second line of a company
# whose ordinary line is elsewhere in the same pool ("China Steel Corp Pref", 2002A.TW, stood in the
# Steel cohort beside 2002.TW). The NAME says so, and cleanup rule 1 already removes a "Preferred"
# type as never a primary common line, so the type is read from the name and the existing rule does
# the rest. Korea's preference series are folded earlier, by PEER-KR-PREF-1.
_PREFERENCE_NAME = re.compile(r"\b(?:prefs?|preferred|preference|pfd|prf)\b", re.IGNORECASE)


def candidate_from_index_row(row) -> Candidate:
    """One cleaned-pool row of the market index as a cohort candidate.

    The industry is EODHD's ``General::Industry`` exactly as the index stores it, with a
    non-breaking space read as the space it was meant to be: two live labels ("Aerospace &\xa0Defense",
    "Construction\xa0& Engineering") carry one, and an exact-string match would silently miss them.
    """
    return Candidate(
        ticker=row.ticker, exchange=row.market or row.ticker.rpartition(".")[2],
        name=row.name, industry=(row.industry or "").replace("\xa0", " ").strip(),
        sector=(row.sector or "").replace("\xa0", " ").strip(),
        market_cap=row.market_cap, currency=row.currency, isin=row.isin,
        primary_ticker=row.primary_ticker,
        security_type="Preferred" if _PREFERENCE_NAME.search(row.name or "") else "Common Stock",
        source=PATH_INDEX, market_cap_usd=row.market_cap_usd)


def build_pool_from_index(defn, pool, progress=None) -> tuple[list[Candidate], str, list[str]]:
    """``(candidates, path_used, log)`` from a ``market_index.CleanPool``. Same contract as
    ``build_pool``: no cleanup beyond what the pool already did, so every later removal is a
    RULE removing a name rather than the source never offering it.

    The pool is the one ``peers`` uses - overrides and aliases applied, one row per company,
    receipts, funds, classification-suspect and size-suspect rows out. What a cohort adds is its
    own: Sao Paulo is left out, the definition's exchanges are honoured, and the industry is
    matched on the code.
    """
    from ..market_index import CleanPool  # noqa: F401  (documented type; import is lazy)
    from .definitions import INDEX_EXCLUDED_MARKETS

    log: list[str] = [
        f"Source: the local market index, through the same cleaned pool the peer groups use "
        f"(snapshot {pool.snapshot or 'unknown'}). No request was made."]
    log.extend(line.strip() for line in pool.lines()[1:])
    if pool.overridden:
        log.append(f"{len(pool.overridden)} row(s) in the pool carry a corrected label "
                   f"(data/label_overrides.yaml): {', '.join(sorted(pool.overridden))}.")
    if pool.aliased:
        log.append(f"{len(pool.aliased)} identity alias(es) applied "
                   f"(data/identity_aliases.yaml): {', '.join(sorted(pool.aliased))}.")

    rows = list(pool.rows)
    left_out = [r for r in rows if r.market in INDEX_EXCLUDED_MARKETS]
    rows = [r for r in rows if r.market not in INDEX_EXCLUDED_MARKETS]
    log.append(f"{len(left_out)} company(ies) on {', '.join(INDEX_EXCLUDED_MARKETS)} (Sao Paulo) "
               f"left out by decision.")
    if not defn.all_index_exchanges:
        codes = set(defn.exchange_codes)
        rows = [r for r in rows if r.market in codes]
        log.append(f"{len(rows)} on {', '.join(sorted(codes))}.")
    wanted = set(defn.industry)
    matched = [r for r in rows if (r.industry or "").replace("\xa0", " ").strip() in wanted]
    log.append(f"{len(matched)} matched the industry code(s) {', '.join(sorted(wanted))}"
               + (" on every index market except Sao Paulo." if defn.all_index_exchanges
                  else "."))
    return [candidate_from_index_row(r) for r in matched], PATH_INDEX, log


def fill_missing(candidates: list[Candidate], filler=None) -> list[str]:
    """Secondary source: yfinance, and ONLY to fill an industry or a market cap that the
    primary left blank for a name it already found.

    Never used to discover a name — a cohort's membership must come from the rule, and a
    second source that can ADD names is a second rule nobody wrote down. Every value it
    supplies is tagged on the candidate, counted in the report's source summary, and
    visible in members.csv.
    """
    if filler is None:
        filler = _yfinance_filler
    log: list[str] = []
    for cand in candidates:
        missing = [f for f in ("industry", "market_cap") if not getattr(cand, f)]
        if not missing:
            continue
        got = filler(cand.ticker, tuple(missing))
        for key, value in (got or {}).items():
            if value in (None, "") or key not in missing:
                continue
            setattr(cand, key, value)
            cand.filled[key] = PATH_YFINANCE
            log.append(f"{cand.ticker}: {key} filled from yfinance ({value!r}).")
    return log


def _yfinance_filler(ticker: str, fields: tuple[str, ...]) -> dict:
    """The real filler. Imported lazily so the package works without yfinance."""
    try:
        import yfinance  # noqa: F401
    except ImportError:
        return {}
    from ..data.provider import select_market_adapter
    try:
        adapter = select_market_adapter("yfinance")
        fundamentals = adapter.get_fundamentals(ticker.split(".")[0])
    except Exception:
        return {}
    out: dict = {}
    if "market_cap" in fields:
        out["market_cap"] = getattr(fundamentals, "market_cap", None)
    if "industry" in fields:
        out["industry"] = getattr(fundamentals, "industry", "") or ""
    return out
