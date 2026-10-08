"""B24-E1 - the AI text check (was "narration check") stops flagging true sentences.

Replayed offline on Kayvon's full EL.PA run (2026-10-08 09:40; fixture: its council text with the flags
stripped, plus the lens verdicts it was checked against): the run showed 8 flags - 6 false, 2 fair
convergence flags. The three checks must now raise none of the 6, and must still catch real errors."""
import json
from pathlib import Path

from aristos_council.narration_check import (check_cross_lens, check_rank_attribution, check_would_rank)

FIX = json.loads((Path(__file__).parent / "fixtures" / "narration" / "elpa_20261008.json").read_text(encoding="utf-8"))
VERDICTS = FIX["verdicts"]


def _flags(text, verdicts=None):
    v = verdicts or VERDICTS
    return check_cross_lens(text, v) + check_rank_attribution(text, v) + check_would_rank(text, v)


def test_the_saved_elpa_run_replays_with_no_false_flags():
    assert FIX["original_flag_count"] == 8          # 6 false + the 2 convergence flags (kept: unchanged code)
    assert _flags(FIX["narrative_without_flags"]) == []


def test_a_rank_is_matched_to_the_lens_the_sentence_names_not_to_the_rank_alone():
    for sentence in ("Magic Formula RAW ranked EL.PA 20th of 21 - a near-bottom placement.",
                     "The voting lenses split: Cyclical Income issued a BUY (1st of 6) while Magic Formula RAW "
                     "issued a SELL (20th of 21), and Earnings Power Value and Quality both issued HOLDs.",
                     "The ROIC of 5.1% drives the Magic Formula RAW SELL at 20th of 21."):
        assert check_would_rank(sentence, VERDICTS) == [], sentence


def test_true_positive_the_would_rank_lens_given_a_rank_without_would_rank_is_still_flagged():
    flags = check_would_rank("Value + Momentum ranked EL.PA 20th of 21.", VERDICTS)
    assert len(flags) == 1 and "WOULD rank" in flags[0]


def test_true_positive_giving_a_non_voting_lens_a_verdict_is_still_flagged():
    for sentence in ("Value + Momentum issued a SELL on EL.PA.", "Defensive Income voted HOLD.",
                     "Growth votes BUY on the name."):
        assert check_would_rank(sentence, VERDICTS), sentence


def test_describing_why_a_lens_excluded_the_company_is_not_a_verdict():
    s = ("The specialist notes that the Defensive Income lens explicitly cited the 46.0% 12-month fall as a "
         "disqualifier, and that the Magic Formula RAW SELL and Earnings Power Value HOLD are consistent with "
         "the technical deterioration.")
    assert check_would_rank(s, VERDICTS) == []
    # ...but the same sentence with a verdict attached to Defensive Income is caught
    assert check_would_rank("The Defensive Income lens issued a SELL while Magic Formula RAW says HOLD.", VERDICTS)


def test_a_section_heading_that_names_the_lens_counts_as_naming_it():
    text = ("##### Forensic - why\n\nThe Forensic lens is a CHECK. It returned a reading of no concern and ranked "
            "EL.PA 12th of 21 (tied with one other name).")
    assert check_rank_attribution(text, VERDICTS) == []
    no_heading = "##### Something else\n\nIt returned a reading of no concern and ranked EL.PA 12th of 21."
    flags = check_rank_attribution(no_heading, VERDICTS)
    assert len(flags) == 1 and "Forensic" in flags[0]                     # true positive survives
    wrong = "##### Quality - why\n\nIt ranked EL.PA 12th of 21."          # the heading names ANOTHER lens
    assert check_rank_attribution(wrong, VERDICTS)


def test_a_real_misattribution_by_name_is_still_caught():
    flags = check_rank_attribution("Quality ranked EL.PA 12th of 21.", VERDICTS)
    assert flags and "Forensic" in flags[0]
