"""NARR-ZERO-1 — a run that narrates nothing must say WHY.

Live, 2026-09-18 18:09: narrator mode, four lenses, lever "all voting lenses agree". No
name was rated BUY by all three voting lenses, so the narration stage was skipped — and the
saved report was the one the RANK stage writes, headed "ranker only, no AI commentary" with
"Narrative: none (ranker-only — no LLM ran)".

Three things were wrong with that at once. The report contradicted the mode the owner had
chosen. Nothing anywhere said the rule had matched zero names. And a CORRECT run was
indistinguishable from a narrator that had crashed — which is the same ambiguity
CLAUDE.md's shipping duty calls out: an absent section reads exactly like a feature that
was never switched on.

Reporting only. No logic, no cost, no verdict moves.
"""

from __future__ import annotations

import pytest

from aristos_council.pipeline import (MultiStrategyResult, RankPipelineResult,
                                      LensAgreement, LensAgreementRow, multi_header_line,
                                      narration_record, narration_zero_line,
                                      thin_voting_lens_note)


def _plan(*, qualified=(), names=(), not_narrated=(), basis="all voting lenses agree",
          level="all", cap=10, skip_marked=True):
    return {"names": list(names), "count": len(names), "basis": basis, "level": level,
            "cap": cap, "skip_marked": skip_marked, "qualified": list(qualified),
            "not_narrated": [dict(n) for n in not_narrated]}


def _result(record=None, narratives=None):
    return MultiStrategyResult(
        strategy_ids=["a"], strategy_names={"a": "A"}, results={}, rows=[],
        meta=({"narration": record} if record is not None else {}),
        narratives=dict(narratives or {}))


# =========================================================================== #
# 1. the record — written ALWAYS, zero included
# =========================================================================== #
def test_the_record_carries_the_mode_the_rule_and_the_counts():
    record = narration_record(_plan(qualified=["A", "B", "C"], names=["A"]),
                              mode="narrator")
    assert record["mode"] == "narrator"
    assert record["rule"] == "all voting lenses agree"
    assert record["eligible"] == 3
    assert record["narrated"] == 1


def test_a_zero_match_run_still_records_everything():
    """"Today a skipped stage leaves no trace at all" — this is that trace."""
    record = narration_record(_plan(qualified=[], names=[]), mode="narrator")
    assert record["mode"] == "narrator"
    assert record["eligible"] == 0 and record["narrated"] == 0
    assert record["rule"]                      # the rule that matched nothing is NAMED


def test_the_doubted_skips_are_counted_separately_from_the_capped_ones():
    plan = _plan(qualified=["A", "B", "C"], names=["A"], not_narrated=[
        {"ticker": "B", "marks": ["doubted by Forensic"]},
        {"ticker": "C", "marks": ["priced high: 90th percentile"]},
    ])
    record = narration_record(plan, mode="narrator")
    assert record["skipped_doubted"] == 1      # B only — a price mark is not a doubt
    assert record["eligible"] == 3 and record["narrated"] == 1


def test_the_plans_own_keys_survive_so_every_existing_reader_is_unaffected():
    plan = _plan(qualified=["A"], names=["A"])
    record = narration_record(plan, mode="narrator")
    for key in ("level", "cap", "skip_marked", "basis", "selected", "qualified",
                "not_narrated"):
        assert key in record, key
    assert record["selected"] == ["A"]


# =========================================================================== #
# 2. the sentence
# =========================================================================== #
def test_a_narrator_run_that_matched_nothing_says_so():
    result = _result(narration_record(_plan(), mode="narrator"))
    assert narration_zero_line(result) == (
        "Narrator requested. No name met the narration rule (all voting lenses agree), "
        "so nothing was narrated and nothing was charged.")


def test_a_ranker_only_run_says_nothing_because_that_is_not_news():
    result = _result(narration_record(_plan(), mode="ranker"))
    assert narration_zero_line(result) == ""


def test_a_narrator_run_that_DID_narrate_says_nothing():
    record = narration_record(_plan(qualified=["A"], names=["A"]), mode="narrator")
    assert narration_zero_line(_result(record, {"A": "prose"})) == ""


def test_a_result_with_no_record_at_all_says_nothing_rather_than_guessing():
    assert narration_zero_line(_result()) == ""


# =========================================================================== #
# 3. the header stops calling a narrator run "ranker only"
# =========================================================================== #
def test_the_header_no_longer_claims_ranker_only_for_a_narrator_run():
    """THE defect. The mode the owner chose was contradicted by the report."""
    result = _result(narration_record(_plan(), mode="narrator"))
    header = multi_header_line(result)
    assert "ranker-only" not in header and "no LLM ran" not in header
    assert "Narrator requested" in header
    assert header.startswith("Verdict: deterministic ranker.")
    # ...and it does not swing the other way into claiming a model DID run
    assert "Narrative: LLM" not in header


