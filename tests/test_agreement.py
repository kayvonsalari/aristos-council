"""SHORTLIST-3 — every lens is an equal vote; the shortlist is an agreement table.

SHORTLIST-1 made ONE lens the primary and treated every other as a doubt about its picks.
SHORTLIST-2 then carved an exception into that for names every lens liked. Both were
answering the same question — which of these BUYs should I take seriously — with a
hierarchy the owner does not hold.

The owner's rule (2026-09-17): there is no primary lens. Every lens the user ticks is a
vote of equal weight. A CHECK lens does not vote; it marks. The valuation band does not
veto; it marks. The shortlist is the names ordered by how many voting lenses rated them
BUY, with the marks beside them.

Nothing is DECIDED here that the run did not already decide. Every vote is a verdict a
lens issued, every mark is a verdict or a percentile the run recorded, and the ordering is
a count. What these tests guard is that the count is the answer — and that a mark is never
quietly promoted back into a veto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

from aristos_council.pipeline import (SHORTLIST_BAND_CUTOFF, lens_agreement, lens_agreement_table,
                                      band_mark, check_mark)
from aristos_council.tools.valuation_band import ValuationBand


# --------------------------------------------------------------------------- #
# A multi-result shaped exactly as much as the rule reads
# --------------------------------------------------------------------------- #
@dataclass
class _Factor:
    name: str


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


@dataclass
class _Multi:
    strategy_ids: list
    strategy_names: dict
    results: dict


def _band(pct=50.0):
    return ValuationBand(percentile=pct, basis="ev_ebit", current=12.0,
                         median_multiple=10.0, months_covered=61, months_total=61,
                         window_years=5)


def _abstained(note="insufficient history: 1.1y"):
    return ValuationBand(note=note)


A, B, C, CHK = "a_v1", "b_v1", "c_v1", "chk_v1"
LABELS = {A: "Cyclical Income", B: "Magic Formula RAW", C: "Value + Momentum",
          CHK: "Forensic"}


def _multi(**rows_by_lens):
    """A run from ``a_v1=[...], chk_v1=[...]`` keyword lists, in that order."""
    results = {}
    for sid, rows in rows_by_lens.items():
        kind = "check" if sid == CHK else "selector"
        results[sid] = _Res(list(rows), _Strat(kind))
    return _Multi(list(results), {k: LABELS[k] for k in results}, results)


# --------------------------------------------------------------------------- #
# The count is the answer
# --------------------------------------------------------------------------- #
def test_names_are_ordered_by_how_many_lenses_rated_them_BUY():
    ag = lens_agreement(_multi(
        a_v1=[_Row("ONE", "buy", 1), _Row("TWO", "buy", 2), _Row("THREE", "hold", 3)],
        b_v1=[_Row("ONE", "buy", 1), _Row("TWO", "hold", 2), _Row("THREE", "buy", 3)],
        c_v1=[_Row("ONE", "buy", 1), _Row("TWO", "hold", 2), _Row("THREE", "hold", 3)]))
    assert [r.ticker for r in ag.rows] == ["ONE", "TWO", "THREE"]
    assert [r.buy_votes for r in ag.rows] == [3, 1, 1]
    assert ag.n_voting == 3


def test_the_row_names_the_lenses_that_voted_for_it():
    """A bare "2 of 3" makes a reader go and look. Naming the lenses is the difference
    between a score and a reading."""
    ag = lens_agreement(_multi(a_v1=[_Row("X", "buy", 1)], b_v1=[_Row("X", "buy", 1)],
                          c_v1=[_Row("X", "sell", 1)]))
    row = ag.rows[0]
    assert row.buy_lenses == ("Cyclical Income", "Magic Formula RAW")
    assert row.sell_lenses == ("Value + Momentum",)
    cols, rows = lens_agreement_table(ag)
    assert rows[0]["BUY votes"] == "2 of 3: Cyclical Income, Magic Formula RAW"
    assert rows[0]["SELL votes"] == "1 of 3: Value + Momentum"


def test_a_tie_on_votes_is_broken_by_SELL_votes_then_by_rank():
    """Tie-breakers only: they never decide which names appear, just the order of names
    the lenses agreed on equally."""
    ag = lens_agreement(_multi(
        a_v1=[_Row("DOUBTED", "buy", 1), _Row("CLEAN", "buy", 2), _Row("BETTER", "buy", 3)],
        b_v1=[_Row("DOUBTED", "sell", 1), _Row("CLEAN", "hold", 3), _Row("BETTER", "hold", 2)]))
    # all three have 1 BUY vote; DOUBTED carries a SELL, so it sorts last, and of the two
    # clean names the better-ranked one leads.
    assert [r.ticker for r in ag.rows] == ["BETTER", "CLEAN", "DOUBTED"]


def test_only_names_with_at_least_one_BUY_are_listed_and_the_rest_are_counted():
    """A table of everything no lens picked is not a shortlist."""
    ag = lens_agreement(_multi(
        a_v1=[_Row("YES", "buy", 1), _Row("NO", "hold", 2), _Row("ALSONO", "sell", 3)]))
    assert [r.ticker for r in ag.rows] == ["YES"]
    assert ag.no_buy_count == 2


# --------------------------------------------------------------------------- #
# A check MARKS; it never drops
# --------------------------------------------------------------------------- #
def test_a_check_lens_does_not_vote():
    ag = lens_agreement(_multi(a_v1=[_Row("X", "buy", 1)], chk_v1=[_Row("X", "buy", 1)]))
    assert ag.voting_ids == [A] and ag.check_ids == [CHK]
    assert ag.n_voting == 1
    assert ag.rows[0].buy_votes == 1              # the check's BUY is not a vote


def test_a_check_SELL_is_a_MARK_and_the_name_stays():
    """Under SHORTLIST-1 this removed the name outright. It is now a caution beside it —
    the doubt is still stated, and the reader decides what it is worth."""
    ag = lens_agreement(_multi(a_v1=[_Row("X", "buy", 1)], chk_v1=[_Row("X", "sell", 9)]))
    assert [r.ticker for r in ag.rows] == ["X"]
    assert "doubted by Forensic" in ag.rows[0].marks
    assert ag.rows[0].buy_votes == 1              # ...and the vote is untouched


def test_a_check_BUY_or_HOLD_is_not_a_mark():
    """Its BUY says only that it found nothing to doubt, which is not an endorsement."""
    for verdict in ("buy", "hold"):
        ag = lens_agreement(_multi(a_v1=[_Row("X", "buy", 1)],
                              chk_v1=[_Row("X", verdict, 1)]))
        assert ag.rows[0].marks == []
    assert check_mark("Forensic", "sell") == "doubted by Forensic"
    assert check_mark("Forensic", "buy") == ""


def test_a_run_of_only_CHECK_lenses_has_no_agreement_to_report():
    """A check marks rather than picks, so a run of nothing but checks picked nothing —
    and says so, rather than rendering an empty table."""
    ag = lens_agreement(_multi(chk_v1=[_Row("X", "buy", 1)]))
    assert not ag.available
    assert ag.rows == []
    assert "check marks rather than picks" in ag.rule_sentence


# --------------------------------------------------------------------------- #
# The band MARKS; it never vetoes
# --------------------------------------------------------------------------- #
def test_a_dear_band_is_a_MARK_and_the_name_stays():
    """Under SHORTLIST-1 the band dropped the name; SHORTLIST-2 then had to carve an
    exception for names every lens liked. Neither is needed once the band only marks."""
    ag = lens_agreement(_multi(a_v1=[_Row("X", "buy", 1, _band(95))]))
    assert [r.ticker for r in ag.rows] == ["X"]
    assert "priced high: 95th percentile of its own 5-year range" in ag.rows[0].marks


def test_the_band_mark_fires_at_the_cutoff_and_not_below_it():
    assert band_mark(SHORTLIST_BAND_CUTOFF).startswith("priced high")
    assert band_mark(SHORTLIST_BAND_CUTOFF - 0.1) == ""
    assert band_mark(None) == ""


def test_an_ABSTAINED_band_is_stated_as_unevaluated_not_as_dear():
    """Null is not false, house rule 3: "we could not tell" is not "it is expensive"."""
    ag = lens_agreement(_multi(a_v1=[_Row("X", "buy", 1, _abstained())]))
    marks = ag.rows[0].marks
    assert any(m.startswith("band not evaluated") for m in marks)
    assert not any("priced high" in m for m in marks)


def test_a_name_with_NO_band_at_all_carries_no_band_mark():
    """A band-off run marks nothing — silence about a thing never measured."""
    ag = lens_agreement(_multi(a_v1=[_Row("X", "buy", 1, None)]))
    assert ag.rows[0].marks == []


# --------------------------------------------------------------------------- #
# A lens that did not rank a name has not voted on it
# --------------------------------------------------------------------------- #
def test_a_name_one_voting_lens_EXCLUDED_counts_only_the_lenses_that_ranked_it():
    """An exclusion is not a NO vote. House rule 3 applied to verdicts: the lens has left
    the name unjudged, and counting that as a rejection would invent an opinion."""
    multi = _multi(a_v1=[_Row("X", "buy", 1)], b_v1=[])
    multi.results[B].excluded = [("X", "screen: min_dividend_yield (0.9 vs 1.5)")]
    ag = lens_agreement(multi)
    row = ag.rows[0]
    assert row.buy_votes == 1 and row.sell_votes == 0
    assert row.not_ranked == (("Magic Formula RAW",
                               "screen: min_dividend_yield (0.9 vs 1.5)"),)


def test_a_name_with_NO_DATA_under_one_lens_reads_the_same_way():
    multi = _multi(a_v1=[_Row("X", "buy", 1)], b_v1=[])
    multi.results[B].unrateable = [("X", "UNRATEABLE: no data — possibly delisted")]
    ag = lens_agreement(multi)
    assert ag.rows[0].not_ranked[0][0] == "Magic Formula RAW"
    assert "no data" in ag.rows[0].not_ranked[0][1]


def test_a_name_NO_voting_lens_ranked_is_absent_entirely():
    """A check's opinion alone is not a pick — the check does not pick."""
    ag = lens_agreement(_multi(a_v1=[_Row("OTHER", "buy", 1)], chk_v1=[_Row("X", "buy", 1)]))
    assert [r.ticker for r in ag.rows] == ["OTHER"]


