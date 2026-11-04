"""What the company page says about the backtest: one sentence for "what this cannot tell you" and the three forms'
figures for the workings. Reads ``backtests_absolute/corrected/<industry>/result.json`` only (the corrected run of ABSOLUTE-BACKTEST-CORRECTED; the
published first run stays under ``backtests_absolute/`` as the baseline and is not read here); nothing is computed here, and an
industry with no finished result gets no sentence (the page keeps its old one).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from . import BACKTEST_DIR, CORRECTED_SUBDIR, FORM_NAMES, FORMS, HOLDS, SURVIVORSHIP

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / BACKTEST_DIR / CORRECTED_SUBDIR
NO_RESULT = ("No backtest of this method exists yet, so nothing shows that companies with this reading went on "
             "to do better.")


def load_industry(slug: Optional[str], root=None) -> Optional[dict]:
    """The finished result of one industry, or None (not backtested, not finished, unreadable)."""
    if not slug:
        return None
    folder = Path(root) if root is not None else DEFAULT_ROOT
    folder = folder / slug
    if not (folder / "DONE.json").exists():
        return None
    try:
        return json.loads((folder / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _row(res: dict, form: int, hold: int) -> Optional[dict]:
    return next((r for r in res.get("rows", []) if r.get("form") == form and r.get("hold_months") == hold), None)


def _version(strategy_id) -> str:
    """'absolute_gate_v2' -> 'version 2': reader text never carries an internal id."""
    tail = str(strategy_id or "").rsplit("_v", 1)[-1]
    return f"version {tail}" if tail.isdigit() else "as recorded"


def _pts(x) -> str:
    return "n/a" if x is None else f"{100.0 * x:+.1f}"


def cannot_sentence(slug: Optional[str], root=None) -> Optional[str]:
    """The sentence that replaces "no backtest of this method exists yet": form 3, 12-month hold, this industry."""
    res = load_industry(slug, root)
    if res is None:
        return None
    r = _row(res, 3, 12)
    if r is None or r.get("ex_basket") is None:
        return ("In this industry over ten years, too few names both passed the gate and cleared the hurdle to measure "
                "a result. The test cannot see companies that failed and vanished.")
    thin = " Too few rounds to judge." if r.get("verdict") == "insufficient" else ""
    luck = r.get("luck")
    matched = f"; random baskets matched that {100.0 * luck:.0f}% of the time" if luck is not None else ""
    return (f"In this industry over ten years, names that passed the gate and cleared the hurdle returned "
            f"{_pts(r['ex_basket'])} points a year against the industry basket{matched}.{thin} "
            "The test cannot see companies that failed and vanished, so it is not proof.")


def workings_lines(slug: Optional[str], root=None) -> list[str]:
    """The three forms' figures, both holds, for the workings. Empty when the industry has no result."""
    res = load_industry(slug, root)
    if res is None:
        return []
    out = [f"{res['industry']}: monthly rounds from {res['window'][0]} to {res['window'][1]}, gate "
           f"{_version(res['gate'])}, valuation {_version(res['valuation'])}, accounts as of each date (filing dates for "
           f"{100.0 * (res.get('decisions_on_filing_date') or 0):.0f}% of the account years, period end + 90 days for the "
           "rest). Yearly figures; a 36-month hold is annualised.", SURVIVORSHIP]
    for hold in HOLDS:
        for form in FORMS:
            r = _row(res, form, hold)
            if r is None:
                continue
            if r.get("ex_basket") is None:
                out.append(f"Form {form}, {FORM_NAMES[form]}, {hold}-month hold: too few names to measure "
                           f"({r.get('n_positions', 0)} of {r.get('n_rounds', 0)} rounds held a position).")
                continue
            luck = r.get("luck")
            tail = ("" if hold != 12 else f" {r['verdict'].capitalize()}.")
            out.append(
                f"Form {form}, {FORM_NAMES[form]}, {hold}-month hold: {_pts(r['ex_basket'])} points a year against the "
                f"industry basket, {_pts(r['ex_index'])} against the world index fund, {_pts(r['ex_cash'])} against cash; "
                f"random baskets matched it {'n/a' if luck is None else f'{100.0 * luck:.0f}%'} of the time; "
                f"dropping the best name {_pts(r['drop_best'])}; winning years {r['years_positive']} of "
                f"{r['years_measured']}; fewer than 4 names in {100.0 * (r.get('share_rounds_under_4') or 0):.0f}% of rounds."
                + tail)
    out.append("The 12-month hold is judged against 2 points a year over the industry basket with random baskets "
               "matching under 5% of the time; the 36-month hold is reported without pass or fail.")
    return out
