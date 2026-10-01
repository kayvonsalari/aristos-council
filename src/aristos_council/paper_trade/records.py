"""PAPER-TRADE-1 — the day's state (what ``enter``/``exit`` did) and the day's record (what
``record`` writes under ``data/local/paper_trade/``).

Two short-lived JSON files carry state between the independent, short-lived commands
(PAPER-TRADE-1 item 6: each connects, acts, disconnects — nothing holds a session all day):
``YYYY-MM-DD_entries.json`` (what ``enter`` attempted) and ``YYYY-MM-DD_exits.json`` (what
``exit`` submitted). ``record`` reads both, plus IBKR's own execution history, and writes the
ONE human-facing file: ``YYYY-MM-DD.csv``.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .config import LONG, SHORT, Paths


# --------------------------------------------------------------------------- #
# enter's own record of what it attempted — the direct witness to the entry fill
# --------------------------------------------------------------------------- #
@dataclass
class EntryAttempt:
    date: str
    ticker: str
    company: str = ""
    direction: str = ""                 # "long" | "short"
    gap_pct: Optional[float] = None
    attempted_at_et: str = ""           # when enter actually ran (may be after ENTER_TARGET)
    shares: int = 0
    quote_bid: Optional[float] = None
    quote_ask: Optional[float] = None
    quote_data_type: str = ""           # "live" | "delayed" | "frozen" | "delayed-frozen" | "unknown"
    limit_price: Optional[float] = None
    order_ref: str = ""
    shortable: Optional[bool] = None    # None: not applicable (long) or undetermined
    shortable_shares: Optional[float] = None
    filled: bool = False
    fill_price: Optional[float] = None
    fill_time_et: str = ""
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class ExitAttempt:
    date: str
    ticker: str
    direction: str = ""                 # the direction being CLOSED (same as the entry's)
    shares: int = 0
    submitted_at_et: str = ""
    order_ref: str = ""
    moc_submitted: bool = False
    error: str = ""


@dataclass
class EnterRun:
    """Everything ``enter`` did on one day, plus the day-level facts ``report`` needs: why a
    whole day was skipped (kill switch, CSV never arrived), if it was."""

    date: str
    csv_seen_at_et: str = ""            # when today's Gap Ledger CSV was first found, if it was
    day_skipped: bool = False
    day_skip_reason: str = ""
    attempts: list = field(default_factory=list)       # list[EntryAttempt]


@dataclass
class ExitRun:
    date: str
    day_skipped: bool = False
    day_skip_reason: str = ""
    exits: list = field(default_factory=list)          # list[ExitAttempt]


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def save_enter_run(paths: Paths, run: EnterRun) -> None:
    _write_json(paths.entries_json, {
        "date": run.date, "csv_seen_at_et": run.csv_seen_at_et,
        "day_skipped": run.day_skipped, "day_skip_reason": run.day_skip_reason,
        "attempts": [asdict(a) for a in run.attempts]})


def load_enter_run(paths: Paths) -> Optional[EnterRun]:
    if not paths.entries_json.exists():
        return None
    data = json.loads(paths.entries_json.read_text(encoding="utf-8"))
    attempts = [EntryAttempt(**a) for a in data.get("attempts", [])]
    return EnterRun(date=data.get("date", ""), csv_seen_at_et=data.get("csv_seen_at_et", ""),
                    day_skipped=data.get("day_skipped", False),
                    day_skip_reason=data.get("day_skip_reason", ""), attempts=attempts)


def save_exit_run(paths: Paths, run: ExitRun) -> None:
    _write_json(paths.exits_json, {
        "date": run.date, "day_skipped": run.day_skipped,
        "day_skip_reason": run.day_skip_reason,
        "exits": [asdict(e) for e in run.exits]})


def load_exit_run(paths: Paths) -> Optional[ExitRun]:
    if not paths.exits_json.exists():
        return None
    data = json.loads(paths.exits_json.read_text(encoding="utf-8"))
    exits = [ExitAttempt(**e) for e in data.get("exits", [])]
    return ExitRun(date=data.get("date", ""), day_skipped=data.get("day_skipped", False),
                   day_skip_reason=data.get("day_skip_reason", ""), exits=exits)


# --------------------------------------------------------------------------- #
# the maths record writes — pure, so it is tested without a gateway
# --------------------------------------------------------------------------- #
def entry_cost_vs_open_pct(direction: str, fill_price: Optional[float],
                           official_open: Optional[float]) -> Optional[float]:
    """Entry slippage versus the official 09:30 open, SIGNED so positive always means
    "worse for us" regardless of direction: a long that paid MORE than the open, or a short
    that sold for LESS than the open, both read positive. None when either price is
    missing — never a manufactured zero."""
    if fill_price is None or official_open is None or official_open == 0:
        return None
    sign = 1.0 if direction == LONG else -1.0
    return sign * (fill_price - official_open) / official_open


def gross_result(direction: str, entry_fill: Optional[float], exit_fill: Optional[float],
                 shares: int) -> Optional[float]:
    if entry_fill is None or exit_fill is None:
        return None
    if direction == LONG:
        return (exit_fill - entry_fill) * shares
    return (entry_fill - exit_fill) * shares


def net_result(gross: Optional[float], entry_commission: Optional[float],
               exit_commission: Optional[float]) -> Optional[float]:
    """Gross minus IBKR's own simulated commissions (reported as positive costs). A
    commission that could not be read is treated as 0 for this figure ONLY when gross
    itself is known — never promoted to hide a missing gross."""
    if gross is None:
        return None
    return gross - (entry_commission or 0.0) - (exit_commission or 0.0)
