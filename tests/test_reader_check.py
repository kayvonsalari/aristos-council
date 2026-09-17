"""READER-1 — the validator, against a facts pack from a REAL frozen run.

The summary is the only part of a report a model composes freely, so it is the only part
that can be wrong in a way no rule caught. These tests are that rule's own test.

The pack in ``fixtures/reader/oil_pack_2026-09-16.json`` is not fabricated: it is built by
replaying the frozen 2026-09-16 oil run out of ``runs/`` with no network
(``fixtures/reader/rebuild_pack.py`` regenerates it), so the numbers the validator is
checked against are numbers a real run actually produced.

One property of that replay is worth stating, because it shapes the fixture: a frozen run
carries the price windows the ORIGINAL run fetched, and that run's 5-year band window is not
among them — so on replay the band abstains for all 106 names. The pack says exactly that
(``evaluated: 0, not_evaluated: 106``) rather than quietly reporting zeros, which is the
behaviour under test as much as anything else here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from aristos_council.agents.schemas import ReaderSummary
from aristos_council.reader_check import WORD_LIMIT, check_summary

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reader"
PACK = json.loads((FIXTURES / "oil_pack_2026-09-16.json").read_text(encoding="utf-8"))


def _summary(**over) -> ReaderSummary:
    """A summary whose every number comes from PACK — the shape and register of the
    prompt's worked example, written against THIS run's facts."""
    base = dict(
        asked=("This run tested 134 oil and gas companies that pay dividends. "
               "Three tests ran, each asking something different."),
        happened=("Magic Formula RAW ranked 105 names and rated 21 BUY. "
                  "Forensic ranked 106 names and rated 22 BUY. "
                  "Cyclical Income ranked only 23 names. "
                  "One rule did that: it wants no dividend cut, and 67 names failed it."),
        survived=("19 of the 21 names Magic Formula RAW rated BUY stayed on the "
                  "shortlist. Ecopetrol and Marathon Petroleum lead it."),
        doubt=("Forensic could not work out its balance-sheet score for 40 names. "
               "The price check could not be worked out for 106 names."),
        cannot_say=("This list was built for income, and the test that picked these "
                    "names looks for value instead."),
    )
    base.update(over)
    return ReaderSummary(**base)


# --------------------------------------------------------------------------- #
# The pack is a real run's
# --------------------------------------------------------------------------- #
def test_the_fixture_pack_is_the_frozen_oil_run():
    assert PACK["cohort"]["size"] == 134
    assert PACK["cohort"]["built_for"] == "income"
    assert [l["name"] for l in PACK["lenses"]] == [
        "Magic Formula RAW", "Forensic", "Cyclical Income"]
    assert [l["kind"] for l in PACK["lenses"]] == ["selector", "check", "selector"]
    # The replay's honest limitation, recorded rather than hidden.
    assert PACK["valuation_band"] == {
        "buckets": {"cheap": 0, "cheapest": 0, "dear": 0, "dearest": 0, "mid": 0},
        "evaluated": 0, "not_evaluated": 106, "withheld_as_implausible": 0}


# --------------------------------------------------------------------------- #
# (a) a correct summary PASSES
# --------------------------------------------------------------------------- #
def test_a_summary_whose_numbers_all_come_from_the_pack_passes():
    check = check_summary(_summary(), PACK)
    assert check.ok, f"tripped on: {check.reason}"
    assert check.words < WORD_LIMIT
    assert check.withheld_line == ""


def test_a_quoted_verdict_label_in_capitals_is_not_advice():
    """"rated BUY" is quoting the run's own output. The lower-case verb is the ban."""
    assert check_summary(_summary(), PACK).ok          # the base text contains "rated BUY"
    bad = _summary(survived="You should buy Ecopetrol.")
    assert not check_summary(bad, PACK).ok


# --------------------------------------------------------------------------- #
# (b) one changed number, and one inserted word
# --------------------------------------------------------------------------- #
def test_one_changed_number_is_withheld_and_the_number_is_named():
    """67 -> 60: a single digit changed inside an otherwise-correct summary."""
    bad = _summary(happened=_summary().happened.replace("67 names", "60 names"))
    check = check_summary(bad, PACK)
    assert not check.ok
    assert "number not in the facts: 60" in check.reason
    assert check.withheld_line == "Summary withheld: number not in the facts: 60"