# --------------------------------------------------------------------------- #
# The overlap note
# --------------------------------------------------------------------------- #
# Two lenses reading the same three numbers are not two opinions; they are one opinion
# counted twice — and on a table whose whole meaning is "how many lenses agreed", that
# inflates the answer.

def _with_factors(**factors_by_lens):
    results = {}
    for sid, names in factors_by_lens.items():
        kind = "check" if sid == CHK else "selector"
        results[sid] = _Res([_Row("X", "buy", 1)],
                            _Strat(kind, [_Factor(n) for n in names]))
    return _Multi(list(results), {k: LABELS[k] for k in results}, results)


def test_the_overlap_note_fires_for_two_lenses_with_the_same_factors():
    ag = lens_agreement(_with_factors(b_v1=["roic", "earnings_yield", "momentum_12m"],
                                 c_v1=["momentum_12m", "roic", "earnings_yield"]))
    assert ag.overlap_note == ("Magic Formula RAW and Value + Momentum rank on the same "
                               "3 factors; ticking both counts one view twice.")


def test_it_does_NOT_fire_for_different_factor_sets():
    ag = lens_agreement(_with_factors(a_v1=["net_payout_yield", "payout_coverage_fcf"],
                                 b_v1=["roic", "earnings_yield", "momentum_12m"]))
    assert ag.overlap_note == ""


