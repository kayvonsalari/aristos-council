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
    # GAP-VIEWER-1 — the company name, from the market index at run time (the same name the
    # news matcher uses, so a headline match and the label on the page can never disagree).
    # Blank when the index has none: an absent name is never a failure, and the viewer looks
    # it up for a CSV written before this column existed.
    company: str = ""
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
    # GAP-PRICE-TRUST-1 — how many DISTINCT prices the window held, and whether the last
    # print was confirmed by the final half hour. The two readings that decide whether the
    # gap is believable at all.
    premarket_prints: Optional[int] = None
    confirm_prints: Optional[int] = None
    confirm_average: Optional[float] = None
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

    # -- GAP-IBKR-1: who the numbers above actually came from ---------------- #
    # "ibkr" when Interactive Brokers verified this name (and its price, volume and spread
    # therefore OVERRODE yfinance's), "yfinance" otherwise. The single most useful column on
    # the row, because it says whether the relative-volume leg ran at all.
    source: str = ""
    # Said once per run when the gateway was not reachable, so a yfinance-only day is legible
    # in the CSV and not only in the report that scrolled past.
    ibkr_note: str = ""
    # IB's own readings, kept BESIDE the screen's columns rather than only folded into them,
    # so a disagreement between the two providers is still visible after the fact.
    ib_last_price: Optional[float] = None
    ib_gap_pct: Optional[float] = None
    ib_premarket_volume: Optional[int] = None
    ib_baseline_median: Optional[float] = None
    ib_relative_volume: Optional[float] = None
    ib_bid: Optional[float] = None
    ib_ask: Optional[float] = None

    # -- GAP-EARLY-CHECKPOINT-1: when the move first showed, IBKR-verified candidates only -- #
    # The first 5-minute bar whose price was already at or beyond the gap threshold in the
    # gap's direction AND whose volume was well above the usual for that time of day. The time
    # is when the bar CLOSED (the earliest anyone could have seen it) and the price is that
    # bar's close. Blank when no bar qualified, and ALWAYS blank on a yfinance-only row: that
    # provider has no pre-market volume, so there is nothing to compare and nothing is guessed.
    first_signal_time_et: str = ""
    first_signal_price: Optional[float] = None
    # The traded price at each pre-market moment (config.PATH_MOMENTS). Blank when the name did
    # not print within 15 minutes after that moment, and never filled from anything else.
    price_0400: Optional[float] = None
    price_0600: Optional[float] = None
    price_0700: Optional[float] = None
    price_0800: Optional[float] = None
    price_0900: Optional[float] = None
    # Why the signal is blank on a row IB DID verify.
    early_signal_note: str = ""

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
    # GAP-PRICE-TRUST-1 diagnostic: the pre-market price against the 09:30 open, as a
    # fraction. This is how the trust thresholds get tuned — it measures directly how often
    # the pre-market print was junk. Blank on rows written before the column existed.
    premarket_vs_open: Optional[float] = None
    outcome_note: str = ""
    outcomes_filled_at_et: str = ""
    # GAP-EARLY-CHECKPOINT-1 — what acting at the first signal would have made, in the gap's
    # direction, as a fraction of the signal price. Blank without a signal.
    signal_move_0900: Optional[float] = None
    signal_move_open: Optional[float] = None
    signal_move_close: Optional[float] = None

    # -- the thresholds this row was screened on --------------------------- #
    cfg_min_price: Optional[float] = None
    cfg_min_avg_volume: Optional[float] = None
    cfg_min_history_days: Optional[int] = None
    cfg_min_abs_gap: Optional[float] = None
    cfg_min_relative_volume: Optional[float] = None
    cfg_require_relative_volume: str = ""
    cfg_wide_spread: Optional[float] = None
    cfg_max_trusted_spread: Optional[float] = None
    cfg_min_premarket_prints: Optional[int] = None
    cfg_min_confirm_prints: Optional[int] = None
    cfg_max_confirm_drift: Optional[float] = None
    cfg_news_lookback_hours: Optional[int] = None
    cfg_early_volume_multiple: Optional[float] = None

    @property
    def direction(self) -> int:
        """+1 gap up, -1 gap down, 0 for no gap or no reading."""
        if self.gap_pct is None or self.gap_pct == 0:
            return 0
        return 1 if self.gap_pct > 0 else -1


FIELDS: tuple[str, ...] = tuple(f.name for f in fields(LedgerRow))

_FLOATS = {"previous_close", "average_volume", "premarket_price", "gap_pct",
           "baseline_median_volume", "relative_volume", "spread_pct", "open_price",
           "price_1000", "price_1130", "close_price", "premarket_vs_open",
           "confirm_average", "ib_last_price", "ib_gap_pct", "ib_baseline_median",
           "ib_relative_volume", "ib_bid", "ib_ask",
           "cfg_min_price", "cfg_min_avg_volume", "cfg_min_abs_gap",
           "cfg_min_relative_volume", "cfg_wide_spread", "cfg_max_trusted_spread",
           "cfg_max_confirm_drift", "first_signal_price", "price_0400", "price_0600",
           "price_0700", "price_0800", "price_0900", "signal_move_0900", "signal_move_open",
           "signal_move_close", "cfg_early_volume_multiple"}
_INTS = {"history_days", "premarket_volume", "baseline_sessions", "headline_count",
         "related_count", "premarket_prints", "confirm_prints", "ib_premarket_volume",
         "cfg_min_history_days", "cfg_min_premarket_prints", "cfg_min_confirm_prints",
         "cfg_news_lookback_hours"}


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
