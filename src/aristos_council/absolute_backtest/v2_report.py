"""ABSOLUTE-BACKTEST-2, stage 3: run the analyses over every extracted industry, check them against the corrected run, and
write the CSVs and ``SUMMARY.md`` under ``backtests_absolute/v2/``. Reads the corrected run; never writes to it."""
from __future__ import annotations

import json
from datetime import date
from multiprocessing import Pool
from pathlib import Path
from typing import Optional

import numpy as np

from . import BACKTEST_DIR, CORRECTED_SUBDIR, PASS_MAX_LUCK, PASS_MIN_EXCESS, SURVIVORSHIP
from . import v2
from .runner import read_csv, write_csv
from .v2_extract import META_FILE, NAMES_FILE

FORM_TITLES = {1: "1 gate alone", 2: "2 gate + cheapest third", 3: "3 gate + absolute verdict",
               4: "4 cheapest third + gross profit (pre-registered)"}


def _work(args):
    slug, path = args
    return v2.analyse_industry(slug, path)


def run_all(out_root: Path, workers: int = 6) -> tuple:
    metas, todo = {}, []
    for child in sorted(out_root.iterdir()):
        if (child / META_FILE).exists() and (child / NAMES_FILE).exists():
            metas[child.name] = json.loads((child / META_FILE).read_text(encoding="utf-8"))
            todo.append((child.name, child / NAMES_FILE))
    with Pool(workers) as pool:
        results = pool.map(_work, todo, chunksize=1)
    return results, metas


# --------------------------------------------------------------------------- #
# the check against the corrected baseline
# --------------------------------------------------------------------------- #
def check_corrected(results, corrected_root: Path) -> dict:
    """Forms 2 and 3: names per round and industry equal to the corrected files; pooled excess and luck equal to its SUMMARY."""
    files = {1: "form1_gate.csv", 2: "form2_cheapest_third.csv", 3: "form3_clears_hurdle.csv"}
    diffs, compared = 0, 0
    for res in results:
        for f, fn in files.items():
            p = corrected_root / res["slug"] / fn
            if not p.exists():
                continue
            theirs = {date.fromisoformat(r["date"]): tuple(sorted(x for x in r["names"].split(";") if x))
                      for r in read_csv(p)}
            for d, mine in zip(res["dates"], res["sel"][f]):
                if d in theirs:
                    compared += 1
                    diffs += int(theirs[d] != mine)
    summ = {(r["form"], r["hold_months"]): r for r in read_csv(corrected_root / "SUMMARY.csv")
            if r["industry"] == "ALL INDUSTRIES"}
    pooled_rows = []
    for f in (1, 2, 3):
        mine = v2.pooled(results, f, "industry")
        theirs = summ[(str(f), "12")]
        pooled_rows.append({"form": f, "ex_mine": mine["ex"], "ex_corrected": float(theirs["ex_basket"]),
                            "luck_mine": mine["luck"], "luck_corrected": float(theirs["luck"])})
    return {"selection_rounds_compared": compared, "selection_rounds_different": diffs, "pooled": pooled_rows}


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #
def pts(x: Optional[float], signed: bool = True) -> str:
    if x is None:
        return "-"
    return f"{x * 100:+.2f}" if signed else f"{x * 100:.2f}"


def pc(x: Optional[float]) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def md_table(header, rows) -> list:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


# --------------------------------------------------------------------------- #
# the tables
# --------------------------------------------------------------------------- #
def table_benchmarks(results, names) -> tuple:
    rows, csv_rows = [], []
    for f in (2, 3):
        for b in v2.BENCHES:
            p = v2.pooled(results, f, b)
            n_pass = 0
            for res in results:
                ex = v2.mean_of(v2.industry_year_excess(res, f, b))
                lk = v2.industry_luck(res, f, b, ex)
                csv_rows.append({"industry": names[res["slug"]], "form": f, "benchmark": v2.BENCH_LABELS[b],
                                 "excess": ex, "luck": lk})
                if ex is not None and lk is not None and ex >= PASS_MIN_EXCESS - 1e-9 and lk < PASS_MAX_LUCK:
                    n_pass += 1
            csv_rows.append({"industry": "ALL INDUSTRIES", "form": f, "benchmark": v2.BENCH_LABELS[b],
                             "excess": p["ex"], "luck": p["luck"]})
            rows.append([FORM_TITLES[f], v2.BENCH_LABELS[b], pts(p["ex"]), pc(p["luck"]), n_pass, p["n_industries"]])
    return rows, csv_rows


