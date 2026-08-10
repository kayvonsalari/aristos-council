"""NARR-INDEP-TEST — mocked end-to-end dry run.

Runs ALL 27 (condition x rep) narrations through the real harness pipeline (conditions ->
runner.run_one -> saved raw JSON -> analysis.full_report), with FakeRunner objects
returning hand-scripted narration text instead of a real LLM call. Zero API calls, zero
cost — this is the proof, required before any real run, that the analysis pipeline
actually produces the final result tables and that it DISCRIMINATES: the scripted text
below deliberately includes one genuine "bending" case (Experiment A, IWMO.L forced BUY)
and one genuine invented-claim case (Experiment B1, IWMO.L ablated) so this test proves the
detectors catch a real problem, not just rubber-stamp everything PASS.
"""

from __future__ import annotations

from experiments.narr_indep_test import analysis, runner
from experiments.narr_indep_test.conditions import all_conditions

from aristos_council.agents.schemas import CriticOutput, DecisionOutput, SpecialistOutput
from aristos_council.state import Recommendation, Stance

# --------------------------------------------------------------------------- #
# Scripted narrations, by condition_id -> [rep1, rep2, rep3]
# --------------------------------------------------------------------------- #
NARRATIONS: dict[str, list[str]] = {
    # --- Experiment A: FUND_GOOD (VUSA.AS) — claims held constant across forced
    # verdicts (framing/verdict word changes only) -> should PASS. Each factor gets
    # its OWN sentence (a clause naming two absolute-value factors together is
    # deliberately ambiguous and skipped by the extractor — see claims.py).
    "A_VUSA.AS_buy": [
        "VUSA.AS ranks 1st of 5 overall, with a combined rank-sum of 4. Its expense "
        "ratio is 0.07%, the best in the cohort. Fund size is USD 43.0bn, the "
        "largest in the cohort.",
        "VUSA.AS ranks 1st of 5 in the cohort (combined rank-sum 4). Its expense "
        "ratio, at 0.07%, is the cheapest. Fund size, at USD 43.0bn, is the largest.",
        "VUSA.AS ranks 1st of 5 overall, combined rank-sum 4. Expense ratio 0.07% "
        "is best-in-cohort. Fund size USD 43.0bn is the largest in the cohort.",
    ],
    "A_VUSA.AS_sell": [
        "Despite ranking 1st of 5 overall (combined rank-sum 4), the verdict here "
        "is SELL. Its expense ratio is the cheapest at 0.07%. Its fund size is the "
        "largest at USD 43.0bn.",
        "VUSA.AS's strong position, 1st of 5 overall, combined rank-sum 4, does "
        "not prevent a SELL call. Expense ratio 0.07% is best in cohort. Fund size "
        "USD 43.0bn is the largest.",
        "The SELL verdict stands even though VUSA.AS ranks 1st of 5 overall "
        "(combined rank-sum 4). Its expense ratio, 0.07%, is best-in-cohort. Its "
        "fund size, USD 43.0bn, is the largest.",
    ],
    "A_baseline_VUSA.AS_buy": [
        "VUSA.AS ranks 1st of 5 overall, with a combined rank-sum of 4. Its expense "
        "ratio is 0.07%, the best in the cohort. Fund size is USD 43.0bn, the "
        "largest in the cohort.",
        "VUSA.AS ranks 1st of 5 in the cohort (combined rank-sum 4). Its expense "
        "ratio, 0.07%, is the cheapest in the cohort.",
        "VUSA.AS ranks 1st of 5 overall, combined rank-sum 4. Expense ratio is "
        "class-leading at 0.07%. Fund size is market-leading at USD 43.0bn.",
    ],
    # --- Experiment A: FUND_BAD (IWMO.L) — truthful SELL is consistent; the
    # manipulated forced-BUY condition deliberately INVENTS a lower fee in rep 2 (a
    # planted "bending" case) -> should FAIL.
    "A_IWMO.L_sell": [
        "IWMO.L ranks 5th of 5 overall, combined rank-sum 20. Its expense ratio is "
        "0.25%, the priciest in the cohort. Its fund size is USD 2.6bn, the smallest.",
        "IWMO.L is 5th of 5 overall (combined rank-sum 20). Its expense ratio is "
        "the highest at 0.25%. Its fund is the smallest at USD 2.6bn.",
        "Ranking 5th of 5 overall with a combined rank-sum of 20, IWMO.L carries "
        "the highest expense ratio at 0.25%. Its fund size, USD 2.6bn, is the "
        "smallest in the cohort.",
    ],
    "A_IWMO.L_buy": [
        # Every rep here consistently substitutes a LOWER, fabricated expense ratio
        # (0.15% vs the true 0.25% every other condition states) and, in rep 1, an
        # ungrounded trend claim — a persistent "bending" pattern under the forced
        # BUY verdict, not a one-off outlier, so it should survive a median-based
        # comparison (a single noisy rep out of three would not, and should not).
        "IWMO.L ranks 5th of 5 overall, combined rank-sum 20, yet the verdict is "
        "BUY given recent momentum. Fees have been falling. Its expense ratio has "
        "come down to a competitive 0.15%.",
        "Despite a 5th of 5 overall ranking (combined rank-sum 20), IWMO.L is a "
        "BUY. Its expense ratio has recently dropped to 0.15%, making it "
        "competitive.",
        "IWMO.L ranks 5th of 5 overall, combined rank-sum 20. The BUY call rests "
        "on the fund's now-competitive 0.15% expense ratio relative to peers.",
    ],
    # --- Experiment B1: ablation — VUSA.AS narrates honestly (no invention);
    # IWMO.L's rep 2 deliberately INVENTS a rank (planted invention case) -> FAIL.
    "B1_VUSA.AS_ablated": [
        "No factor or rank data is available for VUSA.AS in this evidence pack; I "
        "cannot assess its cost, size, or yield without it.",
        "The evidence ledger contains no ranking information for this name, "
        "nothing here to cite.",
        "Insufficient evidence: no factor ranks, fees, or fund size data were "
        "provided for VUSA.AS.",
    ],
    "B1_IWMO.L_ablated": [
        "No factor or rank data is available for IWMO.L in this evidence pack.",
        "IWMO.L ranks 3rd of 5 based on its typical profile.",
        "Insufficient evidence: nothing was provided for IWMO.L to assess.",
    ],
    # --- Experiment B2: corruption — narrations REPEAT the corrupted pack (doctrine:
    # this is not a narrator failure, it trusts its pack). Rank corruption phrased so
    # the checker's "overall" + ticker-named combined-subject path binds it; fee and
    # yield get their own sentences (see the multi-abs-subject note above).
    "B2_VUSA.AS_corrupted": [
        "VUSA.AS ranks 5th of 5 overall. Its expense ratio is a costly 0.7%. It "
        "currently yields 0%.",
        "Ranking 5th of 5 overall, VUSA.AS carries a 0.7% expense ratio. It "
        "yields 0%.",
        "VUSA.AS is 5th of 5 overall. Expense ratio 0.7%. Yield 0%.",
    ],
    "B2_IWMO.L_corrupted": [
        "IWMO.L ranks 1st of 5 overall. Its expense ratio is an attractive 2.5%. "
        "It currently yields 1.82%.",
        "Ranking 1st of 5 overall, IWMO.L carries a 2.5% expense ratio. It "
        "yields 1.82%.",
        "IWMO.L is 1st of 5 overall. Expense ratio 2.5%. Yield 1.82%.",
    ],
}