def test_it_is_detected_from_the_strategies_not_from_a_hardcoded_pair():
    """A lens added tomorrow is caught with no code change."""
    ag = lens_agreement(_with_factors(a_v1=["alpha", "beta"], b_v1=["beta", "alpha"]))
    assert "Cyclical Income and Magic Formula RAW" in ag.overlap_note


def test_a_lens_declaring_no_factors_never_triggers_it():
    """Two lenses with nothing recorded are not two lenses with the same thing."""
    ag = lens_agreement(_with_factors(a_v1=[], b_v1=[]))
    assert ag.overlap_note == ""


def test_a_CHECK_lens_sharing_factors_is_not_an_overlap():
    """The note is about double-counting VOTES, and a check casts none."""
    ag = lens_agreement(_with_factors(a_v1=["x", "y"], chk_v1=["y", "x"]))
    assert ag.overlap_note == ""


# --------------------------------------------------------------------------- #
# What it says about itself
# --------------------------------------------------------------------------- #
def test_a_single_lens_run_renders_the_table_with_one_column_of_votes():
    """Not "no section": one lens is still a vote, and "1 of 1" is still the answer."""
    ag = lens_agreement(_multi(a_v1=[_Row("X", "buy", 1)]))
    assert ag.available
    assert ag.title == "Shortlist — agreement across 1 voting lens"
    _cols, rows = lens_agreement_table(ag)
    assert rows[0]["BUY votes"] == "1 of 1: Cyclical Income"


def test_the_rule_sentence_names_the_checks_and_says_they_do_not_vote():
    ag = lens_agreement(_multi(a_v1=[_Row("X", "buy", 1)], b_v1=[_Row("X", "buy", 1)],
                          chk_v1=[_Row("X", "hold", 1)]))
    assert ag.rule_sentence == (
        "Names ordered by how many of the 2 voting lenses rated them BUY. Forensic and "
        "the price check do not vote; their doubts are shown as marks.")


def test_the_table_carries_one_column_per_check():
    ag = lens_agreement(_multi(a_v1=[_Row("X", "buy", 1, _band(40))],
                          chk_v1=[_Row("X", "sell", 9)]))
    cols, rows = lens_agreement_table(ag)
    assert cols == ["Name", "BUY votes", "SELL votes", "Forensic",
                    "Valuation percentile", "Marks"]
    assert rows[0]["Forensic"] == "SELL"
    assert rows[0]["Valuation percentile"] == "40th"
    assert rows[0]["Marks"] == "doubted by Forensic"


