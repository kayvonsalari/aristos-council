"""PAPER-TRADE-1 — the ``report`` command: per-day and running totals, in plain English.

Reads only what ``record`` already wrote (``data/local/paper_trade/YYYY-MM-DD.csv``) plus,
for the side-by-side line, Gap Ledger's own CSV for the same day (read-only — the SAME
boundary every other command in this package keeps).
"""
from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import GAP_LEDGER_ROOT, root_dir


@dataclass
class DayStats:
    date: str
    attempted: int = 0
    filled: int = 0
    not_shortable: int = 0
    entry_costs_pct: list = field(default_factory=list)       # filled only, not None
    net_results: list = field(default_factory=list)           # filled only, not None
    worst_entries: list = field(default_factory=list)         # [(ticker, date, cost_pct)]

    @property
    def fill_rate(self) -> Optional[float]:
        return (self.filled / self.attempted) if self.attempted else None

    @property
    def hit_rate(self) -> Optional[float]:
        if not self.net_results:
            return None
        wins = sum(1 for r in self.net_results if r > 0)
        return wins / len(self.net_results)


def _read_float(row: dict, key: str) -> Optional[float]:
    raw = (row.get(key) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _read_day_csv(path: Path) -> DayStats:
    stats = DayStats(date=path.stem)
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            stats.attempted += 1
            if row.get("skipped") == "true":
                if "not shortable" in (row.get("skip_reason") or ""):
                    stats.not_shortable += 1
                continue
            cost = _read_float(row, "entry_cost_vs_open_pct")
            if cost is not None:
                stats.entry_costs_pct.append(cost)
                stats.worst_entries.append((row.get("ticker", ""), stats.date, cost))
            net = _read_float(row, "net_result")
            if net is not None:
                stats.filled += 1
                stats.net_results.append(net)
    return stats


def collect_day_stats(root=None) -> list:
    """One ``DayStats`` per recorded day, oldest first."""
    base = root_dir(root)
    if not base.is_dir():
        return []
    return [_read_day_csv(p) for p in sorted(base.glob("????-??-??.csv"))]


def _combine(days: list) -> DayStats:
    total = DayStats(date="all days")
    for day in days:
        total.attempted += day.attempted
        total.filled += day.filled
        total.not_shortable += day.not_shortable
        total.entry_costs_pct += day.entry_costs_pct
        total.net_results += day.net_results
        total.worst_entries += day.worst_entries
    return total


def _pct(x: Optional[float]) -> str:
    """A SIGNED percentage (an entry cost, a return) — "+1.23%" / "-0.50%"."""
    return "n/a" if x is None else f"{x:+.2%}"


def _unsigned_pct(x: Optional[float]) -> str:
    """A plain rate (a fill rate, a hit rate) — "66.7%", never signed."""
    return "n/a" if x is None else f"{x:.1%}"


def _money(x: Optional[float]) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def _day_lines(stats: DayStats) -> list:
    out = [f"{stats.date}: {stats.attempted} candidate(s), {stats.filled} filled "
          f"({_unsigned_pct(stats.fill_rate)} fill rate), {stats.not_shortable} not shortable"]
    if stats.entry_costs_pct:
        avg = statistics.mean(stats.entry_costs_pct)
        med = statistics.median(stats.entry_costs_pct)
        out.append(f"  entry cost vs. the official open: average {_pct(avg)}, median {_pct(med)}")
    if stats.net_results:
        out.append(f"  hit rate {_unsigned_pct(stats.hit_rate)} of {len(stats.net_results)} "
                   f"filled position(s), net result {_money(sum(stats.net_results))}")
    return out


def _worst_five_lines(entries: list) -> list:
    if not entries:
        return []
    worst = sorted(entries, key=lambda e: e[2], reverse=True)[:5]
    return ["worst five entry costs (most expensive vs. the official open):"] + [
        f"  {ticker} ({day}): {_pct(cost)}" for ticker, day, cost in worst]


def side_by_side_lines(day: str, root=None, gap_ledger_root=None) -> list:
    """For every filled name that day: the paper result measured from the REAL entry fill
    (``net_result``, already net of commissions) beside Gap Ledger's own result measured
    from the official open (``move_close``, read read-only from Gap Ledger's own CSV for
    the same day) — so the entry-timing cost is visible as the GAP between the two, not
    just asserted in the headline number above."""
    record_path = root_dir(root) / f"{day}.csv"
    if not record_path.exists():
        return []
    gap_ledger_path = (Path(gap_ledger_root) if gap_ledger_root else GAP_LEDGER_ROOT) / f"{day}.csv"
    gap_ledger_moves: dict = {}
    if gap_ledger_path.exists():
        with gap_ledger_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                move = _read_float(row, "move_close")
                if move is not None:
                    gap_ledger_moves[row.get("ticker", "")] = move
    lines = []
    with record_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("skipped") == "true":
                continue
            net = _read_float(row, "net_result")
            shares = _read_float(row, "shares") or 0
            if net is None or not shares:
                continue
            ticker = row.get("ticker", "")
            paper_pct = net / (shares * (_read_float(row, "entry_fill_price") or 1))
            gl_move = gap_ledger_moves.get(ticker)
            lines.append(
                f"  {ticker}: paper from real entry {_pct(paper_pct)} vs. Gap Ledger from "
                f"the open {_pct(gl_move)}")
    return lines


def format_report(root=None, gap_ledger_root=None) -> str:
    days = collect_day_stats(root)
    if not days:
        return "No recorded days yet under data/local/paper_trade/."
    lines = ["Paper trade — per day", ""]
    for stats in days:
        lines += _day_lines(stats)
    lines += ["", "Running totals"]
    lines += _day_lines(_combine(days))
    lines += [""]
    lines += _worst_five_lines(_combine(days).worst_entries)
    lines += ["", "Side by side (paper's real entry vs. Gap Ledger's own open-to-close), "
             "most recent day:"]
    lines += side_by_side_lines(days[-1].date, root, gap_ledger_root) or ["  nothing filled"]
    return "\n".join(lines)
