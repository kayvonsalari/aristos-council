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
