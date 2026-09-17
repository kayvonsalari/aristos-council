"""NARR-2 — narrate the names the voting lenses AGREE on.

NARR-UNION-1 narrated the UNION of every lens's BUYs. On the 134-name oil dividend list
that is 28 names, 26 of them one lens's pick — so the expensive half of the run was spent
explaining names nothing else agreed with, and the two names both lenses chose were buried
among them.

The owner's rule (2026-09-17): narrate the names the voting lenses agree on, choose that
level, cap the count, and skip the marked ones unless asked otherwise. The agreement table
already holds the votes and the marks, so this is a FILTER over it — nothing here
re-grades, re-ranks or re-counts.

No live LLM call anywhere in this file (CLAUDE.md); the plan is pure and the narration
tests use the fake runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

from aristos_council.pipeline import (DEFAULT_NARRATION_CAP, DEFAULT_NARRATION_LEVEL,
                                      NARRATION_LEVELS, is_marked, narration_basis,
                                      narration_line, narration_plan, qualifies)
from aristos_council.tools.valuation_band import ValuationBand


# --------------------------------------------------------------------------- #
# A multi-result shaped exactly as much as the rule reads
# --------------------------------------------------------------------------- #
@dataclass
class _Strat:
    kind: str = "selector"
    factors: list = field(default_factory=list)


@dataclass
class _Row:
    ticker: str
    verdict: str = "hold"
    cohort_position: Optional[int] = None
    valuation_band: Optional[ValuationBand] = None
    excluded: bool = False
    factor_ranks: dict = field(default_factory=dict)
    imputed_factors: tuple = ()


@dataclass
class _Res:
    ranked: list
    rank_strategy: _Strat = field(default_factory=_Strat)
    names: dict = field(default_factory=dict)
    excluded: list = field(default_factory=list)
    unrateable: list = field(default_factory=list)


A, B, C, CHK = "a_v1", "b_v1", "c_v1", "chk_v1"
LABELS = {A: "Cyclical Income", B: "Magic Formula RAW", C: "Value + Momentum",
          CHK: "Forensic"}


def _band(pct=50.0):
    return ValuationBand(percentile=pct, basis="ev_ebit", current=12.0,
                         median_multiple=10.0, months_covered=61, months_total=61,
                         window_years=5)


def _multi(**rows_by_lens):
    """A REAL MultiStrategyResult carrying a REAL agreement table, so the plan is tested
    through the object the run actually produces."""
    from aristos_council.pipeline import MultiStrategyResult, lens_agreement

    results = {}
    for sid, rows in rows_by_lens.items():
        kind = "check" if sid == CHK else "selector"
        results[sid] = _Res(list(rows), _Strat(kind))
    built = MultiStrategyResult(
        strategy_ids=list(results), strategy_names={k: LABELS[k] for k in results},
        results=results, rows=[], meta={})
    return MultiStrategyResult(
        strategy_ids=built.strategy_ids, strategy_names=built.strategy_names,
        results=built.results, rows=[], meta={},
        lens_agreement=lens_agreement(built))


def _three_voting():
    """Three voting lenses over four names, one at each level of agreement."""
    return _multi(
        a_v1=[_Row("ALL", "buy", 1), _Row("MOST", "buy", 2), _Row("ONE", "buy", 3),
              _Row("NONE", "hold", 4)],
        b_v1=[_Row("ALL", "buy", 1), _Row("MOST", "buy", 2), _Row("ONE", "hold", 3),
              _Row("NONE", "hold", 4)],
        c_v1=[_Row("ALL", "buy", 1), _Row("MOST", "hold", 2), _Row("ONE", "hold", 3),
              _Row("NONE", "sell", 4)])


# --------------------------------------------------------------------------- #
# The three levels
# --------------------------------------------------------------------------- #
def test_ALL_narrates_only_what_every_voting_lens_bought():
    plan = narration_plan(_three_voting(), level="all", cap=60)
    assert plan["names"] == ["ALL"]
    assert plan["qualified"] == ["ALL"]


def test_MOST_narrates_what_more_than_half_bought():
    plan = narration_plan(_three_voting(), level="most", cap=60)
    assert plan["names"] == ["ALL", "MOST"]


def test_ANY_narrates_every_name_on_the_table():
    plan = narration_plan(_three_voting(), level="any", cap=60)
    assert plan["names"] == ["ALL", "MOST", "ONE"]
    assert "NONE" not in plan["names"]          # no lens bought it, so it is not there


def test_the_default_level_is_ALL():
    """The strictest, because it is the one that answers "what did they agree on" — the
    question the levers exist for."""
    assert DEFAULT_NARRATION_LEVEL == "all"
    assert narration_plan(_three_voting(), cap=60)["names"] == ["ALL"]


def test_MOST_equals_ALL_with_two_voting_lenses():
    """More than half of two IS two. The UI says so rather than offering a choice that is
    not one."""
    two = _multi(a_v1=[_Row("BOTH", "buy", 1), _Row("ONE", "buy", 2)],
                 b_v1=[_Row("BOTH", "buy", 1), _Row("ONE", "hold", 2)])
    assert narration_plan(two, level="most", cap=60)["names"] == \
        narration_plan(two, level="all", cap=60)["names"] == ["BOTH"]


def test_a_SINGLE_voting_lens_makes_the_three_levels_one():
    one = _multi(a_v1=[_Row("X", "buy", 1), _Row("Y", "hold", 2)])
    plans = {lvl: narration_plan(one, level=lvl, cap=60)["names"]
             for lvl in NARRATION_LEVELS}
    assert plans == {"all": ["X"], "most": ["X"], "any": ["X"]}


def test_a_CHECK_lens_never_counts_towards_the_level():
    """It does not vote, so "all voting lenses agree" cannot mean "and Forensic too"."""
    with_check = _multi(a_v1=[_Row("X", "buy", 1)], chk_v1=[_Row("X", "hold", 1)])
    assert narration_plan(with_check, level="all", cap=60)["names"] == ["X"]


def test_the_level_predicate_is_pure_and_testable_on_its_own():
    from aristos_council.pipeline import LensAgreementRow

    row = LensAgreementRow(ticker="X", display="X", buy_lenses=("a", "b"))
    assert qualifies(row, "all", 2) and qualifies(row, "most", 3)
    assert not qualifies(row, "all", 3)
    assert qualifies(row, "any", 9)


# --------------------------------------------------------------------------- #
# The cap
# --------------------------------------------------------------------------- #
def test_the_cap_truncates_in_AGREEMENT_TABLE_order():
    """Not alphabetically and not by rank: the table's order is the answer's order, so a
    cap takes the top of it."""
    plan = narration_plan(_three_voting(), level="any", cap=2)
    assert plan["names"] == ["ALL", "MOST"]
    assert [n["ticker"] for n in plan["not_narrated"]] == ["ONE"]


def test_the_default_cap_is_ten():
    assert DEFAULT_NARRATION_CAP == 10


def test_a_cap_of_zero_narrates_nothing_and_says_so():
    plan = narration_plan(_three_voting(), level="any", cap=0)
    assert plan["names"] == [] and len(plan["not_narrated"]) == 3
    assert "Narrated 0 of 3" in narration_line(plan)


# --------------------------------------------------------------------------- #
# The skip flag
# --------------------------------------------------------------------------- #
def _marked():
    """Two unanimous names, one doubted by the check, one priced high."""
    return _multi(
        a_v1=[_Row("DOUBTED", "buy", 1), _Row("DEAR", "buy", 2, _band(95)),
              _Row("CLEAN", "buy", 3, _band(20))],
        b_v1=[_Row("DOUBTED", "buy", 1), _Row("DEAR", "buy", 2), _Row("CLEAN", "buy", 3)],
        chk_v1=[_Row("DOUBTED", "sell", 1), _Row("DEAR", "hold", 2),
                _Row("CLEAN", "hold", 3)])


def test_skip_ON_leaves_out_the_doubted_and_the_priced_high():
    plan = narration_plan(_marked(), level="all", cap=60, skip_marked=True)
    assert plan["names"] == ["CLEAN"]
    assert {n["ticker"] for n in plan["not_narrated"]} == {"DOUBTED", "DEAR"}


def test_skip_OFF_narrates_them_and_the_narrator_is_told_the_mark():
    plan = narration_plan(_marked(), level="all", cap=60, skip_marked=False)
    assert set(plan["names"]) == {"DOUBTED", "DEAR", "CLEAN"}
    assert plan["not_narrated"] == []


def test_the_skipped_names_are_LISTED_with_their_marks_not_silently_dropped():
    plan = narration_plan(_marked(), level="all", cap=60, skip_marked=True)
    by_ticker = {n["ticker"]: n for n in plan["not_narrated"]}
    assert "doubted by Forensic" in by_ticker["DOUBTED"]["marks"]
    assert any("priced high" in m for m in by_ticker["DEAR"]["marks"])
    assert by_ticker["DOUBTED"]["buy_votes"] == 2


def test_NOT_every_mark_skips():
    """"band not evaluated" is an ABSENCE of a reading, not a doubt, and "ranked on 2 of 3
    factors" is a disclosure about the vote rather than about the company. Skipping on
    either would drop names for the run's own gaps."""
    from aristos_council.pipeline import LensAgreementRow

    unmeasured = LensAgreementRow(ticker="X", display="X", buy_lenses=("a",),
                                  band_note="insufficient history: 1.1y")
    partial = LensAgreementRow(ticker="Y", display="Y", buy_lenses=("a",),
                               factor_notes=("a: ranked on 2 of 3 factors",))
    assert not is_marked(unmeasured)
    assert not is_marked(partial)


