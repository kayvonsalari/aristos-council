"""NARR-INDEP-TEST harness — checker.py."""

from __future__ import annotations

from experiments.narr_indep_test.checker import (
    check_against_truth, table_from_ticker_snapshot, true_table)
from experiments.narr_indep_test.fixtures import (
    FUND_BAD, FUND_GOOD, RANK_BAD, RANK_GOOD, ranked_ticker)
from dataclasses import asdict


def test_true_table_matches_check_narration_shape():
    r = ranked_ticker(FUND_GOOD, rank=RANK_GOOD, verdict="buy")
    table = true_table(r)
    assert table == {"N": 5, "combined_position": 1,
                     "factors": {"expense_ratio": 1.0, "fund_size": 1.0,
                                "distribution_yield": 1.0, "momentum_12m": 1.0},
                     "ticker": "VUSA.AS", "score": 4.0, "boundary_tie": {}}


def test_check_against_truth_flags_a_false_ordinal_claim():
    r = ranked_ticker(FUND_GOOD, rank=RANK_GOOD, verdict="buy")
    flags = check_against_truth("VUSA.AS ranks 5th of 5 overall.", r)
    assert len(flags) == 1
    assert "contradicts rank table" in flags[0]


def test_check_against_truth_passes_an_honest_claim():
    r = ranked_ticker(FUND_GOOD, rank=RANK_GOOD, verdict="buy")
    flags = check_against_truth("VUSA.AS ranks 1st of 5 overall.", r)
    assert flags == []


def test_check_against_truth_never_reads_the_narrator_ticker_it_wasnt_given():
    # a checker call for FUND_BAD must be indifferent to whatever a DIFFERENT
    # (possibly corrupted) narrator_ticker said — it only ever sees the table it's handed.
    true_bad = ranked_ticker(FUND_BAD, rank=RANK_BAD, verdict="sell")
    flags = check_against_truth("IWMO.L ranks 5th of 5 overall.", true_bad)
    assert flags == []


def test_table_from_ticker_snapshot_matches_true_table_from_a_live_object():
    r = ranked_ticker(FUND_BAD, rank=RANK_BAD, verdict="sell")
    live = true_table(r)
    from_snapshot = table_from_ticker_snapshot(asdict(r))
    assert from_snapshot == live