def test_the_classic_126_to_120_slip_is_caught():
    """The failure this guard exists for: a plausible number that is not the run's."""
    bad = _summary(happened="Magic Formula RAW ranked 120 names and rated 21 BUY.")
    check = check_summary(bad, PACK)
    assert not check.ok and "number not in the facts: 120" in check.reason


def test_an_inserted_forbidden_word_is_withheld_and_the_word_is_named():
    bad = _summary(survived=_summary().survived + " They look attractive.")
    check = check_summary(bad, PACK)
    assert not check.ok
    assert 'forbidden word: "attractive"' in check.reason
    assert check.withheld_line == 'Summary withheld: forbidden word: "attractive"'


@pytest.mark.parametrize("word", ["should", "recommend", "undervalued", "bargain",
                                  "opportunity", "overvalued"])
def test_every_advice_word_is_caught(word):
    bad = _summary(cannot_say=f"This is a real {word} for patient holders.")
    assert not check_summary(bad, PACK).ok


# --------------------------------------------------------------------------- #
# The other three checks
# --------------------------------------------------------------------------- #
def test_over_the_word_limit_is_withheld_with_the_count():
    bad = _summary(happened=" ".join(["Forensic ranked 106 names."] * 120))
    check = check_summary(bad, PACK)
    assert not check.ok and check.words > WORD_LIMIT
    assert f"{check.words} words" in check.reason


def test_a_missing_field_is_withheld_and_named():
    check = check_summary(_summary(doubt="   "), PACK)
    assert not check.ok and "missing or empty field(s): doubt" in check.reason


def test_a_ticker_the_run_does_not_carry_is_caught():
    bad = _summary(survived="TSLA led the shortlist.")
    check = check_summary(bad, PACK)
    assert not check.ok and "name not in the run: TSLA" in check.reason


def test_a_ticker_the_run_does_carry_is_fine():
    assert check_summary(_summary(survived="MPC and SU are on the shortlist."), PACK).ok


def test_every_problem_is_reported_not_just_the_first():
    """A writer fixing one fault should not meet the next on the following run."""
    bad = _summary(survived="You should buy 999 attractive names.")
    check = check_summary(bad, PACK)
    assert len(check.problems) >= 2
    assert any("forbidden" in p for p in check.problems)
    assert any("999" in p for p in check.problems)


# --------------------------------------------------------------------------- #
# Number handling
# --------------------------------------------------------------------------- #
def test_a_number_written_with_a_thousands_separator_matches():
    assert "134" in str(PACK["cohort"]["size"])
    assert check_summary(_summary(asked="It tested 134 companies that pay dividends."),
                         PACK).ok


def test_a_fraction_in_the_pack_is_quotable_as_a_percentage():
    """The pack holds some limits as fractions; a reader writes them as percentages."""
    pack = {"lenses": [{"name": "X", "cutoff": 0.8}]}
    assert check_summary(
        ReaderSummary(asked="a", happened="The cutoff is 80%.", survived="b",
                      doubt="c", cannot_say="d"), pack).ok


def test_a_number_the_pack_states_only_in_prose_is_quotable():
    """A rule's limit reads "at least $5.0bn"; quoting 5.0 must be allowed."""
    assert any("5.0bn" in r["limit"] for r in PACK["lenses"][2]["rules"])
    assert check_summary(_summary(doubt="The size floor is $5.0bn."), PACK).ok


# --------------------------------------------------------------------------- #
# The prompt's worked example, against THIS pack — a finding, pinned
# --------------------------------------------------------------------------- #
REFERENCE_EXAMPLE = ReaderSummary(
    asked=("It tested 135 oil and gas companies that pay dividends. Three tests ran. "
           "Defensive Income looks for steady payers with a calm share price. Forensic "
           "checks whether the profits are real. Magic Formula looks for cheap, good "
           "businesses."),
    happened=("Defensive Income ranked only 3 of 135 names. One rule did that: it wants "
              "10 years of dividend rises in a row, and 126 names failed it. Forensic "
              "ranked 106 names and rated 21 BUY. Magic Formula ranked 105 and rated 21 "
              "BUY. Only 3 names were BUY on both Forensic and Magic Formula: Marathon "
              "Petroleum, Suncor and Orlen."),
    survived=("No name made the shortlist. The 3 names both tests liked are all at the "
              "top of their own 5-year price range, so the price check dropped them."),
    doubt=("Forensic could not compute its balance-sheet score for 39 of 106 names, "
           "mostly foreign listings. 28 names were too small for any test ($5bn floor). "
           "The price check was withheld for 2 names because the numbers looked wrong."),
    cannot_say=("This list was built for income, and no income test fit it. The BUY "
                "ratings here come from a value test, run while oil prices are at a high."),
)


