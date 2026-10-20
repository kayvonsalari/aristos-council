"""Batch 28 C/D - council opinion fixes: the rank-check false positive, the record heading, the table-fact
rule, the caps row."""
from __future__ import annotations

import pytest

from aristos_council.narration_schema import validate_narration
from tests.test_narration_schema import _well_formed


def test_a_price_over_an_eps_is_not_a_rank_and_is_never_flagged():
    n = _well_formed()
    n.neutral_context = ["The forward P/E is 93.90 / 3.30 on consensus, 28.5 / 3.4 on trailing."]
    n.open_questions = ["Does 93.90 / 3.30 hold?"]
    codes = [i.code for i in validate_narration(n, ranker_verdict="buy")]
    assert "impossible_rank" not in codes and "unparseable_rank" not in codes


def test_a_real_impossible_rank_is_still_flagged_and_listed_once():
    n = _well_formed()
    n.neutral_context = ["It ranks 9/6 on growth."]
    n.open_questions = ["It ranks 9/6 on growth."]          # the same sentence in a second field
    issues = [i for i in validate_narration(n, ranker_verdict="buy") if i.code == "impossible_rank"]
    assert len(issues) == 1 and '"9/6"' in issues[0].detail


def test_a_genuine_rank_with_a_trailing_full_stop_still_parses():
    n = _well_formed()
    n.neutral_context = ["It ranks 3/6."]
    assert all(i.code != "impossible_rank" for i in validate_narration(n, ranker_verdict="buy"))


# --------------------------------------------------------------------------- #
# C12 COUNCIL-RECORD-HEADING-1
# --------------------------------------------------------------------------- #
def _vote(label, verdict):
    from aristos_council.company_report import LensVote
    return LensVote(strategy_id=f"{label}_v1", label=label, kind="selector", asks="", status="ranked",
                    verdict=verdict, position=3, cohort_size=10)


def test_an_all_hold_record_is_stated_as_hold_not_as_zero_buys():
    from aristos_council.company_report import LensVote, build_agreement
    votes = [_vote("A", "hold"), _vote("B", "hold"),
             LensVote(strategy_id="c_v1", label="C", kind="selector", asks="", status="excluded", reason="x")]
    ag = build_agreement(votes)
    assert ag.headline == "HOLD, 2 of 2 votes; 1 lens did not apply to this company"
    assert "BUY" not in ag.headline


def test_the_echoes_buy_warning_disappears_once_the_heading_states_the_record():
    from aristos_council.company_report import build_agreement
    from aristos_council.narration_schema import validate_narration
    ag = build_agreement([_vote("A", "hold"), _vote("B", "hold")])
    n = _well_formed()
    n.echoed_verdict = ag.headline
    codes = [i.code for i in validate_narration(n, ranker_verdict="hold", verdict_of_record=ag.headline)]
    assert "verdict_mismatch" not in codes and "headline_does_not_restate_verdict_of_record" not in codes
