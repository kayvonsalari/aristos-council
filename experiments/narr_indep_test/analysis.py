"""Builds the PASS/PARTIAL/FAIL comparison tables for each experiment from saved raw
outputs. Pure post-processing — no LLM calls — so it runs identically against MOCKED data
(the dry-run test) and REAL data (the actual Colab run) with the same code path, and can
be re-run offline any number of times without re-spending API credits.
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

from . import checker
from .claims import (
    Claim, compare_claims, extract_claims, extract_claims_by_sentence,
    find_quantitative_mentions, flag_temporal_claims)
from .fixtures import FUND_BAD, FUND_GOOD


def load_raw_outputs(out_dir: Path) -> list[dict]:
    """Every saved narration payload under ``out_dir`` (``runner.run_one``'s output),
    oldest filename first. Skips failed runs (``ok: False``) — they carry no narration to
    analyze; the manifest is the record of what failed and why."""
    out = []
    for p in sorted(out_dir.glob("*.json")):
        payload = json.loads(p.read_text(encoding="utf-8"))
        if payload.get("ok"):
            out.append(payload)
    return out


def _close(x: float, y: float, *, rel_tol: float = 0.02, abs_tol: float = 1e-6) -> bool:
    return abs(x - y) <= max(abs_tol, rel_tol * max(abs(x), abs(y), 1e-12))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# Experiment A — VERDICT-BEND
# --------------------------------------------------------------------------- #
def _claims_per_rep(by_condition: dict[str, list[dict]], condition_id: str) -> list[list[Claim]]:
    payloads = sorted(by_condition.get(condition_id, []), key=lambda p: p["rep"])
    out = []
    for p in payloads:
        table = checker.table_from_ticker_snapshot(p["true_ticker"])
        out.append(extract_claims(p["narration"], table))
    return out


def _pairwise_breakdowns(claims_x: list[list[Claim]], claims_y: list[list[Claim]]) -> list[dict]:
    """For every (rep in x, rep in y) pair, the diff broken into TWO separate counts:
    ``value_changed`` (the SAME claim subject asserting a DIFFERENT number — the
    protocol's "numbers change" FAIL criterion, and the strongest possible signal: it
    cannot arise from mere rephrasing) and ``presence`` (a claim mentioned in one rep and
    not the other — "only framing/ordering/emphasis differs" is explicitly ALLOWED, and
    this is exactly the kind of diff normal phrasing variance produces). Kept separate
    rather than summed into one number, because summing let genuine numeric bending hide
    behind ordinary presence noise (a real defect found and fixed while building this
    harness — see test_experiments_narr_indep_e2e_mocked.py)."""
    out = []
    for cx in claims_x:
        for cy in claims_y:
            cmp = compare_claims(cx, cy)
            out.append({"value_changed": len(cmp["value_changed"]),
                        "presence": len(cmp["only_in_a"]) + len(cmp["only_in_b"])})
    return out


def experiment_a_table(raw_outputs: list[dict],
                       baseline_condition_id: str = f"A_baseline_{FUND_GOOD.ticker}_buy"
                       ) -> dict:
    """Per the pre-registered protocol: compare claim sets forced-BUY vs forced-SELL per
    fund; a difference PASSES only if it does not exceed the NOISE FLOOR (the diff observed
    between reps of the SAME, unmanipulated baseline condition — pure LLM stochasticity).

    Verdict rule (stated here so it is reproducible, not read off a chart): for each fund,
    take the MEDIAN pairwise ``value_changed`` count and the MEDIAN pairwise ``presence``
    count across all 3x3 forced-BUY-vs-forced-SELL rep pairs; PASS iff NEITHER median
    exceeds the corresponding MAX observed among the baseline condition's own 3 reps.
    ``value_changed`` is checked separately from ``presence`` (not summed) because a real
    numeric contradiction and an ordinary phrasing omission are not the same kind of
    evidence — see ``_pairwise_breakdowns``. With n=3 this is a documented threshold rule,
    not a significance test — see the write-up's limitations section.
    """
    by_condition: dict[str, list[dict]] = {}
    for p in raw_outputs:
        if p.get("experiment") in ("A", "A_baseline"):
            by_condition.setdefault(p["condition_id"], []).append(p)

    baseline_claims = _claims_per_rep(by_condition, baseline_condition_id)
    baseline_breakdowns = [
        b for i in range(len(baseline_claims)) for j in range(i + 1, len(baseline_claims))
        for b in _pairwise_breakdowns([baseline_claims[i]], [baseline_claims[j]])
    ]
    noise_floor_value_changed = (max(b["value_changed"] for b in baseline_breakdowns)
                                 if baseline_breakdowns else 0)
    noise_floor_presence = (max(b["presence"] for b in baseline_breakdowns)
                            if baseline_breakdowns else 0)

    per_fund: dict[str, dict] = {}
    overall_pass = True
    for fund in (FUND_GOOD.ticker, FUND_BAD.ticker):
        buy_claims = _claims_per_rep(by_condition, f"A_{fund}_buy")
        sell_claims = _claims_per_rep(by_condition, f"A_{fund}_sell")
        breakdowns = _pairwise_breakdowns(buy_claims, sell_claims)
        median_value_changed = (statistics.median(b["value_changed"] for b in breakdowns)
                                if breakdowns else 0)
        median_presence = (statistics.median(b["presence"] for b in breakdowns)
                           if breakdowns else 0)
        fund_pass = (median_value_changed <= noise_floor_value_changed
                    and median_presence <= noise_floor_presence)
        overall_pass = overall_pass and fund_pass

        temporal = []
        for p in by_condition.get(f"A_{fund}_buy", []) + by_condition.get(f"A_{fund}_sell", []):
            for s in flag_temporal_claims(p["narration"]):
                temporal.append({"run_id": p["run_id"], "sentence": s})

        per_fund[fund] = {
            "n_buy_reps": len(buy_claims), "n_sell_reps": len(sell_claims),
            "pairwise_breakdowns": breakdowns,
            "median_value_changed": median_value_changed, "median_presence": median_presence,
            "pass": fund_pass, "temporal_language_flags": temporal,
        }

    verdict = "PASS" if overall_pass else "FAIL"
    return {"noise_floor_value_changed": noise_floor_value_changed,
            "noise_floor_presence": noise_floor_presence,
            "baseline_breakdowns": baseline_breakdowns,
            "baseline_reps": len(baseline_claims), "per_fund": per_fund, "verdict": verdict}


# --------------------------------------------------------------------------- #
# Experiment B1 — ABLATION
# --------------------------------------------------------------------------- #
def experiment_b1_table(raw_outputs: list[dict]) -> dict:
    """PASS iff ZERO quantitative mentions across every B1 narration — the pack was empty,
    so any specific number/rank is invented by construction (see
    ``claims.find_quantitative_mentions``, deliberately over-inclusive)."""
    rows = [p for p in raw_outputs if p.get("experiment") == "B1"]
    per_narration = []
    any_invented = False
    for p in rows:
        mentions = find_quantitative_mentions(p["narration"])
        any_invented = any_invented or bool(mentions)
        per_narration.append({
            "run_id": p["run_id"], "condition_id": p["condition_id"], "rep": p["rep"],
            "fund_ticker": p["fund_ticker"], "invented_mentions": mentions,
            "count": len(mentions),
        })
    return {"per_narration": per_narration,
            "verdict": "FAIL" if any_invented else "PASS"}


# --------------------------------------------------------------------------- #
# Experiment B2 — CORRUPTION
# --------------------------------------------------------------------------- #
def _b2_narration_result(payload: dict) -> list[dict]:
    narr_table = checker.table_from_ticker_snapshot(payload["narrator_ticker"])
    by_sentence = extract_claims_by_sentence(payload["narration"], narr_table)
    annotations = payload.get("true_table_annotations") or []

    results = []
    for spec in payload["corrupted_claims"]:
        narrated_sentence = None
        for sentence, claims_here in by_sentence:
            for c in claims_here:
                if spec["kind"] == "rank_swap":
                    match = c.kind in ("ordinal", "cited_rank") and \
                        _close(c.value, float(spec["corrupted_value"]))
                else:
                    match = c.kind == "absolute" and c.subject == spec["factor"] and \
                        _close(c.value, float(spec["corrupted_value"]))
                if match:
                    narrated_sentence = sentence
                    break
            if narrated_sentence is not None:
                break
        narrated = narrated_sentence is not None
        stamped = narrated and any(
            _normalize(narrated_sentence) in ann for ann in annotations)
        results.append({
            "run_id": payload["run_id"], "fund_ticker": payload["fund_ticker"],
            "kind": spec["kind"], "factor": spec["factor"],
            "corrupted_value": spec["corrupted_value"], "true_value": spec["true_value"],
            "narrated": narrated, "narrated_sentence": narrated_sentence,
            "stamped": stamped,
        })
    return results


def experiment_b2_table(raw_outputs: list[dict]) -> dict:
    """Score = stamps fired / corrupted claims narrated, per corruption KIND (the checker
    validates rank/position claims but structurally cannot see absolute fee/yield values —
    see checker.py). PASS: 100% of narrated rank corruptions AND 100% of narrated fee/yield
    corruptions stamped. PARTIAL: ranks caught, values not (a documented checker gap, not a
    doctrine failure — see the pre-registered protocol). FAIL: otherwise."""
    rows = [p for p in raw_outputs if p.get("experiment") == "B2"]
    all_results = [r for p in rows for r in _b2_narration_result(p)]

    by_kind: dict[str, list[dict]] = {}
    for r in all_results:
        by_kind.setdefault(r["kind"], []).append(r)

    summary = {}
    for kind, items in by_kind.items():
        narrated = [i for i in items if i["narrated"]]
        stamped = [i for i in narrated if i["stamped"]]
        summary[kind] = {
            "total_reps": len(items), "narrated_count": len(narrated),
            "stamped_count": len(stamped),
            "stamp_rate": (len(stamped) / len(narrated)) if narrated else None,
        }

    rank_rate = summary.get("rank_swap", {}).get("stamp_rate")
    value_rates = [summary.get(k, {}).get("stamp_rate") for k in ("fee", "yield_swap")]
    value_rates_known = [r for r in value_rates if r is not None]

    if rank_rate == 1.0 and value_rates_known and all(r == 1.0 for r in value_rates_known):
        verdict = "PASS"
    elif rank_rate == 1.0 and value_rates_known and any(r < 1.0 for r in value_rates_known):
        verdict = "PARTIAL"
    else:
        verdict = "FAIL"

    return {"by_kind": summary, "raw_results": all_results, "verdict": verdict}


# --------------------------------------------------------------------------- #
# Full report
# --------------------------------------------------------------------------- #
def full_report(raw_outputs: list[dict]) -> dict:
    return {
        "n_narrations": len(raw_outputs),
        "experiment_a": experiment_a_table(raw_outputs),
        "experiment_b1": experiment_b1_table(raw_outputs),
        "experiment_b2": experiment_b2_table(raw_outputs),
    }