def test_the_prompts_worked_example_is_a_TONE_exemplar_not_a_claim_about_this_pack():
    """FINDING, pinned so it is not rediscovered as a bug.

    The worked example in ``reader_v1.md`` is prose about the 2026-09-16 run where
    Defensive Income was primary over 135 names. The frozen artefacts in ``runs/`` are a
    DIFFERENT run: 134 names (CTRA had been removed) under Magic Formula RAW, Forensic and
    Cyclical Income, with no Defensive Income lens at all.

    So three of the example's numbers are genuinely absent from this pack, and the
    validator is RIGHT to withhold it:

        135 -> the pack says 134      (the cohort before CTRA was dropped)
        126 -> Defensive Income's streak failures; that lens is not in this run
         39 -> the pack says 40       (Forensic's balance-sheet abstentions)

    That is the guard working, not failing. The prompt says in terms "Do not copy its
    numbers — use the pack's", so the example is a model of REGISTER and LENGTH; checking
    its figures against a pack it was not written from tests the wrong thing. What the
    example must satisfy is the style budget, which it does.
    """
    check = check_summary(REFERENCE_EXAMPLE, PACK)
    assert not check.ok
    assert check.problems == ["number not in the facts: 39, 126, 135"]

    # ...and every OTHER check passes, which is what makes it a usable exemplar: it is
    # inside the word budget, free of advice words, and names only companies in the run.
    assert check.words == 192 and check.words < WORD_LIMIT
    assert not any("forbidden" in p for p in check.problems)
    assert not any("name not in the run" in p for p in check.problems)


def test_the_three_absent_numbers_are_absent_for_the_reason_documented():
    """The claim above, verified rather than asserted in a comment."""
    assert PACK["cohort"]["size"] == 134                        # not 135
    assert "Defensive Income" not in [l["name"] for l in PACK["lenses"]]   # so not 126
    forensic = next(l for l in PACK["lenses"] if l["name"] == "Forensic")
    assert forensic["factor_abstentions"]["altman_z"] == 40     # not 39


# --------------------------------------------------------------------------- #
# READER-2 — two checks the prompt already demanded but nothing enforced
# --------------------------------------------------------------------------- #
# The first live summary (oil_dividend_v1, 2026-09-16 20:47) passed every check and read
# well. Its slips were things the prompt asked for and the checker never looked at.
def _s(**over):
    base = dict(asked="a", happened="b", survived="c", doubt="d", cannot_say="e")
    base.update(over)
    return ReaderSummary(**base)


def test_a_glossed_term_used_bare_is_withheld_and_named():
    from aristos_council.reader_check import GLOSS_TERMS

    assert "percentile" in GLOSS_TERMS
    check = check_summary(_s(doubt="It sits at the 99th percentile of its own range."),
                          {"n": 99})
    assert not check.ok
    assert "term without gloss: percentile" in check.reason


def test_a_glossed_term_passes():
    check = check_summary(
        _s(doubt="It sits at the 99th percentile (dearer than 99% of its own past)."),
        {"n": 99})
    assert check.ok, check.reason


@pytest.mark.parametrize("term", ["free cash flow", "accrual", "momentum"])
def test_every_glossed_term_is_enforced(term):
    check = check_summary(_s(happened=f"The {term} was weak."), {})
    assert not check.ok and f"term without gloss: {term}" in check.reason


def test_the_enforced_list_is_exactly_the_terms_of_art():
    """READER-3. The parametrize above must not drift from the list it claims to cover."""
    from aristos_council.reader_check import GLOSS_TERMS

    assert set(GLOSS_TERMS) == {"percentile", "free cash flow", "accrual", "momentum"}