def table_h2h(results) -> tuple:
    allrows = [dict(r, slug=res["slug"]) for res in results for r in res["h2h"]]
    spec = [("count", "n", 1.0, "{:+.2f}"), ("average size ($bn)", "size", 1e-9, "{:+.2f}"),
            ("cash yield (points)", "yield", 100.0, "{:+.2f}"), ("12-month return (points)", "ret", 100.0, "{:+.2f}")]
    rows, byyear_rows, csv_rows = [], [], []
    for tag, label in (("", "unmatched"), ("m", "matched (equal sizes)")):
        for title, key, scale, fmt in spec:
            if tag and key == "n":
                continue
            d = v2.dist([None if x is None else x * scale for x in v2.h2h_diffs(allrows, tag, key)])
            rows.append([label, title, d["n"], fmt.format(d["mean"]) if d["mean"] is not None else "-",
                         fmt.format(d["median"]) if d["median"] is not None else "-", pc(d["share_pos"])])
            csv_rows.append({"version": label, "metric": title, "scope": "pooled", **d})
        years = sorted({r["date"].year for r in allrows})
        for y in years:
            sub = [r for r in allrows if r["date"].year == y]
            cells = [label, y, len(sub)]
            for title, key, scale, fmt in spec:
                if tag and key == "n":
                    continue
                d = v2.dist([None if x is None else x * scale for x in v2.h2h_diffs(sub, tag, key)])
                csv_rows.append({"version": label, "metric": title, "scope": str(y), **d})
                if key == "ret":
                    cells += [fmt.format(d["mean"]) if d["mean"] is not None else "-",
                              fmt.format(d["median"]) if d["median"] is not None else "-", pc(d["share_pos"])]
                elif key == "yield":
                    cells.append(fmt.format(d["mean"]) if d["mean"] is not None else "-")
                elif key == "size":
                    cells.append(fmt.format(d["mean"]) if d["mean"] is not None else "-")
            byyear_rows.append(cells)
    return rows, byyear_rows, csv_rows, len(allrows)


def table_bootstrap(results) -> tuple:
    rows, csv_rows = [], []
    for f in v2.FORMS_ALL:
        p = v2.pooled(results, f, "industry")
        ds, vals = v2.monthly_series(results, f)
        bs = v2.block_bootstrap(vals)
        rows.append([FORM_TITLES[f], pts(p["ex"]), pc(p["luck"]), pts(bs["point"]),
                     f"{pts(bs['lo'])} to {pts(bs['hi'])}" if bs["lo"] is not None else "-", bs["n"]])
        csv_rows.append({"form": f, "pooled_excess": p["ex"], "luck": p["luck"], "monthly_point": bs["point"],
                         "lo90": bs["lo"], "hi90": bs["hi"], "months": bs["n"]})
    return rows, csv_rows


def table_regimes(results) -> tuple:
    rows, csv_rows = [], []
    for f in v2.FORMS_ALL:
        cells = [FORM_TITLES[f]]
        for label, y0, y1 in v2.REGIMES:
            yrs = set(range(y0, y1 + 1))
            p = v2.pooled(results, f, "industry", years=yrs)
            cells.append(f"{pts(p['ex'])} / {pc(p['luck'])}")
            csv_rows.append({"form": f, "regime": label, "excess": p["ex"], "luck": p["luck"],
                             "years": ",".join(str(y) for y in sorted(p["years"]) if y in yrs)})
        rows.append(cells)
    return rows, csv_rows


def table_spread(results, names) -> tuple:
    wide_by, narrow_by, left_out, counts = [], [], [], {"wide": 0, "narrow": 0, "undefined": 0}
    for res in results:
        thr = res["spread_threshold"]
        if thr is None:
            left_out.append(names[res["slug"]])
            continue
        sp = dict(res["spreads"])
        w, n = [], []
        for r in res["rounds"][2]:
            s = sp.get(r["date"])
            if s is None:
                counts["undefined"] += 1
                continue
            ex = r["ex"].get("industry") if r["positioned"] else None
            (w if s > thr else n).append((r["date"], ex))
            counts["wide" if s > thr else "narrow"] += int(ex is not None)
        wide_by.append(v2.year_means(w))
        narrow_by.append(v2.year_means(n))
    pw, pn = v2.pool_years(wide_by), v2.pool_years(narrow_by)
    rows = [["wide spread (above the industry's own first-five-year median)", pts(v2.mean_of(pw)), counts["wide"], len(pw)],
            ["narrow spread (at or below it)", pts(v2.mean_of(pn)), counts["narrow"], len(pn)]]
    csv_rows = [{"group": "wide", "excess": v2.mean_of(pw), "positioned_rounds": counts["wide"], "years": len(pw)},
                {"group": "narrow", "excess": v2.mean_of(pn), "positioned_rounds": counts["narrow"], "years": len(pn)}]
    return rows, csv_rows, left_out