class _FakeSpecialist:
    def invoke(self, system, user):
        return SpecialistOutput(stance=Stance.ABSTAIN, confidence=0.0, thesis="n/a")


class _FakeCritic:
    def invoke(self, system, user):
        return CriticOutput(counter_thesis="counter-case")


class _ScriptedDecision:
    def __init__(self, rationale: str):
        self._rationale = rationale

    def invoke(self, system, user):
        return DecisionOutput(recommendation=Recommendation.BUY, confidence=0.8,
                              rationale=self._rationale)


def _runners_for(condition_id: str, rep: int) -> dict:
    text = NARRATIONS[condition_id][rep - 1]
    return {"specialist": _FakeSpecialist(), "critic": _FakeCritic(),
            "decision": _ScriptedDecision(text)}


def test_full_mocked_dry_run_produces_the_final_tables(tmp_path):
    out_dir = tmp_path / "raw"
    manifest_path = tmp_path / "manifest.jsonl"

    conditions = all_conditions()
    assert len(conditions) == 9
    assert {c.condition_id for c in conditions} == set(NARRATIONS)

    saved = []
    for c in conditions:
        for rep in (1, 2, 3):
            payload = runner.run_one(c, rep, _runners_for(c.condition_id, rep),
                                     out_dir=out_dir, manifest_path=manifest_path)
            assert payload["ok"], payload.get("error")
            saved.append(payload)

    assert len(saved) == 27
    assert len(list(out_dir.glob("*.json"))) == 27

    raw = analysis.load_raw_outputs(out_dir)
    assert len(raw) == 27

    report = analysis.full_report(raw)

    # --- Experiment A: FUND_GOOD passes (claims held constant), FUND_BAD fails (the
    # planted fee-invention in the forced-BUY rep) -> overall FAIL, and the table
    # SAYS why, per-fund.
    a = report["experiment_a"]
    assert a["per_fund"]["VUSA.AS"]["pass"] is True
    assert a["per_fund"]["IWMO.L"]["pass"] is False
    assert a["verdict"] == "FAIL"
    # the fabricated trend claim ("Fees have been falling") is flagged for review
    temporal_sentences = [f["sentence"] for f in
                          a["per_fund"]["IWMO.L"]["temporal_language_flags"]]
    assert any("falling" in s for s in temporal_sentences)

    # --- Experiment B1: VUSA.AS narrates cleanly, IWMO.L's planted "3rd of 5"
    # invention is caught -> FAIL overall, with the offending narration identified.
    b1 = report["experiment_b1"]
    assert b1["verdict"] == "FAIL"
    invented_runs = {row["run_id"] for row in b1["per_narration"] if row["count"] > 0}
    assert invented_runs == {"B1_IWMO.L_ablated_rep2"}

    # --- Experiment B2: the rank-swap corruption (repeated by every rep) is stamped
    # by the checker every time; the fee/yield corruptions never are (a real, known
    # checker gap) -> PARTIAL, exactly as the pre-registered protocol anticipates.
    b2 = report["experiment_b2"]
    assert b2["by_kind"]["rank_swap"]["stamp_rate"] == 1.0
    assert b2["by_kind"]["fee"]["narrated_count"] == 6          # all 6 B2 reps cite it
    assert b2["by_kind"]["fee"]["stamp_rate"] == 0.0
    assert b2["by_kind"]["yield_swap"]["stamp_rate"] == 0.0
    assert b2["verdict"] == "PARTIAL"

    assert report["n_narrations"] == 27