def test_valuation_is_deliberately_NOT_enforced_either():
    """READER-4, and for a different reason from "balance sheet". It is not that a reader
    knows the word: it is that the bracketed gloss this rule extracted for it was the
    WORST sentence in the 2026-09-17 09:10 summary — "their own price history range
    [valuation (price against its own past)]", a bracket inside a bracket, less legible
    than the jargon it was meant to explain. v4 asks for the plain phrase in the sentence
    instead, and a rule demanding a bracket would work against that."""
    check = check_summary(_s(happened="The valuation was high."), {})
    assert check.ok, check.reason


def test_balance_sheet_is_deliberately_NOT_enforced():
    """READER-3. The gloss rule exists for terms of art a general reader may not hold. A
    balance sheet is a household phrase, and this was the single most common reason a
    written summary was withheld — a summary lost over a word everyone already knows is a
    worse outcome than the word left unglossed."""
    check = check_summary(_s(happened="The balance sheet was weak."), {})
    assert check.ok, check.reason


def test_a_vague_range_is_withheld_and_named():
    """The pack always holds the exact figure; a range is a way of not saying it."""
    check = check_summary(_s(happened="Only 2 to 3 names were rated."), {"a": 2, "b": 3})
    assert not check.ok
    assert "vague range: 2 to 3" in check.reason


def test_an_exact_figure_is_fine():
    assert check_summary(_s(happened="Exactly 134 names were tested."), {"n": 134}).ok


def test_a_date_range_is_not_a_vague_range():
    """"2021 to 2025" is a window, not a hedge — both numbers are in the pack."""
    check = check_summary(_s(happened="It held from 2021 to 2025."),
                          {"from": 2021, "to": 2025})
    assert "vague range" in check.reason      # caught by shape...
    # ...which is a deliberate false positive: the writer should say "for five years", and
    # the pack carries that figure. Recorded here so the behaviour is chosen, not accidental.


# --------------------------------------------------------------------------- #
# The 20:47 live summary, before and after the corrections
# --------------------------------------------------------------------------- #
LIVE_2047_AS_WRITTEN = ReaderSummary(
    asked=("This run tested 134 oil and gas companies built for income. Three tests ran, "
           "each asking something different."),
    happened=("Cyclical Income ranked 23 names. One rule did that: it wants no dividend "
              "cut, and 67 names failed it. Forensic ranked 106 names and rated 22 BUY. "
              "Magic Formula RAW ranked 105 and rated 21 BUY."),
    survived=("No company made the shortlist. Between 2 to 3 names were at the top of "
              "their own price range, and the rest were rated SELL by Forensic."),
    doubt=("Forensic could not work out its score for 40 names. The valuation percentile "
           "could not be worked out for 106 names."),
    cannot_say=("This list was built for income, and the test that picked these names "
                "looks for value instead."),
)


def test_the_20_47_summary_as_written_is_NOW_withheld():
    """It passed every check on the day. The two new checks catch exactly its slips."""
    pack = {"cohort": {"size": 134}, "n": [23, 67, 106, 22, 105, 21, 40, 2, 3]}
    check = check_summary(LIVE_2047_AS_WRITTEN, pack)
    assert not check.ok
    assert "term without gloss: percentile" in check.reason
    assert "vague range: 2 to 3" in check.reason


def test_the_corrected_exemplar_from_the_v2_prompt_passes():
    """The prompt's worked example must itself satisfy the rules it states — an exemplar
    that breaks them teaches the model to break them."""
    corrected = ReaderSummary(
        asked=("This is a list of 134 oil and gas companies built for income. Three tests "
               "ran, each asking something different."),
        happened=("Cyclical Income ranked 43 names and rated 9 BUY. One rule shaped that "
                  "list: it wants no dividend cut in five years, and 39 names failed it. "
                  "Forensic ranked 105 names and rated 22 BUY."),
        survived=("No company made the shortlist. Chord Energy and Magnolia were at the "
                  "top of their own five-year valuation (price against its own past) "
                  "range."),
        doubt=("Forensic could not work out its balance sheet (what a company owns "
               "against what it owes) score for 40 names."),
        cannot_say=("This list was built for income, and the test that picked these names "
                    "looks for value instead."),
    )
    pack = {"n": [134, 43, 9, 5, 39, 105, 22, 40],
            "kept": [{"name": "Chord Energy"}, {"name": "Magnolia"}]}
    check = check_summary(corrected, pack)
    assert check.ok, check.reason


