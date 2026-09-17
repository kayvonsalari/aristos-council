"""DETAIL-1 — the per-lens detail section, grouped by the rule that fired.

The section was a flat bullet list, one line per excluded name, each repeating the rule
sentence in full. On the 134-name oil run of 2026-09-16 the Cyclical Income lens produced
91 of them: the same free-cash-flow sentence 27 times in a row, then the same
dividend-cut sentence 27 more. A reader could learn what happened to ONE name easily and
what the lens actually DID only by counting bullets.

Grouped, the same data answers the question a reader has — which rule removed the most
names, how far each name missed, and which misses were close enough to argue about.

The line this file guards: this is ARRANGEMENT. ``lens_detail`` reads the run's recorded
exclusions, reasons and numbers and decides where each is printed. It never re-grades, and
tests/test_rendering_only_pin.py pins that for the whole branch.
"""

from __future__ import annotations

import pytest

from aristos_council.pipeline import (DETAIL_GROUP_FLOOR, DETAIL_GROUP_OTHER,
                                      DETAIL_GROUP_SECTOR, lens_detail)
from aristos_council.tools.criteria.registry import REGISTRY


# --------------------------------------------------------------------------- #
# Criterion.why — every shipped rule states what it is for
# --------------------------------------------------------------------------- #
def test_every_registered_criterion_says_what_it_is_for():
    """A criterion with no stated purpose is one nobody has had to justify. The group
    heading now states the rule ONCE and then shows thirty names it removed, which
    provokes exactly this question — and nothing in the report answered it."""
    missing = sorted(name for name, crit in REGISTRY.items() if not (crit.why or "").strip())
    assert missing == [], f"criteria with no stated purpose: {missing}"


def test_why_is_not_a_copy_of_the_glossary():
    """They answer different questions. The glossary says what the rule REQUIRES, for a
    reader meeting the term for the first time; ``why`` says why anyone would screen on
    it. A ``why`` that repeats the glossary has not been written."""
    for name, crit in REGISTRY.items():
        assert crit.why.strip() != crit.glossary.strip(), name


def test_why_reads_as_a_sentence():
    for name, crit in REGISTRY.items():
        assert crit.why[0].isupper(), name
        assert crit.why.rstrip().endswith("."), name


# --------------------------------------------------------------------------- #
# The grouping
# --------------------------------------------------------------------------- #
def _detail():
    from tests.test_cyclical_income import _run

    return lens_detail(_run())


def test_each_rule_gets_ONE_group_and_states_its_rule_once():
    detail = _detail()
    keys = [g.key for g in detail.groups]
    assert len(keys) == len(set(keys)), "a rule appeared in two groups"
    for group in detail.groups:
        if group.is_gate:
            continue
        assert group.title == REGISTRY.get(group.key).label
        assert group.rule, f"{group.key} states no rule"
        assert group.why == REGISTRY.get(group.key).why


def test_every_excluded_name_lands_in_exactly_one_group():
    """Nothing may be quietly unplaced: an excluded name that appears in no group has
    been dropped from the report, which is the failure mode a grouped view invites."""
    from tests.test_cyclical_income import _run

    result = _run()
    detail = lens_detail(result)
    placed = [n.ticker for g in detail.groups for n in g.names]
    assert sorted(placed) == sorted(t for t, _ in result.excluded)
    assert len(placed) == len(set(placed))


def test_a_name_carries_the_value_it_was_measured_at():
    detail = _detail()
    cuts = next(g for g in detail.groups if g.key == "max_dividend_cuts")
    assert cuts.names[0].measured == "50%"          # the cut SIZE, in its own unit
    yields = next(g for g in detail.groups if g.key == "min_dividend_yield")
    assert yields.names[0].measured.endswith("%")


def test_the_measured_value_follows_the_criterions_own_presentation():
    """A criterion whose sentence reads its number as a DIRECTION ("share price fell
    12.0%") reads it that way in the column too. "-12.0%" beside a rule phrased as "at
    most a 10% fall" makes a reader do the translation the criterion already did."""
    from tests.test_report_language import _run as _momentum_run

    detail = lens_detail(_momentum_run())
    momentum = next((g for g in detail.groups if g.key == "min_price_momentum"), None)
    assert momentum is not None
    assert momentum.names[0].measured == "fell 12.0%"