def test_a_genuine_ranker_only_run_keeps_its_header():
    result = _result(narration_record(_plan(), mode="ranker"))
    assert multi_header_line(result) == (
        "Verdict: deterministic ranker.  Narrative: none (ranker-only — no LLM ran).")


def test_a_run_with_no_narration_record_at_all_keeps_the_old_header():
    assert "ranker-only" in multi_header_line(_result())


def test_a_narrated_run_is_unchanged():
    record = narration_record(_plan(qualified=["A"], names=["A"]), mode="narrator")
    header = multi_header_line(_result(record, {"A": "prose"}))
    assert "1 name narrated" in header and "Narrator requested" not in header


# =========================================================================== #
# 4. the report section exists rather than being absent
# =========================================================================== #
def test_the_markdown_carries_the_sentence_where_the_sections_would_have_been():
    pytest.importorskip("streamlit")
    import app

    result = _result(narration_record(_plan(), mode="narrator"))
    lines = app._multi_narration_markdown(result)
    assert lines, "an absent section is indistinguishable from a feature switched off"
    body = "\n".join(lines)
    assert "## Narration" in body
    assert "No name met the narration rule" in body


def test_a_ranker_only_run_still_has_no_narration_section():
    pytest.importorskip("streamlit")
    import app

    assert app._multi_narration_markdown(
        _result(narration_record(_plan(), mode="ranker"))) == []


# =========================================================================== #
# 5. the pre-run hint
# =========================================================================== #
def _multi_with_lens_coverage(counts: dict[str, int], total: int):
    """A result where each voting lens ranked ``counts[sid]`` of ``total`` names."""
    def _lens(n):
        rows = [_ranked(f"T{i}") for i in range(n)]
        return RankPipelineResult(ranked=rows, excluded=[], unrateable=[], narratives={},
                                  header="", meta={})

    def _ranked(ticker):
        from aristos_council.rank_engine import RankedTicker
        return RankedTicker(ticker=ticker, factor_ranks={}, factor_values={},
                            combined_rank=0.0, universe_size=total, verdict="buy")

    class _Row:
        def __init__(self, t):
            self.ticker = t

    agreement = LensAgreement(
        voting_ids=list(counts), voting_labels={s: s.replace("_v1", "").title()
                                                for s in counts},
        check_ids=[], check_labels={},
        rows=[LensAgreementRow(ticker="T0", display="T0", buy_lenses=tuple(counts))])
    result = MultiStrategyResult(
        strategy_ids=list(counts), strategy_names={s: s for s in counts},
        results={sid: _lens(n) for sid, n in counts.items()},
        rows=[_Row(f"T{i}") for i in range(total)], meta={})
    return type(result)(**{**result.__dict__, "lens_agreement": agreement})


def test_the_hint_fires_when_a_voting_lens_ranked_1_of_21():
    """The condition that bit the live run: Cyclical Income ranked 1 of 21."""
    result = _multi_with_lens_coverage(
        {"cyclical_income_v1": 1, "magic_formula_raw_v1": 18, "growth_garp_v2": 16}, 21)
    note = thin_voting_lens_note(result, "all")
    assert note == ("Cyclical_Income ranks few names on this list, so unanimous agreement "
                    "may match nothing.")


def test_the_hint_is_silent_when_every_voting_lens_ranked_broadly():
    result = _multi_with_lens_coverage(
        {"cyclical_income_v1": 17, "magic_formula_raw_v1": 18, "growth_garp_v2": 16}, 21)
    assert thin_voting_lens_note(result, "all") == ""


@pytest.mark.parametrize("level", ["most", "any"])
def test_the_hint_is_only_for_the_unanimity_lever(level):
    """"most" and "any" are not hostage to the narrowest lens, so the hint would be noise."""
    result = _multi_with_lens_coverage(
        {"cyclical_income_v1": 1, "magic_formula_raw_v1": 18}, 21)
    assert thin_voting_lens_note(result, level) == ""


def test_the_hint_names_every_thin_lens_not_just_the_first():
    result = _multi_with_lens_coverage(
        {"cyclical_income_v1": 1, "growth_garp_v2": 2, "magic_formula_raw_v1": 18}, 21)
    note = thin_voting_lens_note(result, "all")
    assert "Cyclical_Income" in note and "Growth_Garp" in note
    assert " rank few names" in note                     # plural verb


def test_the_hint_says_nothing_before_a_cohort_has_ever_been_ranked():
    """It needs per-lens counts, which only exist after a run. Silence is the right
    failure: a hint that guessed would be worse than no hint."""
    bare = MultiStrategyResult(strategy_ids=[], strategy_names={}, results={}, rows=[],
                               meta={})
    assert thin_voting_lens_note(bare, "all") == ""
