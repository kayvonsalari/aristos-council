"""Boundary ties in the NARRATION EVIDENCE (VERDICT-TIE-1).

The display half of the ruling is in ``test_tie_disclosure.py``. This is the evidence half:
when the alphabetical tie-break — not a score difference — decided which side of a verdict
boundary a name fell on (live 2026-07-28 ETF run: EUNL.DE HOLD / VWCE.DE SELL, both 10.0),
the fact is handed to the narrator so it can say so honestly, and the annotated table is
AUTHORITATIVE: prose that ORDERS the tied pair ("decisively ranked below EUNL.DE") is a
contradiction and gets annotated. Value comparisons between the same two names are not rank
claims and are left alone — the check never invents a contradiction.

Doctrine unchanged: math judges, LLM writes. No verdict, score or tie-break logic reads any
of this.
"""

from __future__ import annotations

from types import SimpleNamespace

from aristos_council.agents.nodes import _ranker_block
from aristos_council.narration_check import _sentences, check_narration
from aristos_council.pipeline import _annotate_narration, _council_stage
from aristos_council.rank_engine import RankedTicker, boundary_tie_facts
from aristos_council.state import Recommendation, ResearchState

_FACT = {"score": 10.0, "score_display": "10", "verdict": "sell",
         "partners": [{"ticker": "EUNL.DE", "verdict": "hold"}]}

_TABLE = {"N": 5, "combined_position": 5, "ticker": "VWCE.DE",
          "factors": {"expense_ratio": 4.0, "fund_size": 5.0},
          "boundary_tie": _FACT}


def _state(**kw) -> ResearchState:
    return ResearchState(ticker="VWCE.DE", strategy_id="s",
                         ranker_verdict=Recommendation.SELL, ranker_cohort_size=5, **kw)


# --------------------------------------------------------------------------- #
# The narrator's evidence block
# --------------------------------------------------------------------------- #
def test_the_ranker_block_states_the_boundary_tie():
    block = _ranker_block(_state(ranker_boundary_tie=_FACT))
    assert "BOUNDARY TIE" in block
    assert "rank-sum 10 is TIED with EUNL.DE (HOLD)" in block
    assert "alphabetical tie-break" in block
    # …and tells the writer not to order a tied pair (the contradiction the check flags).
    assert "Do NOT describe this name as ranked above, below" in block


def test_a_name_with_no_boundary_tie_gets_an_unchanged_block():
    assert "BOUNDARY TIE" not in _ranker_block(_state())
    assert _ranker_block(_state(ranker_boundary_tie={})) == _ranker_block(_state())


# --------------------------------------------------------------------------- #
# The fact-checker — the annotated table is authoritative
# --------------------------------------------------------------------------- #
def test_ordering_a_tied_pair_is_flagged():
    flags = check_narration("VWCE.DE is decisively ranked below EUNL.DE.", _TABLE)
    assert len(flags) == 1
    assert "orders a TIED pair" in flags[0]
    assert "tied with EUNL.DE at combined rank-sum 10" in flags[0]
    assert "VWCE.DE is decisively ranked below EUNL.DE" in flags[0]


def test_the_reverse_direction_is_flagged_too():
    # A tie cannot be ordered EITHER way — "ahead of" is as false as "below".
    table = dict(_TABLE, ticker="EUNL.DE", combined_position=4,
                 boundary_tie={"score": 10.0, "score_display": "10", "verdict": "hold",
                               "partners": [{"ticker": "VWCE.DE", "verdict": "sell"}]})
    flags = check_narration("EUNL.DE ranks ahead of VWCE.DE on the combined score.", table)
    assert len(flags) == 1 and "orders a TIED pair" in flags[0]


def test_stating_the_tie_honestly_is_not_flagged():
    honest = ("The combined rank-sum of 10 is tied with EUNL.DE, so the verdict split on "
              "the alphabetical tie-break rather than on a score difference.")
    assert check_narration(honest, _TABLE) == []


