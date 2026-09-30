"""BACKTEST-2 item 4b/6 — regenerate the 13x5 results matrix for docs/BACKTEST.md, and stamp the
refresh date into backtests/RUN.md.

Reads the committed ``backtests/SUMMARY.csv`` (never re-runs a backtest, never touches the
network) and prints a markdown table: one row per cohort, one column per lens, each cell the
raw four-valued verdict as one letter plus the mean annual excess —

    P = proven, L = not beyond Luck, N = Not proven, U = insufficient (Untested)

paste the printed table over the one under "The 13x5 results matrix" in docs/BACKTEST.md. Also
(re)writes ``backtests/RUN.md`` with today's date and the ``SUMMARY.csv`` row count, so a reader
of the committed results can see at a glance when they were last refreshed — see "Refreshing the
results" in docs/BACKTEST.md.

    python -m scripts.backtest_badge_matrix [--summary backtests/SUMMARY.csv]

Run this after every refresh (rerun the notebook, run cell 8, unzip into backtests/, commit) —
never hand-edited, so the doc table and RUN.md can never drift from what is actually committed.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUMMARY = ROOT / "backtests" / "SUMMARY.csv"
DEFAULT_RUN_MD = ROOT / "backtests" / "RUN.md"

VERDICT_LETTER = {"proven": "P", "not beyond luck": "L", "not proven": "N", "insufficient": "U"}
LETTER_MEANING = ("P = proven, L = not beyond luck, N = not proven, U = insufficient "
                  "(untested — too little measured history)")


def read_summary(path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def matrix_table(rows: list[dict]) -> str:
    """The markdown table: one row per cohort (in the CSV's own first-seen order), one column per
    lens (same), each cell ``P (+8.1%)`` — or ``—`` when that cohort x lens pair has no row at
    all (should not happen for a complete 13x5 run, but named rather than crashing if it does)."""
    cohorts: list[str] = []
    lenses: list[str] = []
    cells: dict[tuple[str, str], str] = {}
    for r in rows:
        cohort, lens = r["cohort"], r["lens"]
        if cohort not in cohorts:
            cohorts.append(cohort)
        if lens not in lenses:
            lenses.append(lens)
        letter = VERDICT_LETTER.get(r["verdict"], "?")
        excess = r.get("mean excess / yr", "").strip()
        cells[(cohort, lens)] = f"{letter} ({excess}%)" if excess else letter

    head = "| Cohort | " + " | ".join(lenses) + " |"
    sep = "|---|" + "---|" * len(lenses)
    body = [
        "| " + cohort + " | " + " | ".join(cells.get((cohort, lens), "—") for lens in lenses) + " |"
        for cohort in cohorts
    ]
    return "\n".join([head, sep, *body])


def write_run_md(path: Path, *, n_rows: int, today: date) -> None:
    path.write_text(
        f"# Backtest results — last refreshed\n\n"
        f"Refreshed **{today.isoformat()}** — {n_rows} cohort x lens result(s) in `SUMMARY.csv`.\n\n"
        f"Regenerate this file, and the matrix in `docs/BACKTEST.md`, with:\n\n"
        f"```\npython -m scripts.backtest_badge_matrix\n```\n\n"
        f"See docs/BACKTEST.md \"Refreshing the results\" for the full refresh path (rerun the "
        f"notebook, run cell 8, unzip into `backtests/`, commit).\n",
        encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY),
                        help=f"path to SUMMARY.csv (default {DEFAULT_SUMMARY})")
    parser.add_argument("--run-md", default=str(DEFAULT_RUN_MD),
                        help=f"where to write the refresh-date stamp (default {DEFAULT_RUN_MD})")
    parser.add_argument("--no-run-md", action="store_true", help="print the matrix only")
    args = parser.parse_args(argv)

    rows = read_summary(args.summary)
    print(LETTER_MEANING)
    print()
    print(matrix_table(rows))
    if not args.no_run_md:
        write_run_md(Path(args.run_md), n_rows=len(rows),
                    today=datetime.now(timezone.utc).date())
        print(f"\nWrote {args.run_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
