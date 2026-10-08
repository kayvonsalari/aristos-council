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


# --------------------------------------------------------------------------- #
# B24-E9 - how a reader sees the check
# --------------------------------------------------------------------------- #
from aristos_council import ai_text_check as atc  # noqa: E402
from aristos_council.export.report_html import _narration_html, narration_reader_html  # noqa: E402

STAMP = ('[⚠ AI text check: "Value + Momentum issued a SELL on EL.PA" gives Value + Momentum a verdict '
         "or a vote, but that lens did not apply - it has only a would-rank reading, which is not a vote "
         "and never a verdict]")
PROSE = ("##### Cyclical Income - why\n\nValue + Momentum issued a SELL on EL.PA. Quality said HOLD.\n\n"
         "A second paragraph that is fine.")


def test_the_top_line_counts_or_says_no_issues():
    assert atc.top_line(0) == "AI text check: no issues found"
    assert atc.top_line(1) == "AI text check: 1 sentence flagged, marked below"
    assert atc.top_line(3) == "AI text check: 3 sentences flagged, marked below"


def test_old_stamps_written_before_the_rename_still_count_and_read():
    old = STAMP.replace("AI text check", "narration check")
    assert atc.count(PROSE + "\n" + old) == 1 == atc.count(PROSE + "\n" + STAMP)
    assert atc.claim_of(old) == "Value + Momentum issued a SELL on EL.PA"


def test_the_reason_is_plain_english_for_each_kind_of_flag():
    cases = {
        STAMP: "This lens did not vote; the sentence treats it as a vote",
        '[⚠ AI text check: "x" attributes Forensic\'s 12th of 21 rank to Quality - it belongs to Forensic]':
            "This rank belongs to a different lens",
        '[⚠ AI text check: "x" cites a 12th of 21 rank without naming the lens it belongs to (Forensic)]':
            "This rank does not say which lens it belongs to",
        '[⚠ AI text check: "x" cites Value + Momentum\'s 20th of 21 without saying it is only where the '
        "company WOULD rank on that lens's measures - the lens did not apply, so it is not a vote and not a "
        "verdict]": "This is only where the company would rank on a lens that did not apply; it is not a vote",
        '[⚠ AI text check: "x" weighs the lenses against each other - ...]':
            "This weighs the lenses against each other; every lens is an equal vote",
        '[⚠ AI text check: "a b" appears near-verbatim in 2 specialists\' theses (technical, risk) - convergent]':
            "2 specialists used almost the same words; that is not independent analysis",
        '[⚠ AI text check: "x" contradicts rank table - table is authoritative]':
            "This does not match the rank table",
    }
    for stamp, expected in cases.items():
        assert atc.plain_reason(stamp) == expected


def test_the_reader_view_marks_the_flagged_sentence_and_lists_nothing_below():
    html = narration_reader_html(PROSE + "\n" + STAMP)
    assert html.count('class="ar-flag"') == 1
    # the marker sits right after the flagged sentence (and its full stop), before the next sentence
    i_sentence, i_marker, i_next = (html.index("Value + Momentum issued a SELL on EL.PA."),
                                    html.index('class="ar-flag"'), html.index("Quality said HOLD"))
    assert i_sentence < i_marker < i_next
    assert "This lens did not vote; the sentence treats it as a vote" in html          # hover / tap text
    assert 'tabindex="0"' in html                                                       # a tap focuses it
    assert "callout" not in html and "AI text check:" not in html                      # no list at the bottom
    assert "A second paragraph that is fine." in html


def test_a_flag_whose_sentence_cannot_be_found_is_still_marked_at_the_end():
    html = narration_reader_html("Only prose here.\n" + STAMP)
    assert html.count('class="ar-flag"') == 1 and "Only prose here." in html


def test_the_downloaded_report_keeps_the_full_list_and_quotes_each_sentence():
    html = _narration_html(PROSE + "\n" + STAMP)
    assert "AI text check" in html and "Value + Momentum issued a SELL on EL.PA" in html
    assert "narration check" not in html


def test_every_section_gets_the_one_line_in_the_page_and_the_downloads(tmp_path):
    from aristos_council.company_markdown import company_report_markdown
    from aristos_council.company_report import CouncilOpinion, format_company_report
    from aristos_council.company_story import narration_check_line
    from aristos_council.export.report_html import company_report_html
    from tests.test_company_report import RAW, _run
    rep = _run([RAW], tmp_path=tmp_path)
    rep.council_opinion = CouncilOpinion(available=True, narrative=PROSE + "\n" + STAMP)
    for doc in (format_company_report(rep), company_report_markdown(rep), company_report_html(rep)):
        assert "AI text check: 1 sentence flagged, marked below" in doc
    assert narration_check_line(rep).startswith("AI text check: 1 sentence in the council opinion")
    rep.council_opinion = CouncilOpinion(available=True, narrative=PROSE)
    assert "AI text check: no issues found" in format_company_report(rep)
