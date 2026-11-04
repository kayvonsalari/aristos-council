"""SUMMARY.csv and COUNTS.csv from the finished industries, with the overall rows.

The overall row for a form and hold averages the industries' yearly excess (each industry counts once in a year it
has positions), then averages the years. Its luck score is the same construction applied to every one of the 500
random series, so it asks how often a random picker, in every industry at once, did as well.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np

from ..backtest import PROOF_TOLERANCE
from . import BACKTEST_DIR, FORM_NAMES, FORMS, HOLDS, SURVIVORSHIP
from .runner import COUNTS_FILE, DONE_FILE, LUCK_FILE, RESULT_FILE, read_csv, write_csv
from .scoring import FormMetrics, bar_verdict

SUMMARY_COLUMNS = ["industry", "form", "form_name", "hold_months", "verdict", "n_rounds", "n_positions",
                   "years_measured", "winning_years", "mean_form_return", "mean_basket_return", "ex_basket", "ex_index",
                   "ex_cash", "luck", "drop_best", "drop_best_vs_random", "share_rounds_under_4", "median_names_held",
                   "median_qualifying", "share_names_filing_dates", "share_decisions_filing_dates"]


def load_results(out_root=BACKTEST_DIR) -> list[dict]:
    out = []
    for p in sorted(Path(out_root).glob(f"*/{DONE_FILE}")):
        r = p.parent / RESULT_FILE
        if r.exists():
            res = json.loads(r.read_text(encoding="utf-8"))
            res["_folder"] = str(p.parent)
            out.append(res)
    return out


def _row_of(res: dict, form: int, hold: int) -> Optional[dict]:
    return next((r for r in res["rows"] if r["form"] == form and r["hold_months"] == hold), None)


def _median_qualifying(folder: str, form: int) -> Optional[float]:
    try:
        rows = read_csv(Path(folder) / COUNTS_FILE)
    except OSError:
        return None
    vals = [float(r[f"form{form}"]) for r in rows if r.get(f"form{form}") not in (None, "")]
    return float(np.median(vals)) if vals else None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def industry_rows(res: dict) -> list[dict]:
    out = []
    for form in FORMS:
        med = _median_qualifying(res["_folder"], form)
        for hold in HOLDS:
            r = _row_of(res, form, hold)
            if r is None:
                continue
            row = {"industry": res["industry"], **{k: r.get(k) for k in SUMMARY_COLUMNS if k in r},
                   "winning_years": f"{r['years_positive']} of {r['years_measured']}", "median_qualifying": med,
                   "share_names_filing_dates": res.get("names_with_filing_dates"),
                   "share_decisions_filing_dates": res.get("decisions_on_filing_date")}
            out.append(row)
    return out


def overall_row(results: list[dict], form: int, hold: int) -> Optional[dict]:
    key = f"f{form}_h{hold}"
    usable = [r for r in results if _row_of(r, form, hold) and r["year_excess"].get(key)]
    if not usable:
        return None

    def over_years(comp: str) -> tuple[dict, Optional[float]]:
        by_year: dict[str, list] = {}
        for r in usable:
            for y, v in r["year_excess"][key].get(comp, {}).items():
                by_year.setdefault(y, []).append(v)
        yearly = {y: sum(v) / len(v) for y, v in sorted(by_year.items())}
        return yearly, _mean(list(yearly.values()))

    pool_y, ex_pool = over_years("pool")
    _, ex_index = over_years("index")
    _, ex_cash = over_years("cash")
    _, ex_drop = over_years("drop_best")
    rows = [_row_of(r, form, hold) for r in usable]
    # luck: the same pooling applied to every random series
    rand_by_year: dict[int, list] = {}
    n_series = None
    for r in usable:
        p = Path(r["_folder"]) / LUCK_FILE
        if not p.exists():
            continue
        z = np.load(p)
        if f"{key}_rand" not in z.files:
            continue
        years, mat = z[f"{key}_years"], z[f"{key}_rand"]
        n_series = mat.shape[1]
        for i, y in enumerate(years):
            rand_by_year.setdefault(int(y), []).append(mat[i].astype(np.float64))
    luck = None
    if rand_by_year and ex_pool is not None:
        per_year = [np.mean(np.vstack(v), axis=0) for _, v in sorted(rand_by_year.items())]
        overall_series = np.mean(np.vstack(per_year), axis=0)
        luck = float(np.mean(overall_series >= ex_pool - PROOF_TOLERANCE))
    m = FormMetrics(years_measured=len(pool_y), n_positions=sum(x["n_positions"] for x in rows), ex_pool=ex_pool)
    verdict = bar_verdict(m, luck) if hold == 12 else "reported only"
    n_names = [x["share_rounds_under_4"] for x in rows]
    return {"industry": "ALL INDUSTRIES", "form": form, "form_name": FORM_NAMES[form], "hold_months": hold,
            "verdict": verdict, "n_rounds": sum(x["n_rounds"] for x in rows), "n_positions": m.n_positions,
            "years_measured": m.years_measured,
            "winning_years": f"{sum(1 for v in pool_y.values() if v > 0)} of {len(pool_y)}",
            "mean_form_return": _mean([x["mean_form_return"] for x in rows]),
            "mean_basket_return": _mean([x["mean_basket_return"] for x in rows]),
            "ex_basket": ex_pool, "ex_index": ex_index, "ex_cash": ex_cash, "luck": luck, "drop_best": ex_drop,
            "share_rounds_under_4": _mean(n_names), "median_names_held": _mean([x["median_names_held"] for x in rows]),
            "median_qualifying": _mean([_median_qualifying(r["_folder"], form) for r in usable]),
            "share_names_filing_dates": _mean([r.get("names_with_filing_dates") for r in results]),
            "share_decisions_filing_dates": _mean([r.get("decisions_on_filing_date") for r in results])}


def build(out_root=BACKTEST_DIR) -> dict:
    """Write SUMMARY.csv and COUNTS.csv; return ``{"rows", "overall", "passes"}``."""
    out_root = Path(out_root)
    results = load_results(out_root)
    if not results:
        return {"rows": [], "overall": [], "passes": {}}
    rows, overall = [], []
    for form in FORMS:
        for hold in HOLDS:
            o = overall_row(results, form, hold)
            if o:
                overall.append(o)
    for res in results:
        rows.extend(industry_rows(res))
    head = [SURVIVORSHIP,
            f"{len(results)} industries. Gate {results[0]['gate']}, valuation {results[0]['valuation']}. "
            "Excess columns are average yearly excess (a 36-month hold is annualised) of the form's equal-weight "
            "basket over: ex_basket the equal-weight basket of all the industry's members with data that month, "
            "ex_index the world index fund, ex_cash the US 3-month bill. luck is the share of 500 random baskets of "
            "the same size and turnover that did as well (lower is better). The pass bar (12-month hold) is 2 points "
            "a year over the industry basket with luck under 5%; the 36-month hold is reported without pass or fail."]
    write_csv(out_root / "SUMMARY.csv", head, SUMMARY_COLUMNS, overall + rows)
    counts = []
    for res in results:
        for r in read_csv(Path(res["_folder"]) / COUNTS_FILE):
            counts.append({"industry": res["industry"], **r})
    if counts:
        cols = ["industry"] + [c for c in counts[0] if c != "industry"]
        write_csv(out_root / "COUNTS.csv", head[:1], cols, counts)
    passes = {f: [r["industry"] for r in rows if r["form"] == f and r["hold_months"] == 12
                  and r["verdict"] == "passes the bar"] for f in FORMS}
    return {"rows": rows, "overall": overall, "passes": passes}


def _pct(x, signed=True):
    if x is None or x == "":
        return "-"
    x = float(x) * 100
    return f"{x:+.1f}" if signed else f"{x:.1f}"


def text_table(overall: list[dict]) -> list[str]:
    lines = [f"{'form':<34}{'hold':>5}  {'verdict':<22}{'ex basket':>10}{'ex index':>9}{'ex cash':>8}{'luck %':>7}"
             f"{'drop best':>10}{'win yrs':>9}{'<4 names %':>11}"]
    for o in overall:
        lines.append(f"{o['form_name']:<34}{o['hold_months']:>5}  {o['verdict']:<22}{_pct(o['ex_basket']):>10}"
                     f"{_pct(o['ex_index']):>9}{_pct(o['ex_cash']):>8}{_pct(o['luck'], False):>7}"
                     f"{_pct(o['drop_best']):>10}{o['winning_years']:>9}{_pct(o['share_rounds_under_4'], False):>11}")
    return lines
