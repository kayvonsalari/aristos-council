"""NARR-INDEP-TEST harness — runner.py, against FakeRunners (no LLM call, no network).

Proves the plumbing: build a condition, invoke the graph, save a raw-output file, append a
manifest row — BEFORE any real API call. Per the pre-registered protocol, this file makes
zero billed calls.
"""

from __future__ import annotations

import json

from experiments.narr_indep_test import runner
from experiments.narr_indep_test.conditions import (
    experiment_a_conditions, experiment_b1_conditions, experiment_b2_conditions)
from experiments.narr_indep_test.manifest import read_manifest

from aristos_council.agents.schemas import CriticOutput, DecisionOutput, SpecialistOutput
from aristos_council.state import Recommendation, Stance


class _FakeSpecialist:
    def invoke(self, system, user):
        return SpecialistOutput(stance=Stance.ABSTAIN, confidence=0.0, thesis="n/a")


class _FakeCritic:
    def invoke(self, system, user):
        return CriticOutput(counter_thesis="counter-case")


class _FakeDecision:
    """Echoes a canned rationale that CITES the fund's real fee — proves the narrator
    prompt path actually reaches the ledger data, without needing a real model."""

    def __init__(self, rationale: str = "ranked #1 on every factor; fee is 0.07%."):
        self._rationale = rationale

    def invoke(self, system, user):
        return DecisionOutput(recommendation=Recommendation.BUY, confidence=0.8,
                              rationale=self._rationale)


def _fake_runners(rationale: str = "ranked #1 on every factor; fee is 0.07%.") -> dict:
    return {"specialist": _FakeSpecialist(), "critic": _FakeCritic(),
            "decision": _FakeDecision(rationale)}


def test_run_one_saves_raw_output_and_manifest_row(tmp_path):
    inputs = experiment_a_conditions()[0]      # A_VUSA.AS_buy
    out_dir = tmp_path / "raw"
    manifest_path = tmp_path / "manifest.jsonl"

    payload = runner.run_one(inputs, 1, _fake_runners(), out_dir=out_dir,
                             manifest_path=manifest_path)

    assert payload["ok"] is True
    assert payload["run_id"] == "A_VUSA.AS_buy_rep1"
    assert payload["narration"] == "ranked #1 on every factor; fee is 0.07%."
    assert payload["forced_verdict"] == "buy"

    saved = json.loads((out_dir / "A_VUSA.AS_buy_rep1.json").read_text(encoding="utf-8"))
    assert saved["narration"] == payload["narration"]
    assert saved["true_ticker"]["ticker"] == "VUSA.AS"

    rows = read_manifest(manifest_path)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "A_VUSA.AS_buy_rep1"
    assert rows[0]["ok"] is True
    assert rows[0]["raw_output_path"] == str(out_dir / "A_VUSA.AS_buy_rep1.json")


def test_run_one_records_a_failure_without_raising(tmp_path):
    class _Boom:
        def invoke(self, system, user):
            raise RuntimeError("simulated LLM failure")

    inputs = experiment_a_conditions()[0]
    out_dir = tmp_path / "raw"
    manifest_path = tmp_path / "manifest.jsonl"
    runners = {"specialist": _FakeSpecialist(), "critic": _FakeCritic(), "decision": _Boom()}

    payload = runner.run_one(inputs, 1, runners, out_dir=out_dir, manifest_path=manifest_path)

    assert payload["ok"] is False
    assert "simulated LLM failure" in payload["error"]
    rows = read_manifest(manifest_path)
    assert rows[0]["ok"] is False
    assert "simulated LLM failure" in rows[0]["error"]


def test_run_one_checks_against_the_true_table_not_the_corrupted_one(tmp_path):
    # A B2 corrupted condition: the narrator was shown IWMO.L's ticker corrupted to rank
    # 1 (swapped with VUSA.AS). A fake narration that repeats the CORRUPTED claim (rank
    # 1st) must be flagged against the TRUE table (IWMO.L's real rank is 5).
    b2 = experiment_b2_conditions()
    iwmo_corrupted = next(c for c in b2 if c.fund_ticker == "IWMO.L")
    assert iwmo_corrupted.narrator_ticker.cohort_position == 1     # corrupted, confirmed
    assert iwmo_corrupted.true_ticker.cohort_position == 5          # true, confirmed

    runners = _fake_runners("IWMO.L ranks 1st of 5 overall, the best in the cohort.")
    out_dir = tmp_path / "raw"
    manifest_path = tmp_path / "manifest.jsonl"

    payload = runner.run_one(iwmo_corrupted, 1, runners, out_dir=out_dir,
                             manifest_path=manifest_path)

    assert payload["ok"] is True
    assert len(payload["true_table_annotations"]) >= 1
    assert "contradicts rank table" in payload["true_table_annotations"][0]


def test_run_one_on_an_ablated_condition_uses_a_near_empty_evidence_pack(tmp_path):
    b1 = experiment_b1_conditions()[0]         # B1_VUSA.AS_ablated
    runners = _fake_runners("No factor data is available for this name.")
    out_dir = tmp_path / "raw"
    manifest_path = tmp_path / "manifest.jsonl"

    payload = runner.run_one(b1, 1, runners, out_dir=out_dir, manifest_path=manifest_path)

    assert payload["ok"] is True
    assert payload["true_ticker"]["factor_ranks"] == {}
    assert payload["true_ticker"]["factor_values"] == {}