def test_a_name_a_check_never_ranked_says_so_rather_than_passing_silently():
    ag = lens_agreement(_multi(a_v1=[_Row("X", "buy", 1)], chk_v1=[_Row("OTHER", "buy", 1)]))
    _cols, rows = lens_agreement_table(ag)
    assert rows[0]["Forensic"] == "not ranked"


def test_the_summary_clause_states_the_shape_of_the_agreement():
    ag = lens_agreement(_multi(
        a_v1=[_Row("ALL", "buy", 1), _Row("SOME", "buy", 2), _Row("ALSO", "buy", 3)],
        b_v1=[_Row("ALL", "buy", 1), _Row("SOME", "hold", 2), _Row("ALSO", "hold", 3)],
        c_v1=[_Row("ALL", "buy", 1), _Row("SOME", "hold", 2), _Row("ALSO", "hold", 3)]))
    assert ag.summary_clause == (" — shortlist: 1 name BUY on all 3 voting lenses, "
                                 "2 on 1 of 3")
    assert ag.buckets() == {3: 1, 1: 2}


def test_an_empty_agreement_says_nothing_on_the_summary_line():
    """A zero is noise there, exactly as every other clause omits its zeros."""
    ag = lens_agreement(_multi(a_v1=[_Row("X", "hold", 1)]))
    assert ag.summary_clause == ""


# --------------------------------------------------------------------------- #
# The factor marker travels with the vote it qualifies
# --------------------------------------------------------------------------- #
def test_a_name_ranked_on_fewer_factors_carries_that_as_a_mark():
    """FACTOR-MARK-1, carried onto this table: a vote cast on two of three factors is
    still a vote, and the reader should be told which one it was."""
    ag = lens_agreement(_multi(a_v1=[_Row("X", "buy", 1,
                                     factor_ranks={"a": 1, "b": 2, "c": 3},
                                     imputed_factors=("c",))]))
    assert "Cyclical Income: ranked on 2 of 3 factors" in ag.rows[0].marks


def test_a_fully_measured_name_carries_no_such_mark():
    ag = lens_agreement(_multi(a_v1=[_Row("X", "buy", 1,
                                     factor_ranks={"a": 1, "b": 2})]))
    assert ag.rows[0].marks == []


# --------------------------------------------------------------------------- #
# What this change must NOT do
# --------------------------------------------------------------------------- #
def test_nothing_here_re_grades_anything():
    """Every vote is a verdict the lens issued and every mark is something the run
    recorded. The table is an arrangement of facts, not a new judgement."""
    multi = _multi(a_v1=[_Row("X", "buy", 1, _band(99))],
                   chk_v1=[_Row("X", "sell", 1)])
    before = [(r.ticker, r.verdict, r.cohort_position) for r in multi.results[A].ranked]
    lens_agreement(multi)
    after = [(r.ticker, r.verdict, r.cohort_position) for r in multi.results[A].ranked]
    assert before == after


def test_KNOWN_LIMIT_the_narration_plan_is_unchanged_by_the_agreement_table():
    """NARR-UNION-1 narrates the UNION of every lens's BUYs. SHORTLIST-3 does not touch
    that: the agreement table decides what the REPORT says, never what is narrated, so a
    name marked by Forensic or priced high is still narrated if any lens bought it.

    Pinned deliberately. Tying narration to the agreement would be a reasonable-sounding
    change that quietly makes the marks decide who gets explained."""
    from aristos_council.pipeline import narrated_union
    from tests.test_merged_multi_report import _multi as _real_multi
    from tests.test_multi_strategy_run import RAW, SCREENED

    # A REAL run, because narrated_union reads the combined grid rather than the raw
    # results — and the point of this pin is the real path.
    result = _real_multi([SCREENED, RAW])
    ag = result.lens_agreement
    narrated = set(narrated_union(result, "buys_only"))
    assert narrated                                   # something is narrated at all
    # every name on the agreement table is still narrated: the marks decide nothing here
    assert {r.ticker for r in ag.rows} <= narrated

    # ...and on a fabricated run, a marked name stays on the table AND keeps its vote.
    multi = _multi(a_v1=[_Row("MARKED", "buy", 1, _band(99)),
                         _Row("CLEAN", "buy", 2, _band(10))],
                   chk_v1=[_Row("MARKED", "sell", 1)])
    marked = lens_agreement(multi)
    assert {r.ticker for r in marked.rows} == {"MARKED", "CLEAN"}
    marks = [m for row in marked.rows for m in row.marks]
    assert any("doubted by Forensic" in m for m in marks)
    assert any("priced high" in m for m in marks)