# --------------------------------------------------------------------------- #
# READER-4 — roles, plain glosses, and the clean sweep
# --------------------------------------------------------------------------- #
# Three faults in ONE live summary (oil_dividend_v1, 2026-09-17 09:10) produced all three
# checks below. None of them is a matter of taste: each made the summary say something the
# run did not say.

PACK_0917 = json.loads((FIXTURES / "oil_pack_2026-09-17.json").read_text(encoding="utf-8"))


def _replace_field(summary, **over):
    """One field of a ReaderSummary swapped, the rest kept."""
    fields = {f: getattr(summary, f)
              for f in ("asked", "happened", "survived", "doubt", "cannot_say")}
    return ReaderSummary(**{**fields, **over})


# The v4 worked example, verbatim from reader_v4.md. It is written from PACK_0917 above —
# the same run's facts — which is what lets the prompt ship an example that clears the bar
# it sets.
EXEMPLAR_0917 = ReaderSummary(
    asked=("This is a list of 134 oil and gas companies built for income. Three tests "
           "ran. Cyclical Income is the primary picker; it wants a dividend that "
           "survives the cycle, covered and not cut in five years. Magic Formula RAW is "
           "a second picker, not used for the shortlist; it wants cheap, good "
           "businesses. Forensic is a check; it asks whether the profits are real."),
    happened=("Cyclical Income ranked 43 names and rated 9 BUY. Forensic ranked 104 and "
              "rated 21 BUY. Magic Formula RAW ranked 103 and rated 21 BUY. No single "
              "rule decided any of the three lists."),
    survived=("Suncor Energy is the one company all three tests rated BUY, and it is on "
              "the shortlist of 1. It carries a price warning: it sits at the 99th "
              "percentile (dearer than almost all of its own past) of its own five "
              "years, so it costs far more than usual for the profit it makes. All "
              "three tests read those same recent years, so their agreement is not "
              "proof the price is right. Of the 9 names Cyclical Income picked, 8 were "
              "dropped: Technip Energies, Aker Solutions, Inpex, Imperial Oil and "
              "TotalEnergies were rated SELL by Forensic, and Magnolia Oil & Gas, Chord "
              "Energy and Murphy Oil cost far more than usual for the profit they make, "
              "compared with their own last five years."),
    doubt=("Forensic could not work out its distress score for 36 names, mostly foreign "
           "listings. The price check could not be worked out for 8 names, and was "
           "withheld for 19 more because the numbers looked wrong."),
    cannot_say=("This list was built for income, and nothing here says whether the oil "
                "price will hold."),
)

# The 09:10 summary, carrying the four faults the owner named: Magic Formula RAW called a
# check (it is a second picker); Forensic described as looking for growth (it asks whether
# the profits are real); the band glossed as a bracket inside a bracket; and Suncor — the
# one name every test rated BUY — never mentioned.
SUMMARY_0910 = ReaderSummary(
    asked=("This is a list of 134 oil and gas companies built for income. Three tests "
           "ran. Cyclical Income picked the names. Forensic and Magic Formula RAW are "
           "checks; they look for value and growth."),
    happened=("Cyclical Income ranked 43 names and rated 9 BUY. Forensic ranked 104 and "
              "rated 21 BUY. Magic Formula RAW ranked 103 and rated 21 BUY."),
    survived=("No company made the shortlist. The names Cyclical Income picked were "
              "either rated SELL by Forensic or sat at the top of their own price "
              "history range [valuation (price against its own past)]."),
    doubt=("Forensic could not work out its distress score for 36 names. The price check "
           "could not be worked out for 8 names."),
    cannot_say=("This list was built for income, and nothing here says whether the oil "
                "price will hold."),
)


def test_the_live_0910_summary_is_WITHHELD_and_says_why():
    check = check_summary(SUMMARY_0910, PACK_0917)
    assert not check.ok
    joined = " | ".join(check.problems)
    assert "role mismatch: Magic Formula RAW called a check" in joined
    assert "garbled gloss" in joined
    assert "unanimous BUY not mentioned: Suncor Energy Inc." in joined


def test_a_picker_called_a_check_is_named_in_the_reason():
    """A reader told a picker is a check reads its BUYs as "nothing objectionable found"
    rather than "this test chose it", which inverts what the run said."""
    check = check_summary(SUMMARY_0910, PACK_0917)
    reason = "; ".join(p for p in check.problems if p.startswith("role mismatch"))
    assert "Magic Formula RAW" in reason
    assert "Forensic" not in reason          # Forensic IS a check; it is not mismatched


