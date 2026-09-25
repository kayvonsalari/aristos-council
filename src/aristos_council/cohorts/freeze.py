"""COHORT-1 — freezing a built cohort, and versioning it.

A cohort is frozen the moment it is built: ``members.csv`` is the membership of record and
everything downstream reads THAT, never the definition. The definition can be edited at
any time without silently changing what a past run graded — which is the same discipline
strategy files already have (CLAUDE.md rule 7), for the same reason.

``vN`` increments only on ``--rebuild``. A plain ``build`` of an already-built cohort does
nothing and says so, so a nightly job cannot quietly re-cut a cohort under a scoreboard.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .definitions import CohortDefinition
from .source import Candidate
from .symbols import yahoo_symbol

MEMBERS_FILE = "members.csv"
DEFINITION_FILE = "definition.yaml"
REPORT_FILE = "report.md"
REMOVALS_FILE = "removals.log"

# ``market_cap_usd`` (COHORT-3) is LAST, so a members.csv cut before it existed still reads.
MEMBER_COLUMNS = ("ticker", "yahoo_ticker", "exchange", "industry", "market_cap",
                  "currency", "isin", "name", "source", "filled", "market_cap_usd")


@dataclass(frozen=True)
class FrozenCohort:
    slug: str
    version: int
    directory: Path
    members: tuple[Candidate, ...]

    @property
    def universe_id(self) -> str:
        """The id this cohort registers under. ``_v<n>`` is required by the Universe
        model, and it deliberately matches the cohort's own version — one number, so a
        list in the UI and a frozen directory can never disagree about which cut it is."""
        return f"cohort_{self.slug}_v{self.version}"


# --------------------------------------------------------------------------- #
# versions
# --------------------------------------------------------------------------- #
def cohort_dir(root: str | Path, slug: str) -> Path:
    return Path(root) / slug


def existing_versions(root: str | Path, slug: str) -> list[int]:
    """Every vN already on disk for this cohort, ascending."""
    base = cohort_dir(root, slug)
    if not base.exists():
        return []
    out = []
    for child in base.iterdir():
        if child.is_dir() and child.name.startswith("v") and child.name[1:].isdigit():
            out.append(int(child.name[1:]))
    return sorted(out)


def current_version(root: str | Path, slug: str) -> int | None:
    versions = existing_versions(root, slug)
    return versions[-1] if versions else None


def next_version(root: str | Path, slug: str, *, rebuild: bool) -> int:
    """Which vN a build should write.

    A first build is v1. A rebuild is the next integer. A plain build over an existing
    cohort is a caller error — ``builder`` checks ``current_version`` first and skips —
    and raising here rather than silently overwriting is what makes that check load-bearing.
    """
    current = current_version(root, slug)
    if current is None:
        return 1
    if not rebuild:
        raise FileExistsError(
            f"cohort {slug!r} is already built at v{current}; pass --rebuild to cut v{current + 1}")
    return current + 1


def version_dir(root: str | Path, slug: str, version: int) -> Path:
    return cohort_dir(root, slug) / f"v{version}"


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
def write_members(path: Path, members: list[Candidate]) -> Path:
    """members.csv — the membership of record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(MEMBER_COLUMNS)
        for cand in sorted(members, key=lambda c: c.ticker):
            writer.writerow([
                cand.ticker,
                _safe_yahoo(cand.ticker),
                cand.exchange,
                cand.industry,
                "" if cand.market_cap is None else f"{cand.market_cap:.0f}",
                cand.currency,
                cand.isin,
                cand.name,
                cand.source,
                ",".join(f"{k}:{v}" for k, v in sorted(cand.filled.items())),
                "" if cand.market_cap_usd is None else f"{cand.market_cap_usd:.0f}",
            ])
    return path


def _safe_yahoo(ticker: str) -> str:
    from .symbols import SymbolError
    try:
        return yahoo_symbol(ticker)
    except SymbolError:
        return ""


def read_members(path: str | Path) -> list[Candidate]:
    """members.csv back into candidates — what a diff compares against."""
    path = Path(path)
    out: list[Candidate] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cap = row.get("market_cap") or ""
            usd = row.get("market_cap_usd") or ""
            filled = {}
            for pair in (row.get("filled") or "").split(","):
                if ":" in pair:
                    k, _, v = pair.partition(":")
                    filled[k] = v
            out.append(Candidate(
                ticker=row["ticker"], exchange=row.get("exchange", ""),
                name=row.get("name", ""), industry=row.get("industry", ""),
                market_cap=float(cap) if cap else None,
                currency=row.get("currency", ""), isin=row.get("isin", ""),
                source=row.get("source", ""), filled=filled,
                market_cap_usd=float(usd) if usd else None))
    return out


def write_definition_snapshot(path: Path, defn: CohortDefinition, *, version: int,
                              built_on: date, source_path: str) -> Path:
    """The rule exactly as it was when this version was cut.

    Not a pointer to the definition file — a COPY. The file is editable; this is the
    record of what actually produced these names.
    """
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "name": defn.name,
        "slug": defn.slug,
        "version": version,
        "built_on": built_on.isoformat(),
        "built_from": source_path,
        "industry": list(defn.industry_as_written or defn.industry),
        "industry_resolved": list(defn.industry),
        "exchanges": list(defn.exchanges),
        "exchange_codes": list(defn.exchange_codes),
        "min_market_cap": defn.min_market_cap,
        "min_market_cap_usd": defn.min_market_cap_usd,
        "gics_subindustry": list(defn.gics_subindustry),
        "watch": defn.watch,
        "min_history_years": defn.min_history_years,
        "exclude": list(defn.exclude),
        "anchors": list(defn.anchors),
    }
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    return path


def write_removals(path: Path, removals, log: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = list(log) + [r.line() for r in removals]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #
def register_universe(frozen: FrozenCohort, defn: CohortDefinition, *,
                      universes_dir: str | Path, created: str | None = None,
                      overwrite: bool = False) -> Path:
    """Make the frozen cohort a local stock list, so it appears in the UI like any other.

    ``asset_kind="stocks"`` is explicit (ASSET-MODE-1): a cohort of operating companies is
    not an ETF list, and leaving the kind blank would put it behind the wrong switch. The
    vocabulary is the Universe model's — ``stocks``/``etfs``, plural — and it validates, so
    the singular the brief used fails loudly here rather than writing an unloadable file.

    The tickers written are the YAHOO symbols — the rest of Aristos ranks those. A name
    this repo has no translation for is left OUT of the list and named in the return, never
    written as a symbol that will come back UNRATEABLE.
    """
    from ..universe_editor import save_local_universe

    tickers, untranslatable = [], []
    for cand in sorted(frozen.members, key=lambda c: c.ticker):
        symbol = _safe_yahoo(cand.ticker)
        (tickers if symbol else untranslatable).append(symbol or cand.ticker)

    return save_local_universe(
        universes_dir,
        id=frozen.universe_id,
        tickers=tickers,
        created=created or date.today().isoformat(),
        display_name=defn.name,
        role=f"rule-built cohort, v{frozen.version}",
        description=(
            f"Built mechanically by COHORT-1 from "
            f"{', '.join(defn.industry_as_written or defn.industry)} on "
            f"{', '.join(defn.exchanges)}. Membership of record is "
            f"data/local/cohorts/{frozen.slug}/v{frozen.version}/{MEMBERS_FILE}; this list "
            f"is a copy for the UI."
            + (f" {len(untranslatable)} name(s) had no Yahoo symbol and are not in this "
               f"list: {', '.join(untranslatable)}." if untranslatable else "")),
        asset_kind="stocks",
        overwrite=overwrite)