def test_a_value_comparison_with_the_tie_partner_is_not_a_rank_claim():
    # No rank/score word in the clause -> not a rank claim, never flagged.
    assert check_narration("Its expense ratio is lower than EUNL.DE's.", _TABLE) == []


def test_a_comparison_against_a_third_name_is_not_flagged():
    prose = "VWCE.DE and EUNL.DE both score lower than SXR8.DE on momentum."
    assert check_narration(prose, _TABLE) == []


def test_no_boundary_tie_means_no_tie_check():
    table = {k: v for k, v in _TABLE.items() if k != "boundary_tie"}
    assert check_narration("VWCE.DE is decisively ranked below EUNL.DE.", table) == []


def test_a_dotted_ticker_survives_sentence_splitting():
    # The tie partner is a European listing; a split "EUNL.DE" could never be matched.
    assert _sentences("EUNL.DE ranks 4th. VWCE.DE ranks 5th") == \
        ["EUNL.DE ranks 4th", "VWCE.DE ranks 5th"]


def test_decimals_are_still_atomic():
    assert _sentences("CAGR of 31.4% is high. Next") == ["CAGR of 31.4% is high", "Next"]


# --------------------------------------------------------------------------- #
# Wiring — the fact reaches the narrator, and the annotation reaches the prose
# --------------------------------------------------------------------------- #
def _rt(ticker, verdict, combined):
    return RankedTicker(ticker=ticker, factor_ranks={"f1": 5.0, "f2": 5.0},
                        factor_values={}, combined_rank=float(combined),
                        universe_size=5, verdict=verdict)


_RANKED = [_rt("SXR8.DE", "buy", 4), _rt("IWDA.AS", "hold", 6), _rt("VUSA.AS", "hold", 8),
           _rt("EUNL.DE", "hold", 10), _rt("VWCE.DE", "sell", 10)]


def test_annotate_narration_appends_the_tie_annotation():
    rep = SimpleNamespace(decision=SimpleNamespace(
        rationale="VWCE.DE is decisively ranked below EUNL.DE."))
    _annotate_narration(rep, _RANKED[-1], _FACT)
    assert "orders a TIED pair" in rep.decision.rationale
    # The prose itself is never rewritten — the annotation is appended.
    assert rep.decision.rationale.startswith(
        "VWCE.DE is decisively ranked below EUNL.DE.")


def test_council_stage_threads_the_boundary_fact_into_the_narrator_state(monkeypatch):
    seen: list[ResearchState] = []

    class _App:
        def invoke(self, state):
            seen.append(state)
            return state.model_dump()

    monkeypatch.setattr("aristos_council.graph.build_council", lambda *a, **k: _App())
    facts = boundary_tie_facts(_RANKED)
    # The shortlist is a SUBSET of the cohort — the facts must come from the whole cohort,
    # or a tie partner on the other side of the boundary is invisible.
    _council_stage([_RANKED[-1]], SimpleNamespace(id="sc"), None, None, "narrator",
                   boundary_ties=facts)

    assert len(seen) == 1
    assert seen[0].ranker_boundary_tie["partners"] == [
        {"ticker": "EUNL.DE", "verdict": "hold"}]
    assert "BOUNDARY TIE" in _ranker_block(seen[0])


def test_a_name_outside_a_boundary_tie_carries_no_fact(monkeypatch):
    seen: list[ResearchState] = []

    class _App:
        def invoke(self, state):
            seen.append(state)
            return state.model_dump()

    monkeypatch.setattr("aristos_council.graph.build_council", lambda *a, **k: _App())
    _council_stage([_RANKED[0]], SimpleNamespace(id="sc"), None, None, "narrator",
                   boundary_ties=boundary_tie_facts(_RANKED))
    assert seen[0].ranker_boundary_tie == {}
    assert "BOUNDARY TIE" not in _ranker_block(seen[0])
