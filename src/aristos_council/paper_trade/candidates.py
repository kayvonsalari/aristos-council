"""PAPER-TRADE-1 — reads Gap Ledger's daily CSV, read-only.

Does not import ``aristos_council.gap_ledger`` (see the package docstring) — this module
reads ``data/local/gap_ledger/YYYY-MM-DD.csv`` with the plain ``csv`` module against the
column names Gap Ledger's own ``ledger.LedgerRow`` writes, nothing more. If that shape ever
changes, this breaks loudly (a missing column reads as an abstention here, never a guess),
which is the honest failure mode for a reader with no say in the schema it reads.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from .config import GAP_LEDGER_ROOT, LONG, SHORT

GROUP_CANDIDATE = "candidate"
SOURCE_IBKR = "ibkr"


@dataclass(frozen=True)
class Candidate:
    """One IBKR-verified candidate from today's Gap Ledger CSV — exactly the fields
    ``enter`` needs, nothing else copied across the boundary."""

    ticker: str
    company: str
    direction: str                      # "long" (gap up) or "short" (gap down)
    gap_pct: float


def gap_ledger_csv_path(day: date, root: Path | str | None = None) -> Path:
    base = Path(root) if root is not None else GAP_LEDGER_ROOT
    return base / f"{day.isoformat()}.csv"


def _direction(gap_pct: float) -> str:
    return LONG if gap_pct >= 0 else SHORT


def read_ibkr_candidates(day: date, root: Path | str | None = None) -> list[Candidate]:
    """Every IBKR-verified candidate (``group == "candidate"``, ``source == "ibkr"``) from
    that day's Gap Ledger CSV, in file order. ``[]`` when the file does not exist yet — not
    an error, the caller (``enter``) is the one that knows whether that means "keep
    waiting" or "skip the day"."""
    path = gap_ledger_csv_path(day, root)
    if not path.exists():
        return []
    out: list[Candidate] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("group") != GROUP_CANDIDATE or row.get("source") != SOURCE_IBKR:
                continue
            ticker = (row.get("ticker") or "").strip()
            if not ticker:
                continue
            gap_text = row.get("ib_gap_pct") or row.get("gap_pct") or ""
            try:
                gap = float(gap_text)
            except ValueError:
                continue                 # an unreadable gap is not a candidate we can size
            out.append(Candidate(ticker=ticker, company=(row.get("company") or "").strip(),
                                 direction=_direction(gap), gap_pct=gap))
    return out