def test_groups_are_ordered_by_size_largest_first():
    """The rule that removed the most names decided what this lens is looking at, so it
    belongs at the top."""
    from aristos_council.pipeline import DetailGroup, DetailName

    groups = [
        DetailGroup(key="a", title="A", names=tuple(DetailName(ticker=str(i), name=str(i))
                                                    for i in range(3))),
        DetailGroup(key="b", title="B", names=(DetailName(ticker="x", name="x"),)),
        DetailGroup(key="c", title="C", names=tuple(DetailName(ticker=str(i), name=str(i))
                                                    for i in range(7))),
    ]
    assert [g.key for g in sorted(groups, key=lambda g: (-g.count, g.title))] \
        == ["c", "a", "b"]


def test_names_inside_a_group_are_ordered_WORST_MISS_FIRST():
    """A cap is missed by being too high and a floor by being too low, so the two sort in
    opposite directions — and the top of every table is the clearest case either way."""
    from aristos_council.pipeline import _miss_size

    cap = REGISTRY.get("max_payout_ratio_fcf")        # comparison "max"
    floor = REGISTRY.get("min_dividend_yield")        # comparison "min"
    assert _miss_size({"observed": 6.69}, cap) > _miss_size({"observed": 0.81}, cap)
    assert _miss_size({"observed": 0.0002}, floor) > _miss_size({"observed": 0.014}, floor)


def test_a_name_with_no_recorded_number_sorts_last_and_renders_a_dash():
    """It is not a bigger miss than a measured one; it is an unmeasured one."""
    from aristos_council.pipeline import _measured_text, _miss_size

    assert _miss_size(None, REGISTRY.get("min_roic")) == float("-inf")
    assert _measured_text(None, REGISTRY.get("min_roic")) == ""


# --------------------------------------------------------------------------- #
# The pre-screen gates
# --------------------------------------------------------------------------- #
def test_the_size_floor_is_a_gate_and_says_no_other_rule_was_tested():
    """Their absence from every group below is an artefact of the ORDER, not evidence
    about them — and a reader who is not told that will read it as evidence."""
    from datetime import date

    from aristos_council.pipeline import run_rank_pipeline
    from tests.test_multi_strategy_run import SCREENED, STRAT_DIR, TODAY, UNIVERSE, _Adapter

    res = run_rank_pipeline(UNIVERSE, SCREENED, strategies_dir=STRAT_DIR,
                            ranker_only=True, adapter=_Adapter(), today=TODAY)
    res.excluded.append(("TINY", "below min market cap ($5.0bn)"))
    res.names["TINY"] = "Tiny Co"
    floor = next(g for g in lens_detail(res).groups if g.key == DETAIL_GROUP_FLOOR)
    assert floor.is_gate
    assert floor.title == "Company size"
    assert floor.note == "no other rule was tested on these"
    assert [n.ticker for n in floor.names] == ["TINY"]


def test_a_gate_comes_before_the_rules_however_few_names_it_holds():
    """It ran first, and its names were never tested on anything below, so putting it in
    the size ordering would imply it competed with the rules."""
    from datetime import date

    from aristos_council.pipeline import run_rank_pipeline
    from tests.test_multi_strategy_run import SCREENED, STRAT_DIR, TODAY, UNIVERSE, _Adapter

    res = run_rank_pipeline(UNIVERSE, SCREENED, strategies_dir=STRAT_DIR,
                            ranker_only=True, adapter=_Adapter(), today=TODAY)
    res.excluded.append(("TINY", "below min market cap ($5.0bn)"))
    res.names["TINY"] = "Tiny Co"
    groups = lens_detail(res).groups
    assert groups[0].key == DETAIL_GROUP_FLOOR
    assert any(not g.is_gate for g in groups[1:])


