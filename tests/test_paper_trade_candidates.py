"""PAPER-TRADE-1 — reading Gap Ledger's daily CSV, read-only: direction mapping and the
IBKR-verified filter."""
from __future__ import annotations

import csv
from datetime import date

from aristos_council.paper_trade.candidates import read_ibkr_candidates

DAY = date(2026, 9, 29)
FIELDS = ("date", "ticker", "company", "group", "source", "gap_pct", "ib_gap_pct")


def _write(tmp_path, rows):
    path = tmp_path / f"{DAY.isoformat()}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _row(ticker, *, group="candidate", source="ibkr", gap=0.05, company="Example Co"):
    return {"date": DAY.isoformat(), "ticker": ticker, "company": company, "group": group,
           "source": source, "gap_pct": gap, "ib_gap_pct": gap}


def test_a_gap_up_ibkr_candidate_is_long(tmp_path):
    _write(tmp_path, [_row("AAA", gap=0.05)])
    out = read_ibkr_candidates(DAY, tmp_path)
    assert len(out) == 1 and out[0].ticker == "AAA" and out[0].direction == "long"


def test_a_gap_down_ibkr_candidate_is_short(tmp_path):
    _write(tmp_path, [_row("BBB", gap=-0.07)])
    out = read_ibkr_candidates(DAY, tmp_path)
    assert out[0].direction == "short" and out[0].gap_pct == -0.07


def test_a_yfinance_only_row_is_not_traded(tmp_path):
    _write(tmp_path, [_row("CCC", source="yfinance")])
    assert read_ibkr_candidates(DAY, tmp_path) == []


def test_a_baseline_row_is_never_a_candidate(tmp_path):
    _write(tmp_path, [_row("DDD", group="baseline")])
    assert read_ibkr_candidates(DAY, tmp_path) == []


def test_a_missing_file_is_an_empty_list_not_an_error(tmp_path):
    assert read_ibkr_candidates(DAY, tmp_path) == []


def test_several_candidates_keep_file_order(tmp_path):
    _write(tmp_path, [_row("AAA", gap=0.04), _row("BBB", gap=-0.05), _row("CCC", gap=0.06)])
    out = read_ibkr_candidates(DAY, tmp_path)
    assert [c.ticker for c in out] == ["AAA", "BBB", "CCC"]
