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
        "its own valuation history, unless every test rated it BUY (then kept with a "
        "price warning).")


def test_the_rule_sentence_reads_correctly_with_no_checks():
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(40))]), primary_id=SEL)
    assert sl.rule_sentence == ("BUY under Selector and below the 80th percentile of its "
                                "own valuation history.")


def test_the_table_carries_one_column_per_check():
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(40))],
                          [_Row("A", "hold")], [_Row("A", "buy")]), primary_id=SEL)
    cols, rows = shortlist_table(sl)
    assert cols == ["Name", "Rank", "Forensic", "Quality", "Valuation percentile", "Note"]
    assert rows[0]["Forensic"] == "HOLD" and rows[0]["Quality"] == "BUY"
    assert rows[0]["Rank"] == "#1" and rows[0]["Valuation percentile"] == "40th"
    assert rows[0]["Note"] == ""            # SHORTLIST-2: blank unless the band was overruled


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


# --------------------------------------------------------------------------- #
# SHORTLIST-2 — the unanimous-BUY exception
# --------------------------------------------------------------------------- #
# Evidence: the 2026-09-17 09:10 run of oil_dividend_v1. Suncor was rated BUY by ALL THREE
# lenses — the income selector, the value selector and the forensic check — and the
# shortlist dropped it anyway, on the valuation band alone (99th percentile of its own
# five years). The band is a SEPARATE check, not one of the lenses, so it must not
# eliminate a name every lens in the run rated BUY.
#
# The exception is narrow by construction, and these tests exist to keep it narrow: one
# HOLD anywhere, or one lens that never ranked the name, and the band drops it as before.

SEL2 = "sel2_v1"


def _three_lens(primary_rows, second_rows, check_rows):
    """A run with a PRIMARY selector, a SECOND selector and a check — the shape of the
    09:10 run, and the smallest shape in which unanimity means anything."""
    results = {SEL: _Res(primary_rows),
               SEL2: _Res(second_rows, _Strat("selector")),
               CHK: _Res(check_rows, _Strat("check"))}
    names = {SEL: "Cyclical Income", SEL2: "Magic Formula RAW", CHK: "Forensic"}
    return _Multi([SEL, SEL2, CHK], names, results)


def test_a_name_every_lens_rated_BUY_is_KEPT_despite_a_dear_band():
    """The Suncor case. The band would have dropped it at the 95th percentile; every lens
    rated it BUY, so it stays."""
    sl = shortlist(_three_lens([_Row("SU", "buy", 1, _band(95))],
                               [_Row("SU", "buy", 3)], [_Row("SU", "buy", 20)]),
                   primary_id=SEL)
    assert [r.ticker for r in sl.kept] == ["SU"]
    assert not sl.dropped
    assert sl.kept[0].price_caution == 95.0
    assert sl.kept[0].band_percentile == 95.0      # the reading itself is untouched


def test_the_kept_row_carries_the_percentile_that_would_have_dropped_it():
    """A warning that does not carry the number is not a warning, it is a mood."""
    from aristos_council.pipeline import shortlist_caution_badge

    sl = shortlist(_three_lens([_Row("SU", "buy", 1, _band(99))],
                               [_Row("SU", "buy", 3)], [_Row("SU", "buy", 20)]),
                   primary_id=SEL)
    _cols, rows = shortlist_table(sl)
    assert rows[0]["Note"] == "⚠ priced high: 99th percentile of its own 5-year range"
    assert shortlist_caution_badge(99.0) == rows[0]["Note"]


def test_ONE_lens_short_of_unanimous_and_the_band_drops_it_as_before():
    """The same name, the same percentile, one HOLD on the second selector."""
    sl = shortlist(_three_lens([_Row("SU", "buy", 1, _band(95))],
                               [_Row("SU", "hold", 3)], [_Row("SU", "buy", 20)]),
                   primary_id=SEL)
    assert not sl.kept
    assert [r.ticker for r in sl.dropped] == ["SU"]
    assert "95th percentile" in sl.dropped[0].dropped_by
    assert sl.dropped[0].price_caution is None


