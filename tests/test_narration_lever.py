"""NARR-LEVER-1 — the narration rule is applied where the money is spent.

Live, oil services narrator run 2026-09-18 18:34. The header said

    One pass over the union of every lens's BUYs - 9 names narrated - $1.60

and the narration section, four lines further down, said

    1 name narrated - all voting lenses agree; names doubted by a check skipped.

Nine sections existed. Four of them (SPM.MI, 2883.HK, KGS, ODL.OL) were rated BUY by NO
voting lens: they are Forensic's top ranks, and Forensic does not vote. Roughly $1.40 of
the $1.60 went on names the rule had excluded.

DIAGNOSIS: (a). ``narrated_union`` counted ``verdict == "buy"`` across EVERY lens's cells,
check lenses included. A check's top quintile carries ``verdict == "buy"`` internally —
CHECK-WORDS-1 renders it "clean" but never changes the field — so Forensic bought four
narrations on its own say-so. The sentence was written from the NARR-2 plan, which reads
``lens_agreement`` where a check never votes. Two sets, two counts, one report.

Not (b): there is no second "narrate the rest" action in the code, and the section
sentence is not a first-pass figure — it is the plan's own count.
"""
from __future__ import annotations

import pytest

from aristos_council.pipeline import (MultiStrategyCell, MultiStrategyResult,
                                      MultiStrategyRow, multi_header_line,
                                      narrated_union)
from aristos_council.report_language import scrub_check_cell

_RANKED = "ranked"


def _cell(sid, verdict, *, check=False, position=1):
    return MultiStrategyCell(strategy_id=sid, status=_RANKED, position=position,
                             cohort_size=24, verdict=verdict, score=10.0,
                             is_check=check)


def _three_lens():
    """Forensic (a CHECK) tops four names no voting lens bought — the 18:34 shape."""
    voting = ("magic_formula_raw_v1", "growth_garp_v2")
    rows = []
    # one name both voting lenses bought, and which Forensic also likes
    rows.append(MultiStrategyRow(ticker="FTI", display="TechnipFMC", comparable=True,
                                 graded=True, cells={
                                     "magic_formula_raw_v1": _cell("magic_formula_raw_v1", "buy"),
                                     "growth_garp_v2": _cell("growth_garp_v2", "buy"),
                                     "forensic_v1": _cell("forensic_v1", "buy", check=True)}))
    # four names ONLY the check "bought"
    for ticker in ("SPM.MI", "2883.HK", "KGS", "ODL.OL"):
        rows.append(MultiStrategyRow(ticker=ticker, display=ticker, comparable=True,
                                     graded=True, cells={
                                         "magic_formula_raw_v1": _cell("magic_formula_raw_v1", "hold"),
                                         "growth_garp_v2": _cell("growth_garp_v2", "hold"),
                                         "forensic_v1": _cell("forensic_v1", "buy", check=True)}))
    return MultiStrategyResult(
        strategy_ids=list(voting) + ["forensic_v1"],
        strategy_names={"magic_formula_raw_v1": "Magic Formula RAW",
                        "growth_garp_v2": "Growth", "forensic_v1": "Forensic"},
        results={}, rows=rows, meta={})


# =========================================================================== #
# 1. a check's "buy" never buys a narration
# =========================================================================== #
def test_forensics_top_ranks_do_not_enter_the_narration_set():
    """THE bug. Four of the nine sections, and roughly $1.40 of $1.60."""
    union = narrated_union(_three_lens(), "buys_only")
    assert union == ["FTI"]
    for bought_only_by_the_check in ("SPM.MI", "2883.HK", "KGS", "ODL.OL"):
        assert bought_only_by_the_check not in union


def test_a_voting_lenss_buy_still_counts():
    result = _three_lens()
    result.rows[1].cells["growth_garp_v2"] = _cell("growth_garp_v2", "buy")
    assert set(narrated_union(result, "buys_only")) == {"FTI", "SPM.MI"}


