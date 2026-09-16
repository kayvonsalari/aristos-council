"""SHORTLIST-1 — the names a selector picked that no check doubted.

Two runs on 2026-09-16 showed the grid gives a reader no way to tell WHICH BUYs to take
seriously: the lenses answer different questions, so a BUY on one beside a SELL on another
is not a contradiction. The owner's reading rule, made structural: ONE SELECTOR decides
"worth owning"; CHECK lenses and the valuation band only say "reason to doubt that yes".

The rule is DERIVED and has no judgement in it. Every input is a verdict or a percentile
the run already produced; nothing is re-graded, re-ranked or re-weighted. Two properties
matter most and are pinned here:

  * a band that ABSTAINED never drops a name (null is not false, house rule 3 — "we could
    not tell" is not "it is expensive");
  * an EMPTY shortlist with reasons is a RESULT, and reads differently from "there was no
    selector to ask" — which is why ``reason`` is set only in the second case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

from aristos_council.pipeline import (
    SHORTLIST_BAND_CUTOFF,
    shortlist,
    shortlist_table,
)
from aristos_council.tools.valuation_band import ValuationBand


# --------------------------------------------------------------------------- #
# A multi-result shaped exactly as much as the rule reads
# --------------------------------------------------------------------------- #
@dataclass
class _Strat:
    kind: str = "selector"


@dataclass
class _Row:
    ticker: str
    verdict: str = "hold"
    cohort_position: Optional[int] = None
    valuation_band: Optional[ValuationBand] = None
    excluded: bool = False


@dataclass
class _Res:
    ranked: list
    rank_strategy: _Strat = field(default_factory=_Strat)
    names: dict = field(default_factory=dict)


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


SEL, CHK, CHK2 = "sel_v1", "chk_v1", "chk2_v1"


def _multi(primary_rows, check_rows=None, check2_rows=None, *, ids=None):
    results = {SEL: _Res(primary_rows)}
    names = {SEL: "Selector", CHK: "Forensic", CHK2: "Quality"}
    if check_rows is not None:
        results[CHK] = _Res(check_rows, _Strat("check"))
    if check2_rows is not None:
        results[CHK2] = _Res(check2_rows, _Strat("check"))
    return _Multi(ids or list(results), names, results)


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #
def test_a_buy_no_check_doubts_survives():
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(40))],
                          [_Row("A", "hold")]), primary_id=SEL)
    assert [r.ticker for r in sl.kept] == ["A"]
    assert sl.dropped == []
    assert sl.title == "Shortlist — 1 of 1 BUY survived the checks"


def test_a_buy_a_check_rates_sell_is_dropped_and_the_check_is_named():
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(40))],
                          [_Row("A", "sell")]), primary_id=SEL)
    assert sl.kept == []
    assert [r.ticker for r in sl.dropped] == ["A"]
    assert sl.dropped[0].dropped_by == "SELL under Forensic"


def test_every_doubting_check_is_named_not_just_the_first():
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(40))],
                          [_Row("A", "sell")], [_Row("A", "sell")]), primary_id=SEL)
    assert sl.dropped[0].dropped_by == "SELL under Forensic, Quality"


def test_a_check_rating_buy_or_hold_does_not_doubt():
    """A check only ever SUBTRACTS. Its BUY is not an endorsement."""
    for verdict in ("buy", "hold"):
        sl = shortlist(_multi([_Row("A", "buy", 1, _band(40))],
                              [_Row("A", verdict)]), primary_id=SEL)
        assert [r.ticker for r in sl.kept] == ["A"], verdict


def test_a_dear_valuation_drops_the_name_and_names_the_percentile():
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(85))],
                          [_Row("A", "hold")]), primary_id=SEL)
    assert sl.kept == []
    assert "85th percentile of its own history" in sl.dropped[0].dropped_by
    assert "drops 80th and above" in sl.dropped[0].dropped_by


def test_the_band_cutoff_is_inclusive_at_the_boundary():
    """>= the cutoff drops; just below it keeps. No off-by-one at the edge."""
    at = shortlist(_multi([_Row("A", "buy", 1, _band(SHORTLIST_BAND_CUTOFF))]),
                   primary_id=SEL)
    assert at.kept == [] and at.dropped
    just_under = shortlist(_multi([_Row("A", "buy", 1, _band(79.9))]), primary_id=SEL)
    assert [r.ticker for r in just_under.kept] == ["A"]


def test_an_abstained_band_keeps_the_name_and_flags_it():
    """NULL IS NOT FALSE. "We could not tell" is not "it is expensive"."""
    sl = shortlist(_multi([_Row("A", "buy", 1, _abstained())],
                          [_Row("A", "hold")]), primary_id=SEL)
    assert [r.ticker for r in sl.kept] == ["A"]
    assert sl.kept[0].band_percentile is None
    assert "insufficient history" in sl.kept[0].band_note
    cols, rows = shortlist_table(sl)
    assert rows[0]["Valuation percentile"].startswith("not evaluated — ")


def test_a_name_with_no_band_at_all_is_kept():
    """A band-OFF run must not silently empty the shortlist."""
    sl = shortlist(_multi([_Row("A", "buy", 1, None)]), primary_id=SEL)
    assert [r.ticker for r in sl.kept] == ["A"]


def test_a_check_doubt_takes_precedence_over_the_band():
    """One reason per drop, and the doubt is the more specific one."""
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(95))],
                          [_Row("A", "sell")]), primary_id=SEL)
    assert sl.dropped[0].dropped_by == "SELL under Forensic"


def test_only_the_primarys_buys_are_candidates():
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(40)),
                           _Row("B", "hold", 2, _band(10)),
                           _Row("C", "sell", 3, _band(5))]), primary_id=SEL)
    assert sl.candidates == 1 and [r.ticker for r in sl.kept] == ["A"]


def test_an_excluded_name_is_never_a_candidate():
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(40), excluded=True)]),
                   primary_id=SEL)
    assert not sl.available and "rated no name BUY" in sl.reason


def test_the_kept_order_is_the_primarys_rank_order():
    rows = [_Row(t, "buy", i + 1, _band(40)) for i, t in enumerate("ABCD")]
    sl = shortlist(_multi(rows), primary_id=SEL)
    assert [r.ticker for r in sl.kept] == ["A", "B", "C", "D"]
    assert [r.rank_position for r in sl.kept] == [1, 2, 3, 4]


# --------------------------------------------------------------------------- #
# When no list can be formed at all
# --------------------------------------------------------------------------- #
def test_a_check_lens_as_primary_yields_the_no_selector_reason():
    m = _multi([_Row("A", "buy", 1, _band(40))])
    m.results[SEL].rank_strategy = _Strat("check")
    sl = shortlist(m, primary_id=SEL)
    assert not sl.available
    assert "no selector among the lenses picked" in sl.reason
    assert "it doubts, it does not select" in sl.reason
    assert sl.kept == [] and sl.dropped == []


def test_a_selector_that_picked_nothing_says_so():
    sl = shortlist(_multi([_Row("A", "hold", 1, _band(40))]), primary_id=SEL)
    assert not sl.available and "rated no name BUY" in sl.reason


def test_an_empty_shortlist_with_reasons_is_a_RESULT_not_an_absence():
    """The distinction the `reason` field exists for: every candidate was doubted, which
    is a finding, and must not read as "there was nothing to check"."""
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(95)),
                           _Row("B", "buy", 2, _band(99))]), primary_id=SEL)
    assert sl.available                      # a list WAS formed
    assert sl.kept == [] and len(sl.dropped) == 2
    assert sl.reason == ""
    assert sl.title == "Shortlist — 0 of 2 BUYs survived the checks"


def test_a_missing_primary_is_reported_not_raised():
    sl = shortlist(_multi([_Row("A", "buy")]), primary_id="nope_v1")
    assert not sl.available and "did not run" in sl.reason


# --------------------------------------------------------------------------- #
# What it says about itself
# --------------------------------------------------------------------------- #
def test_the_rule_sentence_names_the_lenses_and_the_cutoff():
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(40))],
                          [_Row("A", "hold")]), primary_id=SEL)
    assert sl.rule_sentence == (
        "BUY under Selector, not SELL under Forensic, and below the 80th percentile of "
        "its own valuation history.")


def test_the_rule_sentence_reads_correctly_with_no_checks():
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(40))]), primary_id=SEL)
    assert sl.rule_sentence == ("BUY under Selector and below the 80th percentile of its "
                                "own valuation history.")


def test_the_table_carries_one_column_per_check():
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(40))],
                          [_Row("A", "hold")], [_Row("A", "buy")]), primary_id=SEL)
    cols, rows = shortlist_table(sl)
    assert cols == ["Name", "Rank", "Forensic", "Quality", "Valuation percentile"]
    assert rows[0]["Forensic"] == "HOLD" and rows[0]["Quality"] == "BUY"
    assert rows[0]["Rank"] == "#1" and rows[0]["Valuation percentile"] == "40th"


def test_a_name_a_check_never_ranked_says_so_rather_than_passing_silently():
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(40))], [_Row("B", "hold")]),
                   primary_id=SEL)
    cols, rows = shortlist_table(sl)
    assert rows[0]["Forensic"] == "not ranked"
    assert [r.ticker for r in sl.kept] == ["A"]      # an absent check never doubts


# --------------------------------------------------------------------------- #
# What this branch deliberately does NOT change
# --------------------------------------------------------------------------- #
def test_KNOWN_LIMIT_the_narration_plan_is_unchanged_by_the_shortlist():
    """SHORTLIST-1 does not re-point narration at the shortlist. The owner may later
    choose to narrate the shortlist instead of the union of every lens's BUYs; that is a
    separate decision with its own cost consequences, and this pins the plan until it is
    made.

    Asserted on a REAL pipeline run rather than a stub, because the claim is about what
    the shipped plan does: the narration union is still computed from the GRID (every
    lens's BUYs), not from the shortlist, so a name the shortlist drops is still planned
    for narration and a name only a non-primary lens bought is still in the plan."""
    from datetime import date

    from aristos_council.pipeline import narrated_union, run_multi_strategy_pipeline
    from tests.test_multi_strategy_run import (
        RAW, SCREENED, STRAT_DIR, TODAY, UNIVERSE, _Adapter)

    res = run_multi_strategy_pipeline(UNIVERSE, [SCREENED, RAW],
                                      strategies_dir=STRAT_DIR, adapter=_Adapter(),
                                      today=TODAY)
    plan = narrated_union(res, "buys_only")
    # The plan is the union of every lens's BUYs — including lenses that are NOT the
    # primary, which a shortlist never consults.
    union = {row.ticker for row in res.rows
             if any(c.status == "ranked" and c.verdict == "buy"
                    for c in row.cells.values())}
    assert set(plan) == union and plan

    # The shortlist is drawn only from the PRIMARY's BUYs, so it can never exceed the
    # plan — and the plan does not shrink to it.
    sl = res.shortlist
    assert sl is not None
    assert {r.ticker for r in sl.kept} <= set(plan)
