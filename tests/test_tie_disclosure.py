"""Boundary ties (VERDICT-TIE-1) — display only; supersedes the ITEM 7 tie disclosure.

When a tie group spans a verdict boundary, the deterministic alphabetical tie-break — NOT a
score difference — decided which side of the boundary each name fell on. Live evidence
(2026-07-28 ETF Index Tracker run): EUNL.DE and VWCE.DE both scored 10.0, tied #4 of 5;
EUNL.DE got HOLD and VWCE.DE got SELL purely on the tie-break, and nothing on the table said
the VERDICT had split on chance.

Now EVERY member of such a tie carries the mark in its VERDICT cell —
``⚑ boundary (tied 10 with EUNL.DE — HOLD; tie broken alphabetically)`` — naming the tie
partner(s), the shared score and the differing verdict. (The original ITEM 7 note marked only
the LOWER row and named nothing: ``(=20.0 — tie broken alphabetically)``.) A tie INSIDE one
verdict is not a boundary case and keeps the ``(tied)`` position marker only.

Doctrine unchanged: tied names KEEP their individual ranker verdicts. Scores, the cut, the
tie-break and the verdicts are untouched — this adds transparency, not new judgment.
"""

from __future__ import annotations

from aristos_council.rank_engine import (
    FactorSpec,
    RankedTicker,
    boundary_tie_facts,
    boundary_tie_notes,
    cohort_positions,
    format_position_cell,
    format_verdict_cell,
    rank_universe,
)


def _rt(ticker, verdict, combined, *, n=5, excluded=False):
    return RankedTicker(ticker=ticker, factor_ranks={"f1": 1.0, "f2": 1.0},
                        factor_values={"f1": 1.0, "f2": 1.0},
                        combined_rank=float(combined), universe_size=n, verdict=verdict,
                        excluded=excluded, reason=("screen" if excluded else ""))


# The live ETF-run shape: EUNL.DE (HOLD) and VWCE.DE (SELL) both combined 10.0.
_ETF = [_rt("SXR8.DE", "buy", 4), _rt("IWDA.AS", "hold", 6), _rt("VUSA.AS", "hold", 8),
        _rt("EUNL.DE", "hold", 10), _rt("VWCE.DE", "sell", 10)]


# --------------------------------------------------------------------------- #
# Two-way tie across a verdict boundary — BOTH members flagged
# --------------------------------------------------------------------------- #
def test_two_way_tie_across_a_verdict_boundary_flags_both_members():
    notes = boundary_tie_notes(_ETF)
    assert set(notes) == {"EUNL.DE", "VWCE.DE"}          # both sides, not just the lower row


def test_the_mark_names_the_partner_the_shared_score_and_the_differing_verdict():
    notes = boundary_tie_notes(_ETF)
    assert notes["EUNL.DE"] == (
        "⚑ boundary (tied 10 with VWCE.DE — SELL; tie broken alphabetically)")
    assert notes["VWCE.DE"] == (
        "⚑ boundary (tied 10 with EUNL.DE — HOLD; tie broken alphabetically)")


def test_the_verdict_cell_carries_the_mark():
    notes = boundary_tie_notes(_ETF)
    assert format_verdict_cell("hold", notes["EUNL.DE"]) == (
        "HOLD ⚑ boundary (tied 10 with VWCE.DE — SELL; tie broken alphabetically)")
    assert format_verdict_cell("sell", notes["VWCE.DE"]) == (
        "SELL ⚑ boundary (tied 10 with EUNL.DE — HOLD; tie broken alphabetically)")


def test_an_unflagged_verdict_cell_is_the_bare_verdict():
    # No boundary tie -> byte-identical to what the tables printed before.
    assert format_verdict_cell("buy") == "BUY"
    assert format_verdict_cell("hold", "") == "HOLD"


def test_the_facts_carry_the_partner_verdict_for_the_evidence_layer():
    facts = boundary_tie_facts(_ETF)
    assert facts["VWCE.DE"]["score"] == 10.0
    assert facts["VWCE.DE"]["score_display"] == "10"
    assert facts["VWCE.DE"]["verdict"] == "sell"
    assert facts["VWCE.DE"]["partners"] == [{"ticker": "EUNL.DE", "verdict": "hold"}]


# --------------------------------------------------------------------------- #
# Ties that do NOT span a boundary, and no-tie cohorts
# --------------------------------------------------------------------------- #
def test_tie_within_the_same_verdict_is_not_a_boundary_case():
    ranked = [_rt("A", "hold", 10), _rt("B", "hold", 10)]
    assert boundary_tie_notes(ranked) == {}
    # …and the existing "(tied)" position display still discloses the tie itself.
    pos = cohort_positions(ranked)
    assert pos["A"] == (1, True) and pos["B"] == (1, True)
    assert "(tied)" in format_position_cell(1, 2, True, 10.0, 2)


