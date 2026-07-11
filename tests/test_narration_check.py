"""Narrator rank-semantics post-check (ITEM 4).

The narrator inverts/misstates rank ordinals while its numbers are right. The
deterministic post-check verifies ordinal superlative claims against the authoritative
rank table and APPENDS a machine annotation on a contradiction (never rewrites prose).
The four synthetic cases below reproduce verbatim error fixtures from saved exports;
correct ordinal statements must pass untouched.
"""

from __future__ import annotations

from aristos_council.narration_check import check_narration

# conservative run: DUK best (combined 9, position 1); SO second (combined 12, position 2),
# low_volatility rank 2. growth run: MRK roic rank 21 of 23.
_CONS_SO = {"N": 10, "combined_position": 2,
            "factors": {"low_volatility": 2, "net_payout_yield": 4, "momentum_12m": 6}}
_CONS_DUK = {"N": 10, "combined_position": 1,
             "factors": {"low_volatility": 1, "net_payout_yield": 3, "momentum_12m": 5}}
_GROWTH_MRK = {"N": 23, "combined_position": 20, "factors": {"roic": 21}}


def _flagged(narrative, table) -> bool:
    flags = check_narration(narrative, table)
    return len(flags) == 1 and "contradicts rank table" in flags[0]


# --------------------------------------------------------------------------- #
# The four verbatim error fixtures — each must be flagged
# --------------------------------------------------------------------------- #
def test_fixture_1_rank_2_is_not_second_worst():
    assert _flagged(
        "A rank of 2 out of 10 means SO is the second-worst name in the cohort.",
        _CONS_SO)


def test_fixture_2_combined_12_is_not_the_best():
    assert _flagged(
        "SO carries a combined rank-sum of 12 — the best (lowest) in the cohort.",
        _CONS_SO)


def test_fixture_3_so_low_vol_is_not_best_in_cohort():
    assert _flagged("SO shows the best-in-cohort low volatility of the group.", _CONS_SO)


def test_fixture_4_roic_rank_21_of_23_is_not_second_worst():
    assert _flagged("MRK's ROIC rank 21 out of 23 makes it second-worst on quality.",
                    _GROWTH_MRK)


# --------------------------------------------------------------------------- #
# Correct ordinal statements — must pass untouched
# --------------------------------------------------------------------------- #
def test_correct_second_best_passes():
    assert check_narration(
        "SO ranks 2 out of 10 — second-best in the cohort.", _CONS_SO) == []


def test_correct_best_combined_passes():
    assert check_narration(
        "DUK carries a combined rank-sum of 9 — the best (lowest) in the cohort.",
        _CONS_DUK) == []


def test_correct_best_in_cohort_low_vol_passes():
    assert check_narration(
        "DUK shows the best-in-cohort low volatility of the group.", _CONS_DUK) == []


def test_second_least_volatile_is_not_parsed_as_a_superlative():
    # the correct body phrasing that contradicted fixture 3's summary — not an ordinal
    # token this check recognizes, so it is left alone (no false flag).
    assert check_narration("SO is the second-least-volatile name in the cohort.",
                           _CONS_SO) == []


def test_third_from_the_bottom_is_not_flagged():
    # rank 21 of 23 IS third-worst — 'bottom' is deliberately not an ordinal token.
    assert check_narration("MRK's ROIC rank 21 out of 23 sits third from the bottom.",
                           _GROWTH_MRK) == []


def test_no_ordinal_no_flag():
    assert check_narration("SO earnings look mid-pack; roic rank 5 out of 10.",
                           _CONS_SO) == []


def test_empty_or_degenerate_table_is_safe():
    assert check_narration("", _CONS_SO) == []
    assert check_narration("second-worst name, rank 2 out of 10", {"N": 0}) == []


# --------------------------------------------------------------------------- #
# NARR-CHK-1 — parser fixes: the three 2026-07-11 false positives must pass
# silently; the 2026-07-10 ASML true positive must still flag.
# --------------------------------------------------------------------------- #
# garp_v2 run: NVDA is 1st/2nd/4th/6th on the four factors (rank-sum 13, best overall).
_GARP_NVDA = {"N": 7, "combined_position": 1,
              "factors": {"revenue_growth": 1, "roic": 2, "momentum_12m": 4,
                          "earnings_yield": 6}}
# LLY sits 2nd of 7 overall — "near-best" is an approximation, not a rank-1 claim.
_GARP_LLY = {"N": 7, "combined_position": 2,
             "factors": {"revenue_growth": 3, "roic": 2, "momentum_12m": 2,
                         "earnings_yield": 4}}