def table_form4(results, names) -> tuple:
    f = 4
    p = v2.pooled(results, f, "industry")
    p2 = v2.pooled(results, 2, "industry")
    ds, vals = v2.monthly_series(results, f)
    bs = v2.block_bootstrap(vals)
    status = {"chosen": 0, "fewer than 3 chosen": 0, "no cheap third": 0}
    rounds_all = positioned = 0
    held = []
    for res in results:
        for s, r in zip(res["status4"], res["rounds"][f]):
            status[s] += 1
            rounds_all += 1
            positioned += int(r["positioned"])
            if r["positioned"]:
                held.append(r["n_held"])
    # form 2 on exactly the rounds where form 4 holds
    both = []
    for res in results:
        on = {r["date"] for r in res["rounds"][f] if r["positioned"]}
        both.append(v2.industry_year_excess(res, 2, "industry", keep=lambda d, on=on: d in on))
    p2_same = v2.mean_of(v2.pool_years(both))
    n_ind_pass = 0
    csv_rows = []
    for res in results:
        ex = v2.mean_of(v2.industry_year_excess(res, f, "industry"))
        lk = v2.industry_luck(res, f, "industry", ex)
        npos = sum(1 for r in res["rounds"][f] if r["positioned"])
        csv_rows.append({"industry": names[res["slug"]], "form": 4, "positioned_rounds": npos, "excess": ex, "luck": lk})
        if ex is not None and lk is not None and ex >= PASS_MIN_EXCESS - 1e-9 and lk < PASS_MAX_LUCK:
            n_ind_pass += 1
    csv_rows.append({"industry": "ALL INDUSTRIES", "form": 4, "positioned_rounds": positioned, "excess": p["ex"],
                     "luck": p["luck"]})
    verdict = v2.bar(p["ex"], p["luck"])
    info = {"verdict": verdict, "ex": p["ex"], "luck": p["luck"], "boot": bs, "status": status, "rounds": rounds_all,
            "positioned": positioned, "median_held": float(np.median(held)) if held else None,
            "industries_passing": n_ind_pass, "form2_pooled": p2["ex"], "form2_same_rounds": p2_same,
            "n_industries": p["n_industries"]}
    return info, csv_rows


