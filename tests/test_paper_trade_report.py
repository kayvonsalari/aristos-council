"""PAPER-TRADE-1 — the ``report`` command: per-day/running totals, the worst-five list,
and the paper-vs-Gap-Ledger side-by-side line."""
from __future__ import annotations

import csv

import pytest

from aristos_council.paper_trade.report import (collect_day_stats, format_report,
                                                 side_by_side_lines)
from aristos_council.paper_trade.record import FIELDS as RECORD_FIELDS

GL_FIELDS = ("date", "ticker", "group", "source", "move_close")


def _write_record_csv(root, day, rows):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{day}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(RECORD_FIELDS))
        writer.writeheader()
        for row in rows:
            writer.writerow({**{f: "" for f in RECORD_FIELDS}, **row})


def _row(ticker, *, skipped="false", skip_reason="", cost=None, net=None, shares=None,
        entry_fill=None):
    out = {"ticker": ticker, "skipped": skipped, "skip_reason": skip_reason}
    if cost is not None:
        out["entry_cost_vs_open_pct"] = cost
    if net is not None:
        out["net_result"] = net
    if shares is not None:
        out["shares"] = shares
    if entry_fill is not None:
        out["entry_fill_price"] = entry_fill
    return out


def test_a_day_with_no_recorded_files_says_so(tmp_path):
    assert format_report(tmp_path) == "No recorded days yet under data/local/paper_trade/."


def test_fill_rate_and_not_shortable_count(tmp_path):
    _write_record_csv(tmp_path, "2026-09-29", [
        _row("AAA", cost=0.01, net=10.0, shares=10, entry_fill=10.0),
        _row("BBB", skipped="true", skip_reason="not shortable (real-world)"),
        _row("CCC", skipped="true", skip_reason="not filled"),
    ])
    stats = collect_day_stats(tmp_path)[0]
    assert stats.attempted == 3 and stats.filled == 1 and stats.not_shortable == 1


def test_hit_rate_counts_only_filled_positions(tmp_path):
    _write_record_csv(tmp_path, "2026-09-29", [
        _row("AAA", cost=0.01, net=10.0, shares=10, entry_fill=10.0),
        _row("BBB", cost=-0.01, net=-5.0, shares=10, entry_fill=10.0),
    ])
    stats = collect_day_stats(tmp_path)[0]
    assert stats.hit_rate == pytest.approx(0.5)


def test_worst_five_entries_sort_most_expensive_first(tmp_path):
    rows = [_row(f"T{i}", cost=i * 0.01, net=0.0, shares=1, entry_fill=1.0) for i in range(7)]
    _write_record_csv(tmp_path, "2026-09-29", rows)
    report = format_report(tmp_path)
    assert "T6" in report.split("worst five")[1].splitlines()[1]      # the most expensive


def test_running_totals_combine_every_day(tmp_path):
    _write_record_csv(tmp_path, "2026-09-28", [_row("AAA", cost=0.01, net=10.0, shares=10,
                                                     entry_fill=10.0)])
    _write_record_csv(tmp_path, "2026-09-29", [_row("BBB", cost=0.02, net=20.0, shares=10,
                                                     entry_fill=10.0)])
    report = format_report(tmp_path)
    assert "all days: 2 candidate(s), 2 filled" in report


def test_side_by_side_reads_gap_ledgers_own_move_close(tmp_path):
    record_root = tmp_path / "paper_trade"
    gl_root = tmp_path / "gap_ledger"
    _write_record_csv(record_root, "2026-09-29",
                      [_row("AAA", cost=0.01, net=9.0, shares=100, entry_fill=10.1)])
    gl_root.mkdir()
    with (gl_root / "2026-09-29.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(GL_FIELDS))
        writer.writeheader()
        writer.writerow({"date": "2026-09-29", "ticker": "AAA", "group": "candidate",
                         "source": "ibkr", "move_close": "0.0500"})
    lines = side_by_side_lines("2026-09-29", record_root, gl_root)
    assert len(lines) == 1 and "AAA" in lines[0]
    assert "paper from real entry" in lines[0] and "Gap Ledger from the open" in lines[0]
    assert "+5.00%" in lines[0]


def test_side_by_side_with_no_gap_ledger_file_reads_na_never_a_crash(tmp_path):
    record_root = tmp_path / "paper_trade"
    _write_record_csv(record_root, "2026-09-29",
                      [_row("AAA", cost=0.01, net=9.0, shares=100, entry_fill=10.1)])
    lines = side_by_side_lines("2026-09-29", record_root, tmp_path / "nope")
    assert "n/a" in lines[0]
