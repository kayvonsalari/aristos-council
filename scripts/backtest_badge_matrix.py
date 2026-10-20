"""BACKTEST-2 item 4b/6 — regenerate ``backtests/SUMMARY.csv`` from the committed per-lens CSVs,
print the results matrix for docs/BACKTEST.md, and stamp the refresh date into backtests/RUN.md.

Rebuilds ``SUMMARY.csv`` from ``backtests/<cohort>/<lens>.csv`` with the SAME row construction as
cell 6 of ``notebooks/aristos_backtest.ipynb`` (BACKTEST-DOCS-14-1: a cohort whose per-lens CSVs
were committed without a notebook re-run had no summary rows), then reads it (never re-runs a
backtest, never touches the network; ``--no-rebuild`` skips the rebuild) and prints a markdown table: one row per cohort, one column per lens, each cell the
raw four-valued verdict as one letter plus the mean annual excess —

    P = proven, L = not beyond Luck, N = Not proven, U = insufficient (Untested)

paste the printed table over the one under "The 14x7 results matrix" in docs/BACKTEST.md. Also
(re)writes ``backtests/RUN.md`` with today's date and the ``SUMMARY.csv`` row count, so a reader
of the committed results can see at a glance when they were last refreshed — see "Refreshing the
results" in docs/BACKTEST.md.

    python -m scripts.backtest_badge_matrix [--summary backtests/SUMMARY.csv] [--no-rebuild]

Run this after every refresh (rerun the notebook, run cell 8, unzip into backtests/, commit) —
never hand-edited, so the doc table and RUN.md can never drift from what is actually committed.
"""
from __future__ import annotations

import argparse
import csv
import glob
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUMMARY = ROOT / "backtests" / "SUMMARY.csv"
DEFAULT_RUN_MD = ROOT / "backtests" / "RUN.md"

VERDICT_LETTER = {"proven": "P", "not beyond luck": "L", "not proven": "N", "insufficient": "U"}
LETTER_MEANING = ("P = proven, L = not beyond luck, N = not proven, U = insufficient "
                  "(untested — too little measured history)")


def cohort_folders_without_summary_rows(backtests_dir, summary_rows: list[dict]) -> list[str]:
    """Cohort folders under ``backtests_dir`` that hold per-lens CSVs but have no row in the
    summary (matched on the cohort name each folder's CSVs declare in their header)."""
    from aristos_council.backtest import from_csv
    have = {r["cohort"] for r in summary_rows}
    missing = []
    for folder in sorted(Path(backtests_dir).iterdir()):
        csvs = sorted(folder.glob("*.csv")) if folder.is_dir() else []
        if csvs and from_csv(str(csvs[0])).cohort not in have:
            missing.append(folder.name)
    return missing


def build_summary_table(backtests_dir):
    """One row per ``<cohort>/<lens>.csv`` under ``backtests_dir``, with the verdict — the row
    construction of notebook cell 6, verbatim, so a rebuild reproduces the notebook's file."""
    import pandas as pd

    from aristos_council.backtest import from_csv, verdict

    OUT = str(backtests_dir)
    main_grid_paths = sorted(p for p in glob.glob(f"{OUT}/*/*.csv") if "size_floor" not in p)
    rows = []
    for path in main_grid_paths:
        r = from_csv(path); s = r.summary
        ordered = sorted(r.rounds, key=lambda rnd: rnd.date)
        rows.append({"cohort": r.cohort, "lens": r.lens_id, "verdict": verdict(r, max_luck=r.max_luck),
                     "mean excess / yr": None if s.mean_annual_excess is None else round(100 * s.mean_annual_excess, 1),
                     # BACKTEST-1B: the plain mean beside the same figure with each round's best-returning
                     # BUY name dropped - a wide gap means the lens is proven on one stock, not a basket.
                     "mean excess / yr, drop best": (None if s.mean_annual_excess_drop_best is None
                                                     else round(100 * s.mean_annual_excess_drop_best, 1)),
                     "years positive": f"{s.years_positive} of {s.years_measured}",
                     "hit rate %": None if s.hit_rate is None else round(100 * s.hit_rate),
                     "rounds held": f"{s.n_positions} of {s.n_rounds}",
                     # BACKTEST-1B: the as-of-size-floor-eligible universe size, first round -> last round
                     # - how thin (or not) the cohort was early versus by the end of the window.
                     "n_eligible (first -> last)": (f"{ordered[0].n_eligible} -> {ordered[-1].n_eligible}"
                                                    if ordered else "n/a"),
                     # SIZE-FLOOR-1 bug report (2026-10-02), symptom 1 - n_ranked is how many of
                     # n_eligible the lens's OWN screen actually kept each round. A SCREENED lens
                     # reading n_ranked == n_eligible on every round (same as a screen-LESS lens
                     # always reads) means its screen confirmed zero fails that round - abstained
                     # on everything, not "nothing failed" - usually thin as-of accounts history
                     # (fewer years survived the 90-day lag cut than a criterion's window needs)
                     # or a missing dated series (e.g. invested_capital for min_roic). Not a code
                     # bug - null != false working as designed - but worth seeing: two lenses
                     # that read identically here are not necessarily colluding, they may both be
                     # ranking the same unscreened list for this cohort this round.
                     "n_ranked (first -> last)": (f"{ordered[0].n_ranked} -> {ordered[-1].n_ranked}"
                                                  if ordered else "n/a"),
                     "worst round %": None if s.worst_round_excess is None else round(100 * s.worst_round_excess, 1),
                     # BACKTEST-1C/1D: does this beat a random picker, or just the bar? Low luck % is good;
                     # chance pass rate is how often a random picker "proves" itself on THIS cohort's own
                     # noise; drop-best vs random > 0 means the lens leans on its single best pick LESS
                     # than random picking does. All three None when random_baskets=0. random_mode says
                     # whether the random pickers were turnover-matched (default) or drawn independently
                     # (1C, kept for comparison) - see docs/BACKTEST.md, Skill versus luck.
                     "luck % (lower is better)": (None if s.luck_pct_mean is None
                                                  else round(100 * s.luck_pct_mean, 1)),
                     "chance pass rate %": (None if s.luck_pct_pass is None
                                            else round(100 * s.luck_pct_pass, 1)),
                     "drop-best vs random": (None if s.drop_best_vs_random is None
                                             else round(100 * s.drop_best_vs_random, 1)),
                     "random_mode": r.random_mode if r.random_baskets else None})
    return pd.DataFrame(rows).sort_values(["cohort", "lens"])


def read_summary(path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def matrix_table(rows: list[dict]) -> str:
    """The markdown table: one row per cohort (in the CSV's own first-seen order), one column per
    lens (same), each cell ``P (+8.1%)`` — or ``—`` when that cohort x lens pair has no row at
    all (should not happen for a complete 14x7 run, but named rather than crashing if it does)."""
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
    parser.add_argument("--no-rebuild", action="store_true",
                        help="read SUMMARY.csv as it is instead of rebuilding it from the per-lens CSVs")
    parser.add_argument("--no-run-md", action="store_true", help="print the matrix only")
    args = parser.parse_args(argv)

    if not args.no_rebuild:
        build_summary_table(Path(args.summary).parent).to_csv(args.summary, index=False)
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
