#!/usr/bin/env python3
"""Automated acceptance pass — post-send batch + 4C + RAW-1 + 4C-FIX-1.

Covers the SCRIPTABLE subset of acceptance_protocol_2026-07-09.md:
T0 (discovery/visibility), T2 (run + tie note + auto-freeze), T3 (KO cohort
context), T4 (MU divergence + context), T5 (GS sector rationale, flag
silent), T6 (PARA no-data), T7 (GARP v2 membership), T8 (RAW: no screen
exclusions, AMZN ranked-not-excluded), T10 (energy watch hidden/visible).

NOT covered, stays manual (~15 min): T1 visual Strategy-tab check, the four
evidence screenshots (this script saves the equivalent TEXT outputs to
acceptance_out/ for eyeballing), T9 narrator prose judgment.

Run from the repo root, same environment the UI uses:
    python acceptance_check.py
Uses live yfinance (no keys). Writes nothing to the repo except the
gitignored runs/ freeze records and acceptance_out/ (delete after).
"""
from __future__ import annotations

import re
import sys
import traceback
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "acceptance_out"
OUT.mkdir(exist_ok=True)

from aristos_council.company_check import format_company_check, run_company_check
from aristos_council.data.cache import DEFAULT_CACHE_DIR, CachingAdapter
from aristos_council.data.provider import select_market_adapter
from aristos_council.pipeline import run_rank_pipeline

STRATS, UNIS, RUNS = ROOT / "strategies", ROOT / "universes", ROOT / "runs"
RESULTS: list[tuple[str, str, str]] = []


def log(test: str, result: str, note: str = "") -> None:
    RESULTS.append((test, result, note))
    print(f"[{result:>4}] {test}: {note}"[:200])


def adapter():
    return CachingAdapter(select_market_adapter(), cache_dir=DEFAULT_CACHE_DIR,
                          today=date.today())


def check(test: str, fn) -> None:
    try:
        fn()
    except Exception as e:  # noqa: BLE001 — acceptance harness must not die mid-pass
        log(test, "FAIL", f"exception: {e.__class__.__name__}: {e}")
        traceback.print_exc(limit=2)


# ---------- T0: discovery + visibility -------------------------------------
def t0() -> None:
    try:  # true UI path (needs streamlit importable, as on the app machine)
        sys.path.insert(0, str(ROOT))
        from app import list_rank_strategy_options
        visible = {strat.id for (_lbl, _path, strat) in
                   list_rank_strategy_options(STRATS) if getattr(strat, "ui", "") != "hidden"}
    except ModuleNotFoundError:  # loader-equivalent fallback
        from aristos_council.pipeline import load_rank_strategy_from_id
        visible = set()
        for f in STRATS.glob("*.yaml"):
            try:
                st = load_rank_strategy_from_id(f.stem, STRATS)
            except Exception:
                continue  # screens / council-only configs
            if getattr(st, "factors", None) and getattr(st, "ui", "") != "hidden":
                visible.add(st.id)
    expected = {"conservative_plus_v1", "magic_formula_momentum_v1",
                "growth_garp_v2", "magic_formula_raw_v1"}
    hidden_ok = "growth_garp_v1" not in visible and "dividend_aristocrats_v1" not in visible
    if visible == expected and hidden_ok:
        log("T0", "PASS", f"visible set exactly {sorted(visible)}")
    else:
        log("T0", "FAIL", f"visible={sorted(visible)} expected={sorted(expected)}")


# ---------- T2: defensive run — tie note, auto-freeze -----------------------
def t2() -> None:
    before = {p.name for p in RUNS.glob("*_conservative_plus_v1")} if RUNS.exists() else set()
    res = run_rank_pipeline(None, strategy_id="conservative_plus_v1",
                            universe_id="defensive_income_16_v1",
                            universes_dir=UNIS, strategies_dir=STRATS,
                            ranker_only=True, freeze_dir=RUNS, adapter=adapter())
    rows = [f"{r.ticker} {r.verdict} {r.combined}" for r in res.ranked]
    (OUT / "t2_defensive_ranker.txt").write_text(
        "\n".join(rows + [f"EXCLUDED {t}: {why}" for t, why in res.excluded]),
        encoding="utf-8")
    after = {p.name for p in RUNS.glob("*_conservative_plus_v1")}
    froze = bool(after - before)
    # tie ACROSS a verdict boundary in the structured result (the annotation
    # itself is presentation-layer -> T1-class visual check in the UI report)
    boundary_tie = any(a.combined == b.combined and a.verdict != b.verdict
                       for a, b in zip(res.ranked, res.ranked[1:]))
    log("T2", "PASS" if froze else "FAIL",
        f"auto-freeze new record: {froze}; boundary tie present in data: "
        f"{boundary_tie} (if True, confirm the '(=score — tie broken "
        f"alphabetically)' annotation visually in the UI report; if False, "
        f"drift dissolved the MRK/PG tie — ODD, note it)")


# ---------- helper: run a company check, save text, return it ---------------
def cc(ticker: str, strategy: str, reference: str, fname: str) -> str:
    res = run_company_check(ticker, strategy, reference, adapter=adapter(),
                            strategies_dir=STRATS, universes_dir=UNIS,
                            runs_dir=RUNS, today=date.today())
    text = format_company_check(res)
    (OUT / fname).write_text(text, encoding="utf-8")
    return text