def test_no_tie_no_flag():
    ranked = [_rt("A", "buy", 9), _rt("B", "hold", 12), _rt("C", "sell", 15)]
    assert boundary_tie_notes(ranked) == {}


def test_excluded_names_never_participate_in_a_tie():
    ranked = list(_ETF)
    ranked.append(_rt("X", "hold", 10, excluded=True))    # same score, screened out
    assert set(boundary_tie_notes(ranked)) == {"EUNL.DE", "VWCE.DE"}
    assert "X" not in boundary_tie_facts(ranked)


# --------------------------------------------------------------------------- #
# Three-way tie spanning a boundary — ALL members flagged
# --------------------------------------------------------------------------- #
_THREE = [_rt("W", "buy", 8), _rt("A", "hold", 12), _rt("B", "hold", 12),
          _rt("C", "sell", 12)]


def test_three_way_tie_spanning_a_boundary_flags_all_three():
    notes = boundary_tie_notes(_THREE)
    assert set(notes) == {"A", "B", "C"}
    # Each member names the tie partner(s) whose verdict DIFFERS from its own.
    assert notes["A"] == "⚑ boundary (tied 12 with C — SELL; tie broken alphabetically)"
    assert notes["B"] == "⚑ boundary (tied 12 with C — SELL; tie broken alphabetically)"
    assert notes["C"] == ("⚑ boundary (tied 12 with A — HOLD, B — HOLD; "
                          "tie broken alphabetically)")


# --------------------------------------------------------------------------- #
# The ranking itself is untouched — display + evidence only
# --------------------------------------------------------------------------- #
_SPECS2 = [FactorSpec("f1", "high"), FactorSpec("f2", "high")]

# D and E swap 4th/5th across the two factors -> both combine to 9, straddling the
# quintile HOLD/SELL boundary of a 5-name cohort (index 4 = SELL).
_ROWS = [("A", {"f1": 50.0, "f2": 50.0}), ("B", {"f1": 40.0, "f2": 40.0}),
         ("C", {"f1": 30.0, "f2": 30.0}), ("D", {"f1": 20.0, "f2": 10.0}),
         ("E", {"f1": 10.0, "f2": 20.0})]


def test_flagging_changes_no_verdict_no_score_and_no_position():
    ranked = rank_universe(_ROWS, _SPECS2)
    assert [r.ticker for r in ranked] == ["A", "B", "C", "D", "E"]
    assert [r.combined_rank for r in ranked] == [2.0, 4.0, 6.0, 9.0, 9.0]
    # The tie-break kept D above E (alphabetical) and the boundary fell between them.
    assert [r.verdict for r in ranked] == ["buy", "hold", "hold", "hold", "sell"]
    assert [r.rank_position for r in ranked] == [1, 2, 3, 4, 5]

    notes = boundary_tie_notes(ranked)
    assert set(notes) == {"D", "E"}
    # …and computing the marks left every ranker output exactly as above.
    assert [r.verdict for r in ranked] == ["buy", "hold", "hold", "hold", "sell"]
    assert [r.combined_rank for r in ranked] == [2.0, 4.0, 6.0, 9.0, 9.0]
    assert [(r.cohort_position, r.cohort_tied) for r in ranked] == [
        (1, False), (2, False), (3, False), (4, True), (4, True)]


# --------------------------------------------------------------------------- #
# The mark reaches the rendered report
# --------------------------------------------------------------------------- #
def _result(ranked):
    from aristos_council.pipeline import RankPipelineResult

    return RankPipelineResult(
        ranked=ranked, excluded=[], unrateable=[], narratives={},
        header="Verdict: deterministic ranker.  Narrative: none (ranker-only — no LLM ran).",
        meta={"rank_strategy_id": "s", "screen_strategy_id": "sc", "universe_id": "u",
              "council_mode": "ranker-only", "ranker_only": True, "universe_size": 5,
              "ranked_count": 5, "shortlist": [], "est_cost": 0.0},
        council_mode="ranker-only")


def test_both_marks_flow_into_the_cli_report():
    from aristos_council.pipeline import format_cli_report

    report = format_cli_report(_result(_ETF))
    assert ("HOLD ⚑ boundary (tied 10 with VWCE.DE — SELL; tie broken alphabetically)"
            in report)
    assert ("SELL ⚑ boundary (tied 10 with EUNL.DE — HOLD; tie broken alphabetically)"
            in report)
    # Unflagged rows keep the plain verdict column.
    assert "BUY   #1 of 5 " in report


def test_a_same_verdict_tie_adds_nothing_to_the_report():
    from aristos_council.pipeline import format_cli_report

    report = format_cli_report(_result([_rt("A", "hold", 10), _rt("B", "hold", 10)]))
    assert "boundary" not in report
    assert "(tied)" in report                      # the tie itself is still disclosed


def test_pipeline_reexport_delegates_to_the_rank_engine():
    from aristos_council.pipeline import tie_boundary_notes

    assert tie_boundary_notes(_ETF) == boundary_tie_notes(_ETF)
