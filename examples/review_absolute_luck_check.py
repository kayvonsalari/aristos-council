"""ABSOLUTE-BACKTEST-REVIEW, part A item 5: is the random baseline of the luck test centred on the pool?

    python examples/review_absolute_luck_check.py PASS_DIR [--out backtests_absolute/cuts/luck_check.txt]

The luck test draws 500 persistent random baskets (``backtest._TurnoverBaskets``) of the same size and turnover as the form.
A random basket drawn without bias from the pool earns, on average, the pool's return minus the cost, so its excess over the
basket is minus the cost (-0.5 points on a 12-month hold). This script measures what the persistent baskets actually earn,
and recomputes the pooled luck of each form against fresh uniform draws of the same size (no persistence), with the same
pooling as ``SUMMARY.csv``. Read-only over the pass dump of ``review_absolute_pass.py``.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import date
from multiprocessing import Pool
from pathlib import Path

import numpy as np

END = date(2026, 10, 31)
COST = 0.005
DRAWS = 500


def work(args):
    slug, pass_dir = args
    from aristos_council.absolute_backtest.scoring import RoundInput, luck_test, metrics_for, score_rounds
    from aristos_council.absolute_backtest.selection import NameReading, select_forms
    from aristos_council.backtest import _year_group_means, add_months
    d = json.load(gzip.open(Path(pass_dir) / f"{slug}.json.gz", "rt", encoding="utf-8"))
    dates = [date.fromisoformat(x) for x in sorted(d["rounds"])]
    scorable = [x for x in dates if add_months(x, 12) <= END]
    rng = np.random.default_rng(abs(hash(slug)) % (2 ** 32))
    out = {"slug": slug}
    for form in (1, 2, 3):
        ins = []
        for x in scorable:
            rows = d["rounds"][x.isoformat()]
            rd = [NameReading(t, eligible=True, gate_state=g, backtest_state=bt, base_yield=y) for t, g, bt, y, h12, h36 in rows]
            pool = {t: h12 for t, g, bt, y, h12, h36 in rows if h12 is not None}
            ins.append(RoundInput(x, add_months(x, 12), pool, tuple(select_forms(rd)[form]), None, None))
        scores = score_rounds(ins, 12, COST)
        m = metrics_for(scores)
        lk = luck_test(ins, scores, 12, COST, industry_slug=slug, label=f"abs_form{form}_h12", metrics=m)
        persistent_cols, fresh_cols, dts = [], [], []
        for ri, s in zip(ins, scores):
            if not s.positioned:
                continue
            vals = np.array(list(ri.pool_returns.values()))
            k = min(s.n_held, len(vals))
            idx = np.argsort(rng.random((DRAWS, len(vals))), axis=1)[:, :k]
            fresh_cols.append(vals[idx].mean(axis=1) - COST - vals.mean())
            dts.append(s.date)
        fresh = _year_group_means(np.column_stack(fresh_cols), dts) if fresh_cols else None
        out[form] = {"ex": m.ex_pool, "years_ex": m.year_ex_pool, "lk_years": lk.years, "lk": lk.random_year_means,
                     "fresh_years": tuple(sorted({x.year for x in dts})), "fresh": fresh}
    return out


def pooled_luck(items, key, ex_key="years_ex"):
    by_year, rand_p, rand_f = {}, {}, {}
    for it in items:
        v = it[key]
        for y, x in v["years_ex"].items():
            by_year.setdefault(y, []).append(x)
        if v["lk"] is not None:
            for i, y in enumerate(v["lk_years"]):
                rand_p.setdefault(int(y), []).append(v["lk"][i].astype(np.float64))
        if v["fresh"] is not None:
            for i, y in enumerate(v["fresh_years"]):
                rand_f.setdefault(int(y), []).append(v["fresh"][i])
    ex = np.mean([np.mean(x) for _, x in sorted(by_year.items())])
    res = {"ex": ex}
    for name, r in (("persistent", rand_p), ("fresh", rand_f)):
        series = np.mean(np.vstack([np.mean(np.vstack(v), axis=0) for _, v in sorted(r.items())]), axis=0)
        res[name] = {"mean": series.mean(), "sd": series.std(), "luck": float(np.mean(series >= ex - 1e-9))}
        if name == "persistent":                    # the persistent null moved to its unbiased centre (minus the cost), spread kept
            res["recentred"] = {"luck": float(np.mean(series - series.mean() - COST >= ex - 1e-9))}
    return res


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("pass_dir")
    ap.add_argument("--out", default="backtests_absolute/cuts/luck_check.txt")
    a = ap.parse_args(argv)
    slugs = sorted(p.name[:-8] for p in Path(a.pass_dir).glob("*.json.gz"))
    with Pool(6) as pool:
        items = pool.map(work, [(s, a.pass_dir) for s in slugs])
    lines = ["LUCK CHECK, pooled over 58 industries, 12-month hold. Random excess over the basket, points a year (an unbiased "
             "random basket earns minus the cost: -0.50).", f"{'form':>5} {'form ex':>8} | {'persistent mean':>16} {'sd':>5} {'luck':>6} | {'fresh mean':>11} {'sd':>5} {'luck':>6}"]
    for form in (1, 2, 3):
        r = pooled_luck(items, form)
        lines.append(f"{form:>5} {100 * r['ex']:>+8.2f} | {100 * r['persistent']['mean']:>+16.2f} {100 * r['persistent']['sd']:>5.2f} "
                     f"{100 * r['persistent']['luck']:>5.1f}% | {100 * r['fresh']['mean']:>+11.2f} {100 * r['fresh']['sd']:>5.2f} {100 * r['fresh']['luck']:>5.1f}% | {100 * r['recentred']['luck']:>13.1f}%")
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1:])
