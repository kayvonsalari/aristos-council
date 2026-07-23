"""RANK-DISPLAY-1 — cohort position (#N of M) alongside the rank-SUM everywhere.

A bare combined rank-SUM (e.g. "21.0") reads like an ordinal position and invites the
misread that names ranked 11-20 exist in a 10-name cohort. The fix is display-only: show
the ORDINAL position first with the sum as detail — ``#1 of 9 · score 11 (best 3 · worst
27)`` — with ties sharing a position, M = the rateable (ranked) cohort only, and the
best/worst bounds derived from the factor count and M. The deterministic ranking
(combined_rank values, verdicts, the sequential rank_position the narration check reads)
is byte-unchanged.
"""

from __future__ import annotations

from aristos_council.rank_engine import (
    FactorSpec,
    RankedTicker,
    cohort_positions,
    format_position_cell,
    format_score,
    rank_universe,
)


def _rt(ticker, combined, *, factors=2, n=9, excluded=False):
    return RankedTicker(
        ticker=ticker, factor_ranks={f"f{i}": 1.0 for i in range(factors)},
        factor_values={}, combined_rank=float(combined), universe_size=n,
        excluded=excluded, reason=("screen" if excluded else ""))


# --------------------------------------------------------------------------- #
# The shared formatter — the ONE string every display uses
# --------------------------------------------------------------------------- #
def test_format_position_cell_matches_the_issue_example():
    # #1 of 9 · score 11 (best 3 · worst 27) — 3 factors, cohort of 9.
    assert format_position_cell(1, 9, False, 11.0, 3) == \
        "#1 of 9 · score 11 (best 3 · worst 27)"


def test_format_position_cell_marks_a_tie():
    assert format_position_cell(1, 9, True, 11.0, 3) == \
        "#1 of 9 (tied) · score 11 (best 3 · worst 27)"


def test_format_position_cell_fractional_score_keeps_one_decimal():
    # averaged-tie ranks are the only non-integers; whole numbers render bare.
    assert format_score(11.0) == "11" and format_score(11.5) == "11.5"
    assert "score 11.5 " in format_position_cell(2, 5, False, 11.5, 2)


def test_format_position_cell_without_a_position_falls_back_to_bare_score():
    assert format_position_cell(None, 9, False, 11.0, 3) == "score 11"


def test_bounds_track_the_factor_count():
    # best = number of factors; worst = factors × cohort size.
    assert format_position_cell(1, 9, False, 2.0, 2).endswith("(best 2 · worst 18)")
    assert format_position_cell(1, 9, False, 3.0, 3).endswith("(best 3 · worst 27)")


# --------------------------------------------------------------------------- #
# Cohort positions — ties share, excluded names never inflate M
# --------------------------------------------------------------------------- #
def test_cohort_positions_share_a_position_on_ties():
    # combined 9, 12, 20, 20, 22 -> positions 1, 2, 3, 3, 5 (competition ranking).
    ranked = [_rt("DUK", 9), _rt("SO", 12), _rt("MRK", 20), _rt("PG", 20), _rt("CL", 22)]
    pos = cohort_positions(ranked)
    assert pos["DUK"] == (1, False)
    assert pos["SO"] == (2, False)
    assert pos["MRK"] == (3, True)          # tied with PG
    assert pos["PG"] == (3, True)
    assert pos["CL"] == (5, False)          # the tie consumed position 4


def test_excluded_names_do_not_inflate_the_cohort():
    ranked = [_rt("A", 2), _rt("B", 4), _rt("X", 99, excluded=True)]
    pos = cohort_positions(ranked)
    assert "X" not in pos                    # excluded -> no position
    assert pos["A"] == (1, False) and pos["B"] == (2, False)
    # M in the rendered cell counts only the two ranked names.
    m = sum(1 for r in ranked if not r.excluded)
    assert m == 2 and "#1 of 2 " in format_position_cell(1, m, False, 2.0, 2)


# --------------------------------------------------------------------------- #
# End-to-end through rank_universe — ranking byte-unchanged, positions assigned
# --------------------------------------------------------------------------- #
_SPECS2 = [FactorSpec("f1", "high"), FactorSpec("f2", "high")]


def test_rank_universe_assigns_tie_shared_positions_and_leaves_ranking_unchanged():
    # A and B tie on the combined rank (each 1st on one factor, 2nd on the other).
    rows = [("A", {"f1": 10.0, "f2": 5.0}), ("B", {"f1": 5.0, "f2": 10.0})]
    ranked = rank_universe(rows, _SPECS2, cut="top_k", k=1)
    by = {r.ticker: r for r in ranked}

    # ranking outputs unchanged: both combine to 3.0; rank_position stays SEQUENTIAL.
    assert by["A"].combined_rank == 3.0 and by["B"].combined_rank == 3.0
    assert {by["A"].rank_position, by["B"].rank_position} == {1, 2}

    # display-only cohort position SHARES the tie.
    assert by["A"].cohort_position == 1 and by["A"].cohort_tied is True
    assert by["B"].cohort_position == 1 and by["B"].cohort_tied is True


def test_explain_leads_with_the_ordinal_after_ranking():
    rows = [("A", {"f1": 10.0, "f2": 10.0}), ("B", {"f1": 5.0, "f2": 5.0}),
            ("C", {"f1": 1.0, "f2": 1.0})]
    ranked = rank_universe(rows, _SPECS2, cut="top_k", k=1)
    by = {r.ticker: r for r in ranked}

    expl = by["A"].explain()
    assert expl.startswith("A:")
    assert "#1 of 3 — " in expl                     # ordinal first
    assert "combined rank-sum 2 across a 3-name cohort" in expl   # sum as detail
    assert "(best 2, worst 6)" in expl              # bounds disclosed


def test_excluded_ticker_explain_has_no_position():
    rows = [("A", {"f1": 1.0, "f2": 1.0}),
            ("B", {"f1": None, "f2": 2.0})]         # B missing f1 under exclude mode
    specs = [FactorSpec("f1", "high", missing="exclude"), FactorSpec("f2", "high")]
    ranked = rank_universe(rows, specs, cut="top_k", k=1)
    by = {r.ticker: r for r in ranked}
    assert by["B"].excluded and by["B"].cohort_position is None
    assert by["A"].cohort_position == 1             # M = 1 (B never inflates it)
    assert by["A"].universe_size == 1