def test_a_check_called_a_picker_is_caught_the_other_way_round():
    summary = _replace_field(SUMMARY_0910, asked=(
        "This is a list of 134 oil and gas companies built for income. Forensic is the "
        "selector."))
    check = check_summary(summary, PACK_0917)
    assert any("Forensic called a picker" in p for p in check.problems)


def test_a_role_word_about_an_UNNAMED_test_is_not_a_mismatch():
    """The rule fires only where a NAME and a role word share a sentence: a summary
    explaining that "one test is a check" is describing the run, not mis-labelling a
    test."""
    summary = _replace_field(SUMMARY_0910, asked=(
        "This is a list of 134 oil and gas companies built for income. One test is a "
        "check: it only raises doubts."))
    reasons = " ".join(check_summary(summary, PACK_0917).problems)
    assert "role mismatch" not in reasons


def test_a_correctly_labelled_summary_raises_no_role_problem():
    reasons = " ".join(check_summary(EXEMPLAR_0917, PACK_0917).problems)
    assert "role mismatch" not in reasons


def test_a_bracket_inside_a_bracket_is_withheld():
    """A gloss exists to be read. One that needs its own gloss has failed."""
    check = check_summary(
        _s(survived="They sat at the top of their own price history range "
                    "[valuation (price against its own past)]."), {})
    assert any(p.startswith("garbled gloss") for p in check.problems)


def test_an_ORDINARY_gloss_is_untouched():
    check = check_summary(
        _s(doubt="Free cash flow (cash left after costs) was negative for 2 names."),
        {"n": 2})
    assert not any("garbled gloss" in p for p in check.problems), check.reason


def test_a_summary_that_omits_a_unanimous_BUY_is_withheld_and_names_it():
    """A name every test rated BUY is the strongest single fact a multi-test run
    produces. The 09:10 summary omitted the only one it had."""
    summary = _replace_field(EXEMPLAR_0917, survived=(
        "One company stayed on the shortlist. The other 8 were dropped."))
    check = check_summary(summary, PACK_0917)
    assert "unanimous BUY not mentioned: Suncor Energy Inc." in "; ".join(check.problems)


def test_the_TICKER_alone_satisfies_the_unanimous_rule():
    """A summary that says "SU" has named the company; the rule is about mentioning it,
    not about which of its two names is used."""
    summary = _replace_field(EXEMPLAR_0917, survived=(
        "SU is the one company all three tests rated BUY, and it is on the shortlist "
        "of 1."))
    reasons = " ".join(check_summary(summary, PACK_0917).problems)
    assert "unanimous BUY not mentioned" not in reasons


def test_a_run_with_no_unanimous_name_demands_nothing():
    pack = {**PACK_0917, "unanimous_buy": []}
    reasons = " ".join(check_summary(EXEMPLAR_0917, pack).problems)
    assert "unanimous BUY" not in reasons


# --------------------------------------------------------------------------- #
# The v4 exemplar — written from the pack that ships beside it, and proven
# --------------------------------------------------------------------------- #
def test_the_v4_exemplar_PASSES_every_check():
    """The v1-v3 exemplars were prose about a DIFFERENT run, so the validator rightly
    refused them and a test had to pin that as a finding rather than a bug. v4's is
    written from the pack in this repo, so the prompt now ships an example that clears the
    bar it sets — which is the only kind a writer can safely imitate."""
    check = check_summary(EXEMPLAR_0917, PACK_0917)
    assert check.ok, check.reason
    assert check.words <= WORD_LIMIT


def test_the_exemplar_in_the_PROMPT_is_the_one_that_was_checked():
    """A verified example that then drifts from the file is worse than none. Every
    sentence of the checked text must appear in reader_v4.md."""
    from aristos_council.reader import prompt_text

    text = prompt_text().replace("\n> ", " ").replace("\n", " ")
    for field in ("asked", "happened", "survived", "doubt", "cannot_say"):
        for sentence in re.split(r"(?<=[.!?])\s+", getattr(EXEMPLAR_0917, field)):
            if sentence.strip():
                assert " ".join(sentence.split()) in " ".join(text.split()), sentence
