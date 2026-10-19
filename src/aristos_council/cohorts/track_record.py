"""BACKTEST-RULE-1 - which frozen cohorts have no track record yet.

A badge exists only for a cohort/lens pair with a CSV under ``backtests/<slug>/<lens>.csv``;
anything else reads "untested here". This lists the frozen cohorts that are missing rows for
one or more voting lenses so a new cohort cannot ship without anyone noticing. Read-only, no
network. "Voting lenses" are the lenses the committed backtests already cover (the union of
``backtests/*/*.csv`` stems): check lenses (Forensic, valuation band) are not backtested and
never appear there.
"""
from __future__ import annotations

from pathlib import Path

from .freeze import MEMBERS_FILE, current_version

BACKTESTS_ROOT = Path(__file__).resolve().parents[3] / "backtests"


def backtested_lenses(backtests: str | Path = BACKTESTS_ROOT) -> list[str]:
    base = Path(backtests)
    if not base.is_dir():
        return []
    return sorted({p.stem for p in base.glob("*/*.csv")})


def missing_track_records(root: str | Path, backtests: str | Path = BACKTESTS_ROOT
                          ) -> list[tuple[str, list[str]]]:
    """[(cohort_slug, [lens ids with no backtest CSV])] for every frozen cohort under ``root``
    that lacks at least one. A cohort counts as frozen when its newest vN has a members file."""
    base, bt = Path(root), Path(backtests)
    lenses = backtested_lenses(bt)
    out: list[tuple[str, list[str]]] = []
    if not base.is_dir():
        return out
    for cohort in sorted(p for p in base.iterdir() if p.is_dir()):
        version = current_version(base, cohort.name)
        if version is None or not (cohort / f"v{version}" / MEMBERS_FILE).is_file():
            continue
        missing = [lens for lens in lenses if not (bt / cohort.name / f"{lens}.csv").is_file()]
        if missing:
            out.append((cohort.name, missing))
    return out


def format_missing(rows: list[tuple[str, list[str]]], all_lenses: list[str] | None = None) -> str:
    if not rows:
        return "Track records: every frozen cohort has a backtest row for every voting lens."
    lines = [f"no track record yet - {len(rows)} frozen cohort(s) missing backtest rows "
             "(badges read 'untested here'; see docs/BACKTEST.md 'When to re-run the backtest'):"]
    for slug, missing in rows:
        what = ", ".join(missing) if not all_lenses or len(missing) < len(all_lenses)             else f"all {len(all_lenses)} voting lenses"
        lines.append(f"  {slug}: no track record yet for {what}")
    return "\n".join(lines)
