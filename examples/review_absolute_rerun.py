"""ABSOLUTE-BACKTEST-REVIEW: re-run named industries with and without the later-split restatement, from the day cache.

    python examples/review_absolute_rerun.py SLUG [SLUG ...]

Writes ``backtests_absolute/cuts/rerun/<slug>_{old,fixed}.json`` (the result dict, no per-round files) and prints the
12-month rows side by side. "old" switches the restatement off (``later_split_factor`` returns None, the previous rule);
"fixed" is the code as it stands. Everything else, the data and the thresholds, is identical. No EODHD units are spent
while the 2026-11-04 day cache is present.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from aristos_council.absolute_backtest import evaluate, runner
from aristos_council.absolute_backtest.market import HistoricalBonds

END = date(2026, 10, 31)
OUT = Path("backtests_absolute/cuts/rerun")


def run(slug: str, label: str, bench, bonds):
    original = evaluate.later_split_factor
    if label == "old":
        evaluate.later_split_factor = lambda f, full: None
    try:
        res = runner.run_industry(slug, end=END, out_root=OUT / label, benchmarks=bench, bonds=bonds, write=False)
    finally:
        evaluate.later_split_factor = original
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{slug}_{label}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


def main(slugs):
    bonds = HistoricalBonds(cache_dir=runner._cache_dir())
    bench = runner.Benchmarks.fetch(date(END.year - runner.YEARS - 3, 1, 1), END)
    for slug in slugs:
        old, new = run(slug, "old", bench, bonds), run(slug, "fixed", bench, bonds)
        print(f"\n== {slug}  ({old['members']} members)")
        print(f"{'form':>4} {'hold':>4} | {'ex basket old':>13} {'fixed':>7} | {'luck old':>8} {'fixed':>6} | {'win yrs old':>11} {'fixed':>6} | "
              f"{'names held old':>14} {'fixed':>6}")
        for ro, rn in zip(old["rows"], new["rows"]):
            f = lambda x: "  -  " if x is None else f"{100 * x:+.1f}"
            g = lambda x: "-" if x is None else f"{100 * x:.0f}%"
            print(f"{ro['form']:>4} {ro['hold_months']:>4} | {f(ro['ex_basket']):>13} {f(rn['ex_basket']):>7} | {g(ro['luck']):>8} {g(rn['luck']):>6} | "
                  f"{str(ro['years_positive']) + '/' + str(ro['years_measured']):>11} {str(rn['years_positive']) + '/' + str(rn['years_measured']):>6} | "
                  f"{ro['median_names_held']:>14} {rn['median_names_held']:>6}")


if __name__ == "__main__":
    main(sys.argv[1:])