# --------------------------------------------------------------------------- #
# What the run records and what the report says
# --------------------------------------------------------------------------- #
def test_the_plan_is_recorded_on_the_run():
    """"Which rule did this run apply, and what did it leave out" must be answerable from
    the record rather than by re-deriving it."""
    from aristos_council.pipeline import narrate_multi_strategy

    result = _marked()
    result.meta["council_frame"] = None            # nothing to narrate; the plan still runs
    narrated = narrate_multi_strategy(result, level="all", cap=5, skip_marked=True)
    plan = (narrated.meta or result.meta).get("narration")
    assert plan is not None
    assert plan["level"] == "all" and plan["cap"] == 5 and plan["skip_marked"] is True
    assert plan["selected"] == ["CLEAN"]
    assert {n["ticker"] for n in plan["not_narrated"]} == {"DOUBTED", "DEAR"}


def test_the_line_states_the_rule_and_the_counts():
    plan = narration_plan(_marked(), level="all", cap=60, skip_marked=True)
    assert narration_line(plan) == (
        "Narrated 1 of 3 names that met the rule (all voting lenses agree; doubted or "
        "priced-high names skipped).")


def test_a_run_that_narrated_NOTHING_says_which_rule_produced_nothing():
    """Never a silent empty section. An absent section is indistinguishable from a feature
    that was never switched on — the corollary this repo has paid for twice."""
    nobody = _multi(a_v1=[_Row("X", "hold", 1)], b_v1=[_Row("X", "hold", 1)])
    plan = narration_plan(nobody, level="all", cap=60)
    assert plan["count"] == 0
    assert narration_line(plan) == (
        "No company met the rule (all voting lenses agree); nothing narrated. "
        "Change the rule to narrate more.")


