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
