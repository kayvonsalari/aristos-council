"""NARR-EVIDENCE-1 — the narrator's evidence pack and the rendered rank table must read
the SAME position, or an honest narrator gets stamped for contradicting itself.

`RankedTicker.explain()` — what `ranker_explanation` hands the narrator (see
`agents/nodes._ranker_block`) — renders the tie-shared `cohort_position` ("#1 of 3
(tied)"), the exact ordinal `ranked_table_rows` shows readers (RANK-DISPLAY-1). But
`pipeline._annotate_narration`'s fact-check table was built from the sequential
`rank_position` instead (a leftover from RANK-DISPLAY-1's original split of the two
fields). The two diverge for any name after a tie, so a narrator honestly repeating its
own evidence pack's ordinal for a tied name — "#1 of 3" — was checked against a
DIFFERENT number (2) and stamped a false "contradicts rank table" on its opening rank
line. Systematic on any run with a tie: live on the 2026-08-04 bond and equity runs.

Fix: `_annotate_narration` now reads `cohort_position`, matching `ranked_table_rows` and
`explain()` — one source of truth for the position a claim is checked against.
"""

from __future__ import annotations

from types import SimpleNamespace

from aristos_council.pipeline import _annotate_narration
from aristos_council.rank_engine import FactorSpec, rank_universe, ranked_table_rows


def _rep(rationale: str) -> SimpleNamespace:
    return SimpleNamespace(decision=SimpleNamespace(rationale=rationale))


def _table_cell(ranked, ticker: str) -> str:
    rows, _ = ranked_table_rows(ranked)
    return next(row for row in rows if row["Name"] == ticker)["Position (score)"]


# --------------------------------------------------------------------------- #
# (a) a TIE — cohort_position and rank_position diverge for the tied name
# --------------------------------------------------------------------------- #
def test_tied_name_evidence_pack_position_matches_the_ranked_table():
    # A and B tie for the top combined rank (each best on one factor); C trails.
    rows = [("A", {"f1": 10.0, "f2": 5.0}), ("B", {"f1": 5.0, "f2": 10.0}),
            ("C", {"f1": 1.0, "f2": 1.0})]
    specs = [FactorSpec("f1", "high"), FactorSpec("f2", "high")]
    ranked = rank_universe(rows, specs, cut="top_k", k=2)
    b = next(r for r in ranked if r.ticker == "B")

    # the bug's precondition: the two position fields genuinely disagree here.
    assert b.rank_position != b.cohort_position

    # the evidence pack (explain()) and the rendered table must show the SAME ordinal.
    assert "#1 of 3" in b.explain()
    assert _table_cell(ranked, "B").startswith("#1 of 3 (tied)")


def test_opening_rank_line_no_longer_stamped_for_a_tied_name():
    rows = [("A", {"f1": 10.0, "f2": 5.0}), ("B", {"f1": 5.0, "f2": 10.0}),
            ("C", {"f1": 1.0, "f2": 1.0})]
    specs = [FactorSpec("f1", "high"), FactorSpec("f2", "high")]
    ranked = rank_universe(rows, specs, cut="top_k", k=2)
    b = next(r for r in ranked if r.ticker == "B")

    # a narrator honestly quoting its own evidence pack (cohort_position, combined_rank).
    opening = "B ranks 1st of 3 in the cohort, with a combined rank-sum of 3."
    rep = _rep(opening)
    _annotate_narration(rep, b)
    assert rep.decision.rationale == opening          # no annotation appended


def test_reproduces_the_pre_fix_false_stamp_against_the_sequential_position():
    # regression pin: checking the SEQUENTIAL rank_position (the old behaviour) against
    # this same honest opening line DOES flag it — proving the fix's precondition is real,
    # not a vacuous test.
    from aristos_council.narration_check import check_narration
    rows = [("A", {"f1": 10.0, "f2": 5.0}), ("B", {"f1": 5.0, "f2": 10.0}),
            ("C", {"f1": 1.0, "f2": 1.0})]
    specs = [FactorSpec("f1", "high"), FactorSpec("f2", "high")]
    ranked = rank_universe(rows, specs, cut="top_k", k=2)
    b = next(r for r in ranked if r.ticker == "B")
    stale_table = {"N": b.universe_size, "combined_position": b.rank_position,
                   "factors": dict(b.factor_ranks), "ticker": "B", "score": b.combined_rank}
    flags = check_narration(
        "B ranks 1st of 3 in the cohort, with a combined rank-sum of 3.", stale_table)
    assert len(flags) == 1 and "contradicts rank table" in flags[0]


# --------------------------------------------------------------------------- #
# (b) an IMPUTED rank — a factor absent for one name still checks cleanly
# --------------------------------------------------------------------------- #
def test_imputed_factor_evidence_pack_matches_the_ranked_table():
    # B is missing momentum_12m; under 'neutral' mode it is imputed from B's other ranks.
    rows = [("A", {"roic": 10.0, "momentum_12m": 10.0}),
            ("B", {"roic": 5.0, "momentum_12m": None}),
            ("C", {"roic": 1.0, "momentum_12m": 1.0})]
    specs = [FactorSpec("roic", "high"), FactorSpec("momentum_12m", "high")]
    ranked = rank_universe(rows, specs, missing="neutral", cut="top_k", k=2)
    b = next(r for r in ranked if r.ticker == "B")

    assert "momentum_12m" in b.imputed_factors
    assert b.factor_ranks["momentum_12m"] == b.factor_ranks["roic"]   # imputed = own mean
    assert _table_cell(ranked, "B").startswith(f"#{b.cohort_position} of 3")


def test_opening_rank_line_not_stamped_when_a_factor_is_imputed():
    rows = [("A", {"roic": 10.0, "momentum_12m": 10.0}),
            ("B", {"roic": 5.0, "momentum_12m": None}),
            ("C", {"roic": 1.0, "momentum_12m": 1.0})]
    specs = [FactorSpec("roic", "high"), FactorSpec("momentum_12m", "high")]
    ranked = rank_universe(rows, specs, missing="neutral", cut="top_k", k=2)
    b = next(r for r in ranked if r.ticker == "B")

    opening = (f"B ranks 2nd of 3 in the cohort. Its momentum_12m rank is 2nd of 3 "
               f"(imputed, no value was available), matching its roic standing.")
    rep = _rep(opening)
    _annotate_narration(rep, b)
    assert rep.decision.rationale == opening           # no annotation appended
