"""LIST-UI-1 (Batch 19B) — the pure logic behind the "Cohort / list" half of the Analyse tab.

The page asks ONE question: "What set of companies do you want ranked against each other?" and
offers three answers — a BUILT cohort, a SAVED list, or pasted tickers. This module holds what
those answers need that is not a widget: the cohort dropdown's options (read from the real
definitions and the frozen ``members.csv`` of each BUILT cohort — nothing hard-coded), the live
ticker resolver (offline, against the local market index), and the one sentence that states what
Run will do. All of it is pure or reads local files only: no network, no provider, no model.

Nothing here touches a vote, a rank or a number. It decides WHICH tickers go into the run that
already exists, and says so in words.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .plurals import plural

_ROOT = Path(__file__).resolve().parents[2]

# The sector word at the front of a cohort name ("Consumer - Auto Manufacturers"), as the
# definitions write it. Shown in full where a reader sees it.
_SECTOR_WORDS = {"Tech": "Technology", "Comms": "Communications", "Health": "Health care"}

SOURCE_COHORT = "Built cohort"
SOURCE_SAVED = "Saved list"
SOURCE_PASTE = "Paste tickers"


# --------------------------------------------------------------------------- #
# built cohorts
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CohortOption:
    slug: str
    name: str                    # "Consumer - Auto Manufacturers"
    sector: str                  # "Consumer"
    industry: str                # "Auto Manufacturers"
    version: int
    members: tuple               # run tickers (Yahoo form: what every consumer runs)
    floor_usd: Optional[float]

    @property
    def sector_label(self) -> str:
        return _SECTOR_WORDS.get(self.sector, self.sector)

    @property
    def label(self) -> str:
        """``Consumer · Auto Manufacturers · 28 names · $1bn+`` — sector first so the dropdown
        reads grouped (and a search for the sector finds its cohorts)."""
        return f"{self.sector_label} · {self.industry} · {self.detail}"

    @property
    def detail(self) -> str:
        floor = f" · {format_floor(self.floor_usd)}" if self.floor_usd else ""
        return f"{plural(len(self.members), 'name')}{floor}"


def format_floor(floor_usd: Optional[float]) -> str:
    """``1e9`` -> ``$1bn+``; ``2.5e9`` -> ``$2.5bn+``. Empty when there is no floor."""
    if not floor_usd:
        return ""
    return f"${floor_usd / 1e9:g}bn+"


def split_cohort_name(name: str) -> tuple[str, str]:
    """``"Consumer - Auto Manufacturers"`` -> ``("Consumer", "Auto Manufacturers")``; a name with
    no sector prefix keeps the whole name as its industry, under an empty sector."""
    head, sep, tail = (name or "").partition(" - ")
    return (head.strip(), tail.strip()) if sep else ("", (name or "").strip())


def cohort_options(*, cohorts_root=None, definitions_path=None) -> list[CohortOption]:
    """Every cohort that has been BUILT, grouped by sector (then industry). A defined-but-not-yet-
    built cohort has no frozen members, so it cannot be run and is not offered — ``unbuilt_count``
    says how many were left out so the page can be honest about it."""
    from .cohorts.builder import DEFAULT_ROOT
    from .cohorts.definitions import load_definitions
    from .cohorts.freeze import MEMBERS_FILE, current_version, read_members, version_dir
    from .cohorts.symbols import SymbolError, yahoo_symbol

    root = Path(cohorts_root) if cohorts_root else DEFAULT_ROOT
    path = Path(definitions_path) if definitions_path else _ROOT / "data" / "cohort_definitions.yaml"
    try:
        defs = load_definitions(path)
    except Exception:                                       # noqa: BLE001 — no list, not a crash
        return []
    out: list[CohortOption] = []
    for defn in defs:
        version = current_version(root, defn.slug)
        if version is None:
            continue
        try:
            members = read_members(version_dir(root, defn.slug, version) / MEMBERS_FILE)
        except Exception:                                   # noqa: BLE001 — a bad file is skipped
            continue
        tickers = []
        for cand in members:
            try:
                tickers.append(yahoo_symbol(cand.ticker))
            except SymbolError:
                tickers.append(cand.ticker)
        if not tickers:
            continue
        sector, industry = split_cohort_name(defn.name)
        out.append(CohortOption(
            slug=defn.slug, name=defn.name, sector=sector, industry=industry,
            version=version, members=tuple(tickers),
            floor_usd=getattr(defn, "min_market_cap_usd", None)))
    out.sort(key=lambda o: (o.sector_label.casefold(), o.industry.casefold()))
    return out


def unbuilt_cohort_count(*, cohorts_root=None, definitions_path=None) -> int:
    from .cohorts.builder import DEFAULT_ROOT
    from .cohorts.definitions import load_definitions
    from .cohorts.freeze import current_version

    root = Path(cohorts_root) if cohorts_root else DEFAULT_ROOT
    path = Path(definitions_path) if definitions_path else _ROOT / "data" / "cohort_definitions.yaml"
    try:
        defs = load_definitions(path)
    except Exception:                                       # noqa: BLE001
        return 0
    return sum(1 for d in defs if current_version(root, d.slug) is None)


def cohort_labels(options) -> list[str]:
    """One label per option, each naming exactly one cohort."""
    return [o.label for o in options]


# --------------------------------------------------------------------------- #
# saved lists
# --------------------------------------------------------------------------- #
def saved_list_label(name: str, n: int, thesis: str = "") -> str:
    """``Defensive names (income) · 16 names`` — the count LAST (the page and its tests read it
    there), the built-for tag, when the list states one, beside the name."""
    tag = f" ({thesis})" if thesis else ""
    return f"{name}{tag} · {plural(n, 'name')}"


# --------------------------------------------------------------------------- #
# the ticker resolver
# --------------------------------------------------------------------------- #
# Exchanges the local index does not carry (docs/MARKET_INDEX.md: their codes 404 on this plan).
_NOT_COVERED_SUFFIXES = {".T": "Tokyo", ".MI": "Milan"}


@dataclass(frozen=True)
class Recognised:
    typed: str
    send: str                    # the form the run uses (".US" stripped, EODHD form translated)
    name: str


@dataclass(frozen=True)
class Unrecognised:
    typed: str
    suggestion: str = ""         # a ticker that IS in the index, or ""
    why: str = ""                # set when the exchange is simply not covered


@dataclass(frozen=True)
class Resolution:
    recognised: tuple = ()
    unrecognised: tuple = ()
    checked: bool = True         # False when there is no index to check against

    @property
    def send(self) -> list[str]:
        """The tickers that may go to the provider — recognised ones only, de-duplicated. When
        nothing could be checked (no local index) the typed list passes through unchanged."""
        seen: list[str] = []
        for r in self.recognised:
            if r.send not in seen:
                seen.append(r.send)
        return seen

    def line(self) -> str:
        """The live line under the box: ``3 recognised: NVIDIA, AMD, Intel`` and, per bad ticker,
        ``NVDA.XX not recognised - did you mean NVDA?`` ('' when the box is empty)."""
        if not self.checked:
            return ""
        parts = []
        if self.recognised:
            names = ", ".join(_short_name(r.name) or r.send for r in self.recognised)
            parts.append(f"{len(self.recognised)} recognised: {names}")
        for u in self.unrecognised:
            if u.why:
                parts.append(f"{u.typed} not recognised - {u.why}")
            elif u.suggestion:
                parts.append(f"{u.typed} not recognised - did you mean {u.suggestion}?")
            else:
                parts.append(f"{u.typed} not recognised")
        return "\n".join(parts)


_LEGAL_TAIL = (" Corporation", " Corp.", " Corp", " Incorporated", " Inc.", " Inc", " Ltd.", " Ltd",
               " Limited", " PLC", " plc", " Holdings", " Holding", " Company", " Co.",
               " Group", " AG", " SE", " NV", " N.V.", " S.A.", " SA", " Ordinary Shares",
               " Common Stock")


def _short_name(name: str) -> str:
    """``NVIDIA Corporation`` -> ``NVIDIA``; ``Advanced Micro Devices, Inc.`` -> ``Advanced Micro
    Devices``. Cut at the first comma, then legal suffixes from the end — never at an ampersand."""
    text = (name or "").split(",")[0].strip()
    changed = True
    while changed:
        changed = False
        for tail in _LEGAL_TAIL:
            if text.endswith(tail) and len(text) > len(tail):
                text = text[: -len(tail)].strip()
                changed = True
    return text


def normalise_pasted(ticker: str) -> str:
    """Upper-case; a trailing ``.US`` (EODHD's way of writing a US listing) is dropped because
    this box takes yfinance style, where a US ticker is bare."""
    t = (ticker or "").strip().upper().rstrip(".")
    return t[:-3] if t.endswith(".US") and len(t) > 3 else t


@dataclass
class IndexLookup:
    """``yahoo ticker / EODHD ticker -> (yahoo ticker, name)`` over the local index, built once."""
    by_symbol: dict = field(default_factory=dict)
    ticker_list: list = field(default_factory=list)

    @classmethod
    def from_rows(cls, rows) -> "IndexLookup":
        by: dict = {}
        for r in rows:
            yahoo = (getattr(r, "yahoo_ticker", "") or "").upper()
            eod = (getattr(r, "ticker", "") or "").upper()
            name = getattr(r, "name", "") or ""
            send = yahoo or eod
            if not send:
                continue
            by.setdefault(send, (send, name))
            if eod:
                by.setdefault(eod, (send, name))
        return cls(by_symbol=by, ticker_list=sorted(k for k in by if "." not in k or len(k) < 12))

    def empty(self) -> bool:
        return not self.by_symbol


_LOOKUP_CACHE: dict = {}


def default_lookup(root=None) -> IndexLookup:
    """The lookup over the real local index, read once per process."""
    key = str(root) if root else "__default__"
    if key not in _LOOKUP_CACHE:
        from .market_index import IndexStore, load_config
        try:
            index_root = Path(root) if root else Path(load_config()["root"])
            rows = IndexStore(index_root).load()
        except Exception:                                   # noqa: BLE001 — no index, no check
            rows = []
        _LOOKUP_CACHE[key] = IndexLookup.from_rows(rows)
    return _LOOKUP_CACHE[key]


def resolve_tickers(tickers, lookup: Optional[IndexLookup] = None) -> Resolution:
    """Check each pasted ticker against the local index BEFORE a run. Recognised ones carry the
    company's name and the form the run will use; the rest are listed with a suggestion if one is
    close. With no index to check against, nothing is judged (``checked=False``)."""
    lookup = lookup if lookup is not None else default_lookup()
    typed_all = [str(t) for t in tickers if str(t).strip()]
    if lookup.empty():
        return Resolution(recognised=tuple(Recognised(t, normalise_pasted(t), "") for t in typed_all),
                          checked=False)
    good: list[Recognised] = []
    bad: list[Unrecognised] = []
    for typed in typed_all:
        norm = normalise_pasted(typed)
        hit = lookup.by_symbol.get(norm)
        if hit is not None:
            good.append(Recognised(typed, hit[0], hit[1]))
            continue
        bad.append(_unrecognised(typed, norm, lookup))
    return Resolution(recognised=tuple(good), unrecognised=tuple(bad))


def _unrecognised(typed: str, norm: str, lookup: IndexLookup) -> Unrecognised:
    for suffix, place in _NOT_COVERED_SUFFIXES.items():
        if norm.endswith(suffix):
            return Unrecognised(typed, why=f"{place} is not covered by the local index, so it "
                                           "cannot be checked or run")
    base = norm.rpartition(".")[0] if "." in norm else ""
    if base and base in lookup.by_symbol:
        return Unrecognised(typed, suggestion=lookup.by_symbol[base][0])
    if len(norm) >= 3:
        close = difflib.get_close_matches(norm, lookup.ticker_list, n=1, cutoff=0.85)
        if close:
            return Unrecognised(typed, suggestion=lookup.by_symbol[close[0]][0])
    return Unrecognised(typed)


# --------------------------------------------------------------------------- #
# the sentence above Run
# --------------------------------------------------------------------------- #
def run_sentence(source: str, *, n_names: int, n_lenses: int, deterministic: bool,
                 cohort_industry: str = "", n_left_out: int = 0) -> str:
    """One sentence stating what Run will do.

    Cohort:  ``Rank 28 Auto Manufacturers under 9 lenses, deterministic, no model calls.``
    Pasted / saved:  ``Rank these 3 names against each other; a pasted list is its own peer group
    and carries no track-record badges.``"""
    lenses = plural(n_lenses, "lens", "lenses")
    calls = "deterministic, no model calls" if deterministic else "with model narration (priced below)"
    if source == SOURCE_COHORT and cohort_industry:
        return f"Rank {n_names} {cohort_industry} under {lenses}, {calls}."
    left = (f" ({plural(n_left_out, 'ticker')} not recognised and left out.)" if n_left_out else "")
    own = ("a pasted list" if source == SOURCE_PASTE else "a saved list")
    return (f"Rank these {plural(n_names, 'name')} against each other under {lenses}, {calls}; "
            f"{own} is its own peer group and carries no track-record badges.{left}")


# --------------------------------------------------------------------------- #
# one-off overrides (this run only)
# --------------------------------------------------------------------------- #
OVERRIDES_TITLE = "One-off overrides (this run only)"
OVERRIDE_CONFIRM_SENTENCE = ("Narrated runs stop and ask when the estimated cost is above this "
                             "figure; 0 means always ask.")
OVERRIDE_SIZE_SENTENCE = ("Changes the smallest company size the lenses accept for this run only; "
                          "the lens files themselves never change.")