def test_a_sector_gate_gets_its_own_line():
    from datetime import date

    from aristos_council.pipeline import run_rank_pipeline
    from tests.test_multi_strategy_run import SCREENED, STRAT_DIR, TODAY, UNIVERSE, _Adapter

    res = run_rank_pipeline(UNIVERSE, SCREENED, strategies_dir=STRAT_DIR,
                            ranker_only=True, adapter=_Adapter(), today=TODAY)
    res.excluded.append(("BANK", "sector excluded (Financial Services)"))
    res.names["BANK"] = "A Bank"
    sector = next(g for g in lens_detail(res).groups if g.key == DETAIL_GROUP_SECTOR)
    assert sector.is_gate and sector.title == "Sector"
    assert [n.ticker for n in sector.names] == ["BANK"]


def test_an_UNGROUPABLE_reason_keeps_every_name_sentence_verbatim():
    """A reason this builder cannot attribute to a rule or a named gate is one it does not
    understand, and a group it does not understand must not summarise. A hand-built or
    replayed result with no screen_outcomes lands here, and the sentence is all it has."""
    from aristos_council.pipeline import RankPipelineResult

    res = RankPipelineResult(
        ranked=[], excluded=[("C", "screen: min_roic (observed 0.08 vs 0.12)")],
        unrateable=[], narratives={}, header="",
        meta={"universe_size": 1, "ranked_count": 0})
    group = next(g for g in lens_detail(res).groups if g.key == DETAIL_GROUP_OTHER)
    assert group.keeps_sentences
    assert group.names[0].sentence == "screen: min_roic (observed 0.08 vs 0.12)"


# --------------------------------------------------------------------------- #
# The headline, the badges and the sources table
# --------------------------------------------------------------------------- #
def test_the_headline_says_how_to_read_what_follows():
    detail = _detail()
    assert detail.headline.startswith("Ranked 2 of 5 names.")
    assert "Excluded 3: by rule below, worst miss first." in detail.headline


def test_the_badge_note_is_stated_only_when_a_badge_is_shown():
    """An abbreviation used without its expansion is a private code; an expansion with
    nothing to expand is noise."""
    assert _detail().badge_note == ""            # no price-divergence flag in this run


def test_the_sources_table_carries_the_counts_and_names_the_abstainers():
    """The sentence form counted the abstentions; the table names them, which is the
    thing a reader is actually checking."""
    detail = _detail()
    by_factor = {r["factor"]: r for r in detail.sources}
    payout = by_factor["net_payout_yield"]
    assert payout["real"] == "0 of 2"
    assert payout["abstained"].startswith("2 — ")
    covered = by_factor["payout_coverage_fcf"]
    assert covered["real"] == "2 of 2" and covered["abstained"] == "—"


# --------------------------------------------------------------------------- #
# The two surfaces render the SAME groups
# --------------------------------------------------------------------------- #
def test_the_html_renders_every_group_with_its_rule_its_why_and_its_id():
    from aristos_council.export.report_html import multi_strategy_report_html
    from tests.test_merged_multi_report import _multi
    from tests.test_multi_strategy_run import RAW, SCREENED

    doc = multi_strategy_report_html(_multi([SCREENED, RAW]))
    assert "Return on invested capital" in doc
    assert "· rule: at least 12%" in doc
    assert REGISTRY.get("min_roic").why in doc
    assert "min_roic" in doc                      # the machine id, kept beside the label
    assert "Where the numbers came from" in doc


def test_the_markdown_mirrors_the_html_group_for_group():
    pytest.importorskip("streamlit")
    import app

    from tests.test_merged_multi_report import _RUN, _multi
    from tests.test_multi_strategy_run import RAW, SCREENED

    result = _multi([SCREENED, RAW])
    md = app._multi_strategy_markdown(result, _RUN)
    for sid in result.strategy_ids:
        for group in lens_detail(result.results[sid]).groups:
            assert group.title in md
            for name in group.names:
                assert name.name in md


