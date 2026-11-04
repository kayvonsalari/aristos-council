"""ABSOLUTE-BACKTEST-REVIEW, part B: three cuts of the existing 12-month results.

    python examples/review_absolute_cuts.py PASS_DIR [--workers 6] [--out backtests_absolute/cuts]

``PASS_DIR`` is the output of ``review_absolute_pass.py`` (every eligible name of every round with its gate state, base
yield and returns). Nothing is evaluated here: the forms' names are rebuilt with the runner's own ``select_forms`` and scored
with the runner's own ``score_rounds`` / ``metrics_for`` / ``luck_test``. First the full 12-month result of each industry is
rebuilt and compared with ``result.json`` (and the chosen names with the form files); the cuts are printed only with that check.

Cut 1: rounds entered 2016 to 2020 against 2021 to 2025, all three forms.
Cut 2: forms 2 and 3 on rounds that held at least 4 names, and on the industries where at least 80% of rounds did.
Cut 3: the cheapest third of ALL eligible names (no gate) against form 2 (the cheapest third of the gate-passers).

Excess is the yearly average over the industry's equal-weight basket, pooled the way ``SUMMARY.csv`` pools it (each
industry counts once in a year, then the years are averaged); luck is the share of 500 random baskets of the same size and
turnover, from the same pool, that did as well, pooled the same way.
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
SPLIT_YEAR = 2021
FORMS = (1, 2, 3)


def _read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(line for line in fh if not line.startswith("#")))


def work(args):
    slug, pass_dir, root = args
    from aristos_council.absolute_backtest import FORM_FILES
    from aristos_council.absolute_backtest.scoring import RoundInput, luck_test, metrics_for, score_rounds
    from aristos_council.absolute_backtest.selection import NameReading, cheapest_third, select_forms
    from aristos_council.backtest import add_months

    with gzip.open(Path(pass_dir) / f"{slug}.json.gz", "rt", encoding="utf-8") as fh:
        dump = json.load(fh)
    folder = Path(root) / slug
    stored_rows = {f: {r["date"]: r for r in _read_csv(folder / FORM_FILES[f])} for f in FORMS}
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    dates = sorted(date.fromisoformat(d) for d in dump["rounds"])
    scorable = [d for d in dates if add_months(d, 12) <= END]

    def num(x):
        return float(x) if x not in (None, "") else None

    chosen, inputs_by = {}, {k: [] for k in ("f1", "f2", "f3", "nogate")}
    name_match = {2: [0, 0], 3: [0, 0]}
    removed_by_gate = []                      # (12m return of a name the gate removed from the cheapest third, basket 12m return)
    kept_by_gate = []
    for d in scorable:
        rows = dump["rounds"][d.isoformat()]
        readings = [NameReading(t, eligible=True, gate_state=g, backtest_state=bt, base_yield=y) for t, g, bt, y, h12, h36 in rows]
        sel = select_forms(readings)
        everyone = cheapest_third(readings)            # the same cheapness rule on every eligible name, gate ignored
        pool = {t: h12 for t, g, bt, y, h12, h36 in rows if h12 is not None}
        idx, cash = num(stored_rows[1][d.isoformat()].get("index_ann_h12")), num(stored_rows[1][d.isoformat()].get("cash_ann_h12"))
        ex = add_months(d, 12)
        for key, names in (("f1", sel[1]), ("f2", sel[2]), ("f3", sel[3]), ("nogate", everyone)):
            inputs_by[key].append(RoundInput(d, ex, pool, tuple(names), idx, cash))
        for f in (2, 3):
            stored = stored_rows[f][d.isoformat()]["names"]
            name_match[f][1] += 1
            name_match[f][0] += (";".join(sel[f]) == stored)
        if pool:
            mean_all = sum(pool.values()) / len(pool)
            for t in set(everyone) - set(sel[2]):
                if t in pool:
                    removed_by_gate.append((pool[t], mean_all))
            for t in set(everyone) & set(sel[2]):
                if t in pool:
                    kept_by_gate.append((pool[t], mean_all))

    def run(key, label, keep=lambda d, s: True):
        inputs, scores = inputs_by[key], score_rounds(inputs_by[key], 12, COST)
        pairs = [(i, s) for i, s in zip(inputs, scores) if keep(i.date, s)]
        if not pairs:
            return None
        ii, ss = [p[0] for p in pairs], [p[1] for p in pairs]
        m = metrics_for(ss)
        lk = luck_test(ii, ss, 12, COST, industry_slug=slug, label=label, metrics=m)
        m.luck = lk.luck
        return {"m": m, "years": lk.years, "rand": lk.random_year_means, "rounds": len(ss),
                "held": [s.n_held for s in ss],
                "ex_index_year": __import__("aristos_council.absolute_backtest.scoring", fromlist=["year_means"]).year_means(ss, "ex_index"),
                "form_year": __import__("aristos_council.absolute_backtest.scoring", fromlist=["year_means"]).year_means(ss, "form_ann"),
                "basket_year": __import__("aristos_council.absolute_backtest.scoring", fromlist=["year_means"]).year_means([s for s in ss if s.positioned], "pool_ann")}

    out = {"slug": slug, "industry": result["industry"], "variants": {}, "name_match": name_match,
           "removed": removed_by_gate, "kept": kept_by_gate}
    for f in FORMS:
        lab = f"abs_form{f}_h12"
        out["variants"][f"full_f{f}"] = run(f"f{f}", lab)
        out["variants"][f"early_f{f}"] = run(f"f{f}", lab, lambda d, s: d.year < SPLIT_YEAR)
        out["variants"][f"late_f{f}"] = run(f"f{f}", lab, lambda d, s: d.year >= SPLIT_YEAR)
        if f in (2, 3):
            out["variants"][f"ge4_f{f}"] = run(f"f{f}", lab, lambda d, s: s.n_held >= 4)
    out["variants"]["nogate_f2"] = run("nogate", "abs_form2_h12")
    stored = {r["form"]: r for r in result["rows"] if r["hold_months"] == 12}
    out["stored"] = {f: {"ex_basket": stored[f]["ex_basket"], "luck": stored[f]["luck"]} for f in FORMS}
    return out


def pooled(variants):
    """(ex_basket, ex_index, luck, winning years of N, years, rounds, positions, median held) over a list of variant dicts."""
    variants = [v for v in variants if v is not None and v["m"].year_ex_pool]
    if not variants:
        return None
    by_year, by_year_idx, rand_by_year = {}, {}, {}
    for v in variants:
        for y, x in v["m"].year_ex_pool.items():
            by_year.setdefault(y, []).append(x)
        for y, x in v["ex_index_year"].items():
            by_year_idx.setdefault(y, []).append(x)
        if v["rand"] is not None:
            for i, y in enumerate(v["years"]):
                rand_by_year.setdefault(int(y), []).append(v["rand"][i].astype(np.float64))
    yearly = {y: sum(x) / len(x) for y, x in sorted(by_year.items())}
    ex = sum(yearly.values()) / len(yearly)
    yearly_idx = {y: sum(x) / len(x) for y, x in sorted(by_year_idx.items())}
    luck = None
    if rand_by_year:
        series = np.mean(np.vstack([np.mean(np.vstack(v), axis=0) for _, v in sorted(rand_by_year.items())]), axis=0)
        luck = float(np.mean(series >= ex - 1e-9))
    held = [h for v in variants for h in v["held"]]
    return {"industries": len(variants), "ex_basket": ex, "ex_index": (sum(yearly_idx.values()) / len(yearly_idx)) if yearly_idx else None,
            "luck": luck, "win": sum(1 for x in yearly.values() if x > 0), "years": len(yearly), "yearly": yearly,
            "rounds": sum(v["rounds"] for v in variants), "positions": sum(v["m"].n_positions for v in variants),
            "median_held": float(np.median(held)) if held else None,
            "share_under_4": sum(1 for h in held if h < 4) / len(held) if held else None}


def pct(x, signed=True):
    return "" if x is None else (f"{100 * x:+.2f}" if signed else f"{100 * x:.1f}")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("pass_dir")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="backtests_absolute/cuts")
    ap.add_argument("--root", default="backtests_absolute")
    a = ap.parse_args(argv)
    slugs = sorted(p.name[:-8] for p in Path(a.pass_dir).glob("*.json.gz"))
    with Pool(a.workers) as pool:
        res = pool.map(work, [(s, a.pass_dir, a.root) for s in slugs])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    lines = []

    # ---- validation: the rebuilt full results against the stored ones
    worst, nm = 0.0, {2: [0, 0], 3: [0, 0]}
    luck_diff = []
    for r in res:
        for f in FORMS:
            v = r["variants"][f"full_f{f}"]
            st = r["stored"][f]
            if v and st["ex_basket"] is not None:
                worst = max(worst, abs(v["m"].ex_pool - st["ex_basket"]))
                if st["luck"] is not None and v["m"].luck is not None:
                    luck_diff.append(abs(v["m"].luck - st["luck"]))
        for f in (2, 3):
            nm[f][0] += r["name_match"][f][0]
            nm[f][1] += r["name_match"][f][1]
    lines.append("VALIDATION (the rebuilt full 12-month results against result.json and the form files)")
    lines.append(f"  largest difference in ex_basket over {len(res)} industries x 3 forms: {100 * worst:.4f} points")
    lines.append(f"  luck: mean absolute difference {np.mean(luck_diff):.4f}, largest {np.max(luck_diff):.4f}")
    lines.append(f"  rounds whose chosen names equal the form file: form 2 {nm[2][0]}/{nm[2][1]}, form 3 {nm[3][0]}/{nm[3][1]}")
    for f in FORMS:
        p = pooled([r["variants"][f"full_f{f}"] for r in res])
        lines.append(f"  pooled form {f}: ex_basket {pct(p['ex_basket'])}  luck {pct(p['luck'], False)}%  (SUMMARY.csv has the same)")

    # ---- cut 1
    cols = ["form", "period", "industries", "rounds", "years", "ex_basket_pts", "ex_index_pts", "luck_pct", "winning_years"]
    rows1, years_rows = [], []
    for f in FORMS:
        for label, key in (("2016-2020 entries", "early"), ("2021-2025 entries", "late"), ("all entries", "full")):
            p = pooled([r["variants"][f"{key}_f{f}"] for r in res])
            rows1.append({"form": f, "period": label, "industries": p["industries"], "rounds": p["rounds"], "years": p["years"],
                          "ex_basket_pts": pct(p["ex_basket"]), "ex_index_pts": pct(p["ex_index"]), "luck_pct": pct(p["luck"], False),
                          "winning_years": f"{p['win']} of {p['years']}"})
        pf = pooled([r["variants"][f"full_f{f}"] for r in res])
        for y, x in pf["yearly"].items():
            years_rows.append({"form": f, "entry_year": y, "ex_basket_pts": pct(x)})
    _write(out / "cut1_by_period.csv", cols, rows1)
    _write(out / "cut1_by_year.csv", ["form", "entry_year", "ex_basket_pts"], years_rows)

    # ---- cut 2
    cols2 = ["form", "restriction", "industries", "rounds", "median_names_held", "share_rounds_under_4_pct", "ex_basket_pts", "luck_pct", "winning_years"]
    rows2 = []
    for f in (2, 3):
        sets = [("all rounds (as published)", [r["variants"][f"full_f{f}"] for r in res]),
                ("rounds holding 4 or more names", [r["variants"][f"ge4_f{f}"] for r in res]),
                ("industries with 4+ names in 80%+ of rounds",
                 [r["variants"][f"full_f{f}"] for r in res
                  if r["variants"][f"full_f{f}"] and r["variants"][f"full_f{f}"]["m"].share_thin is not None
                  and r["variants"][f"full_f{f}"]["m"].share_thin <= 0.2])]
        for label, vs in sets:
            p = pooled(vs)
            rows2.append({"form": f, "restriction": label, "industries": p["industries"], "rounds": p["rounds"],
                          "median_names_held": p["median_held"], "share_rounds_under_4_pct": pct(p["share_under_4"], False),
                          "ex_basket_pts": pct(p["ex_basket"]), "luck_pct": pct(p["luck"], False), "winning_years": f"{p['win']} of {p['years']}"})
    _write(out / "cut2_basket_size.csv", cols2, rows2)

    # ---- cut 3
    cols3 = ["variant", "industries", "rounds", "median_names_held", "ex_basket_pts", "luck_pct", "winning_years"]
    rows3 = []
    for label, key in (("form 2: cheapest third of the gate-passers", "full_f2"), ("cheapest third of all eligible names (no gate)", "nogate_f2")):
        p = pooled([r["variants"][key] for r in res])
        rows3.append({"variant": label, "industries": p["industries"], "rounds": p["rounds"], "median_names_held": p["median_held"],
                      "ex_basket_pts": pct(p["ex_basket"]), "luck_pct": pct(p["luck"], False), "winning_years": f"{p['win']} of {p['years']}"})
    _write(out / "cut3_gate_effect.csv", cols3, rows3)
    removed = [x for r in res for x in r["removed"]]
    kept = [x for r in res for x in r["kept"]]
    extra3 = []
    for label, xs in (("names the gate takes out of the cheapest third", removed), ("names both versions hold", kept)):
        if xs:
            extra3.append(f"  {label}: {len(xs)} name-rounds, mean 12-month return {100 * np.mean([a for a, _ in xs]):.1f}% against the "
                          f"basket's {100 * np.mean([b for _, b in xs]):.1f}% in the same rounds")
    per_ind = []
    for r in res:
        g, n = r["variants"]["full_f2"], r["variants"]["nogate_f2"]
        if g and n:
            per_ind.append({"industry": r["industry"], "form2_pts": pct(g["m"].ex_pool), "nogate_pts": pct(n["m"].ex_pool),
                            "difference_pts": pct(g["m"].ex_pool - n["m"].ex_pool)})
    _write(out / "cut3_by_industry.csv", ["industry", "form2_pts", "nogate_pts", "difference_pts"], per_ind)

    for title, path in (("CUT 1: by period (12-month hold, points a year over the industry basket)", "cut1_by_period.csv"),
                        ("CUT 2: basket size", "cut2_basket_size.csv"), ("CUT 3: the gate's own contribution", "cut3_gate_effect.csv")):
        lines += ["", title] + ["  " + ln for ln in (out / path).read_text(encoding="utf-8").splitlines()]
    lines += extra3
    yy = (out / "cut1_by_year.csv").read_text(encoding="utf-8").splitlines()
    lines += ["", "ENTRY YEAR (form, year, ex_basket)"] + ["  " + ln for ln in yy]
    (out / "cuts_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def _write(path, cols, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main(sys.argv[1:])
