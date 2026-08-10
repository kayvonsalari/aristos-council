"""NARR-INDEP-TEST harness — conditions.py."""

from __future__ import annotations

from experiments.narr_indep_test.conditions import (
    REPS, all_conditions, experiment_a_baseline_condition, experiment_a_conditions,
    experiment_b1_conditions, experiment_b2_conditions)
from experiments.narr_indep_test.fixtures import FUND_BAD, FUND_GOOD, RANK_BAD, RANK_GOOD


def test_experiment_a_has_four_conditions_two_funds_two_verdicts():
    conds = experiment_a_conditions()
    assert len(conds) == 4
    assert {c.narrator_ticker.verdict for c in conds} == {"buy", "sell"}
    assert {c.fund_ticker for c in conds} == {FUND_GOOD.ticker, FUND_BAD.ticker}


def test_experiment_a_forcing_leaves_evidence_untouched_only_verdict_changes():
    conds = {c.condition_id: c for c in experiment_a_conditions()}
    buy = conds[f"A_{FUND_GOOD.ticker}_buy"]
    sell = conds[f"A_{FUND_GOOD.ticker}_sell"]
    assert buy.narrator_ticker.factor_ranks == sell.narrator_ticker.factor_ranks
    assert buy.narrator_ticker.factor_values == sell.narrator_ticker.factor_values
    assert buy.narrator_ticker.combined_rank == sell.narrator_ticker.combined_rank
    assert buy.narrator_ticker.cohort_position == sell.narrator_ticker.cohort_position
    assert buy.narrator_ticker.verdict != sell.narrator_ticker.verdict


def test_experiment_a_narrator_ticker_and_true_ticker_are_identical_object():
    # forcing only manipulates the verdict — nothing for the checker to diverge on.
    for c in experiment_a_conditions():
        assert c.narrator_ticker is c.true_ticker


def test_experiment_a_baseline_is_fund_goods_own_truthful_pairing():
    baseline = experiment_a_baseline_condition()
    assert baseline.fund_ticker == FUND_GOOD.ticker
    assert baseline.narrator_ticker.verdict == "buy"
    assert baseline.experiment == "A_baseline"


def test_experiment_b1_ablates_evidence_but_keeps_each_funds_own_verdict():
    conds = {c.fund_ticker: c for c in experiment_b1_conditions()}
    assert conds[FUND_GOOD.ticker].narrator_ticker.verdict == "buy"
    assert conds[FUND_BAD.ticker].narrator_ticker.verdict == "sell"
    for c in conds.values():
        assert c.narrator_ticker.factor_ranks == {}
        assert c.narrator_ticker.factor_values == {}


def test_experiment_b2_corrupts_the_narrator_ticker_but_not_the_true_one():
    conds = {c.fund_ticker: c for c in experiment_b2_conditions()}
    good = conds[FUND_GOOD.ticker]
    # narrator sees FUND_GOOD swapped to FUND_BAD's rank; true ticker keeps the real rank.
    assert good.narrator_ticker.cohort_position == RANK_BAD
    assert good.true_ticker.cohort_position == RANK_GOOD
    assert good.narrator_ticker is not good.true_ticker
    # verdict and ticker identity stay UNCORRUPTED (only the evidence pack is).
    assert good.narrator_ticker.verdict == good.true_ticker.verdict == "buy"
    assert good.narrator_ticker.ticker == good.true_ticker.ticker == FUND_GOOD.ticker


def test_experiment_b2_fee_is_corrupted_ten_x_and_yield_is_swapped():
    conds = {c.fund_ticker: c for c in experiment_b2_conditions()}
    good = conds[FUND_GOOD.ticker]
    assert good.narrator_ticker.factor_values["expense_ratio"] == \
        FUND_GOOD.expense_ratio * 10.0
    assert good.narrator_ticker.factor_values["distribution_yield"] == \
        FUND_BAD.distribution_yield          # swapped with the OTHER fund's true yield
    assert good.true_ticker.factor_values["expense_ratio"] == FUND_GOOD.expense_ratio
    assert good.true_ticker.factor_values["distribution_yield"] == \
        FUND_GOOD.distribution_yield


def test_experiment_b2_records_exactly_three_corruptions_per_condition():
    for c in experiment_b2_conditions():
        kinds = {spec["kind"] for spec in c.corrupted_claims}
        assert kinds == {"rank_swap", "fee", "yield_swap"}


def test_all_conditions_totals_nine_conditions_twenty_seven_reps():
    conds = all_conditions()
    assert len(conds) == 9
    assert len(conds) * REPS == 27
    assert len({c.condition_id for c in conds}) == 9   # every id unique