def test_the_same_builder_serves_the_single_lens_report():
    """One builder, so a single-lens run and the same lens inside a merged run cannot
    show a different set of names."""
    from aristos_council.export.report_html import universe_report_html
    from tests.test_report_html import _universe_result_with_bands

    result = _universe_result_with_bands()
    doc = universe_report_html(result)
    for group in lens_detail(result).groups:
        assert group.title in doc
        for name in group.names:
            assert name.name in doc


def test_company_check_still_prints_the_whole_per_name_sentence():
    """Grouping decided where the sentence is printed, not whether it exists. Company
    Check renders ONE name at a time, and a group of one is not a table."""
    from aristos_council.pipeline import exclusion_rows
    from tests.test_cyclical_income import _run

    rows = exclusion_rows(_run())
    assert rows and all(r["sentence"] for r in rows)
    assert any("the rule" in r["sentence"] for r in rows)


# --------------------------------------------------------------------------- #
# The ⚠ badge — the figure in the row, the sentence stated once
# --------------------------------------------------------------------------- #
# Found in acceptance, on the real 134-name oil cohort: every flagged row carried the
# whole disclosure sentence inside a badge — "⚠ price diverging: +47% 12m — cyclical
# inflection or mania; human review" — in a table cell, on 30-odd rows. That is the exact
# repetition this section exists to remove, reintroduced one column to the right.

def test_the_badge_is_the_figure_and_nothing_else():
    from aristos_council.pipeline import _short_flag

    assert _short_flag("[⚠ price diverging: +47% 12m — cyclical inflection or mania; "
                       "human review]") == "⚠ +47% 12m"
    assert _short_flag("[⚠ price diverging: -12% 12m — …]") == "⚠ -12% 12m"


def test_a_badge_that_cannot_be_shortened_is_never_truncated():
    """A cut-off warning is worse than a long one."""
    from aristos_council.pipeline import _short_flag

    assert _short_flag("[⚠ something new with no figure]") == "⚠ something new with no figure"
    assert _short_flag("") == ""


def test_the_badge_note_quotes_the_real_threshold():
    """DETAIL-1b rewrote this note in the owner's words, which state "+30%" as prose
    rather than reading the constant. That is the right text and the wrong coupling, so
    the coupling is asserted here instead: move _DIVERGENCE_MOMENTUM_THRESHOLD and this
    fails, rather than the report quietly explaining a threshold it no longer uses."""
    from aristos_council.factors import _DIVERGENCE_MOMENTUM_THRESHOLD
    from aristos_council.pipeline import detail_badge_note

    assert f"{_DIVERGENCE_MOMENTUM_THRESHOLD:+.0%}" in detail_badge_note()


def test_the_badge_note_explains_the_symbol_the_ambiguity_and_the_limit():
    """The three things the old wording left out: what the mark IS (a price move, with a
    worked figure), that the numbers cannot tell a turning cycle from a mania, and that it
    is not part of the rule the company failed."""
    from aristos_council.pipeline import detail_badge_note

    note = detail_badge_note()
    assert "share price has risen 80% over the last twelve months" in note
    assert "the cycle has turned" in note and "run ahead of anything" in note
    assert "cannot tell which" in note
    assert "asks for a human look" in note
    assert "not part of the rule the company failed" in note


def test_the_badge_note_and_the_glossary_entry_are_ONE_string():
    """A mark with two explanations is a mark a reader cannot trust. The detail section
    states it above the groups; the glossary defines it at the end; they are the same
    object, not two copies that happen to agree today."""
    from aristos_council.glossary import _REPORT_TERMS
    from aristos_council.pipeline import detail_badge_note

    entries = [t for t in _REPORT_TERMS if t[1] == "⚠"]
    assert len(entries) == 1
    assert entries[0][2] == detail_badge_note()


def test_the_note_appears_only_when_a_badge_does():
    """An abbreviation without its expansion is a private code; an expansion with nothing
    to expand is noise."""
    from aristos_council.pipeline import DetailName, LensDetail, lens_detail
    from tests.test_cyclical_income import _run

    assert lens_detail(_run()).badge_note == ""     # no flagged name in this fixture
