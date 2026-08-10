"""NARR-INDEP-TEST harness — analysis.py, against hand-built payloads (isolated from
runner.py — the full pipeline integration is covered by
test_experiments_narr_indep_e2e_mocked.py)."""

from __future__ import annotations

import json
from dataclasses import asdict

from experiments.narr_indep_test import analysis
from experiments.narr_indep_test.fixtures import (
    FUND_BAD, FUND_GOOD, RANK_BAD, RANK_GOOD, ranked_ticker)


def _payload(condition_id, experiment, fund_ticker, rep, narration, ticker,
            corrupted_claims=()):
    return {
        "run_id": f"{condition_id}_rep{rep}", "experiment": experiment,
        "condition_id": condition_id, "fund_ticker": fund_ticker, "rep": rep,
        "narration": narration, "narrator_ticker": asdict(ticker),
        "true_ticker": asdict(ticker), "corrupted_claims": list(corrupted_claims),
        "true_table_annotations": [], "ok": True,
    }


def test_load_raw_outputs_skips_failed_runs(tmp_path):
    ok = {"run_id": "a", "ok": True}
    bad = {"run_id": "b", "ok": False, "error": "boom"}
    (tmp_path / "a.json").write_text(json.dumps(ok), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(bad), encoding="utf-8")
    loaded = analysis.load_raw_outputs(tmp_path)
    assert len(loaded) == 1 and loaded[0]["run_id"] == "a"


def test_experiment_a_table_passes_when_claims_are_identical_across_verdicts():
    good_r = ranked_ticker(FUND_GOOD, rank=RANK_GOOD, verdict="buy")
    bad_r = ranked_ticker(FUND_BAD, rank=RANK_BAD, verdict="sell")
    text_good = "VUSA.AS ranks 1st of 5 overall, combined rank-sum 4."
    text_bad = "IWMO.L ranks 5th of 5 overall, combined rank-sum 20."

    raw = (
        [_payload(f"A_baseline_{FUND_GOOD.ticker}_buy", "A_baseline", FUND_GOOD.ticker,
                 i, text_good, good_r) for i in (1, 2, 3)]
        + [_payload(f"A_{FUND_GOOD.ticker}_buy", "A", FUND_GOOD.ticker, i, text_good, good_r)
          for i in (1, 2, 3)]
        + [_payload(f"A_{FUND_GOOD.ticker}_sell", "A", FUND_GOOD.ticker, i, text_good, good_r)
          for i in (1, 2, 3)]
        + [_payload(f"A_{FUND_BAD.ticker}_buy", "A", FUND_BAD.ticker, i, text_bad, bad_r)
          for i in (1, 2, 3)]
        + [_payload(f"A_{FUND_BAD.ticker}_sell", "A", FUND_BAD.ticker, i, text_bad, bad_r)
          for i in (1, 2, 3)]
    )
    table = analysis.experiment_a_table(raw)
    assert table["verdict"] == "PASS"
    assert table["per_fund"][FUND_GOOD.ticker]["pass"] is True
    assert table["per_fund"][FUND_BAD.ticker]["pass"] is True


def test_experiment_b1_table_fails_on_any_invented_number():
    r = ranked_ticker(FUND_GOOD, rank=RANK_GOOD, verdict="buy")
    raw = [
        _payload("B1_x", "B1", "X", 1, "No data is available.", r),
        _payload("B1_x", "B1", "X", 2, "It ranks 2nd of 5.", r),   # invented
    ]
    table = analysis.experiment_b1_table(raw)
    assert table["verdict"] == "FAIL"
    counts = {row["run_id"]: row["count"] for row in table["per_narration"]}
    assert counts["B1_x_rep1"] == 0
    assert counts["B1_x_rep2"] > 0


def test_experiment_b1_table_passes_when_nothing_invented():
    r = ranked_ticker(FUND_GOOD, rank=RANK_GOOD, verdict="buy")
    raw = [_payload("B1_x", "B1", "X", i, "No data is available.", r) for i in (1, 2)]
    assert analysis.experiment_b1_table(raw)["verdict"] == "PASS"


def test_experiment_b2_table_partial_when_ranks_caught_but_values_not():
    r = ranked_ticker(FUND_GOOD, rank=RANK_GOOD, verdict="buy")
    corrupted = [
        {"kind": "rank_swap", "factor": "combined", "true_value": 1, "corrupted_value": 5},
        {"kind": "fee", "factor": "expense_ratio", "true_value": 0.07,
         "corrupted_value": 0.7},
    ]
    p = _payload("B2_x", "B2", "X", 1,
                "VUSA.AS ranks 5th of 5 overall. Its expense ratio is 0.7%.", r,
                corrupted_claims=corrupted)
    # simulate what runner.py would have computed: the checker flags the rank claim
    # (against the TRUE table, position 1) but has nothing for the fee.
    from experiments.narr_indep_test.checker import check_against_truth
    p["true_table_annotations"] = check_against_truth(p["narration"], r)

    table = analysis.experiment_b2_table([p])
    assert table["verdict"] == "PARTIAL"
    assert table["by_kind"]["rank_swap"]["stamp_rate"] == 1.0
    assert table["by_kind"]["fee"]["stamp_rate"] == 0.0