# 2026-07-10 growth run: NVO's rank-sum 8 beat ASML's 9, so ASML is 2nd overall, NOT best.
_GROWTH_ASML = {"N": 10, "combined_position": 2,
                "factors": {"revenue_growth": 3, "roic": 2, "momentum_12m": 4,
                            "earnings_yield": 1}}


def test_nvda_multi_factor_sentence_passes_silently():
    # defect (b): ordinals bound to the factor each NAMES — all four are correct.
    assert check_narration(
        "1st on revenue_growth, 2nd on ROIC, 4th on momentum_12m, 6th on earnings_yield, "
        "sum 13 lowest.", _GARP_NVDA) == []


def test_lly_near_best_lines_pass_silently():
    # a hedged superlative ("near-best") is an approximation, not a rank-1 claim.
    assert check_narration("Rank 2/7 (near-best).\nRank 2/7 (near-best).", _GARP_LLY) == []


def test_asml_best_combined_still_flags():
    # the true positive stays caught: ASML claims the best combined rank-sum, but NVO's 8
    # beats its 9 -> ASML is 2nd.
    assert _flagged("ASML has the best combined rank-sum in the cohort.", _GROWTH_ASML)


def test_correct_ordinals_in_arbitrary_factor_order_pass():
    # factors named out of table-column order — each must validate against its own name.
    assert check_narration(
        "6th on earnings_yield, 1st on revenue_growth, 4th on momentum_12m, 2nd on ROIC.",
        _GARP_NVDA) == []


def test_genuine_numeric_contradiction_still_annotates():
    # a digit ordinal that is wrong for the factor it names is still flagged.
    assert _flagged("NVDA sits 1st on ROIC in the cohort.", _GARP_NVDA)


def test_decimal_is_atomic_claim_not_truncated():
    # defect (a): "31.4" must not split the sentence; the flagged claim carries it whole.
    flags = check_narration(
        "With a revenue CAGR of 31.4%, ASML has the best combined rank-sum.",
        _GROWTH_ASML)
    assert len(flags) == 1 and "contradicts rank table" in flags[0]
    assert "31.4" in flags[0]                    # the decimal survived intact, not "31"


def test_decimal_sentence_does_not_spuriously_split_or_flag():
    # a correct sentence with a decimal stays one sentence and passes.
    assert check_narration(
        "NVDA posted a revenue CAGR of 31.4% and ranks 1st on revenue_growth.",
        _GARP_NVDA) == []


# --------------------------------------------------------------------------- #
# NARR-CHK-2 — three new false-positive classes on the 2026-07-11 financials run
# must pass silently; the ASML true positive + all NARR-CHK-1 fixtures stay as-is.
# --------------------------------------------------------------------------- #
# financials run: GS is 4th overall (combined 21), 3rd on momentum of 16.
_FIN_GS = {"N": 16, "combined_position": 4, "ticker": "GS",
           "factors": {"price_to_book": 11, "return_on_equity": 7, "momentum_12m": 3}}


def test_theoretical_bound_arithmetic_aside_passes():
    # class 1: "worst possible = 48" is cohort arithmetic (a theoretical bound), not a
    # claim that GS is worst.
    assert check_narration(
        "GS carries a combined rank-sum of 20 (lower is better; worst possible = 48).",
        _FIN_GS) == []


def test_generic_hypothetical_superlative_not_bound_to_the_name_passes():
    # class 2: "the best-ranked name" is a generic subject, not a claim that GS is best.
    assert check_narration(
        "Even the best-ranked name in the cohort is not insulated from sector-level "
        "drawdowns.", _FIN_GS) == []


def test_compound_relative_ordinal_third_best_passes():
    # class 3: "third-best" is rank 3 (true here), not the bare "best" (rank 1).
    assert check_narration(
        "Momentum (rank 3/16 — top quartile): GS's aggregate earns the third-best "
        "momentum rank.", _FIN_GS) == []


def test_narr_chk2_does_not_lose_the_asml_true_positive():
    # the ASML class of catch is preserved — a real "best combined rank-sum" on a
    # non-rank-1 name still flags.
    assert _flagged("ASML has the best combined rank-sum in the cohort.", _GROWTH_ASML)
    # and a genuine financials contradiction still annotates.
    assert _flagged("GS holds the best combined rank-sum in the cohort.", _FIN_GS)


def test_loose_cohort_claim_that_names_the_ticker_still_flags():
    # the legitimate catch survives: a claim that NAMES the narrated name and calls it
    # "the worst in the cohort" (GS is 4th of 16, not worst) still annotates — this is the
    # class the pipeline post-check test exercises.
    assert _flagged("GS is the worst name in the cohort.", _FIN_GS)