def test_the_basis_reads_as_a_rule_in_words():
    assert narration_basis("all", True) == (
        "all voting lenses agree; doubted or priced-high names skipped")
    assert narration_basis("any", False) == "any voting lens"


# --------------------------------------------------------------------------- #
# What this must NOT change
# --------------------------------------------------------------------------- #
def test_the_plan_never_reaches_for_a_name_NO_lens_bought():
    for level in NARRATION_LEVELS:
        plan = narration_plan(_three_voting(), level=level, cap=60)
        assert "NONE" not in plan["names"]


def test_narrating_keeps_the_agreement_table_on_the_result():
    """REGRESSION. The multi narrator rebuilt the result by hand, listing the fields it
    carried over — so every field added since was silently dropped by narrating.
    SHORTLIST-3's ``lens_agreement`` was, and the NARR-2 plan then read a narrated run as
    "nothing qualifies"."""
    from aristos_council.pipeline import narrate_multi_strategy

    result = _three_voting()
    result.meta["council_frame"] = None
    narrated = narrate_multi_strategy(result)
    assert narrated.lens_agreement is not None
    assert [r.ticker for r in narrated.lens_agreement.rows] == \
        [r.ticker for r in result.lens_agreement.rows]


def test_a_single_lens_result_keeps_its_own_shortlist_plan():
    """One lens is still a vote, and a single-lens run has no agreement table to filter."""
    class _Single:
        meta = {"shortlist": ["A", "B"]}

    plan = narration_plan(_Single(), level="all", cap=60)
    assert plan["names"] == ["A", "B"]
    assert plan["basis"] == "every name the ranker rated BUY"