def test_NOT_RANKED_breaks_unanimity():
    """A lens that excluded the name, or could not rate it, has not agreed — it has left
    the name unjudged, and an absent opinion is not a favourable one (house rule 3)."""
    sl = shortlist(_three_lens([_Row("SU", "buy", 1, _band(95))],
                               [_Row("OTHER", "buy", 3)], [_Row("SU", "buy", 20)]),
                   primary_id=SEL)
    assert not sl.kept and [r.ticker for r in sl.dropped] == ["SU"]
    assert "95th percentile" in sl.dropped[0].dropped_by


def test_an_EXCLUDED_name_breaks_unanimity_too():
    sl = shortlist(_three_lens([_Row("SU", "buy", 1, _band(95))],
                               [_Row("SU", "buy", 3, excluded=True)],
                               [_Row("SU", "buy", 20)]),
                   primary_id=SEL)
    assert not sl.kept and [r.ticker for r in sl.dropped] == ["SU"]


def test_a_check_SELL_still_drops_a_name_even_though_that_cannot_be_unanimous():
    """The step order is kept so the rule reads as written: a doubt about the BUSINESS
    outranks a doubt about the PRICE, and the exception applies only to the second."""
    sl = shortlist(_three_lens([_Row("SU", "buy", 1, _band(95))],
                               [_Row("SU", "buy", 3)], [_Row("SU", "sell", 20)]),
                   primary_id=SEL)
    assert not sl.kept
    assert sl.dropped[0].dropped_by == "SELL under Forensic"


def test_a_unanimous_name_BELOW_the_cutoff_is_kept_with_NO_warning():
    """The exception fires only where the band would otherwise have dropped the name."""
    sl = shortlist(_three_lens([_Row("SU", "buy", 1, _band(40))],
                               [_Row("SU", "buy", 3)], [_Row("SU", "buy", 20)]),
                   primary_id=SEL)
    assert [r.ticker for r in sl.kept] == ["SU"]
    assert sl.kept[0].price_caution is None
    assert sl.caution_clause == ""


def test_an_abstained_band_is_still_kept_and_is_not_a_price_warning():
    """Null is not false. An unmeasured band is not an overruled one."""
    sl = shortlist(_three_lens([_Row("SU", "buy", 1, _abstained())],
                               [_Row("SU", "buy", 3)], [_Row("SU", "buy", 20)]),
                   primary_id=SEL)
    assert [r.ticker for r in sl.kept] == ["SU"]
    assert sl.kept[0].price_caution is None
    assert sl.kept[0].band_note


def test_unanimity_needs_at_least_two_lenses():
    """Agreement among one lens is the lens repeating itself."""
    from aristos_council.pipeline import unanimous_buys

    one = _multi([_Row("A", "buy", 1, _band(95))])
    assert unanimous_buys(one) == set()
    sl = shortlist(one, primary_id=SEL)
    assert not sl.kept and [r.ticker for r in sl.dropped] == ["A"]
    # ...and such a run does not advertise a rule that cannot fire for it
    assert "unless every test" not in sl.rule_sentence


def test_the_kept_order_is_still_the_primarys_rank_order():
    """An overruled name takes its place in the list, not a place at the end of it."""
    sl = shortlist(
        _three_lens([_Row("A", "buy", 1, _band(40)), _Row("SU", "buy", 2, _band(95)),
                     _Row("C", "buy", 3, _band(10))],
                    [_Row("A", "buy"), _Row("SU", "buy"), _Row("C", "buy")],
                    [_Row("A", "buy"), _Row("SU", "buy"), _Row("C", "buy")]),
        primary_id=SEL)
    assert [r.ticker for r in sl.kept] == ["A", "SU", "C"]
    assert [r.ticker for r in sl.cautioned] == ["SU"]


# --------------------------------------------------------------------------- #
# What the counts say
# --------------------------------------------------------------------------- #
def test_the_title_counts_the_warnings():
    sl = shortlist(_three_lens([_Row("SU", "buy", 1, _band(99))],
                               [_Row("SU", "buy", 3)], [_Row("SU", "buy", 20)]),
                   primary_id=SEL)
    assert sl.title == ("Shortlist — 1 of 1 BUY survived the checks "
                        "(1 with a price warning)")


