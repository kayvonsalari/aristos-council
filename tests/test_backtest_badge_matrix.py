"""BACKTEST-2 item 4b/6 — the docs-matrix and RUN.md generator, on a small fabricated CSV (never
the real committed backtests/SUMMARY.csv, so this test cannot drift when the results refresh)."""
from __future__ import annotations

import csv
from pathlib import Path

from scripts.backtest_badge_matrix import matrix_table, read_summary, write_run_md

_ROWS = [
    {"cohort": "Materials - Diversified Mining", "lens": "magic_formula_raw_v1",
     "verdict": "proven", "mean excess / yr": "16.0"},
    {"cohort": "Materials - Diversified Mining", "lens": "growth_garp_v2",
     "verdict": "insufficient", "mean excess / yr": "2.5"},
    {"cohort": "Tech - Semiconductors", "lens": "magic_formula_raw_v1",
     "verdict": "not proven", "mean excess / yr": "-7.9"},
    {"cohort": "Tech - Semiconductors", "lens": "growth_garp_v2",
     "verdict": "not beyond luck", "mean excess / yr": "5.7"},
]


def _write_csv(tmp_path: Path) -> Path:
    path = tmp_path / "SUMMARY.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["cohort", "lens", "verdict", "mean excess / yr"])
        writer.writeheader()
        writer.writerows(_ROWS)
    return path


def test_read_summary_round_trips_the_csv(tmp_path):
    path = _write_csv(tmp_path)
    rows = read_summary(path)
    assert rows == _ROWS


def test_matrix_table_letters_and_excess_one_row_per_cohort_one_column_per_lens():
    table = matrix_table(_ROWS)
    lines = table.splitlines()
    assert lines[0] == "| Cohort | magic_formula_raw_v1 | growth_garp_v2 |"
    assert lines[1] == "|---|---|---|"
    assert "| Materials - Diversified Mining | P (16.0%) | U (2.5%) |" in lines
    assert "| Tech - Semiconductors | N (-7.9%) | L (5.7%) |" in lines


def test_a_missing_cohort_lens_pair_is_named_never_padded():
    rows = _ROWS + [{"cohort": "A New Cohort", "lens": "magic_formula_raw_v1",
                     "verdict": "proven", "mean excess / yr": "1.0"}]
    table = matrix_table(rows)
    line = next(ln for ln in table.splitlines() if ln.startswith("| A New Cohort"))
    assert line == "| A New Cohort | P (1.0%) | — |"


def test_write_run_md_states_the_date_and_the_row_count(tmp_path):
    from datetime import date
    path = tmp_path / "RUN.md"
    write_run_md(path, n_rows=65, today=date(2026, 9, 30))
    text = path.read_text(encoding="utf-8")
    assert "2026-09-30" in text and "65 cohort x lens result(s)" in text
    assert "python -m scripts.backtest_badge_matrix" in text


def test_the_real_committed_summary_csv_has_65_rows_and_every_verdict_is_known():
    from scripts.backtest_badge_matrix import DEFAULT_SUMMARY, VERDICT_LETTER
    rows = read_summary(DEFAULT_SUMMARY)
    assert len(rows) == 65
    assert all(r["verdict"] in VERDICT_LETTER for r in rows)
