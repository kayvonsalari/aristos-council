"""ABSOLUTE-BACKTEST-CORRECTED: the published run against the corrected one, from the two SUMMARY.csv / COUNTS.csv files.

    python examples/compare_absolute_corrected.py [BASELINE_DIR] [CORRECTED_DIR]

Writes ``<CORRECTED_DIR>/COMPARISON.md`` (pooled table, pass lists, per-industry 12-month differences, withheld-name counts)
and prints it.
"""
import sys
from pathlib import Path

from aristos_council.absolute_backtest.runner import read_csv

base_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "backtests_absolute")
corr_dir = Path(sys.argv[2] if len(sys.argv) > 2 else "backtests_absolute/corrected")
B, C = read_csv(base_dir / "SUMMARY.csv"), read_csv(corr_dir / "SUMMARY.csv")
f = lambda x: None if x in (None, "") else float(x)
pts = lambda x: "n/a" if x is None else f"{100 * x:+.2f}"
pc = lambda x: "n/a" if x is None else f"{100 * x:.1f}%"
key = lambda r: (r["industry"], int(r["form"]), int(r["hold_months"]))
bi, ci = {key(r): r for r in B}, {key(r): r for r in C}
out = []
out += ["## Pooled over every industry (points a year over the industry basket / luck)", "",
        "| Form | Hold | Baseline excess | Baseline luck | Corrected excess | Corrected luck | Change in excess | Verdict base -> corrected |",
        "|---|---|---|---|---|---|---|---|"]
for form in (1, 2, 3):
    for hold in (12, 36):
        k = ("ALL INDUSTRIES", form, hold)
        b, c = bi.get(k), ci.get(k)
        if not b or not c:
            continue
        d = None if f(b["ex_basket"]) is None or f(c["ex_basket"]) is None else f(c["ex_basket"]) - f(b["ex_basket"])
        out.append(f"| {form} {c['form_name']} | {hold} | {pts(f(b['ex_basket']))} | {pc(f(b['luck']))} | {pts(f(c['ex_basket']))} | "
                   f"{pc(f(c['luck']))} | {pts(d)} | {b['verdict']} -> {c['verdict']} |")
inds = sorted({r["industry"] for r in C if r["industry"] != "ALL INDUSTRIES"})
out += ["", "## Pass lists (12-month hold: 2 points over the basket and luck under 5%)", ""]
for form in (1, 2, 3):
    pb = {i for i in inds if bi.get((i, form, 12), {}).get("verdict") == "passes the bar"}
    pcx = {i for i in inds if ci.get((i, form, 12), {}).get("verdict") == "passes the bar"}
    out.append(f"- **Form {form}**: baseline {len(pb)}, corrected {len(pcx)}.")
    out.append(f"  - corrected list: {', '.join(sorted(pcx)) or 'none'}")
    out.append(f"  - dropped off: {', '.join(sorted(pb - pcx)) or 'none'}; new: {', '.join(sorted(pcx - pb)) or 'none'}")
out += ["", "## Per industry, 12-month hold, forms 2 and 3 (excess pts / luck): baseline -> corrected", "",
        "| Industry | Form 2 baseline | Form 2 corrected | Change | Form 3 baseline | Form 3 corrected | Change |", "|---|---|---|---|---|---|---|"]
for i in inds:
    cells = [i]
    for form in (2, 3):
        b, c = bi.get((i, form, 12)), ci.get((i, form, 12))
        if not b or not c:
            cells += ["-", "-", "-"]
            continue
        eb, ec = f(b["ex_basket"]), f(c["ex_basket"])
        fmt = lambda r, e: "n/a" if e is None else f"{pts(e)} / {pc(f(r['luck']))}" + (" PASS" if r["verdict"] == "passes the bar" else "")
        cells += [fmt(b, eb), fmt(c, ec), "n/a" if eb is None or ec is None else pts(ec - eb)]
    out.append("| " + " | ".join(cells) + " |")
try:
    cb, cc = read_csv(base_dir / "COUNTS.csv"), read_csv(corr_dir / "COUNTS.csv")
    tot = lambda rows, col: sum(int(r[col] or 0) for r in rows)
    out += ["", "## Name-rounds (all industries, all rounds)", "", "| Count | Baseline | Corrected |", "|---|---|---|"]
    for col in ("pool", "no_accounts", "gate_qualifies", "gate_excluded", "split_withheld", "form1", "form2", "form3"):
        out.append(f"| {col} | {tot(cb, col):,} | {tot(cc, col):,} |")
except OSError:
    pass
text = "\n".join(out) + "\n"
(corr_dir / "COMPARISON.md").write_text(text, encoding="utf-8")
print(text)