def test_two_warnings_read_as_a_plural():
    sl = shortlist(
        _three_lens([_Row("A", "buy", 1, _band(95)), _Row("B", "buy", 2, _band(88))],
                    [_Row("A", "buy"), _Row("B", "buy")],
                    [_Row("A", "buy"), _Row("B", "buy")]),
        primary_id=SEL)
    assert "(2 with a price warnings)" not in sl.title
    assert sl.title.endswith("(2 with price warnings)")


def test_a_shortlist_with_no_warning_counts_exactly_as_before():
    sl = shortlist(_multi([_Row("A", "buy", 1, _band(40))], [_Row("A", "hold")]),
                   primary_id=SEL)
    assert sl.title == "Shortlist — 1 of 1 BUY survived the checks"


def test_the_run_meta_records_which_names_the_band_was_overruled_for():
    """"Did the band ever get overruled, and for whom" must be answerable from the record
    rather than by re-deriving the rule."""
    sl = shortlist(_three_lens([_Row("SU", "buy", 1, _band(99))],
                               [_Row("SU", "buy", 3)], [_Row("SU", "buy", 20)]),
                   primary_id=SEL)
    assert sl.unanimous_override == ["SU"]


# --------------------------------------------------------------------------- #
# The warning reaches every surface, and says the same thing on each
# --------------------------------------------------------------------------- #
def _cautioned_multi():
    return _three_lens([_Row("SU", "buy", 1, _band(99)), _Row("A", "buy", 2, _band(30))],
                       [_Row("SU", "buy"), _Row("A", "buy")],
                       [_Row("SU", "buy"), _Row("A", "hold")])


def test_the_html_carries_the_badge_and_states_its_meaning_ONCE():
    from aristos_council.export.report_html import _shortlist_section
    from aristos_council.pipeline import SHORTLIST_CAUTION_NOTE

    html = _shortlist_section(shortlist(_cautioned_multi(), primary_id=SEL))
    assert "priced high: 99th percentile of its own 5-year range" in html
    assert 'class="badge"' in html
    # ONCE. A note repeated per row is the repetition the report keeps removing.
    assert html.count(SHORTLIST_CAUTION_NOTE.split(".")[0]) == 1


def test_the_markdown_mirrors_the_html():
    pytest.importorskip("streamlit")
    import app

    from aristos_council.pipeline import SHORTLIST_CAUTION_NOTE, shortlist_table

    sl = shortlist(_cautioned_multi(), primary_id=SEL)
    md = "\n".join(app._shortlist_markdown(sl, shortlist_table))
    assert "priced high: 99th percentile of its own 5-year range" in md
    assert SHORTLIST_CAUTION_NOTE in md
    assert sl.title in md
    for row in sl.kept:
        assert row.display in md


def test_the_note_is_absent_when_no_row_carries_a_warning():
    from aristos_council.export.report_html import _shortlist_section

    html = _shortlist_section(
        shortlist(_multi([_Row("A", "buy", 1, _band(40))], [_Row("A", "hold")]),
                  primary_id=SEL))
    assert "priced high" not in html
    assert "not proof the price is justified" not in html


def test_the_summary_line_never_counts_the_shortlist_without_the_warning():
    """A count that hides the warning is the one thing this exception must not produce."""
    from dataclasses import dataclass, field

    from aristos_council.pipeline import multi_summary_line

    multi = _cautioned_multi()
    sl = shortlist(multi, primary_id=SEL)

    @dataclass
    class _Full:
        strategy_ids: list
        strategy_names: dict
        results: dict
        rows: list = field(default_factory=list)
        meta: dict = field(default_factory=dict)
        shortlist: object = None

    full = _Full(multi.strategy_ids, multi.strategy_names, multi.results,
                 meta={"universe_size": 2}, shortlist=sl)
    line = multi_summary_line(full)
    assert "shortlist: 2 of 2 BUYs (1 with a price warning)" in line
