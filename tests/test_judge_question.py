"""ABSOLUTE-PAGE-1 (docs/DESIGN_absolute.md section k): the judges answer the ten-year question.

What is pinned: the question's text and the v3 prompt that asks it; the YES / NO / ABSTAIN parser (BUY, HOLD and
SELL are no longer answers); the pack stays free of the code's verdict, hurdle and returns; the agreement line (YES
against "clears hurdle", NO against "below hurdle", everything else "no comparison"); and that the OLD records stay
as they are - labelled answers to the old question, kept out of the new agreement figure, with the bar restarted.
Every model call is a fake; the log is a tmp directory.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from aristos_council.judges import answer as ans
from aristos_council.judges import log as jlog
from aristos_council.judges import panel, render, score
from aristos_council.judges import report as jreport
from aristos_council.judges.blind_pack import build_blind_pack, find_leaks
from aristos_council.judges.config import (OLD_QUESTION_VERSION, PROMPT_VERSION, QUESTION_START_DATE,
                                           QUESTION_TEXT, QUESTION_VERSION, prompt_text, question_of)
from tests.test_company_report import RAW, STRAT_DIR, _Adapter, _run
from tests.test_judge_panel import _reply, _run_panel, _scripted

pytestmark = pytest.mark.min_group(3)


@pytest.fixture
def report(tmp_path):
    return _run([RAW, "forensic_v1"], tmp_path=tmp_path, save=False)


def test_the_prompt_asks_the_design_question_in_full_and_names_its_three_answers():
    assert PROMPT_VERSION == "judge_prompt_v3" and QUESTION_VERSION == "ten_year_hurdle_v1"
    text = prompt_text()
    for part in ("Using only the evidence pack and anything you know about the company, which you must label",
                 "would a person who buys this company at today's price and holds it for ten years more likely than "
                 "not earn more than the ten-year government bond yield of the company's currency plus 5 points a year",
                 "Answer YES, NO or ABSTAIN, with a confidence and the one fact that decided it"):
        assert part in text
    assert "BUY|HOLD|SELL" not in text and '"verdict": "YES|NO|ABSTAIN"' in text
    assert QUESTION_TEXT.startswith("Using only the evidence pack") and QUESTION_TEXT.endswith("that decided it.")
    old_prompt = Path(__file__).resolve().parents[1] / "src" / "aristos_council" / "judges" / "judge_prompt_v2.md"
    assert old_prompt.exists()                                           # the record of the old question


@pytest.mark.parametrize("word", ["YES", "NO", "ABSTAIN", "yes", " no "])
def test_yes_no_and_abstain_are_the_only_answers(word):
    got, why = ans.parse_answer(_reply(word))
    assert why == "" and got.verdict == word.strip().upper()


@pytest.mark.parametrize("word", ["BUY", "HOLD", "SELL", "MAYBE", ""])
def test_the_old_answers_are_not_answers_any_more(word):
    got, why = ans.parse_answer(_reply(word))
    assert got is None and why == "verdict is not one of YES, NO, ABSTAIN"


def test_the_pack_carries_no_verdict_hurdle_growth_requirement_or_return_range(report):
    text = build_blind_pack(report, _Adapter().get_fundamentals("CO"), strategies_dir=STRAT_DIR).text()
    low = text.lower()
    for word in ("hurdle", "implied growth", "return range", "clears", "qualifies", "below hurdle", "verdict",
                 "owner's return"):
        assert word not in low, word
    # ...and the scan that keeps it so now knows the new vocabulary
    for leak in ("the hurdle is 9.9%", "implied growth 25%", "return range 4% to 8%"):
        assert find_leaks(leak, strategies_dir=STRAT_DIR, check_generic_names=False), leak


def _stable_judges(verdicts):
    out = []
    for i, v in enumerate(verdicts):
        j = panel.JudgeResult(f"f{i}", f"J{i}", "m", status=panel.STABLE)
        j.calls = [panel.JudgeCall(1, "ok", v, "low", "r", "w"), panel.JudgeCall(2, "ok", v, "low", "r", "w")]
        out.append(j)
    return out


def test_yes_against_clears_and_no_against_below_and_nothing_else_is_compared():
    rec = lambda side, state, word: panel.RecordSnapshot(verdict=side, state=state, word=word)
    clears = rec("CLEARS", "qualifies_clears_hurdle", "Qualifies, clears hurdle")
    below = rec("BELOW", "qualifies_below_hurdle", "Qualifies, below hurdle")
    stable = lambda *v: _stable_judges(v)
    assert panel.panel_agreement(stable("YES", "YES"), clears) == panel.AGREES
    assert panel.panel_agreement(stable("NO", "NO"), below) == panel.AGREES
    assert panel.panel_agreement(stable("NO", "NO"), clears) == panel.DISAGREES
    assert panel.panel_agreement(stable("YES", "YES"), below) == panel.DISAGREES
    assert panel.panel_agreement(stable("ABSTAIN", "ABSTAIN"), clears) == panel.NO_STABLE
    for state, word in (("excluded", "Excluded"), ("not_covered", "Not covered"),
                        ("not_enough_data", "Not enough data")):
        assert panel.panel_agreement(stable("YES", "NO"), rec("", state, word)) == panel.NO_COMPARISON


def test_the_page_line_after_the_code_has_finished(report):
    """The panel runs on a report whose verdict the code already reached, and the line says whether they agree."""
    caller = _scripted({"anthropic": [_reply("NO")], "openai": [_reply("NO")], "google": [_reply("YES")]})
    result = _run_panel(report, caller)
    state = report.check.absolute.verdict.state if report.check.absolute is not None else ""
    assert result.record.state == state and result.question == QUESTION_VERSION
    (line, *_rest) = render.summary_lines(result)
    assert line.startswith("Verdict: ") and "Panel: 1 YES, 2 NO" in line
    if state in ("qualifies_clears_hurdle", "qualifies_below_hurdle"):
        assert line.endswith(("agrees.", "disagrees.", "mixed against the verdict."))
    else:
        assert line.endswith("no comparison.")                    # this fixture has no priced verdict


def test_the_judge_cards_and_exports_say_yes_no_and_abstained_never_buy_hold_sell(report):
    caller = _scripted({"anthropic": [_reply("YES")], "openai": [_reply("NO")], "google": [_reply("ABSTAIN")]})
    report.judge_panel = _run_panel(report, caller)
    md = "\n".join(render.markdown_lines(report.judge_panel))
    assert "**Claude (claude-test)" in md and "YES" in md and "NO" in md and "abstained" in md
    assert not re.search(r"\b(?:BUY|HOLD|SELL)\b", md.replace("BUY, HOLD or SELL", ""))
    assert "ten-year government bond yield" in render.CAPTION and QUESTION_START_DATE == "2026-11-04"
    assert "older question" in render.CAPTION


# --------------------------------------------------------------------------- #
# old records stay as they are, and stay out of the new figure
# --------------------------------------------------------------------------- #
OLD_SUMMARY = ("run_date,ticker,pack_hash,verdict_of_record,lens_split,record_headline,claude_verdict,"
               "claude_confidence,openai_verdict,openai_confidence,gemini_verdict,gemini_confidence,panel_split,"
               "panel_agreement,judges_answered,peers\n"
               "2026-10-19T09:01:00+00:00,KO,abc123,HOLD,BUY 0 / HOLD 2 / SELL 0,HOLD,HOLD,medium,HOLD,low,BUY,high,"
               "\"2 HOLD, 1 BUY\",mixed,3,PEP;PG\n")


def test_an_old_summary_file_is_upgraded_in_place_and_its_rows_are_kept(tmp_path, report):
    folder = tmp_path / "judges"
    folder.mkdir()
    path = folder / jlog.SUMMARY_NAME
    path.write_text(OLD_SUMMARY, encoding="utf-8", newline="")
    assert tuple(OLD_SUMMARY.splitlines()[0].split(",")) in jlog._PREVIOUS[jlog.SUMMARY_NAME]   # a known header
    caller = _scripted({"anthropic": [_reply("NO")], "openai": [_reply("NO")], "google": [_reply("NO")]})
    jlog.write_log(_run_panel(report, caller), folder)
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    assert tuple(rows[0].keys()) == jlog.SUMMARY_COLUMNS and jlog.SUMMARY_COLUMNS[-2:] == ("question", "hurdle")
    old, new = rows
    assert old["ticker"] == "KO" and old["claude_verdict"] == "HOLD" and old["panel_agreement"] == "mixed"
    assert old["question"] == "" and new["question"] == QUESTION_VERSION
    assert question_of(old) == OLD_QUESTION_VERSION and question_of(new) == QUESTION_VERSION


def test_old_rows_are_not_mixed_into_the_new_agreement_figure():
    rows = [{"ticker": "KO", "panel_agreement": "agrees", "question": ""},
            {"ticker": "PG", "panel_agreement": "disagrees", "question": ""},
            {"ticker": "A", "panel_agreement": "agrees", "question": QUESTION_VERSION},
            {"ticker": "B", "panel_agreement": "no_comparison", "question": QUESTION_VERSION},
            {"ticker": "C", "panel_agreement": "mixed", "question": QUESTION_VERSION}]
    text = score.agreement_text(rows)
    assert "1 agree, 0 disagree, 1 mixed, 1 no comparison, out of 3 panel run(s)" in text
    assert "2 earlier run(s) answered the older BUY, HOLD or SELL question and are not counted" in text
    assert f"restarted on {QUESTION_START_DATE}" in text and "30-name bar" in text


def test_the_judge_report_labels_an_old_row_as_the_older_question():
    old = {"run_date": "2026-10-19T09:01:00+00:00", "ticker": "KO", "verdict_of_record": "HOLD",
           "lens_split": "BUY 0 / HOLD 2 / SELL 0", "claude_verdict": "HOLD", "panel_split": "1 HOLD",
           "panel_agreement": "agrees", "pack_hash": "x", "question": ""}
    new = {**old, "run_date": "2026-11-04T09:00:00+00:00", "verdict_of_record": "Qualifies, below hurdle",
           "claude_verdict": "NO", "question": QUESTION_VERSION}
    out = jreport.format_report([old, new])
    assert "(older question: BUY, HOLD or SELL)" in out and "(ten-year question)" in out


def _series(start, end_ret_by_days):
    from datetime import date, timedelta
    d0 = date(2026, 10, 1)
    return [(d0 + timedelta(days=k), 100.0 * (1 + r)) for k, r in end_ret_by_days]


def test_yes_is_right_when_the_return_beats_the_pro_rated_hurdle_and_no_when_it_does_not():
    assert score.yes_no_outcome("YES", 0.051, 0.10, 6) == score.RIGHT          # 10% x 6/12 = 5%: 5.1% beats it
    assert score.yes_no_outcome("YES", 0.05, 0.10, 6) == score.WRONG           # equal is not beating
    assert score.yes_no_outcome("NO", 0.05, 0.10, 6) == score.RIGHT
    assert score.yes_no_outcome("NO", 0.30, 0.10, 12) == score.WRONG


def test_scoring_rows_of_the_ten_year_question_against_the_recorded_hurdle():
    from datetime import date
    prices = {"CO": _series(0, [(0, 0.0), (91, 0.06), (182, 0.04), (365, 0.30), (400, 0.30)])}
    row = {"run_date": "2026-10-01T09:00:00+00:00", "ticker": "CO", "verdict_of_record": "Qualifies, below hurdle",
           "claude_verdict": "YES", "openai_verdict": "NO", "gemini_verdict": "ABSTAIN", "peers": "",
           "question": QUESTION_VERSION, "hurdle": "0.0993"}
    out = score.score_rows([row], prices, date(2027, 11, 1))
    by = {(r["subject"], int(r["window_months"])): r for r in out}
    # 3 months: 6% beat 9.93% x 3/12 = 2.5%; 6 months: 4% did not beat 4.97%; 12 months: 30% beat 9.93%
    assert by[("claude", 3)]["status"] == score.RIGHT and by[("openai", 3)]["status"] == score.WRONG
    assert by[("claude", 6)]["status"] == score.WRONG and by[("openai", 6)]["status"] == score.RIGHT
    assert by[("claude", 12)]["status"] == score.RIGHT and by[("openai", 12)]["status"] == score.WRONG
    assert all(by[("gemini", m)]["status"] == score.NOT_SCORED for m in (3, 6, 12))       # ABSTAIN is never scored
    assert all(by[("lenses", m)]["status"] == score.NOT_SCORED for m in (3, 6, 12))
    assert {r["rule"] for r in out} == {score.RULE_NEW}


def test_an_old_row_keeps_the_old_rule_and_says_so_and_a_new_row_without_a_hurdle_is_no_data():
    from datetime import date
    prices = {"CO": _series(0, [(0, 0.0), (91, 0.10), (400, 0.10)]), "A": _series(0, [(0, 0.0), (95, 0.0), (400, 0.0)]),
              "B": _series(0, [(0, 0.0), (95, 0.0), (400, 0.0)]), "C": _series(0, [(0, 0.0), (95, 0.0), (400, 0.0)])}
    old = {"run_date": "2026-10-01T09:00:00+00:00", "ticker": "CO", "verdict_of_record": "BUY", "claude_verdict": "BUY",
           "openai_verdict": "", "gemini_verdict": "", "peers": "A;B;C", "question": ""}
    new = {**old, "verdict_of_record": "Qualifies, below hurdle", "claude_verdict": "YES", "question": QUESTION_VERSION,
           "hurdle": "", "peers": ""}
    out = score.score_rows([old, new], prices, date(2027, 11, 1))
    olds = [r for r in out if r["rule"] == score.RULE_OLD and r["subject"] == "claude" and int(r["window_months"]) == 3]
    news = [r for r in out if r["rule"] == score.RULE_NEW and r["subject"] == "claude" and int(r["window_months"]) == 3]
    assert olds[0]["status"] == score.RIGHT                       # BUY: beat its cohort by 10 points
    assert news[0]["status"] == score.NO_DATA                     # no hurdle was recorded
    assert "pro-rated to the window" in score.summary_text(out) and "Older rows keep the rule above" in score.summary_text(out)


def test_the_summary_log_records_the_hurdle_so_a_row_can_be_scored_later(tmp_path, report):
    caller = _scripted({"anthropic": [_reply("NO")], "openai": [_reply("NO")], "google": [_reply("NO")]})
    result = _run_panel(report, caller)
    assert jlog.SUMMARY_COLUMNS[-2:] == ("question", "hurdle")
    row = jlog.summary_row(result)
    expected = "" if result.record.hurdle is None else f"{result.record.hurdle:.6f}"
    assert row[-1] == expected and row[-2] == QUESTION_VERSION
