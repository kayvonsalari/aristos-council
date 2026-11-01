"""BATCH 32 (2026-11-01, after the 12:33 CET MSFT run) - follow-ups to batch 31.

1 SUMMARY-COUNT-1   a true count of lenses was withheld; the withheld note must be a whole sentence
2 JUDGE-FIGURE-TOLERANCE-1   two false alarms in the judge figure check
3 SPLIT-WORDING-1   "No lens split: ... except ..." says there is no split and describes one
4 BADGE-EXPLAIN-1   the badge column is "Track record" and is explained on the page
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aristos_council.company_report import LensVote, build_agreement
from aristos_council.company_story import (TRACK_RECORD_HEADER, TRACK_RECORD_INTRO,
                                           badge_legend, badge_tip, shows_track_record, story_page,
                                           summary_note)
from aristos_council.export.report_html import company_report_html
from aristos_council.company_markdown import company_report_markdown
from aristos_council.company_report import format_company_report
from aristos_council.reader_check import check_summary
from aristos_council.summary_counts import count_problems
from tests.test_company_story import _real


# --------------------------------------------------------------------------- #
# 1 SUMMARY-COUNT-1
# --------------------------------------------------------------------------- #
DIV = "dividend yield 0.75%; the rule requires at least 1.5%"
MSFT_PACK = {
    "company": {"name": "Microsoft Corporation (MSFT)"},
    "lenses": [{"name": n, "votes": v} for n, v in [
        ("Value + Momentum", True), ("Defensive Income", True), ("Cyclical Income", True),
        ("Earnings Power Value", True), ("Financials", True), ("Forensic", False),
        ("Growth", True), ("Magic Formula RAW", True), ("Quality", True)]],
    "agreement": {
        "available": True, "lenses_that_voted": 5, "lenses_that_did_not_apply": 3,
        "check_lenses_that_do_not_vote": 1, "buy_votes": 1, "buy_lenses": ["Earnings Power Value"],
        "hold_lenses": ["Value + Momentum", "Growth", "Magic Formula RAW", "Quality"], "sell_lenses": [],
        "buy_lenses_to_name": ["Earnings Power Value"],
        "does_not_apply": [f"Defensive Income ({DIV})", f"Cyclical Income ({DIV})",
                           "Financials (applies only to banks and insurers, not Technology companies)"]},
}
# The 2026-11-01 12:33 summary was withheld on: "Three voting tests ... because Microsoft is in the technology s"
# but the table shows 1. It said three tests did not apply and named a reason the table holds.
TRUE_SENTENCE = ("Three voting tests did not apply, because Microsoft pays a small dividend and is in the "
                 "technology sector, not banking.")
SECTOR_ONLY = "Three voting tests did not apply because Microsoft is in the technology sector."


def test_1_the_true_total_with_a_reason_the_table_holds_is_not_a_mismatch():
    assert count_problems(TRUE_SENTENCE, MSFT_PACK) == []
    assert count_problems(SECTOR_ONLY, MSFT_PACK) == []          # the saved run's shape: total + one true reason


def test_1_the_other_count_forms_still_pass():
    ok = ("Three voting tests did not apply: two because Microsoft's dividend yield is 0.75% against a 1.5% "
          "minimum and one because Microsoft is in the technology sector, not banking.")
    assert count_problems(ok, MSFT_PACK) == []


def test_1_a_wrong_total_or_a_reason_the_table_lacks_is_still_a_mismatch():
    assert count_problems("Two voting tests did not apply because Microsoft is in the technology sector.",
                          MSFT_PACK)
    assert count_problems("Three voting tests did not apply because revenue growth was too low.", MSFT_PACK)
    assert count_problems("Two because Microsoft is in the technology sector.", MSFT_PACK)


def test_1_the_note_is_one_sentence_and_quotes_the_whole_offending_sentence():
    wrong = ("Two voting tests did not apply because Microsoft is in the technology sector, a business with "
             "no bank licence and a very long second half that the old fragment cut off at sixty characters.")
    fields = {"asked": "Microsoft was compared with its peers. Earnings Power Value rated it BUY.",
              "happened": wrong, "survived": "Nothing else.", "doubt": "Nothing else.",
              "cannot_say": "Nothing else."}
    check = check_summary(fields, MSFT_PACK)
    assert not check.ok
    note = summary_note(SimpleNamespace(summary=SimpleNamespace(available=False, note=check.withheld_line)))
    assert note.startswith("The model's summary was withheld because its count of lenses that did not apply "
                           "because of bank or insurer (2) did not match the table (1); the sentence was: ")
    assert f'"{wrong}"' in note                                    # whole, never a fragment
    assert "Summary withheld" not in note                          # the internal prefix is not said twice
    assert note.count("withheld") == 1


def test_1_other_reasons_keep_their_old_shape_without_the_doubled_prefix():
    note = summary_note(SimpleNamespace(summary=SimpleNamespace(
        available=False, note="Summary withheld: number not in the facts: 60")))
    assert note == "The model's plain-English summary was withheld: number not in the facts: 60."


# --------------------------------------------------------------------------- #
# 2 JUDGE-FIGURE-TOLERANCE-1
# --------------------------------------------------------------------------- #
PACK = ("PRICE AND TRADING\n- Analysts' average price target 631.09, 21% above the price\n"
        "FINANCIAL SERIES\n- Free cash flow 66.97bn in FY2026; revenue 331.8bn\n- EV/EBIT 23.1, 9th percentile")


def _call(**kw):
    from aristos_council.judges.panel import JudgeCall
    return JudgeCall(call_no=1, status="ok", verdict="HOLD", confidence="medium", **kw)


def test_2_a_figure_rounded_to_the_judges_own_precision_matches():
    from aristos_council.judges.figures import FIGURE_MARK, check_call
    call = _call(reason="The average target is $631. [pack] Free cash flow was $67.0bn in FY2026. [pack] "
                        "Revenue was $331.8bn. [pack]")
    assert check_call(call, PACK) == 0 and FIGURE_MARK not in call.reason


def test_2_a_figure_that_does_not_round_to_the_pack_is_still_marked():
    from aristos_council.judges.figures import FIGURE_MARK, check_call
    for wrong in ("$632", "$629.69", "$631.10"):
        call = _call(reason=f"The average target is {wrong}. [pack]")
        assert check_call(call, PACK) == 1, wrong
        assert FIGURE_MARK in call.reason
    call = _call(reason="Free cash flow was $68.0bn in FY2026. [pack]")
    assert check_call(call, PACK) == 1


def test_2_the_would_change_my_mind_and_key_risk_lines_are_exempt():
    from aristos_council.judges.figures import FIGURE_MARK, check_call
    call = _call(reason="Revenue was $331.8bn. [pack]",
                 would_change_my_mind="The price falling to $17.00 on unchanged earnings.",
                 key_risk="A drop in free cash flow below $40.0bn would change the picture.")
    assert check_call(call, PACK) == 0
    assert FIGURE_MARK not in call.would_change_my_mind and FIGURE_MARK not in call.key_risk


def test_2_the_same_figure_in_a_fact_line_is_still_checked():
    from aristos_council.judges.figures import FIGURE_MARK, check_call
    call = _call(reason="The share price is $17.00. [pack]")
    assert check_call(call, PACK) == 1 and FIGURE_MARK in call.reason


# --------------------------------------------------------------------------- #
# 3 SPLIT-WORDING-1
# --------------------------------------------------------------------------- #
def _vote(label, verdict, pos=3, of=13):
    return LensVote(label.lower().replace(" ", "_") + "_v1", label, status="ranked", verdict=verdict,
                    position=pos, cohort_size=of)


def _msft_agreement():
    return build_agreement([_vote("Earnings Power Value", "buy"), _vote("Value + Momentum", "hold"),
                            _vote("Growth", "hold"), _vote("Magic Formula RAW", "hold"),
                            _vote("Quality", "hold")])


SAVED_SENTENCE = ("No lens split: every voting lens issued HOLD except Earnings Power Value, which issued BUY.")


def test_3_the_split_sentence_is_counted_from_the_votes():
    ag = _msft_agreement()
    assert ag.split_sentence == "The lenses split 1 BUY / 4 HOLD"
    same = build_agreement([_vote("Quality", "hold"), _vote("Growth", "hold")])
    assert same.split_sentence == "The lenses agree: all 2 say HOLD"
    three = build_agreement([_vote("Quality", "buy"), _vote("Growth", "hold"), _vote("Value + Momentum", "sell")])
    assert three.split_sentence == "The lenses split 1 BUY / 1 HOLD / 1 SELL"
    assert build_agreement([_vote("Quality", "hold")]).split_sentence == ""      # one vote has nothing to split


def test_3_the_narrator_is_handed_the_sentence_and_told_to_use_it():
    from aristos_council.agents.nodes import _agreement_block
    from aristos_council.agents.prompts import STRUCTURED_NARRATION
    from aristos_council.company_report import _council_agreement_row
    row = _council_agreement_row(_msft_agreement())
    assert row["lens_split"] == "The lenses split 1 BUY / 4 HOLD"
    block = _agreement_block(SimpleNamespace(agreement_row=row))
    assert 'LENS SPLIT SENTENCE' in block and '"The lenses split 1 BUY / 4 HOLD"' in block
    assert "'The lenses split 1 BUY / 4 HOLD' when the votes differ" in STRUCTURED_NARRATION
    assert "'The lenses agree: all 5 say HOLD' ONLY when every vote is the same" in STRUCTURED_NARRATION
    assert "NEVER write 'no lens split'" in STRUCTURED_NARRATION


def _narration(note):
    from aristos_council.agents.schemas import Narration, SpecialistView
    return Narration(echoed_verdict="HOLD (1 BUY, 4 HOLD, 0 SELL)", lens_verdicts=[], lens_attribution=[],
                     neutral_context=["x"], disagreement_note=note, open_questions=["q"],
                     specialist_views=[SpecialistView(specialist="fundamental", stance="bullish",
                                                      confidence=0.7, reasoning="Bullish.")])


def _codes(narration, record):
    from aristos_council.narration_schema import validate_narration
    return [i.code for i in validate_narration(narration, verdict_of_record=record)]


def test_3_no_split_over_differing_votes_is_flagged():
    record = "HOLD (1 BUY, 4 HOLD, 0 SELL)"
    assert "split_wording_contradicts_the_votes" in _codes(_narration(SAVED_SENTENCE), record)
    assert "split_wording_contradicts_the_votes" in _codes(_narration("The lenses agree: four say HOLD."), record)
    assert "split_wording_contradicts_the_votes" not in _codes(
        _narration("The lenses split 1 BUY / 4 HOLD: Earnings Power Value says BUY."), record)


def test_3_a_split_claimed_over_identical_votes_is_flagged():
    record = "HOLD (0 BUY, 5 HOLD, 0 SELL)"
    assert "split_wording_contradicts_the_votes" in _codes(_narration("The lenses split on this name."), record)
    assert "split_wording_contradicts_the_votes" not in _codes(
        _narration("The lenses agree: all 5 say HOLD."), record)
    # no counts on the record line -> never guessed
    assert "split_wording_contradicts_the_votes" not in _codes(_narration(SAVED_SENTENCE), "BUY on 1 of 5 votes")


# --------------------------------------------------------------------------- #
# 4 BADGE-EXPLAIN-1
# --------------------------------------------------------------------------- #
def test_4a_the_column_is_called_track_record_in_all_four_renderers(tmp_path):
    from aristos_council import company_cards
    rep = _real(tmp_path)
    assert TRACK_RECORD_HEADER == "Track record"
    assert story_page(rep).headers == ("Lens", "Vote or mark", "Track record", "Reason")
    assert "Track record" in format_company_report(rep).split("SHOW THE WORKINGS")[0].split("Lens ")[1]
    assert "| Lens | Vote or mark | Track record | Reason |" in company_report_markdown(rep)
    assert "<th>Track record</th>" in company_report_html(rep)
    assert "Track record" in company_cards.lens_table(rep) and ">Badge<" not in company_cards.lens_table(rep)


def test_4b_one_line_under_the_table_comes_before_the_existing_track_record_lines(tmp_path):
    rep = _real(tmp_path)
    assert shows_track_record(rep)
    assert TRACK_RECORD_INTRO == ("Track record: whether this lens beat its industry group in a ten-year test. It "
                                  "describes the lens, not the company, and changes no vote. Full definitions "
                                  "under Lens notes.")
    page = story_page(rep)
    assert page.track_intro == TRACK_RECORD_INTRO
    text, md, html = format_company_report(rep), company_report_markdown(rep), company_report_html(rep)
    for doc in (text, md, html):
        assert doc.count(TRACK_RECORD_INTRO) == 1
        if page.caption:
            assert doc.index(TRACK_RECORD_INTRO) < doc.index(page.caption)
    assert text.index(TRACK_RECORD_INTRO) < text.index("SHOW THE WORKINGS")


def test_4c_lens_notes_open_with_a_legend_built_from_the_badge_codes_own_strings(tmp_path):
    from aristos_council.backtest import (BADGE_LABELS, LABEL_BAR, LABEL_BEHIND, LABEL_NO_EDGE, LABEL_PASSED,
                                          LABEL_UNDER_TEST, LABEL_UNTESTED)
    legend = badge_legend()
    assert len(legend) == len(BADGE_LABELS) + 1 and legend[-1].startswith("none: ")
    for label in BADGE_LABELS:                    # a label added to the code cannot be left out of the legend
        assert any(line.startswith(f"{label}: ") for line in legend), label
    for label in (LABEL_PASSED, LABEL_BAR, LABEL_BEHIND, LABEL_NO_EDGE, LABEL_UNTESTED, LABEL_UNDER_TEST):
        assert label in BADGE_LABELS
    rep = _real(tmp_path)
    text, md, html = format_company_report(rep), company_report_markdown(rep), company_report_html(rep)
    notes_text = text[text.index("LENS NOTES"):]
    notes_md = md[md.index("### Lens notes"):]
    notes_html = html[html.index("<h2>Lens notes</h2>"):]
    assert notes_text.split("\n")[1].strip() == legend[0]               # first thing under the heading
    assert "passed the test here" in notes_html
    for line in legend:
        assert line in text and line in md
        assert line.replace("'", "&#x27;") in html or line in html
    # the legend is the FIRST thing under the Lens notes heading
    assert notes_md.split("\n")[2].startswith("- " + legend[0])


def test_4c_no_legend_and_no_explanation_where_no_lens_has_a_track_record(tmp_path):
    rep = _real(tmp_path)
    rep.votes = [LensVote(v.strategy_id, v.label, kind=v.kind, status=v.status, verdict=v.verdict,
                          position=v.position, cohort_size=v.cohort_size, reason=v.reason, asks=v.asks)
                 for v in rep.votes]                                         # every badge gone
    assert not shows_track_record(rep)
    legend_line_start = "passed the test here: in this industry"
    assert story_page(rep).track_intro == ""
    for doc in (format_company_report(rep), company_report_markdown(rep), company_report_html(rep)):
        assert "whether this lens beat its industry group" not in doc
        assert legend_line_start not in doc


def test_4d_html_and_streamlit_show_each_badge_values_meaning_as_a_tooltip(tmp_path):
    from aristos_council import company_cards
    rep = _real(tmp_path)
    page = story_page(rep)
    badges = {r.badge for r in page.rows if r.badge != "none"}
    assert badges
    html, cards = company_report_html(rep), company_cards.lens_table(rep)
    import html as _h
    for badge in badges:
        tip = badge_tip(badge)
        assert tip
        assert f'title="{_h.escape(tip, quote=True)}"' in html
        assert f'title="{_h.escape(tip)}"' in cards or f'title="{_h.escape(tip, quote=True)}"' in cards
    assert badge_tip("none").startswith("the lens did not vote")
    assert badge_tip("proven here") == badge_tip("passed the test here")     # a saved report's old word still reads


def test_4e_the_wider_group_line_says_track_record_not_badges():
    from aristos_council.company_story import badge_group_note
    group = SimpleNamespace(broad=True)
    rep = SimpleNamespace(peer_group=group, track_record_caption="Track record from the Semiconductors cohort, "
                                                                    "10 years to Oct 2026")
    note = badge_group_note(rep)
    assert note == ("Track record is from tests on Semiconductors companies; this run compared it with a wider "
                    "sector group, so the track record is a weaker guide.")
    assert "badge" not in note.lower()


def test_4f_the_story_says_track_record_not_badge(tmp_path):
    from aristos_council.company_story import story_paragraphs
    rep = _real(tmp_path)
    doubt = dict(story_paragraphs(rep))["What to doubt."]
    assert "badge" not in doubt.lower()
