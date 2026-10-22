"""SWEEP-1 - the set file: ``data/sweep_sets.yaml`` parsed into typed entries.

Pure parsing and validation, no network. A malformed file or entry is a ``SweepSetError`` that names
the entry, so a typo stops the sweep before it spends anything."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "sweep_sets.yaml"
ALL_STOCK = "all_stock"
COMPANY, LIST = "company", "list"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class SweepSetError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    id: str
    kind: str                               # company | list
    ticker: str = ""
    cohort: str = ""
    tickers: tuple = ()
    lenses: object = ALL_STOCK              # "all_stock" or a tuple of strategy ids
    council: bool = False
    judges: Optional[bool] = None           # None: follow the command line

    @property
    def is_company(self) -> bool:
        return self.kind == COMPANY

    def flags(self) -> dict:
        return {"council": self.council, "judges": self.judges}


@dataclass(frozen=True)
class SweepSet:
    name: str
    entries: tuple = field(default_factory=tuple)

    def ordered(self) -> tuple:
        """Company entries first, then lists, each group in file order."""
        return (tuple(e for e in self.entries if e.is_company)
                + tuple(e for e in self.entries if not e.is_company))


def _entry(raw: dict, where: str) -> Entry:
    if not isinstance(raw, dict):
        raise SweepSetError(f"{where}: an entry must be a mapping, got {type(raw).__name__}")
    unknown = set(raw) - {"id", "kind", "ticker", "cohort", "tickers", "lenses", "council", "judges"}
    if unknown:
        raise SweepSetError(f"{where}: unknown key(s) {sorted(unknown)}")
    eid, kind = str(raw.get("id") or ""), raw.get("kind")
    if not _ID.match(eid):
        raise SweepSetError(f"{where}: id {eid!r} is not a usable folder name")
    if kind not in (COMPANY, LIST):
        raise SweepSetError(f"{eid}: kind must be 'company' or 'list', got {kind!r}")
    lenses = raw.get("lenses", ALL_STOCK)
    if lenses != ALL_STOCK:
        if not (isinstance(lenses, list) and lenses and all(isinstance(x, str) for x in lenses)):
            raise SweepSetError(f"{eid}: lenses must be 'all_stock' or a list of strategy ids")
        lenses = tuple(lenses)
    judges = raw.get("judges")
    if judges is not None and not isinstance(judges, bool):
        raise SweepSetError(f"{eid}: judges must be true or false")
    if not isinstance(raw.get("council", False), bool):
        raise SweepSetError(f"{eid}: council must be true or false")
    if kind == COMPANY:
        ticker = str(raw.get("ticker") or "").strip()
        if not ticker or raw.get("cohort") or raw.get("tickers"):
            raise SweepSetError(f"{eid}: a company entry has a 'ticker' and nothing else to run")
        return Entry(id=eid, kind=kind, ticker=ticker, lenses=lenses,
                     council=bool(raw.get("council", False)), judges=judges)
    cohort, tickers = str(raw.get("cohort") or "").strip(), raw.get("tickers") or []
    if bool(cohort) == bool(tickers) or raw.get("ticker"):
        raise SweepSetError(f"{eid}: a list entry has exactly one of 'cohort' or 'tickers'")
    if tickers and not (isinstance(tickers, list) and all(isinstance(t, str) for t in tickers)):
        raise SweepSetError(f"{eid}: tickers must be a list of strings")
    if raw.get("council"):
        raise SweepSetError(f"{eid}: the council opinion is a company-page option, not a list one")
    return Entry(id=eid, kind=kind, cohort=cohort, tickers=tuple(tickers), lenses=lenses,
                 judges=judges)


def load_sets(path: Optional[Path] = None) -> dict:
    """``{set name: SweepSet}`` from the YAML file."""
    path = Path(path) if path else DEFAULT_PATH
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SweepSetError(f"cannot read the set file {path}: {exc}") from exc
    sets = (doc or {}).get("sets") if isinstance(doc, dict) else None
    if not isinstance(sets, dict) or not sets:
        raise SweepSetError(f"{path}: no 'sets:' mapping")
    out: dict = {}
    for name, rows in sets.items():
        if not isinstance(rows, list) or not rows:
            raise SweepSetError(f"set {name!r}: needs a non-empty list of entries")
        entries = tuple(_entry(r, f"set {name!r} entry {i + 1}") for i, r in enumerate(rows))
        ids = [e.id for e in entries]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise SweepSetError(f"set {name!r}: duplicate entry id(s) {dup}")
        out[str(name)] = SweepSet(str(name), entries)
    return out


def get_set(name: str, path: Optional[Path] = None) -> SweepSet:
    sets = load_sets(path)
    if name not in sets:
        raise SweepSetError(f"no set named {name!r}; the file has: {', '.join(sorted(sets))}")
    return sets[name]
