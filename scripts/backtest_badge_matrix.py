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
import re
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUMMARY = ROOT / "backtests" / "SUMMARY.csv"
DEFAULT_RUN_MD = ROOT / "backtests" / "RUN.md"
DEFAULT_RECORD = ROOT / "docs" / "BACKTEST_RECORD.md"
STRATEGIES_DIR = ROOT / "strategies"

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


# --------------------------------------------------------------------------- #
# docs/BACKTEST_RECORD.md - every number in the record is GENERATED between <!-- GEN:x --> markers
# --------------------------------------------------------------------------- #
VERDICT_WORDS = {"proven": "passed the test", "not beyond luck": "passed the bar, luck not ruled out",
                 "not proven": "did not clear the bar", "insufficient": "untested"}
_GEN = re.compile(r"(<!-- GEN:(\w+) -->)(.*?)(<!-- /GEN:\2 -->)", re.S)


def lens_names() -> dict:
    """{lens id: display name}, read from the strategy files - never typed here."""
    import yaml
    out = {}
    for path in STRATEGIES_DIR.glob("*.yaml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:                                   # noqa: BLE001 - a bad file is just unnamed
            continue
        if isinstance(data, dict) and data.get("display_name"):
            out[path.stem] = str(data["display_name"])
    return out


def _short(cohort: str) -> str:
    _, sep, tail = cohort.partition(" - ")
    return tail if sep else cohort


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _signed(x: float) -> str:
    return f"{x:+.1f}"


def counts_block(rows: list[dict]) -> str:
    n = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICT_WORDS}
    lenses = {r["lens"] for r in rows}
    cohorts = {r["cohort"] for r in rows}
    return (f"{len(lenses)} lenses were tested in {len(cohorts)} industry groups, {len(rows)} pairs in "
            f"all. {n['proven']} passed the test, {n['not beyond luck']} passed the bar but luck is not "
            f"ruled out, {n['not proven']} did not clear the bar, and {n['insufficient']} could not be "
            f"judged.")


def lens_table_block(rows: list[dict]) -> str:
    names = lens_names()
    lenses = sorted({r["lens"] for r in rows}, key=lambda l: names.get(l, l))
    out = ["| Lens | Passed the test | Passed the bar, luck not ruled out | Did not clear the bar | "
           "Untested | Average excess, %/yr (judged pairs) | Where it passed the test |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    for lens in lenses:
        mine = [r for r in rows if r["lens"] == lens]
        c = {v: sum(1 for r in mine if r["verdict"] == v) for v in VERDICT_WORDS}
        judged = [_f(r["mean excess / yr"]) for r in mine if r["verdict"] != "insufficient"]
        judged = [x for x in judged if x is not None]
        avg = _signed(sum(judged) / len(judged)) + (f" ({len(judged)} groups)" if len(judged) < 14 else "") \
            if judged else "n/a"
        passed = ", ".join(f"{_short(r['cohort'])} {_signed(_f(r['mean excess / yr']))}"
                           for r in mine if r["verdict"] == "proven") or "none"
        out.append(f"| {names.get(lens, lens)} | {c['proven']} | {c['not beyond luck']} | "
                   f"{c['not proven']} | {c['insufficient']} | {avg} | {passed} |")
    return "\n".join(out)


def industry_block(rows: list[dict]) -> str:
    """Per cohort: each pair that cleared the bar, with its drop-best comparison (the exclusion
    method is stated once, in docs/BACKTEST.md); cohorts with nothing to show are listed last."""
    names = lens_names()
    out, quiet = [], []
    for cohort in sorted({r["cohort"] for r in rows}):
        mine = [r for r in rows if r["cohort"] == cohort]
        cleared = [r for r in mine if r["verdict"] in ("proven", "not beyond luck")]
        if not cleared:
            untested = sum(1 for r in mine if r["verdict"] == "insufficient")
            quiet.append(f"{_short(cohort)} ({untested} of {len(mine)} untested)")
            continue
        parts = []
        for r in cleared:
            d, vs = _f(r["mean excess / yr, drop best"]), _f(r["drop-best vs random"])
            tail = ""
            if d is not None and vs is not None:
                tail = f"; without its best pick each year {_signed(d)}, {_signed(vs)} against random baskets"
            tag = "passed the test" if r["verdict"] == "proven" else "luck not ruled out"
            parts.append(f"{names.get(r['lens'], r['lens'])} {_signed(_f(r['mean excess / yr']))} "
                         f"({tag}{tail})")
        out.append(f"- **{_short(cohort)}**: " + "; ".join(parts) + ".")
    out.append("")
    out.append("No lens cleared the bar in: " + "; ".join(quiet) + ".")
    return "\n".join(out)


def evidence_block(backtests_dir) -> str:
    from aristos_council.backtest import evidence_summary
    ev = evidence_summary(backtests_dir)
    return f"{ev.sentence()}\n\n{ev.calibration_line()}"


def overlap_block(backtests_dir) -> str:
    """CONC-READ-1: pairs that cleared the bar together in a cohort, and whether they are one result."""
    from aristos_council.backtest import overlap_report
    names = lens_names()
    pairs = overlap_report(backtests_dir)
    if not pairs:
        return "No two lenses cleared the bar together in the same industry group."
    out = []
    for ov in sorted(pairs, key=lambda o: -o.mean_jaccard):
        a, b = names.get(ov.lens_a, ov.lens_a), names.get(ov.lens_b, ov.lens_b)
        line = (f"- {_short(ov.cohort)}: {a} and {b} - mean overlap {ov.mean_jaccard:.0%}, identical "
                f"baskets in {ov.identical_rounds} of {ov.shared_rounds} months")
        if ov.counts_as_one:
            line += (f". These two results share {ov.mean_jaccard:.0%} of their picks and count as one "
                     f"result, not two.")
        out.append(line)
    return "\n".join(out)


def record_blocks(rows: list[dict], backtests_dir) -> dict:
    return {"EVIDENCE": evidence_block(backtests_dir), "COUNTS": counts_block(rows),
            "LENSTABLE": lens_table_block(rows), "INDUSTRY": industry_block(rows),
            "OVERLAP": overlap_block(backtests_dir)}


def fill_record(text: str, blocks: dict) -> str:
    """Replace the body of every ``<!-- GEN:x -->`` block; a block with no generator is an error."""
    def one(m):
        if m.group(2) not in blocks:
            raise KeyError(f"no generator for GEN:{m.group(2)}")
        return f"{m.group(1)}\n{blocks[m.group(2)]}\n{m.group(4)}"
    return _GEN.sub(one, text)


def update_record(path: Path, rows: list[dict], backtests_dir) -> None:
    path.write_text(fill_record(path.read_text(encoding="utf-8"), record_blocks(rows, backtests_dir)),
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
    parser.add_argument("--record", default=str(DEFAULT_RECORD),
                        help=f"docs/BACKTEST_RECORD.md whose GEN blocks are rewritten (default {DEFAULT_RECORD})")
    parser.add_argument("--no-record", action="store_true", help="leave the record document alone")
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
    if not args.no_record and Path(args.record).exists():
        update_record(Path(args.record), rows, Path(args.summary).parent)
        print(f"Wrote the generated blocks of {args.record}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
