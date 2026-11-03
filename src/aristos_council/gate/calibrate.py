"""ABSOLUTE-GATE-1 calibration: run the gate over every built cohort member and report AGGREGATES.

    python -m aristos_council.gate calibrate [--budget UNITS] [--limit N]
    python -m aristos_council.gate freeze

``calibrate`` reads each name's fundamentals once (EODHD, 10 units a name, dated statements, through
the same day-cache the backtests use), evaluates the gate, and appends one line per name to a LOCAL
state file under ``data/local/gate_calibration/`` (git-ignored: it holds tickers). It stops cleanly at
``--budget`` and a later run resumes where it stopped. When the universe is finished it writes the
tracked aggregate file ``data/gate_calibration_v1.json`` and ``docs/GATE_CALIBRATION.md``.

Output is AGGREGATES ONLY: per industry counts and shares, percentiles. Never a ticker.

``freeze`` writes the measured boundaries into ``gates/absolute_gate_v1.yaml`` (once) and rewrites
the two output files against the frozen file.

It refuses to start if a market index build or a cohort watcher run happened today: those spend the
same EODHD quota and the owner asked that they never overlap.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ..tools import gate_readings as gr
from . import engine
from .definition import GATES_DIR, DEFAULT_GATE_ID, load_gate

_ROOT = Path(__file__).resolve().parents[3]
COHORTS_DIR = _ROOT / "data" / "local" / "cohorts"
STATE_DIR = _ROOT / "data" / "local" / "gate_calibration"
OUT_JSON = _ROOT / "data" / "gate_calibration_v1.json"
OUT_MD = _ROOT / "docs" / "GATE_CALIBRATION.md"
UNITS_PER_FUNDAMENTALS = 10
THIN_INDUSTRY = 5                      # an industry under this many names is listed but never "excluded wholesale"
ACCRUAL_PERCENTILE, INTENSITY_PERCENTILE = 90, 80
PERCENTILES = (5, 25, 50, 75, 90, 95)

# Exchanges whose Yahoo suffix EODHD answers 404 to (BAS.DE, CRDA.L, 005930.KS, CAR.AX): fetched in EODHD's
# own form. Found on the first calibration pass, where 343 names of exactly these four failed to read.
EODHD_ONLY_SUFFIXES = frozenset({"LSE", "XETRA", "KO", "AU"})

RULE_KEYS = ("earnings_quality", "solvency", "accounting_health", "cash_runway")
WARN_KEYS = ("highly_leveraged", "distress_score", "dividend_cover", "runway_warning")


class StopRun(Exception):
    """A clean stop: budget reached, quota refused, or a same-day build was found."""


# --------------------------------------------------------------------------- #
# same-day checks
# --------------------------------------------------------------------------- #
def same_day_runs(today: Optional[date] = None, root: Path = _ROOT) -> list[str]:
    """Logs of the market index build and of any cohort watcher that were written TODAY (local date)."""
    today = today or date.today()
    candidates = [root / "data" / "local" / "market_index" / "build.log"]
    cohorts = root / "data" / "local" / "cohorts"
    candidates += sorted(cohorts.glob("*watch*.log")) if cohorts.exists() else []
    candidates += sorted((root / "data" / "local").glob("*watch*.log"))
    found = []
    for p in candidates:
        if p.exists() and datetime.fromtimestamp(p.stat().st_mtime).date() == today:
            found.append(str(p.relative_to(root)))
    return found


# --------------------------------------------------------------------------- #
# the universe: union of the latest version of every built cohort
# --------------------------------------------------------------------------- #
def load_universe(cohorts_dir: Path = COHORTS_DIR) -> list[dict]:
    seen: dict[str, dict] = {}
    if not cohorts_dir.exists():
        return []
    for d in sorted(p for p in cohorts_dir.iterdir() if p.is_dir()):
        versions = sorted((v for v in d.iterdir() if re.fullmatch(r"v\d+", v.name)),
                          key=lambda p: int(p.name[1:]))
        if not versions or not (versions[-1] / "members.csv").exists():
            continue
        with open(versions[-1] / "members.csv", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                t = (row.get("ticker") or "").strip()
                if t and t not in seen:
                    seen[t] = {"ticker": t, "yahoo": (row.get("yahoo_ticker") or "").strip() or t,
                               "industry": (row.get("industry") or "").strip() or "(no industry)"}
    return list(seen.values())


# --------------------------------------------------------------------------- #
# one name -> one record (aggregates are built from records; the file never leaves data/local)
# --------------------------------------------------------------------------- #
def record_for(entry: dict, f, definition) -> dict:
    g = engine.evaluate_gate(f, definition)
    ai = gr.abnormal_investment(f)
    rules = {r.key: {"result": r.result, "value": r.value, "note": r.note} for r in g.rules}
    warns = {w.key: {"result": w.result, "value": w.value} for w in g.warnings}
    return {"ticker": entry["ticker"], "industry": entry["industry"], "status": "ok",
            "sector": getattr(f, "sector", None), "state": g.state, "rules": rules,
            "warnings": warns, "abnormal_investment": ai.value}


def _read_records(state_file: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if state_file.exists():
        for line in state_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["ticker"]] = rec
    return out


def collect(universe: list[dict], *, adapter, definition, state_dir: Path, budget: Optional[int],
            limit: Optional[int] = None, say=print, eodhd_form: frozenset = frozenset()) -> dict:
    """Fetch and evaluate every name not already recorded. Stops cleanly at the budget or on a quota
    refusal. ``adapter`` is a caching adapter whose ``misses`` count real provider calls."""
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "records.jsonl"
    done = _read_records(state_file)
    todo = [e for e in universe if e["ticker"] not in done]
    if limit is not None:
        todo = todo[:limit]
    spent = 0
    consecutive_403 = 0
    say(f"universe {len(universe)}; recorded {len(done)}; to do {len(todo)}; budget "
        f"{'none' if budget is None else str(budget) + ' units'}")
    stopped = ""
    with open(state_file, "a", encoding="utf-8") as fh:
        for i, entry in enumerate(todo, 1):
            if budget is not None and spent + UNITS_PER_FUNDAMENTALS > budget:
                stopped = f"budget of {budget} units reached"
                break
            before = adapter.misses
            try:
                # EODHD's own symbol (BAS.XETRA, CRDA.LSE) for the names a first pass could not read in
                # the Yahoo form (BAS.DE, CRDA.L): the provider answers 404 to a suffix it does not use
                f = adapter.get_fundamentals(
                    entry["ticker"] if (entry["ticker"] in eodhd_form
                                        or entry["ticker"].rsplit(".", 1)[-1] in EODHD_ONLY_SUFFIXES)
                    else entry["yahoo"])
                rec = record_for(entry, f, definition)
                consecutive_403 = 0
            except Exception as exc:                                        # noqa: BLE001
                msg = str(exc)
                if "HTTP 402" in msg:
                    stopped = "EODHD refused on quota (HTTP 402)"
                    break
                consecutive_403 = consecutive_403 + 1 if "HTTP 403" in msg else 0
                rec = {"ticker": entry["ticker"], "industry": entry["industry"], "status": "error",
                       "error": type(exc).__name__ + ": " + msg[:120]}
                if consecutive_403 >= 5:
                    stopped = "five HTTP 403 refusals in a row (quota or plan)"
                    break
            spent += UNITS_PER_FUNDAMENTALS if (adapter.misses > before or rec["status"] == "error") else 0
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            done[entry["ticker"]] = rec
            if i % 100 == 0:
                say(f"  {i}/{len(todo)}  units this run {spent}")
    return {"records": done, "units_this_run": spent, "stopped": stopped,
            "remaining": len([e for e in universe if e["ticker"] not in done])}


# --------------------------------------------------------------------------- #
# aggregation (aggregates only; the accrual boundary is a parameter so the freeze can re-apply it)
# --------------------------------------------------------------------------- #
def _pct(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    import numpy as np
    return float(np.percentile(np.array(values, dtype=float), p))


def rederive_state(rec: dict, accrual_boundary: float) -> str:
    """The record's gate state under a given accrual boundary (the boundary is the only threshold that
    is measured, so it is the only one re-applied; every other rule result is as the engine gave it)."""
    if rec["state"] == engine.NOT_COVERED:
        return rec["state"]
    rules = rec["rules"]
    acc = rules["earnings_quality"]
    acc_fail = acc["value"] is not None and acc["value"] > accrual_boundary
    other_fail = any(r["result"] == engine.FAIL for k, r in rules.items() if k != "earnings_quality")
    if acc_fail or other_fail:
        return engine.EXCLUDED
    if acc["value"] is None:
        return engine.NOT_ENOUGH_DATA
    sol = rules["solvency"]
    if sol["result"] == engine.NOT_EVALUATED:
        # a loss-maker is settled by the runway (which must have passed); any other gap is missing data
        if rules["cash_runway"]["result"] != engine.PASS:
            return engine.NOT_ENOUGH_DATA
        if sol.get("note") != engine.LOSS_MAKER_NOTE:
            return engine.NOT_ENOUGH_DATA
    return engine.QUALIFIES


def aggregate(records: dict[str, dict], *, accrual_boundary: float, leverage_warn: float = 6.0) -> dict:
    ok = [r for r in records.values() if r["status"] == "ok"]
    errors = [r for r in records.values() if r["status"] != "ok"]
    accrual = [r["rules"]["earnings_quality"]["value"] for r in ok
               if r["rules"]["earnings_quality"]["value"] is not None]
    leverage = [r["warnings"]["highly_leveraged"]["value"] for r in ok
                if r["warnings"]["highly_leveraged"]["value"] is not None]
    solvency = [r["rules"]["solvency"]["value"] for r in ok if r["rules"]["solvency"]["value"] is not None]
    abn = [r["abnormal_investment"] for r in ok if r["abnormal_investment"] is not None]

    def dist(values):
        return {"n": len(values), **{f"p{p}": _pct(values, p) for p in PERCENTILES}}

    by_industry: dict[str, list[dict]] = {}
    for r in ok:
        by_industry.setdefault(r["industry"], []).append(r)

    def block(rs: list[dict]) -> dict:
        n = len(rs)
        states = [rederive_state(r, accrual_boundary) for r in rs]
        out = {"n": n, "state": {s: sum(1 for x in states if x == s) / n for s in
                                 (engine.EXCLUDED, engine.QUALIFIES, engine.NOT_ENOUGH_DATA, engine.NOT_COVERED)},
               "excluded_by": {}, "not_evaluated": {}, "warnings": {}, "not_applicable": {}}
        for k in RULE_KEYS:
            if k == "earnings_quality":
                fails = sum(1 for r in rs if r["rules"][k]["value"] is not None
                            and r["rules"][k]["value"] > accrual_boundary)
            else:
                fails = sum(1 for r in rs if r["rules"][k]["result"] == engine.FAIL)
            out["excluded_by"][k] = fails / n
            out["not_evaluated"][k] = sum(1 for r in rs if r["rules"][k]["result"] == engine.NOT_EVALUATED) / n
            out["not_applicable"][k] = sum(1 for r in rs if r["rules"][k]["result"] == engine.NOT_APPLICABLE) / n
        for k in WARN_KEYS:
            out["warnings"][k] = sum(1 for r in rs if r["warnings"][k]["result"] == engine.WARN) / n
            out["not_evaluated"][k] = sum(1 for r in rs if r["warnings"][k]["result"] == engine.NOT_EVALUATED) / n
        return out

    industries = {name: block(rs) for name, rs in sorted(by_industry.items())}
    wholesale = []
    for name, b in industries.items():
        if b["n"] >= THIN_INDUSTRY and b["state"][engine.EXCLUDED] > 0.5:
            rule = max(b["excluded_by"], key=lambda k: b["excluded_by"][k])
            wholesale.append({"industry": name, "n": b["n"], "share_excluded": b["state"][engine.EXCLUDED],
                              "rule": rule, "rule_share": b["excluded_by"][rule]})
    return {"names_with_data": len(ok), "names_without_data": len(errors),
            "overall": block(ok) if ok else {}, "industries": industries,
            "accrual_ratio": dist(accrual), "net_debt_to_ebitda": dist(leverage),
            "cash_flow_over_debt": dist(solvency),
            "abnormal_investment": {**dist(abn), "p80": _pct(abn, INTENSITY_PERCENTILE)},
            "measured_accrual_boundary": _pct(accrual, ACCRUAL_PERCENTILE),
            "accrual_boundary_used": accrual_boundary,
            "leverage_warn_above": leverage_warn,
            "wholesale_excluded_industries": wholesale}


# --------------------------------------------------------------------------- #
# outputs
# --------------------------------------------------------------------------- #
def write_json(agg: dict, meta: dict, path: Path = OUT_JSON) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"meta": meta, **agg}, indent=1, sort_keys=False), encoding="utf-8")
    return path


def _p(x: Optional[float], digits: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _share(x: float) -> str:
    return f"{100 * x:.0f}%"


def write_markdown(agg: dict, meta: dict, path: Path = OUT_MD) -> Path:
    o = agg["overall"]
    lines = [
        "# Gate calibration (ABSOLUTE-GATE-1)", "",
        f"Run on {meta['date']}. Universe: {meta['universe_size']} names (the union of the built "
        f"cohort members). Names with accounts: {agg['names_with_data']}; without: "
        f"{agg['names_without_data']}. EODHD units spent: {meta['units_spent']} "
        f"(10 units a fundamentals request). Aggregates only: no company is named on this page.", "",
        "The gate has four rules that can exclude a company and two warnings that never do "
        "(docs/DESIGN_absolute.md, section b). This page says how often each one fires on our names, so "
        "the one threshold that is measured, not argued, can be frozen. It changes no vote and no verdict.", "",
        "## The measured boundaries", "",
        f"- Accrual ratio: the 90th percentile of the {agg['accrual_ratio']['n']} names with the ratio is "
        f"**{_p(agg['measured_accrual_boundary'], 4)}**. Gate version 1 froze that figure as its boundary; "
        f"gate version 2 uses the owner's 0.10 and reports this one for information.",
        f"- Abnormal investment, top fifth: the 80th percentile of the {agg['abnormal_investment']['n']} names "
        f"with the measure is **{_p(agg['abnormal_investment']['p80'], 4)}**. Recorded for phase 2; nothing "
        "uses it yet.", "",
        "## All names", "",
        "| Result | Share of names |", "|---|---|",
    ]
    for key, label in ((engine.EXCLUDED, "Excluded"), (engine.QUALIFIES, "Qualifies"),
                       (engine.NOT_ENOUGH_DATA, "Not enough data"), (engine.NOT_COVERED, "Not covered")):
        lines.append(f"| {label} | {_share(o['state'][key])} |")
    labels = {"earnings_quality": "Earnings quality (accrual ratio)", "solvency": "Solvency (cash flow over debt)",
              "accounting_health": "Accounting health (F-score 0 or 1)", "cash_runway": "Cash runway under 12 months"}
    lines += ["", "## How often each rule fires", "",
              "| Rule | Excludes | Not evaluated | Does not apply |", "|---|---|---|---|"]
    for k in RULE_KEYS:
        lines.append(f"| {labels[k]} | {_share(o['excluded_by'][k])} | {_share(o['not_evaluated'][k])} | "
                     f"{_share(o['not_applicable'][k])} |")
    lines += ["", "| Warning | Fires | Not evaluated |", "|---|---|---|",
              f"| Highly leveraged (above {agg['leverage_warn_above']:.0f} times net debt / EBITDA, or cash flow "
              f"under 12% of debt) | {_share(o['warnings']['highly_leveraged'])} | "
              f"{_share(o['not_evaluated']['highly_leveraged'])} |",
              f"| Distress score low (Z'' under 1.10) | {_share(o['warnings']['distress_score'])} | "
              f"{_share(o['not_evaluated']['distress_score'])} |",
              f"| Dividend cover under 1.5 | {_share(o['warnings']['dividend_cover'])} | "
              f"{_share(o['not_evaluated']['dividend_cover'])} |",
              f"| Cash runway 12 to 24 months | {_share(o['warnings']['runway_warning'])} | "
              f"{_share(o['not_evaluated']['runway_warning'])} |", "",
              "## Distributions", "",
              "| Measure | Names | " + " | ".join(f"{p}th" for p in PERCENTILES) + " |",
              "|---|---|" + "---|" * len(PERCENTILES)]
    for key, label in (("accrual_ratio", "Accrual ratio"), ("net_debt_to_ebitda", "Net debt / EBITDA (times)"),
                       ("cash_flow_over_debt", "Cash flow before working capital / total debt"),
                       ("abnormal_investment", "Abnormal investment")):
        d = agg[key]
        lines.append(f"| {label} | {d['n']} | " + " | ".join(_p(d[f"p{p}"]) for p in PERCENTILES) + " |")
    lines += ["", "## Industries excluded wholesale", ""]
    if agg["wholesale_excluded_industries"]:
        lines += ["More than half of the names in these industries are excluded by the rules. They are "
                  "marked \"not covered by the gate in this release\". The thresholds are not loosened to "
                  "admit them.", "", "| Industry | Names | Excluded | Main rule |", "|---|---|---|---|"]
        for w in agg["wholesale_excluded_industries"]:
            lines.append(f"| {w['industry']} | {w['n']} | {_share(w['share_excluded'])} | "
                         f"{labels[w['rule']]} ({_share(w['rule_share'])}) |")
    else:
        lines.append(f"None. (An industry needs at least {THIN_INDUSTRY} names and more than half of them "
                     "excluded.)")
    lines += ["", "## By industry", "",
              "| Industry | Names | Excluded | Accrual | Solvency | F-score | Runway | Highly leveraged | Cover warning | Not enough data |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for name, b in agg["industries"].items():
        lines.append(
            f"| {name}{' (thin)' if b['n'] < THIN_INDUSTRY else ''} | {b['n']} | {_share(b['state'][engine.EXCLUDED])} | "
            f"{_share(b['excluded_by']['earnings_quality'])} | {_share(b['excluded_by']['solvency'])} | "
            f"{_share(b['excluded_by']['accounting_health'])} | {_share(b['excluded_by']['cash_runway'])} | "
            f"{_share(b['warnings']['highly_leveraged'])} | {_share(b['warnings']['dividend_cover'])} | "
            f"{_share(b['state'][engine.NOT_ENOUGH_DATA])} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# the freeze: write measured values into the gate file, once
# --------------------------------------------------------------------------- #
def _replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise StopRun(f"gate file does not contain exactly one {old!r}; it may already be frozen")
    return text.replace(old, new)


def freeze_gate_file(agg: dict, meta: dict, gate_id: str = DEFAULT_GATE_ID, gates_dir: Path = GATES_DIR) -> Path:
    path = Path(gates_dir) / f"{gate_id}.yaml"
    text = path.read_text(encoding="utf-8")
    boundary = round(agg["measured_accrual_boundary"], 4)
    p80 = round(agg["abnormal_investment"]["p80"], 4)
    text = _replace_once(text, "    exclude_above: 0.10\n", f"    exclude_above: {boundary}\n")
    text = _replace_once(text, "    boundary_status: stand_in   # judgment until measured; replaced by the measured 90th percentile",
                         f"    boundary_status: measured   # the 90th percentile of the accrual ratio, {meta['date']}")
    text = _replace_once(text, "status: stand_in            # becomes `frozen` when the calibration boundaries are written in",
                         "status: frozen")
    text = _replace_once(text, "  warn_above: null            # the measured 80th percentile",
                         f"  warn_above: {p80}            # the measured 80th percentile, {meta['date']}")
    wh = agg["wholesale_excluded_industries"]
    if wh:
        block = "not_covered_industries:\n" + "".join(
            f"  - industry: {json.dumps(w['industry'])}\n    names: {w['n']}\n"
            f"    share_excluded: {w['share_excluded']:.3f}\n    rule: {w['rule']}\n"
            f"    rule_share: {w['rule_share']:.3f}\n" for w in wh)
    else:
        block = "not_covered_industries: []\n"
    text = _replace_once(text, "not_covered_industries: []\n", block)
    text = _replace_once(text, "  date: null\n  universe_size: null\n  units_spent: null\n",
                         f"  date: {meta['date']}\n  universe_size: {meta['universe_size']}\n"
                         f"  units_spent: {meta['units_spent']}\n")
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _default_adapter():
    from ..data.backtest_feed import BacktestFeed
    from ..data.cache import DEFAULT_CACHE_DIR, CachingAdapter
    from ..data.retry import RetryAdapter
    return CachingAdapter(RetryAdapter(BacktestFeed()), cache_dir=DEFAULT_CACHE_DIR, today=date.today())


def _meta(universe: list[dict], state_dir: Path) -> dict:
    spent_file = state_dir / "units.json"
    spent = json.loads(spent_file.read_text(encoding="utf-8")).get("units", 0) if spent_file.exists() else 0
    return {"date": date.today().isoformat(), "universe_size": len(universe), "units_spent": spent,
            "gate": DEFAULT_GATE_ID}


def _add_units(state_dir: Path, units: int) -> None:
    f = state_dir / "units.json"
    total = (json.loads(f.read_text(encoding="utf-8")).get("units", 0) if f.exists() else 0) + units
    f.write_text(json.dumps({"units": total}), encoding="utf-8")


def cmd_calibrate(args) -> int:
    runs = same_day_runs()
    if runs:
        print("STOP: a market index build or watcher run was logged today (" + ", ".join(runs) + "). "
              "Not running; they share the EODHD quota. Run this tomorrow, or after confirming they are done.")
        return 2
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except Exception:                                                            # noqa: BLE001
        pass
    universe = load_universe(Path(args.cohorts))
    if not universe:
        print("no built cohort members found under", args.cohorts)
        return 1
    if args.redo:
        # Re-evaluate every name with the current code. Within the same calendar day the fundamentals are
        # in the day-cache, so this spends no units; on a later day it would refetch (and say so).
        prev = STATE_DIR / "records.jsonl"
        if prev.exists():
            prev.replace(STATE_DIR / "records.previous.jsonl")
    definition = load_gate()
    adapter = _default_adapter()
    eodhd_form = frozenset()
    if args.retry_errors:
        # drop the names that failed to read and fetch them again in EODHD's own symbol form
        recs = _read_records(STATE_DIR / "records.jsonl")
        failed = frozenset(t for t, r in recs.items() if r["status"] != "ok")
        kept = [r for t, r in recs.items() if t not in failed]
        with open(STATE_DIR / "records.jsonl", "w", encoding="utf-8") as fh:
            for r in kept:
                fh.write(json.dumps(r) + chr(10))
        eodhd_form = failed
        print(f"retrying {len(failed)} names that failed to read, in EODHD's own symbol form")
    out = collect(universe, adapter=adapter, definition=definition, state_dir=STATE_DIR,
                  budget=args.budget, limit=args.limit, eodhd_form=eodhd_form)
    _add_units(STATE_DIR, out["units_this_run"])
    meta = _meta(universe, STATE_DIR)
    print(f"units this run {out['units_this_run']}; total {meta['units_spent']}; remaining names {out['remaining']}")
    if out["stopped"]:
        print("stopped:", out["stopped"], "- run again to resume")
    if out["remaining"] == 0 or args.finish:
        agg = aggregate(out["records"], accrual_boundary=definition.accrual_exclude_above,
                        leverage_warn=definition.leverage_warn_above)
        write_json(agg, meta)
        write_markdown(agg, meta)
        print("wrote", OUT_JSON.relative_to(_ROOT), "and", OUT_MD.relative_to(_ROOT))
    return 0


def cmd_freeze(args) -> int:
    if load_gate().frozen:
        print(f"{load_gate().id} is already frozen (its thresholds are the owner's); nothing to freeze. "
              "A new set of thresholds is a new gate version, never an edit.")
        return 0
    universe = load_universe(Path(args.cohorts))
    records = _read_records(STATE_DIR / "records.jsonl")
    missing = [e for e in universe if e["ticker"] not in records]
    if missing:
        print(f"STOP: {len(missing)} names are not yet recorded; finish `calibrate` first.")
        return 2
    meta = _meta(universe, STATE_DIR)
    boundary = _pct([r["rules"]["earnings_quality"]["value"] for r in records.values()
                     if r["status"] == "ok" and r["rules"]["earnings_quality"]["value"] is not None],
                    ACCRUAL_PERCENTILE)
    agg = aggregate(records, accrual_boundary=boundary)
    freeze_gate_file(agg, meta)
    agg = aggregate(records, accrual_boundary=round(boundary, 4))
    write_json(agg, meta)
    write_markdown(agg, meta)
    print(f"frozen: accrual boundary {boundary:.4f}; abnormal investment p80 {agg['abnormal_investment']['p80']:.4f}; "
          f"{len(agg['wholesale_excluded_industries'])} industries excluded wholesale")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aristos_council.gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calibrate", help="run the gate over the built cohort members (10 EODHD units a name)")
    c.add_argument("--budget", type=int, default=None, help="stop after this many EODHD units")
    c.add_argument("--limit", type=int, default=None, help="at most this many names this run")
    c.add_argument("--cohorts", default=str(COHORTS_DIR))
    c.add_argument("--finish", action="store_true", help="write the outputs even if names remain")
    c.add_argument("--retry-errors", action="store_true", dest="retry_errors",
                   help="fetch again, in EODHD's own symbol form, the names that failed to read")
    c.add_argument("--redo", action="store_true",
                   help="re-evaluate every name with the current code (free from the day-cache)")
    c.set_defaults(fn=cmd_calibrate)
    f = sub.add_parser("freeze", help="write the measured boundaries into the gate file (once)")
    f.add_argument("--cohorts", default=str(COHORTS_DIR))
    f.set_defaults(fn=cmd_freeze)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except StopRun as exc:
        print("STOP:", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
