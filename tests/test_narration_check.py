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
