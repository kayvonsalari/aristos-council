"""ABSOLUTE-BACKTEST-RECHECK: the published 12-month results against the corrected ones, on a subset of industries.

    python examples/recheck_absolute_subset.py OLD_PASS_DIR FIXED_PASS_DIR [--out backtests_absolute/cuts/recheck]

OLD_PASS_DIR: ``review_absolute_pass.py`` output with the old split rule (reproduces the published files).
FIXED_PASS_DIR: the same with ``--fixed`` (the later-split restatement), for the subset only.

Variants (all 12-month hold, same window, same selection and scoring code as the runner):
  old    published: old split rule, basket pays no cost, persistent random baskets as drawn
  split  the split fix alone
  cost   the cost correction alone, with the luck baseline re-centred (they belong together)
  both   both
The cost correction: the industry basket pays the same 0.5% round trip as the forms. On a 12-month hold this adds exactly the
cost to every excess. The luck correction: each year's 500 random series are re-centred on the pool (a random basket earns the
pool's return minus the cost, so its excess over a basket that also pays the cost is zero); their spread is kept.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from datetime import date
from multiprocessing import Pool
from pathlib import Path

import numpy as np

END = date(2026, 10, 31)
COST = 0.005
BAR_EXCESS, BAR_LUCK, TOL = 0.02, 0.05, 1e-9


def score(dump, slug):
    from aristos_council.absolute_backtest.scoring import RoundInput, luck_test, metrics_for, score_rounds
    from aristos_council.absolute_backtest.selection import NameReading, select_forms
    from aristos_council.backtest import add_months
    dates = [date.fromisoformat(x) for x in sorted(dump["rounds"])]
    out = {}
    scorable = [x for x in dates if add_months(x, 12) <= END]
    for form in (1, 2, 3):
        ins = []
        for x in scorable:
            rows = dump["rounds"][x.isoformat()]
            rd = [NameReading(t, eligible=True, gate_state=g, backtest_state=bt, base_yield=y) for t, g, bt, y, h12, h36 in rows]
            pool = {t: h12 for t, g, bt, y, h12, h36 in rows if h12 is not None}
            ins.append(RoundInput(x, add_months(x, 12), pool, tuple(select_forms(rd)[form]), None, None))
        sc = score_rounds(ins, 12, COST)
        m = metrics_for(sc)
        lk = luck_test(ins, sc, 12, COST, industry_slug=slug, label=f"abs_form{form}_h12", metrics=m)
        out[form] = {"year_ex": dict(m.year_ex_pool), "ex": m.ex_pool, "luck": lk.luck, "years": lk.years, "rand": lk.random_year_means,
                     "median_held": m.median_held, "n_positions": m.n_positions,
                     "median_pool": float(np.median([s.n_pool for s in sc])) if sc else None}
    return out


def work(args):
    slug, old_dir, fixed_dir = args
    old = json.load(gzip.open(Path(old_dir) / f"{slug}.json.gz", "rt", encoding="utf-8"))
    new = json.load(gzip.open(Path(fixed_dir) / f"{slug}.json.gz", "rt", encoding="utf-8"))
    return {"slug": slug, "old": score(old, slug), "new": score(new, slug),
            "no_accounts": new.get("no_accounts", []), "unpriced": new.get("unpriced", []), "members": new.get("members")}


def variant(res, which, cost, recentre):
    """Per form: year_ex, ex, luck, rand after the chosen corrections."""
    src = res[which]
    out = {}
    for f, v in src.items():
        shift = COST if cost else 0.0
        year_ex = {y: x + shift for y, x in v["year_ex"].items()}
        ex = (v["ex"] + shift) if v["ex"] is not None else None
        rand = v["rand"]
        if rand is not None and recentre:
            r = rand.astype(np.float64)
            rand = r - r.mean(axis=1, keepdims=True)
        luck = v["luck"]
        if recentre and rand is not None and ex is not None:
            luck = float(np.mean(rand.mean(axis=0) >= ex - TOL))
        out[f] = {**v, "year_ex": year_ex, "ex": ex, "rand": rand, "luck": luck}
    return out


def passes(ex, luck):
    return ex is not None and luck is not None and ex >= BAR_EXCESS - 1e-6 and luck < BAR_LUCK


def pool(items):
    by_year, rand_by_year = {}, {}
    for v in items:
        for y, x in v["year_ex"].items():
            by_year.setdefault(y, []).append(x)
        if v["rand"] is not None:
            for i, y in enumerate(v["years"]):
                rand_by_year.setdefault(int(y), []).append(v["rand"][i].astype(np.float64))
    yearly = {y: sum(x) / len(x) for y, x in by_year.items()}
    ex = sum(yearly.values()) / len(yearly)
    series = np.mean(np.vstack([np.mean(np.vstack(v), axis=0) for _, v in sorted(rand_by_year.items())]), axis=0)
    return ex, float(np.mean(series >= ex - TOL))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("old_dir")
    ap.add_argument("fixed_dir")
    ap.add_argument("--out", default="backtests_absolute/cuts/recheck")
    ap.add_argument("--subset", default="backtests_absolute/cuts/recheck_subset.json")
    a = ap.parse_args(argv)
    slugs = json.loads(Path(a.subset).read_text(encoding="utf-8"))
    with Pool(6) as p:
        res = p.map(work, [(s, a.old_dir, a.fixed_dir) for s in slugs])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    V = {"old": ("old", False, False), "split": ("new", False, False), "cost": ("old", True, True), "both": ("new", True, True)}
    table = {r["slug"]: {k: variant(r, *args) for k, args in V.items()} for r in res}
    # the published numbers must be reproduced by "old"
    pub = {}
    for r in res:
        d = json.loads(Path("backtests_absolute", r["slug"], "result.json").read_text(encoding="utf-8"))
        pub[r["slug"]] = {x["form"]: x for x in d["rows"] if x["hold_months"] == 12}
    worst = max(abs(table[s]["old"][f]["ex"] - pub[s][f]["ex_basket"]) for s in table for f in (1, 2, 3))
    lines = [f"CHECK: 'old' reproduces the published 12-month excess to within {100 * worst:.4f} points",
             "SUBSET: " + ", ".join(slugs),
             f"UNAVAILABLE (not cached, skipped and counted): {sum(len(r['no_accounts']) for r in res)} of {sum(r['members'] for r in res)} members had no accounts, "
             f"{sum(len(r['unpriced']) for r in res)} no prices"]
    rows = []
    for form in (1, 2, 3):
        lines += ["", f"FORM {form}, 12-month hold. Excess over the industry basket in points a year / luck % / median names held (basket size)"]
        lines.append(f"{'industry':44}{'old':>20}{'split only':>20}{'cost only':>20}{'both':>20}{'both-old':>10}  bar old->new")
        for r in res:
            s = r["slug"]
            cells, row = [], {"industry": s, "form": form}
            for k in V:
                v = table[s][k][form]
                cells.append(f"{100 * v['ex']:+6.2f}/{100 * v['luck']:5.1f}/{v['median_held']:4.1f}" if v["ex"] is not None else "-")
                row.update({f"{k}_ex_pts": round(100 * v["ex"], 3), f"{k}_luck_pct": round(100 * v["luck"], 1), f"{k}_median_names": v["median_held"]})
            d = table[s]["both"][form]["ex"] - table[s]["old"][form]["ex"]
            po, pn = passes(table[s]["old"][form]["ex"], table[s]["old"][form]["luck"]), passes(table[s]["both"][form]["ex"], table[s]["both"][form]["luck"])
            row.update({"both_minus_old_pts": round(100 * d, 3), "bar_old": po, "bar_new": pn})
            rows.append(row)
            lines.append(f"{s:44}" + "".join(f"{c:>20}" for c in cells) + f"{100 * d:>+10.2f}  {'PASS' if po else 'no'} -> {'PASS' if pn else 'no'}")
        cells = []
        for k in V:
            ex, lk = pool([table[s][k][form] for s in table])
            cells.append((ex, lk))
        meds = [np.median([table[s]["old"][form]["median_held"] for s in table]), np.median([table[s]["both"][form]["median_held"] for s in table])]
        lines.append(f"{'POOLED over the subset':44}" + "".join(f"{100 * e:+6.2f}/{100 * l:5.1f}/{m:4.1f}".rjust(20) for (e, l), m in zip(cells, [meds[0], meds[0], meds[0], meds[1]]))
                     + f"{100 * (cells[3][0] - cells[0][0]):>+10.2f}")
        lines.append(f"{'  of which: split fix':44}{100 * (cells[1][0] - cells[0][0]):>+10.2f}   cost + luck centring: {100 * (cells[2][0] - cells[0][0]):+.2f}")
        changed = [r["industry"] for r in rows if r["form"] == form and r["bar_old"] != r["bar_new"]]
        lines.append(f"  industries whose pass/fail changes: {changed or 'none'}")
    (out / "recheck.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with open(out / "recheck.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1:])