# ---------- T3: KO — live cohort context ------------------------------------
def t3() -> None:
    t = cc("KO", "conservative_plus_v1", "defensive_income_16_v1", "t3_KO.txt")
    no_ref = "no reference run available" in t
    payout = re.search(r"max_payout_ratio_fcf\s+observed\s+1\.1", t) or "1.1" in t
    if no_ref:
        log("T3", "FAIL", "STILL 'no reference run available' after T2 froze a "
                          "record — freeze/reader broken in the live path. STOP, "
                          "send t3_KO.txt verbatim.")
    else:
        log("T3", "PASS", f"cohort context present; payout ~1.19 fail visible: {bool(payout)}")


# ---------- T4: MU — divergence flag + context ------------------------------
def t4() -> None:
    t = cc("MU", "magic_formula_momentum_v1", "growth_40_v1", "t4_MU.txt")
    flag = "price diverging" in t
    ctx = "no reference run available" not in t
    m = re.search(r"price diverging: ([+\-]\d+%)", t)
    log("T4", "PASS" if (flag and ctx) else ("ODD" if flag else "FAIL"),
        f"flag={'fires ' + m.group(1) if m else 'ABSENT'}; cohort context={ctx} "
        f"(context may need a growth_40 frozen record — CC's CLI baselines wrote "
        f"them; absent = ODD, investigate runs/)")


# ---------- T5: GS — sector rationale, flag silent ---------------------------
def t5() -> None:
    t = cc("GS", "magic_formula_momentum_v1", "growth_40_v1", "t5_GS.txt")
    gate = "excluded by this strategy" in t
    rationale = "Greenblatt exclusion" in t
    flag_silent = "price diverging" not in t
    ok = gate and rationale and flag_silent
    log("T5", "PASS" if ok else "FAIL",
        f"sector gate={gate}; rationale line={rationale}; divergence flag "
        f"correctly silent={flag_silent}")


# ---------- T6: PARA — no-data path ------------------------------------------
def t6() -> None:
    t = cc("PARA", "magic_formula_momentum_v1", "growth_40_v1", "t6_PARA.txt")
    ok = ("no data" in t.lower() or "unrateable" in t.lower())
    fabricated = re.search(r"observed\s+\d", t)
    log("T6", "PASS" if (ok and not fabricated) else "FAIL",
        f"no-data path={ok}; fabricated values={bool(fabricated)}")


# ---------- T7: GARP v2 membership -------------------------------------------
def t7() -> None:
    res = run_rank_pipeline(None, strategy_id="growth_garp_v2",
                            universe_id="growth_40_v1",
                            universes_dir=UNIS, strategies_dir=STRATS,
                            ranker_only=True, freeze_dir=RUNS, adapter=adapter())
    have = {r.ticker for r in res.ranked}
    (OUT / "t7_garp_v2.txt").write_text(
        "\n".join(f"{r.ticker} {r.verdict} {r.combined}" for r in res.ranked),
        encoding="utf-8")
    ok = {"ADBE", "NVDA", "GOOGL", "LLY"} <= have
    log("T7", "PASS" if ok else "FAIL",
        f"ranked: {sorted(have)} (ADBE present = the fix working; META/MSFT "
        f"membership can drift with momentum sign — note, don't fail)")


# ---------- T8: RAW — no screen exclusions, AMZN ranked ----------------------
def t8() -> None:
    t = cc("AMZN", "magic_formula_raw_v1", "growth_40_v1", "t8_AMZN_raw.txt")
    screen_fail = re.search(r"^\s*FAIL\s+min_", t, re.M)
    excluded_on_screen = "Would be EXCLUDED" in t and "screen" in t.split("Would be EXCLUDED")[1][:80]
    log("T8", "PASS" if not (screen_fail or excluded_on_screen) else "FAIL",
        f"screen-criterion FAIL lines: {bool(screen_fail)}; screen exclusion "
        f"claimed: {excluded_on_screen} (RAW has no screens — gates only)")


# ---------- T10: energy watch hidden by default -------------------------------
def t10() -> None:
    import yaml  # part of the project deps
    u = yaml.safe_load((UNIS / "energy_watch_v1.yaml").read_text(encoding="utf-8"))
    hidden = bool(u.get("role")) and "observation" in str(u.get("role", ""))
    dn = u.get("display_name", "")
    log("T10", "PASS" if hidden else "ODD",
        f"role='{u.get('role','')}' display_name='{dn}' (UI toggle behaviour "
        f"itself is a T1-class visual check)")


if __name__ == "__main__":
    print(f"Acceptance pass — {date.today()} — repo: {ROOT}")
    for name, fn in [("T0", t0), ("T2", t2), ("T3", t3), ("T4", t4),
                     ("T5", t5), ("T6", t6), ("T7", t7), ("T8", t8),
                     ("T10", t10)]:
        check(name, fn)
    print("\n=== SUMMARY ===")
    for t, r, n in RESULTS:
        print(f"{t:>4}  {r:<4}  {n[:110]}")
    fails = [t for t, r, _ in RESULTS if r == "FAIL"]
    print(f"\n{len(RESULTS)} checks, {len(fails)} FAIL" +
          (f" -> {fails}" if fails else " — scriptable subset green."))
    print(f"Text outputs for eyeballing (MU/GS/PARA/KO etc.): {OUT}/")
    print("Still manual: T1 (Strategy tab, visual), screenshots T3–T6 "
          "(demo evidence), T9 (narrator prose judgment).")
