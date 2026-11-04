"""ABSOLUTE-BACKTEST-REVIEW, part B support: one pass over the same rounds that keeps what the result files do not.

    python examples/review_absolute_pass.py OUT_DIR [--workers 6] [--only slug ...]

The result files keep each form's chosen names and its scores, not the names that were not chosen. Two of the cuts need them:
the cheapest third WITHOUT the gate needs the base yield of every eligible name, and luck for a subset of rounds needs the
pool each round was drawn from. This script reads every name of every round exactly as the runner does, through the day
cache (no EODHD units while the 2026-11-04 cache is present), with one difference: the gate's verdict is recorded and then
overridden to "qualifies" so that the valuation runs for the gate-excluded names too. It uses the previous split rule (no
restatement of later splits) so that it reproduces the result files; ``--check`` compares form 2 and form 3 with them.

Writes ``OUT_DIR/<slug>.json.gz``: per round date, every eligible name with its real gate state, its backtest-form state, its
base yield and its 12- and 36-month total returns.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from dataclasses import replace
from datetime import date
from multiprocessing import Pool
from pathlib import Path

END = date(2026, 10, 31)


def work(args):
    slug, out_dir = args
    from aristos_council.absolute_backtest import evaluate, runner
    from aristos_council.absolute_backtest.market import HistoricalBonds, Tape
    from aristos_council.absolute_value.definition import load_definition
    from aristos_council.backtest import _size_floor, add_months, load_cohort_members, round_dates
    from aristos_council.data.backtest_feed import MemoAdapter, lookback_start
    from aristos_council.gate.definition import load_gate
    from aristos_council.gate.engine import QUALIFIES, evaluate_gate

    evaluate.later_split_factor = lambda f, full: None                       # the rule the result files were made with
    real, current = {}, [None]
    orig_gate, orig_name = evaluate.evaluate_gate, evaluate.evaluate_name

    def gate(f, d):
        g = evaluate_gate(f, d)
        real[current[0]] = g.state
        return replace(g, state=QUALIFIES)

    def name(ticker, *a, **k):
        current[0] = ticker
        real.pop(ticker, None)
        return orig_name(ticker, *a, **k)

    evaluate.evaluate_gate, evaluate.evaluate_name = gate, name
    members, version, caps = load_cohort_members(slug)
    floor, _ = _size_floor(slug, None, version, caps) if caps else (None, "")
    dates = round_dates(date(END.year - runner.YEARS, END.month, 1), END, 12, 1)
    memo = MemoAdapter(runner.default_feed(), start=lookback_start(dates[0]), end=END)
    bonds = HistoricalBonds(cache_dir=runner._cache_dir())
    tapes, fulls = {}, {}
    for t in members:
        try:
            tapes[t] = Tape(memo.get_price_history(t, start=lookback_start(dates[0]), end=END).bars)
        except Exception:                                                      # noqa: BLE001
            tapes[t] = None
        try:
            fulls[t] = memo.get_fundamentals(t)
        except Exception:                                                      # noqa: BLE001
            fulls[t] = None
    gdef, vdef = load_gate(), load_definition()
    rounds = {}
    for d in dates:
        readings, _ = evaluate.evaluate_round(d, members, memo, fulls, tapes, member_caps=caps, size_floor=floor,
                                              gate_def=gdef, value_def=vdef, bonds=bonds)
        rows = []
        for t, r in zip(members, readings):
            if not r.eligible or tapes.get(t) is None:
                continue
            h12 = tapes[t].total_return(d, add_months(d, 12)) if add_months(d, 12) <= END else None
            h36 = tapes[t].total_return(d, add_months(d, 36)) if add_months(d, 36) <= END else None
            rows.append([t, real.get(t, ""), r.backtest_state, r.base_yield, h12, h36])
        rounds[d.isoformat()] = rows
    out = Path(out_dir) / f"{slug}.json.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump({"slug": slug, "floor": floor, "rounds": rounds}, fh)
    return slug, sum(len(v) for v in rounds.values())


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args(argv)
    from aristos_council.absolute_backtest.runner import list_industries
    slugs = [s for s, _ in list_industries() if not a.only or s in a.only]
    todo = [(s, a.out_dir) for s in slugs if not (Path(a.out_dir) / f"{s}.json.gz").exists()]
    with Pool(a.workers) as pool:
        for slug, n in pool.imap_unordered(work, todo):
            print(f"done {slug}: {n} name-rounds", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
