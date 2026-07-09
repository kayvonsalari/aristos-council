"""Tie disclosure (ITEM 7) — display only.

When two ranked names share a combined score across a verdict boundary (MRK/PG both
20.0, HOLD/SELL), the lower row is annotated `(=<score> — tie broken alphabetically)`.
Ordering, cutting, and verdicts are unchanged — this only discloses the tie-break.
"""

from __future__ import annotations

from aristos_council.pipeline import tie_boundary_notes
from aristos_council.rank_engine import RankedTicker


def _rt(ticker, verdict, combined):
    return RankedTicker(ticker=ticker, factor_ranks={"f": 1.0}, factor_values={"f": 1.0},
                        combined_rank=float(combined), universe_size=5, verdict=verdict)


# The conservative-run shape: MRK (HOLD) and PG (SELL) both combined 20.0.
_RANKED = [_rt("DUK", "buy", 9), _rt("SO", "buy", 12), _rt("MRK", "hold", 20),
           _rt("PG", "sell", 20), _rt("CL", "sell", 22)]


def test_tie_across_a_verdict_boundary_annotates_the_lower_row():
    notes = tie_boundary_notes(_RANKED)
    assert notes == {"PG": "(=20.0 — tie broken alphabetically)"}


def test_tie_within_the_same_verdict_is_not_annotated():
    # CL is also SELL and tied with... no; give two HOLDs at the same score -> no boundary.
    ranked = [_rt("A", "hold", 10), _rt("B", "hold", 10)]
    assert tie_boundary_notes(ranked) == {}


def test_no_tie_no_annotation():
    ranked = [_rt("A", "buy", 9), _rt("B", "hold", 12), _rt("C", "sell", 15)]
    assert tie_boundary_notes(ranked) == {}


def test_excluded_names_are_ignored():
    ranked = list(_RANKED)
    ranked.append(RankedTicker(ticker="X", factor_ranks={}, factor_values={},
                               combined_rank=20.0, universe_size=5, verdict="hold",
                               excluded=True, reason="screen"))
    assert tie_boundary_notes(ranked) == {"PG": "(=20.0 — tie broken alphabetically)"}


def test_annotation_flows_into_the_cli_report():
    from aristos_council.pipeline import RankPipelineResult, format_cli_report

    note = tie_boundary_notes(_RANKED)["PG"]
    result = RankPipelineResult(
        ranked=_RANKED, excluded=[], unrateable=[], narratives={},
        header="Verdict: deterministic ranker.  Narrative: none (ranker-only — no LLM ran).",
        meta={"rank_strategy_id": "s", "screen_strategy_id": "sc", "universe_id": "u",
              "council_mode": "ranker-only", "ranker_only": True, "universe_size": 5,
              "ranked_count": 5, "shortlist": [], "est_cost": 0.0},
        council_mode="ranker-only")
    assert note in format_cli_report(result)
