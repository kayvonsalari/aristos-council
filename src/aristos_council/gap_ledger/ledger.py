"""GAP-LEDGER-1 step 4 — the day's CSV, and the baseline that makes it mean something.

One file per trading day, ``data/local/gap_ledger/YYYY-MM-DD.csv``, gitignored. It holds
every candidate with all its numbers, flags and links — AND an equal-size control group
drawn from the names that passed step 1 but not step 2.

The control group is the whole point of the file. "Gapping names carried on 58% of the
time" is not a finding; it is a fact about the market that week. The question the scorecard
can actually answer is whether the screen's names carried on MORE OFTEN than comparable
names that did not qualify, and that question needs the comparison logged on the same days,
from the same pool, at the same checkpoints. Drawing the control from step-1 survivors
(rather than from the whole market) holds price, liquidity and history roughly fixed, so
what differs is the gap and the volume — which is what the screen claims to select on.

The sample is RANDOM but SEEDED FROM THE DATE, so a given day's control group is the same
one however often the day is re-run. Reproducible without being cherry-picked: the seed is
fixed before the draw and derived from something nobody chooses.

Outcome columns are written EMPTY at run time and filled by ``outcomes``. Empty therefore
means "not filled in yet", and the scorecard says so rather than treating a blank as a
zero.
"""
from __future__ import annotations

import csv
import logging
import random
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

from .config import DEFAULT_ROOT, NY

_log = logging.getLogger(__name__)

GROUP_CANDIDATE = "candidate"
GROUP_BASELINE = "baseline"


@dataclass
class LedgerRow:
    """One name on one day. Field order IS the CSV column order."""

    # -- identity ---------------------------------------------------------- #
    date: str = ""                       # the market date, YYYY-MM-DD, New York
    ticker: str = ""
    group: str = GROUP_CANDIDATE
    run_at_et: str = ""
    window_start_et: str = ""
    window_end_et: str = ""

    # -- step 1 ------------------------------------------------------------ #
    previous_close: Optional[float] = None
    previous_session: str = ""
    average_volume: Optional[float] = None
    history_days: Optional[int] = None

    # -- step 2 ------------------------------------------------------------ #
    premarket_price: Optional[float] = None
    gap_pct: Optional[float] = None
    premarket_volume: Optional[int] = None
    baseline_median_volume: Optional[float] = None
    baseline_sessions: Optional[int] = None
    relative_volume: Optional[float] = None
    # Why the ratio is absent on a row that was kept anyway (see GapConfig). A candidate
    # carrying this was selected on its GAP ALONE.
    relative_volume_note: str = ""
    spread_pct: Optional[float] = None
    spread_note: str = ""
    screen_passed: str = ""              # "true" / "false" / "" — three-valued, as text
    screen_note: str = ""                # why it did not pass, for a baseline row

    # -- step 3 ------------------------------------------------------------ #
    # "news found" / "no news found" / "related, not matched" / "news not fetched"
    news_found: str = ""
    # GAP-NEWS-MATCH-1 — HOW the printed story was attributed to this name, so the
    # attribution is auditable rather than asserted. Empty when nothing matched.
    news_match: str = ""
    headline_count: Optional[int] = None
    headline: str = ""
    news_source: str = ""
    news_published_et: str = ""
    news_link: str = ""
    # Stories the provider returned for this ticker that are NOT about it. Kept in the
    # record — they are evidence about the provider's tagging — but never printed as the
    # name's news.
    related_count: Optional[int] = None
    related_headline: str = ""
    related_link: str = ""
    reason: str = ""                     # the --explain line, or the no-reason marker

    # -- step 4, filled after the close by ``outcomes`` -------------------- #
    open_price: Optional[float] = None
    price_1000: Optional[float] = None
    price_1130: Optional[float] = None
    close_price: Optional[float] = None
    outcome_note: str = ""
    outcomes_filled_at_et: str = ""

    # -- the thresholds this row was screened on --------------------------- #
    cfg_min_price: Optional[float] = None
    cfg_min_avg_volume: Optional[float] = None
    cfg_min_history_days: Optional[int] = None
    cfg_min_abs_gap: Optional[float] = None
    cfg_min_relative_volume: Optional[float] = None
    cfg_require_relative_volume: str = ""
    cfg_wide_spread: Optional[float] = None
    cfg_news_lookback_hours: Optional[int] = None

    @property
    def direction(self) -> int:
        """+1 gap up, -1 gap down, 0 for no gap or no reading."""
        if self.gap_pct is None or self.gap_pct == 0:
            return 0
        return 1 if self.gap_pct > 0 else -1