# --------------------------------------------------------------------------- #
def main(out_root, workers: int = 6) -> None:
    out_root = Path(out_root)
    corrected_root = Path(BACKTEST_DIR) / CORRECTED_SUBDIR
    results, metas = run_all(out_root, workers)
    names = {s: m["industry"] for s, m in metas.items()}
    check = check_corrected(results, corrected_root)
    head = [SURVIVORSHIP, "ABSOLUTE-BACKTEST-2: 12-month hold, every basket and benchmark pays 0.5% round trip. Pooling: by "
            "calendar year of entry inside an industry, then across industries, then across years. Definitions were frozen in "
            "PREREGISTRATION.md before the run."]
    t1, t1csv = table_benchmarks(results, names)
    write_csv(out_root / "benchmarks.csv", head, ["industry", "form", "benchmark", "excess", "luck"], t1csv)
    t2, t2y, t2csv, n_h2h = table_h2h(results)
    write_csv(out_root / "headtohead_summary.csv", head, ["version", "metric", "scope", "n", "mean", "median", "share_pos"],
              t2csv)
    h2h_rows = [{"industry": names[res["slug"]], **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items()}}
                for res in results for r in res["h2h"]]
    if h2h_rows:
        write_csv(out_root / "headtohead_rounds.csv", head, list(h2h_rows[0]), h2h_rows)
    t3, t3csv = table_bootstrap(results)
    write_csv(out_root / "bootstrap.csv", head, ["form", "pooled_excess", "luck", "monthly_point", "lo90", "hi90", "months"], t3csv)
    t4, t4csv = table_regimes(results)
    write_csv(out_root / "regimes.csv", head, ["form", "regime", "excess", "luck", "years"], t4csv)
    t4s, t4scsv, left_out = table_spread(results, names)
    write_csv(out_root / "spread.csv", head, ["group", "excess", "positioned_rounds", "years"], t4scsv)
    f4, f4csv = table_form4(results, names)
    write_csv(out_root / "form4_by_industry.csv", head, ["industry", "form", "positioned_rounds", "excess", "luck"], f4csv)

    L = ["# ABSOLUTE-BACKTEST-2: SUMMARY", "", f"> {SURVIVORSHIP}", "",
         f"{len(results)} industries; 12-month hold; every basket and benchmark pays 0.5% round trip. Excess in points a year, "
         "luck = share of 500 random baskets that did as well (lower is better). Corrected baseline untouched.", "",
         "## Check against the corrected baseline", "",
         f"Forms 1 to 3 rebuilt from the name tables: {check['selection_rounds_compared']} industry-rounds compared with the "
         f"corrected run's files, {check['selection_rounds_different']} differ.", ""]
    L += md_table(["Form", "Excess (this job)", "Excess (corrected)", "Luck (this job)", "Luck (corrected)"],
                  [[r["form"], pts(r["ex_mine"]), pts(r["ex_corrected"]), pc(r["luck_mine"]), pc(r["luck_corrected"])]
                   for r in check["pooled"]])
    L += ["", "## 1. Extra benchmarks (forms 2 and 3)", ""]
    L += md_table(["Form", "Benchmark", "Pooled excess", "Luck", "Industries passing the bar", "Industries"], t1)
    L += ["", f"## 2. Head to head, form 3 minus form 2 ({n_h2h} industry-months where both hold 3 or more names)", ""]
    L += md_table(["Version", "Metric", "Industry-months", "Mean", "Median", "Share positive"], t2)
    L += ["", "By year of entry (return: mean, median, share positive; yield and size: mean of the difference):", ""]
    L += md_table(["Version", "Year", "Rows", "Size ($bn) mean", "Yield (pts) mean", "Return mean", "Return median",
                   "Return share +"], t2y)
    L += ["", "## 3. Block bootstrap of the pooled excess over the industry basket (12-month blocks, 1,000 draws)", ""]
    L += md_table(["Form", "Pooled excess (years basis)", "Luck", "Monthly-basis point", "90% interval", "Months"], t3)
    L += ["", "## 4a. Fixed regimes (excess / luck against the industry basket)", ""]
    L += md_table(["Form", *[r[0] for r in v2.REGIMES]], t4)
    L += ["", "## 4b. Valuation spread: form 2 excess over the industry basket", ""]
    L += md_table(["Rounds", "Form 2 excess", "Positioned industry-rounds", "Years"], t4s)
    L += ["", f"Industries left out (fewer than {v2.SPREAD_MIN_ROUNDS} defined spreads in their first five years): "
          f"{len(left_out)}" + (f" ({'; '.join(left_out)})" if left_out else "") + "."]
    b = f4["boot"]
    L += ["", "## 5. Form 4 (pre-registered)", ""]
    L += md_table(["Measure", "Value"], [
        ["Pooled 12-month excess over the industry basket", pts(f4["ex"]) + " points a year"],
        ["Luck", pc(f4["luck"])],
        ["Bootstrap 90% interval (monthly basis, point " + pts(b["point"]) + ")",
         f"{pts(b['lo'])} to {pts(b['hi'])}" if b["lo"] is not None else "-"],
        ["Industries with a pooled excess at or above 2 points and luck under 5%", f4["industries_passing"]],
        ["Industry-rounds: held / skipped for fewer than 3 chosen / no cheap third",
         f"{f4['positioned']} / {f4['status']['fewer than 3 chosen']} / {f4['status']['no cheap third']} of {f4['rounds']}"],
        ["Median names held", f4["median_held"]],
        ["Form 2, same industry-rounds as form 4 (context)", pts(f4["form2_same_rounds"])],
        ["Form 2, all rounds (context)", pts(f4["form2_pooled"])],
        ["Bar: at least +2.00 points and luck under 5%", f4["verdict"]]])
    (out_root / "SUMMARY.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (out_root / "results.json").write_text(json.dumps({
        "check": check, "benchmarks": t1, "headtohead": t2, "headtohead_by_year": t2y, "bootstrap": t3, "regimes": t4,
        "spread": t4s, "spread_left_out": left_out,
        "form4": {k: v for k, v in f4.items()}}, indent=1, default=str), encoding="utf-8")
    print("\n".join(L))