def test_coverage_all_is_unaffected_because_it_is_not_about_buying():
    """"all" narrates every RANKED name and never read a verdict, so the guard must not
    have touched it."""
    assert len(narrated_union(_three_lens(), "all")) == 5


def test_a_run_with_no_check_lens_is_unchanged():
    plain = _three_lens()
    for row in plain.rows:
        row.cells.pop("forensic_v1")
    assert narrated_union(plain, "buys_only") == ["FTI"]


# =========================================================================== #
# 2. the header, the section and the record agree
# =========================================================================== #
def _narrated(rule="all voting lenses agree", narrated=1):
    return MultiStrategyResult(
        strategy_ids=["a"], strategy_names={"a": "A"}, results={}, rows=[],
        narratives={f"T{i}": "prose" for i in range(narrated)},
        meta={"council_mode": "narrator",
              "narration": {"mode": "narrator", "rule": rule, "eligible": narrated,
                            "narrated": narrated, "skipped_doubted": 0}})


def test_the_header_states_the_rule_that_was_actually_applied():
    line = multi_header_line(_narrated())
    assert "all voting lenses agree" in line
    assert "union of every lens's BUYs" not in line
    assert "1 name narrated" in line


def test_the_header_count_and_the_record_count_agree():
    result = _narrated(narrated=3)
    assert "3 names narrated" in multi_header_line(result)
    assert result.meta["narration"]["narrated"] == 3


def test_a_run_carrying_narratives_never_claims_no_LLM_ran():
    """It printed exactly that above two narration sections, and the assertion meant to
    catch it was case-broken so it never fired."""
    stale = _narrated()
    stale.meta["council_mode"] = "ranker-only"        # left over from the rank stage
    line = multi_header_line(stale)
    assert "no llm ran" not in line.lower()
    assert "ranker-only" not in line.lower()


def test_a_genuinely_unnarrated_run_still_says_ranker_only():
    bare = MultiStrategyResult(strategy_ids=["a"], strategy_names={"a": "A"}, results={},
                               rows=[], meta={})
    assert "no LLM ran" in multi_header_line(bare)


# =========================================================================== #
# 3. CHECK-WORDS-1 regression: no "BUY" in a Forensic cell, anywhere
# =========================================================================== #
@pytest.mark.parametrize("cell,expected", [
    ("BUY — CLEAN", "CLEAN"),
    ("BUY - clean", "clean"),
    ("SELL — doubted", "doubted"),
    ("HOLD — NO CONCERN", "NO CONCERN"),
    ("clean", "clean"),
])
def test_a_check_cell_loses_the_verdict_word(cell, expected):
    assert scrub_check_cell(cell) == expected
    assert "buy" not in scrub_check_cell(cell).lower()


def test_a_voting_lenss_cell_is_left_exactly_alone():
    """Detected by CONTENT: no check word, no scrub. A voting lens's BUY is its own."""
    for cell in ("BUY", "SELL", "HOLD", "BUY — FTI ranked #2 of 24"):
        assert scrub_check_cell(cell) == cell


def test_the_rendered_lens_table_never_puts_BUY_in_a_check_cell():
    from aristos_council.agents.schemas import LensVerdictItem, Narration
    from aristos_council.narration_render import narration_markdown

    narration = Narration(
        echoed_verdict="BUY - FTI ranked #2 of 24.",
        lens_verdicts=[
            LensVerdictItem(lens="Forensic", verdict="BUY — CLEAN", position=2,
                            cohort_size=24),
            LensVerdictItem(lens="Magic Formula RAW", verdict="buy", position=5,
                            cohort_size=24)])
    body = narration_markdown(narration)
    forensic_row = next(l for l in body.splitlines() if l.startswith("| Forensic "))
    assert "BUY" not in forensic_row, forensic_row
    assert "CLEAN" in forensic_row
    # ...and the voting lens keeps its verdict word
    raw_row = next(l for l in body.splitlines() if l.startswith("| Magic Formula RAW "))
    assert "BUY" in raw_row