FIELDS: tuple[str, ...] = tuple(f.name for f in fields(LedgerRow))

_FLOATS = {"previous_close", "average_volume", "premarket_price", "gap_pct",
           "baseline_median_volume", "relative_volume", "spread_pct", "open_price",
           "price_1000", "price_1130", "close_price", "cfg_min_price",
           "cfg_min_avg_volume", "cfg_min_abs_gap", "cfg_min_relative_volume",
           "cfg_wide_spread"}
_INTS = {"history_days", "premarket_volume", "baseline_sessions", "headline_count",
         "related_count", "cfg_min_history_days", "cfg_news_lookback_hours"}


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def ledger_path(day: date, root: str | Path = DEFAULT_ROOT) -> Path:
    return Path(root) / f"{day.isoformat()}.csv"


def ledger_days(root: str | Path = DEFAULT_ROOT) -> list[date]:
    """Every logged day, oldest first. A filename that is not a date is ignored rather
    than raising — the directory is a local one and may hold notes."""
    out: list[date] = []
    for path in sorted(Path(root).glob("*.csv")):
        try:
            out.append(date.fromisoformat(path.stem))
        except ValueError:
            continue
    return out


# --------------------------------------------------------------------------- #
# io
# --------------------------------------------------------------------------- #
def write_day(day: date, rows: Sequence[LedgerRow], *,
              root: str | Path = DEFAULT_ROOT) -> Path:
    """Write the day's file whole, candidates first then the control group.

    Whole-file, via a temp file and a replace, for the same reason ``market_index`` does
    it: a run interrupted mid-write must not leave a truncated record where a readable one
    used to be.
    """
    path = ledger_path(day, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = ([r for r in rows if r.group == GROUP_CANDIDATE]
               + [r for r in rows if r.group != GROUP_CANDIDATE])
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FIELDS))
        writer.writeheader()
        for row in ordered:
            writer.writerow({k: ("" if v is None else v)
                             for k, v in asdict(row).items()})
    tmp.replace(path)
    return path


def read_day(day: date, root: str | Path = DEFAULT_ROOT) -> list[LedgerRow]:
    """The day's rows, or an empty list when the day was never logged."""
    path = ledger_path(day, root)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [_row_from_record(record) for record in csv.DictReader(handle)]


def read_all(root: str | Path = DEFAULT_ROOT) -> dict[date, list[LedgerRow]]:
    """Every logged day. Ordered oldest first so the scorecard reads chronologically."""
    return {day: read_day(day, root) for day in ledger_days(root)}


def _row_from_record(record: dict) -> LedgerRow:
    """One CSV record back into a row. An unparseable number becomes None — a missing
    reading, never a zero, which is the same null-is-not-false rule the screens run on."""
    values: dict = {}
    for name in FIELDS:
        raw = record.get(name, "")
        text = "" if raw is None else str(raw).strip()
        if name in _FLOATS:
            values[name] = _as_float(text)
        elif name in _INTS:
            values[name] = _as_int(text)
        else:
            values[name] = text
    return LedgerRow(**values)


def _as_float(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _as_int(text: str) -> Optional[int]:
    value = _as_float(text)
    return None if value is None else int(value)


# --------------------------------------------------------------------------- #
# the control group
# --------------------------------------------------------------------------- #
def day_seed(day: date) -> int:
    """The day's draw seed. Derived from the date so it is fixed before the draw and
    chosen by nobody."""
    return int(day.strftime("%Y%m%d"))


def sample_baseline(pool: Iterable[str], size: int, day: date) -> list[str]:
    """``size`` names from ``pool``, drawn with the day's seed, sorted for a stable record.

    The pool is sorted BEFORE the draw: ``random.sample`` over an unordered set would give
    a different answer per process (hash randomization), which would quietly defeat the
    point of seeding it. A pool smaller than ``size`` is returned whole — an under-filled
    control group is visible in the row count, and inventing names to fill it would not be.
    """
    names = sorted({t.strip().upper() for t in pool if t and t.strip()})
    if size <= 0 or not names:
        return []
    if len(names) <= size:
        return names
    return sorted(random.Random(day_seed(day)).sample(names, size))


# --------------------------------------------------------------------------- #
# formatting helpers shared by the CLI and the app
# --------------------------------------------------------------------------- #
def et_stamp(when: Optional[datetime]) -> str:
    """A timestamp in ET, to the minute. Empty for None."""
    if when is None:
        return ""
    return when.astimezone(NY).isoformat(timespec="minutes")
