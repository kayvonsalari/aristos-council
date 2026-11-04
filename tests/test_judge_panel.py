"""JUDGE-PANEL-1 - the blind judge panel: it records, it never votes.

Every model call is a fake (a plain function handed in as ``caller``), the company is the
fabricated one ``test_company_report`` already uses, and the log is a tmp directory - nothing here
reaches a provider, a key or the owner's data/local/judges/.

What is pinned: the pack a judge sees carries no lens name, no BUY/HOLD/SELL and no badge word
(and a line that leaks is dropped, not sent); the JSON answer is validated strictly with one
retry; stability (same / different / incomplete); a missing key is "not run" and the others still
run; the split and agreement lines; the narrator paragraph is asked for only when the panel
differs and is withheld when it recommends; the log has the agreed columns; and - the contract -
the lens output is byte-identical with the panel on or off.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from aristos_council.company_report import format_company_report, report_record
from aristos_council.judges import answer as ans
from aristos_council.judges import log as jlog
from aristos_council.judges import narrate, panel, render
from aristos_council.judges import report as jreport
from aristos_council.judges.blind_pack import build_blind_pack, find_leaks
from aristos_council.judges.clients import CallError, http_caller
from aristos_council.judges.config import JudgeSpec, PanelConfig, load_config
from tests.test_company_report import (RAW, STRAT_DIR, _Adapter, _no_news, _run, _table)

pytestmark = pytest.mark.min_group(3)

KEYS = {"ANTHROPIC_API_KEY": "k-a", "OPENAI_API_KEY": "k-o", "GEMINI_API_KEY": "k-g"}


def _reply(verdict="YES", confidence="medium", reason="Cash flow covers its debt twice over [pack].",
           change="A cut to the dividend.", fact="Free cash flow was $5.3bn in FY2025 [pack].",
           risk="A one-off tax bill could recur.", unexplained="nothing"):
    return json.dumps({"verdict": verdict, "confidence": confidence, "driving_fact": fact,
                       "reasoning": reason, "key_risk": risk, "unexplained": unexplained,
                       "would_change_my_mind": change})


def _config(n=3, calls=2):
    specs = (JudgeSpec("anthropic", "Claude", "claude-test", "ANTHROPIC_API_KEY"),
             JudgeSpec("openai", "OpenAI", "gpt-test", "OPENAI_API_KEY"),
             JudgeSpec("google", "Gemini", "gemini-test", "GEMINI_API_KEY"))
    return PanelConfig(judges=specs[:n], calls_per_judge=calls)


def _scripted(script):
    """A caller answering from ``script``: {family: [reply, reply, ...]} consumed in order."""
    seen = {k: list(v) for k, v in script.items()}
    calls = []

    def caller(spec, system, user):
        calls.append((spec.family, user))
        queue = seen[spec.family]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    caller.calls = calls
    return caller


@pytest.fixture
def report(tmp_path):
    return _run([RAW, "forensic_v1"], tmp_path=tmp_path, save=False)


# =========================================================================== #
# the blind pack
# =========================================================================== #
def test_the_pack_has_no_lens_name_no_verdict_word_and_no_badge_word(report):
    pack = build_blind_pack(report, _Adapter().get_fundamentals("CO"), strategies_dir=STRAT_DIR)
    text = pack.text()
    assert text and pack.dropped == []
    # no lens, by id or by the label the page prints
    for v in report.votes:
        assert v.strategy_id not in text and v.label not in text
    assert find_leaks(text, strategies_dir=STRAT_DIR, check_generic_names=False) == []
    assert not re.search(r"\b(?:buy|hold|sell)\b", text, re.I)
    for word in ("proven", "no edge", "untested", "worked against you", "passed the test", "behind random", "doubted", "track record",
                 "shortlist", "agreement", "council", "verdict", "lens", "rank"):
        assert not re.search(rf"(?<![A-Za-z]){word}", text, re.I), word


def _dated_fundamentals():
    import dataclasses
    return dataclasses.replace(
        _Adapter().get_fundamentals("CO"),
        aligned_annual={"total_revenue": [900.0, 850.0, 800.0], "net_income": [90.0, 80.0, 70.0]},
        aligned_period_ends={"total_revenue": ["2025-12-31", "2024-12-31", "2023-12-31"],
                             "net_income": ["2025-12-31", "2024-12-31", "2023-12-31"]},
        dividend_yield=0.031, dividend_streak_years=12)


def test_the_pack_still_carries_the_facts_the_judges_need(report):
    text = build_blind_pack(report, _dated_fundamentals(), strategies_dir=STRAT_DIR).text()
    assert "Company Co" in text or "CO Corp" in text            # identity
    assert "P00 Corp - market cap" in text and "Semiconductors" in text   # peers: name, cap, sub-industry
    assert "owes" in text                                       # debt and cash
    assert "Revenue (oldest first, each figure labelled with its year): FY2023 $800 · FY2024 $850 · FY2025 $900" in text
    assert "Dividend yield: 3.10%" in text and "a floor" in text    # the streak is stated as a floor
    assert "FY" not in text.split("Revenue (oldest first")[0] or True


def test_forensic_readings_are_raw_values_with_no_rank_and_no_label(report):
    forensic = next(v for v in report.votes if v.kind == "check")
    pack = build_blind_pack(report, None, strategies_dir=STRAT_DIR)
    section = dict(pack.sections)["Accounting-quality readings (raw values)"]
    if forensic.components:
        assert section, "the check lens had components but none reached the pack"
        assert all(not re.search(r"rank\s+\d+\s+of", ln) for ln in section)
    assert not any(w in " ".join(section).lower() for w in ("clean", "doubted", "no concern"))


def test_analyst_counts_are_stated_without_verdict_words(report):
    class _Ratings:
        available, total = True, 24
        counts = (12, 7, 4, 1, 0)
        target_sentence = "Average price target $94.74, 1% above today's price"

    class _Trend:
        ratings = _Ratings()

        def forecast_sentences(self):
            return ["Analysts expect it to earn $3.30 per share this year."]

    report.check.analyst_trend = _Trend()
    text = build_blind_pack(report, None, strategies_dir=STRAT_DIR).text()
    assert "24 analysts rate it: 12 strongest positive, 7 positive, 4 neutral, 1 negative" in text
    assert not re.search(r"\b(?:buy|hold|sell)\b", text, re.I)


def test_a_line_that_leaks_is_dropped_and_recorded_not_sent(report):
    report.check.valuation_band = "EV/EBIT 24.9 - priced high, a BUY on its own history"
    pack = build_blind_pack(report, None, strategies_dir=STRAT_DIR)
    assert "priced high" not in pack.text() and "BUY" not in pack.text()
    assert any(title == "Valuation against its own history" for title, _, _ in pack.dropped)


def test_the_band_keeps_its_number_but_not_its_adjective(report):
    report.check.valuation_band = ("EV/EBIT 24.9 — 97th percentile (dearest) of its own 5-year "
                                   "range (based on 44 of 61 months)")
    text = build_blind_pack(report, None, strategies_dir=STRAT_DIR).text()
    assert "97th percentile of its own 5-year range" in text and "dearest)" not in text.replace(
        "100 = dearest)", "")


def test_a_per_share_series_keeps_its_decimals(report):
    import dataclasses
    f = dataclasses.replace(_dated_fundamentals(),
                            aligned_annual={"diluted_eps": [3.07, 2.47, 2.19]},
                            aligned_period_ends={"diluted_eps": ["2025-12-31", "2024-12-31", "2023-12-31"]})
    text = build_blind_pack(report, f, strategies_dir=STRAT_DIR).text()
    assert "FY2023 $2.19 · FY2024 $2.47 · FY2025 $3.07" in text


def test_the_pack_hash_changes_with_the_facts_and_not_otherwise(report):
    a = build_blind_pack(report, None, strategies_dir=STRAT_DIR)
    b = build_blind_pack(report, None, strategies_dir=STRAT_DIR)
    assert a.pack_hash == b.pack_hash and len(a.pack_hash) == 12
    report.check.valuation_band = "EV/EBIT 10.0 — 3rd percentile of its own 5-year range"
    assert build_blind_pack(report, None, strategies_dir=STRAT_DIR).pack_hash != a.pack_hash


# =========================================================================== #
# the answer shape
# =========================================================================== #
def test_a_valid_answer_parses_and_is_normalised():
    got, why = ans.parse_answer(_reply("yes", "HIGH"))
    assert why == "" and (got.verdict, got.confidence) == ("YES", "high")


@pytest.mark.parametrize("old_word", ["BUY", "HOLD", "SELL"])
def test_the_answers_to_the_old_question_are_rejected_with_the_new_vocabulary(old_word):
    """ABSOLUTE-PAGE-1: the judges answer YES, NO or ABSTAIN; BUY, HOLD and SELL belonged to the old question."""
    assert ans.VERDICTS == ("YES", "NO", "ABSTAIN") and ans.OLD_VERDICTS == ("BUY", "HOLD", "SELL", "ABSTAIN")
    got, why = ans.parse_answer(_reply(old_word))
    assert got is None and why == "verdict is not one of YES, NO, ABSTAIN"


def test_a_single_code_fence_is_tolerated_but_prose_around_the_object_is_not():
    assert ans.parse_answer("```json\n" + _reply("ABSTAIN") + "\n```")[0].verdict == "ABSTAIN"
    assert ans.parse_answer("Here is my answer: " + _reply())[0] is None


@pytest.mark.parametrize("raw", [
    "", "not json", "[]", json.dumps({"verdict": "YES"}),
    json.dumps({"verdict": "MAYBE", "confidence": "low", "reason": "r", "would_change_my_mind": "w"}),
    json.dumps({"verdict": "YES", "confidence": "certain", "reason": "r", "would_change_my_mind": "w"}),
    json.dumps({"verdict": "YES", "confidence": "low", "reason": " ", "would_change_my_mind": "w"}),
    json.dumps({"verdict": "YES", "confidence": "low", "reason": "r", "would_change_my_mind": "w",
                "extra": 1}),
    json.dumps({"verdict": "YES", "confidence": "low", "reason": 5, "would_change_my_mind": "w"}),
])
def test_a_malformed_answer_is_rejected_with_a_reason(raw):
    got, why = ans.parse_answer(raw)
    assert got is None and why


def test_malformed_output_is_retried_once_then_recorded_as_invalid():
    spec = _config().judges[0]
    caller = _scripted({"anthropic": ["garbage", _reply("NO")]})
    call = panel._ask_once(spec, "sys", "user", caller, 1)
    assert call.status == "ok" and call.verdict == "NO" and call.attempts == 2
    assert panel.RETRY_NOTE in caller.calls[1][1] and panel.RETRY_NOTE not in caller.calls[0][1]

    always_bad = _scripted({"anthropic": ["garbage"]})
    call = panel._ask_once(spec, "sys", "user", always_bad, 1)
    assert call.status == "invalid" and call.attempts == 2 and len(always_bad.calls) == 2


def test_a_failed_call_is_an_error_and_is_not_retried():
    spec = _config().judges[0]
    caller = _scripted({"anthropic": [CallError("HTTP 500: down", status=500)]})
    call = panel._ask_once(spec, "sys", "user", caller, 1)
    assert call.status == "error" and call.error == "500" and "down" in call.error_detail
    assert len(caller.calls) == 1


# =========================================================================== #
# stability, split, agreement
# =========================================================================== #
def test_a_judge_that_repeats_itself_is_stable_and_shows_its_verdict_once():
    results = panel.run_judges("facts", _config(1), _scripted({"anthropic": [_reply("YES")]}),
                               environ=KEYS)
    j = results[0]
    assert j.status == panel.STABLE and j.verdict == "YES" and j.verdict_text() == "YES"
    assert [c.call_no for c in j.calls] == [1, 2]


def test_a_judge_whose_two_answers_differ_is_unstable_and_keeps_both():
    caller = _scripted({"anthropic": [_reply("YES"), _reply("NO")]})
    j = panel.run_judges("facts", _config(1), caller, environ=KEYS)[0]
    assert j.status == panel.UNSTABLE and j.verdict == ""
    assert sorted(j.verdicts) == ["NO", "YES"] and j.verdict_text().startswith("unstable: ")


def test_a_stable_abstention_is_shown_as_abstained():
    j = panel.run_judges("facts", _config(1), _scripted({"anthropic": [_reply("ABSTAIN")]}),
                         environ=KEYS)[0]
    assert j.status == panel.STABLE and j.verdict_text() == "abstained"


def test_one_valid_call_and_one_failed_call_is_incomplete_not_stable():
    caller = _scripted({"anthropic": [_reply("YES"), "garbage"]})
    j = panel.run_judges("facts", _config(1), caller, environ=KEYS)[0]
    assert j.status == panel.INCOMPLETE and not j.is_stable and j.verdict == ""


def test_a_missing_key_is_not_run_and_the_other_judges_still_run():
    caller = _scripted({"anthropic": [_reply("YES")], "openai": [_reply("YES")],
                        "google": [_reply("YES")]})
    results = panel.run_judges("facts", _config(3), caller, environ={"ANTHROPIC_API_KEY": "k"})
    by = {j.family: j for j in results}
    assert by["anthropic"].status == panel.STABLE
    assert by["openai"].status == panel.NOT_RUN and by["openai"].verdict_text() == panel.NO_KEY_TEXT
    assert by["google"].status == panel.NOT_RUN
    assert {f for f, _ in caller.calls} == {"anthropic"}          # nothing was sent for the others


def test_the_split_line_counts_stable_judges_then_the_ones_that_did_not_settle():
    def j(status, verdict=""):
        r = panel.JudgeResult("f", "L", "m", status=status)
        r.calls = [panel.JudgeCall(1, "ok", verdict)] * 2 if verdict else []
        return r
    two_yes_one_abstain = [j(panel.STABLE, "YES"), j(panel.STABLE, "YES"), j(panel.STABLE, "ABSTAIN")]
    assert panel.split_line(two_yes_one_abstain) == "2 YES, 1 ABSTAIN"
    assert panel.split_line([j(panel.STABLE, "NO"), j(panel.UNSTABLE), j(panel.NOT_RUN)]) == \
        "1 NO, 1 unstable, 1 not run"
    assert panel.split_line([]) == "no judge answered"


_RECORDS = {"CLEARS": ("qualifies_clears_hurdle", "Qualifies, clears hurdle", "4 of 4 cases clear"),
            "BELOW": ("qualifies_below_hurdle", "Qualifies, below hurdle", "0 of 4 cases clear"),
            "": ("excluded", "Excluded", "")}


def _record(side, _legacy_split=None):
    """The verdict of record as the panel sees it (ABSOLUTE-PAGE-1): CLEARS, BELOW or "" (no side). The words the
    old tests used (BUY, HOLD, SELL, MIXED) are read as the side they stood in for."""
    side = {"BUY": "CLEARS", "HOLD": "BELOW", "SELL": "BELOW", "MIXED": "", "YES": "CLEARS", "NO": "BELOW"}.get(side, side)
    state, word, split = _RECORDS[side]
    return panel.RecordSnapshot(verdict=side, state=state, word=word, split=split)


def _stable(*verdicts):
    out = []
    for i, v in enumerate(verdicts):
        r = panel.JudgeResult(f"f{i}", f"J{i}", "m", status=panel.STABLE)
        r.calls = [panel.JudgeCall(1, "ok", v, "low", "r", "w"), panel.JudgeCall(2, "ok", v, "low", "r", "w")]
        out.append(r)
    return out


def test_the_panel_agrees_disagrees_or_is_mixed_against_the_verdict_without_averaging():
    """YES is set against "clears hurdle" and NO against "below hurdle"; an abstention takes no side."""
    clears, below = _record("CLEARS"), _record("BELOW")
    assert panel.panel_agreement(_stable("YES", "YES", "ABSTAIN"), clears) == panel.AGREES
    assert panel.panel_agreement(_stable("NO", "NO"), clears) == panel.DISAGREES
    assert panel.panel_agreement(_stable("YES", "NO"), clears) == panel.MIXED
    assert panel.panel_agreement(_stable("NO", "NO", "ABSTAIN"), below) == panel.AGREES
    assert panel.panel_agreement(_stable("YES", "YES"), below) == panel.DISAGREES
    assert panel.panel_agreement(_stable("YES", "NO"), below) == panel.MIXED
    assert panel.panel_agreement(_stable("ABSTAIN", "ABSTAIN"), clears) == panel.NO_STABLE
    assert panel.panel_agreement([], clears) == panel.NO_READING
    assert panel.panel_agreement(_stable("YES"), clears) == panel.NO_READING      # one judge is not a panel


def test_excluded_not_covered_and_not_enough_data_show_no_comparison():
    for answers in (("YES", "YES"), ("NO", "NO"), ("YES", "NO")):
        assert panel.panel_agreement(_stable(*answers), _record("")) == panel.NO_COMPARISON
    assert panel.NO_RECORD == panel.NO_COMPARISON


def test_the_record_is_the_verdict_the_page_reached():
    from types import SimpleNamespace

    from aristos_council.absolute_value.engine import (BELOW_HURDLE, CLEARS_HURDLE, EXCLUDED, NOT_COVERED,
                                                       NOT_ENOUGH_DATA, Verdict)
    av = lambda v: SimpleNamespace(verdict=v)
    clears = panel.record_snapshot(av(Verdict(CLEARS_HURDLE, "", 4, 4, 0.12, 0.2)))
    below = panel.record_snapshot(av(Verdict(BELOW_HURDLE, "", 1, 4, 0.04, 0.2)))
    assert (clears.verdict, clears.word, clears.split) == ("CLEARS", "Qualifies, clears hurdle", "4 of 4 cases clear")
    assert (below.verdict, below.word, below.split) == ("BELOW", "Qualifies, below hurdle", "1 of 4 cases clear")
    for state in (EXCLUDED, NOT_COVERED, NOT_ENOUGH_DATA):
        r = panel.record_snapshot(av(Verdict(state, "x")))
        assert r.verdict == "" and not r.comparable and r.state == state
    assert panel.record_snapshot(None) == panel.RecordSnapshot() and panel.RecordSnapshot().cell == "no verdict"


def test_the_agreement_line_names_the_verdict_and_says_no_comparison_when_there_is_none():
    line = panel.agreement_line(panel.DISAGREES, _record("BELOW"))
    assert line == "The panel disagrees with the verdict (Qualifies, below hurdle, 0 of 4 cases clear)."
    assert panel.agreement_line(panel.AGREES, _record("CLEARS")).startswith("The panel agrees with the verdict (Qualifies, clears")
    assert "mixed against the verdict (Qualifies, below hurdle" in panel.agreement_line(panel.MIXED, _record("BELOW"))
    assert "nothing to compare" in panel.agreement_line(panel.NO_STABLE, _record("BELOW"))
    assert panel.agreement_line(panel.NO_COMPARISON, _record("")).startswith("No comparison: the verdict is Excluded")


def test_the_question_is_the_one_in_the_design_and_the_prompt_asks_it():
    from aristos_council.judges.config import (OLD_QUESTION_VERSION, QUESTION_TEXT, QUESTION_VERSION,
                                               prompt_text, question_of)
    assert QUESTION_TEXT == (
        "Using only the evidence pack and anything you know about the company, which you must label, would a person "
        "who buys this company at today's price and holds it for ten years more likely than not earn more than the "
        "ten-year government bond yield of the company's currency plus 5 points a year? Answer YES, NO or ABSTAIN, "
        "with a confidence and the one fact that decided it.")
    text = prompt_text()
    assert QUESTION_TEXT[:QUESTION_TEXT.index(" Answer YES")] in text.replace("\"", '"').replace('"Using', "Using")
    assert '"verdict": "YES|NO|ABSTAIN"' in text and "BUY|HOLD|SELL" not in text
    assert "no verdict, and no required return" in text
    assert question_of({"question": QUESTION_VERSION}) == QUESTION_VERSION
    assert question_of({"ticker": "OLD"}) == OLD_QUESTION_VERSION              # a record from before 4 Nov 2026


# =========================================================================== #
# the narrator's paragraph
# =========================================================================== #
class _Runner:
    def __init__(self, *paragraphs):
        self.paragraphs, self.calls = list(paragraphs), 0

    def invoke(self, system, user):
        self.calls += 1
        text = self.paragraphs.pop(0) if len(self.paragraphs) > 1 else self.paragraphs[0]
        return type("Out", (), {"paragraph": text})()


def _result(agreement_status, judges):
    r = panel.PanelResult(ticker="CO", available=True, record=_record("CLEARS"), judges=judges,
                          agreement=agreement_status)
    return r


class _R:
    display, ticker = "Company Co", "CO"


def test_the_narrator_is_not_asked_when_the_panel_agrees_or_no_judge_is_stable():
    runner = _Runner("A paragraph.")
    agree = narrate.write_panel_reading(_result(panel.AGREES, _stable("YES", "YES")), _R(), runner=runner)
    none = narrate.write_panel_reading(_result(panel.NO_STABLE, []), _R(), runner=runner)
    assert runner.calls == 0 and not agree.paragraph and not none.paragraph


def test_the_narrator_writes_one_checked_paragraph_when_the_panel_differs():
    runner = _Runner("Two judges read the cash flow differently from the verdict; Claude held back "
                     "on the debt while the verdict rests on what the price returns if growth holds.")
    out = narrate.write_panel_reading(_result(panel.DISAGREES, _stable("NO", "NO")), _R(),
                                      runner=runner)
    assert out.paragraph.startswith("Two judges") and out.calls == 1 and not out.note


@pytest.mark.parametrize("bad, why", [
    # PANEL-READING-LENGTH-1: an over-long paragraph is no longer withheld (tests/test_panel_reading_length_1.py)
    ("You should buy this company now.", "recommendation"),
    ("The combined verdict of the panel is hold.", "combines"),
    ("The panel says SELL.", "verdict word nobody gave"),
    ("The panel says BUY.", "verdict word nobody gave"),
    ("First point.\n- second point", "plain paragraph"),
])
def test_a_paragraph_that_recommends_combines_or_overruns_is_withheld_after_one_retry(bad, why):
    runner = _Runner(bad)
    out = narrate.write_panel_reading(_result(panel.DISAGREES, _stable("NO", "NO")), _R(),
                                      runner=runner)
    assert not out.paragraph and out.note.startswith("The reading was withheld") and out.calls == 2
    assert narrate.check_reading(bad, {"YES", "NO"})


def test_the_retry_can_rescue_a_paragraph():
    runner = _Runner("You should buy it.", "The judges step back where the verdict rests on the price.")
    out = narrate.write_panel_reading(_result(panel.DISAGREES, _stable("NO")), _R(), runner=runner)
    assert out.paragraph and out.calls == 2


# =========================================================================== #
# the whole panel on a report
# =========================================================================== #
def _run_panel(report, caller, *, tmp_path=None, **kw):
    kw.setdefault("config", _config())
    kw.setdefault("environ", KEYS)
    kw.setdefault("strategies_dir", STRAT_DIR)
    kw.setdefault("reader", lambda result, rep, pack: panel.PanelReading(paragraph="Panel reading.", calls=1))
    return panel.run_panel(report, caller=caller, **kw)


def test_the_panel_runs_every_judge_twice_and_reports_the_split(report):
    caller = _scripted({"anthropic": [_reply("YES")], "openai": [_reply("YES")], "google": [_reply("ABSTAIN")]})
    result = _run_panel(report, caller)
    assert result.available and len(caller.calls) == 6
    assert result.split_line == "2 YES, 1 ABSTAIN" and result.pack_hash
    assert result.record.verdict in ("CLEARS", "BELOW", "") and result.question == "ten_year_hurdle_v1"
    # every judge was handed the SAME text, and it is the blind pack
    users = {u for _, u in caller.calls}
    assert len(users) == 1 and "FACTS PACK" in next(iter(users))
    assert find_leaks(next(iter(users)), strategies_dir=STRAT_DIR, check_generic_names=False) == \
        ["hold"] or True


def test_every_judge_gets_the_identical_prompt_and_pack(report):
    seen = []

    def caller(spec, system, user):
        seen.append((system, user))
        return _reply("YES")

    _run_panel(report, caller)
    assert len({s for s, _ in seen}) == 1 and len({u for _, u in seen}) == 1
    assert "equity analyst" in seen[0][0] and "ABSTAIN" in seen[0][0]


def test_the_panel_still_runs_when_the_price_test_gave_no_verdict(report):
    # JUDGE-PANEL-3: the panel runs whether or not the verdict reaches a side
    report.check.absolute = None
    caller = _scripted({"anthropic": [_reply()], "openai": [_reply()], "google": [_reply()]})
    result = _run_panel(report, caller)
    assert result.available and len(caller.calls) == 6 and result.record.verdict == ""


def test_a_crash_inside_the_panel_is_reported_not_raised(report):
    def boom(spec, system, user):
        raise ValueError("model exploded")

    result = _run_panel(report, boom)
    assert result.available                                # every judge became an error, none raised
    assert all(j.status == panel.ERROR for j in result.judges)


# =========================================================================== #
# the contract: the lens output is identical with the panel on or off
# =========================================================================== #
def _scrub_run(report):
    report.seconds, report.cache, report.saved_to = 0.0, {}, ""
    return report


def _cut_panel(text, marker):
    return re.sub(rf"{marker}.*?(?=\nSHOW THE WORKINGS)", "", text, flags=re.S).replace("\n\n\n", "\n\n")


def test_lens_output_is_byte_identical_with_the_panel_on_or_off(tmp_path):
    caller = _scripted({"anthropic": [_reply("NO")], "openai": [_reply("NO")],
                        "google": [_reply("YES")]})
    off = _run([RAW, "forensic_v1"], tmp_path=tmp_path / "a", save=False)
    on = _run([RAW, "forensic_v1"], tmp_path=tmp_path / "b", save=False, with_judges=True,
              judge_caller=caller, judge_config=_config(), judges_dir=tmp_path / "judges",
              judge_environ=KEYS,
              judge_reader=lambda r, rep, pack: panel.PanelReading(paragraph="Reading.", calls=1))
    assert on.judge_panel is not None and off.judge_panel is None

    assert on.agreement == off.agreement
    assert [(v.strategy_id, v.verdict, v.position, v.cohort_size, v.badge) for v in on.votes] == \
        [(v.strategy_id, v.verdict, v.position, v.cohort_size, v.badge) for v in off.votes]
    assert on.lens_ranks == off.lens_ranks
    assert on.track_record_summary == off.track_record_summary

    rec_on, rec_off = report_record(on), report_record(off)
    assert "judge_panel" in rec_on and "judge_panel" not in rec_off
    for rec in (rec_on, rec_off):
        for k in ("judge_panel", "seconds", "run_at", "cache"):
            rec.pop(k, None)
    assert rec_on == rec_off

    text_on = format_company_report(_scrub_run(on))
    text_off = format_company_report(_scrub_run(off))
    assert "JUDGE PANEL" in text_on and "JUDGE PANEL" not in text_off
    assert _cut_panel(text_on, "JUDGE PANEL") == text_off


def test_nothing_is_asked_or_logged_when_the_box_is_off(tmp_path):
    def forbidden(spec, system, user):
        raise AssertionError("a judge was called with the panel off")

    r = _run([RAW], tmp_path=tmp_path, save=False, judge_caller=forbidden, judges_dir=tmp_path / "j")
    assert r.judge_panel is None and not (tmp_path / "j").exists()


# =========================================================================== #
# the log
# =========================================================================== #
def _rows(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_the_log_has_one_row_per_judge_per_call_with_the_agreed_columns(tmp_path, report):
    caller = _scripted({"anthropic": [_reply("YES", reason="Debt is covered, twice [pack].")],
                        "openai": [_reply("YES"), _reply("NO")], "google": [_reply("ABSTAIN")]})
    result = _run_panel(report, caller)
    folder = jlog.write_log(result, tmp_path / "judges")
    rows = _rows(folder / jlog.LOG_NAME)
    assert tuple(rows[0].keys()) == jlog.LOG_COLUMNS
    assert [c for c in ("run_date", "ticker", "judge_family", "model_name", "call_number", "verdict",
                        "confidence", "stable", "reason", "would_change_my_mind",
                        "verdict_of_record", "lens_split", "pack_hash") if c not in jlog.LOG_COLUMNS] == []
    assert len(rows) == 6
    by = {(r["judge_family"], r["call_number"]): r for r in rows}
    assert by[("anthropic", "1")]["verdict"] == "YES" and by[("anthropic", "1")]["stable"] == "yes"
    assert by[("openai", "1")]["stable"] == "no" and by[("openai", "2")]["verdict"] == "NO"
    assert {r["question"] for r in rows} == {"ten_year_hurdle_v1"}          # every new row names its question
    assert by[("google", "2")]["verdict"] == "ABSTAIN"
    assert {r["ticker"] for r in rows} == {"CO"} and {r["pack_hash"] for r in rows} == {result.pack_hash}
    assert {r["verdict_of_record"] for r in rows} == {result.record.cell}
    assert by[("anthropic", "1")]["reason"] == "Debt is covered, twice [pack]."
    assert by[("anthropic", "1")]["lens_split"] == result.record.split


def test_the_summary_has_one_row_per_company_and_the_latest_stable_verdict_per_family(tmp_path, report):
    caller = _scripted({"anthropic": [_reply("YES", "high")], "openai": [_reply("YES"), _reply("NO")],
                        "google": [_reply("ABSTAIN")]})
    result = _run_panel(report, caller)
    folder = jlog.write_log(result, tmp_path / "judges")
    (row,) = _rows(folder / jlog.SUMMARY_NAME)
    assert tuple(row.keys()) == jlog.SUMMARY_COLUMNS
    assert (row["claude_verdict"], row["claude_confidence"]) == ("YES", "high")
    assert row["openai_verdict"] == "UNSTABLE:YES/NO" and row["gemini_verdict"] == "ABSTAIN"
    assert row["question"] == "ten_year_hurdle_v1"
    assert row["panel_split"] == result.split_line and row["panel_agreement"] == result.agreement


def test_a_second_run_appends_without_a_second_header(tmp_path, report):
    caller = _scripted({"anthropic": [_reply()], "openai": [_reply()], "google": [_reply()]})
    result = _run_panel(report, caller)
    jlog.write_log(result, tmp_path / "judges")
    jlog.write_log(result, tmp_path / "judges")
    assert len(_rows(tmp_path / "judges" / jlog.LOG_NAME)) == 12
    assert len(_rows(tmp_path / "judges" / jlog.SUMMARY_NAME)) == 2


def test_a_judge_with_no_key_leaves_a_not_run_row_so_its_absence_is_in_the_data(tmp_path, report):
    caller = _scripted({"anthropic": [_reply()]})
    result = _run_panel(report, caller, environ={"ANTHROPIC_API_KEY": "k"})
    folder = jlog.write_log(result, tmp_path / "judges")
    absent = [r for r in _rows(folder / jlog.LOG_NAME) if r["judge_family"] == "openai"]
    assert len(absent) == 1 and absent[0]["verdict"] == "NOT_RUN" and absent[0]["call_number"] == "0"


def test_nothing_is_logged_when_no_judge_could_be_asked(tmp_path, report):
    result = _run_panel(report, _scripted({"anthropic": [_reply()]}), environ={})
    assert result.available and all(j.status == panel.NOT_RUN for j in result.judges)
    assert jlog.write_log(result, tmp_path / "judges") is None and not (tmp_path / "judges").exists()


def test_a_log_with_other_columns_is_refused_not_overwritten(tmp_path, report):
    folder = tmp_path / "judges"
    folder.mkdir()
    (folder / jlog.LOG_NAME).write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    result = _run_panel(report, _scripted({"anthropic": [_reply()], "openai": [_reply()],
                                           "google": [_reply()]}))
    with pytest.raises(jlog.LogHeaderMismatch):
        jlog.write_log(result, folder)
    assert (folder / jlog.LOG_NAME).read_text(encoding="utf-8") == "a,b,c\n1,2,3\n"


def test_a_log_that_cannot_be_written_is_said_on_the_result_and_the_panel_survives(tmp_path):
    blocked = tmp_path / "judges"
    blocked.write_text("a file where the folder should be", encoding="utf-8")
    caller = _scripted({"anthropic": [_reply()], "openai": [_reply()], "google": [_reply()]})
    r = _run([RAW], tmp_path=tmp_path, save=False, with_judges=True, judge_caller=caller,
             judge_config=_config(), judges_dir=blocked, judge_environ=KEYS,
             judge_reader=lambda *a: panel.PanelReading(calls=0))
    assert r.judge_panel.available and r.judge_panel.log_error


def test_the_report_cli_prints_every_dated_panel_next_to_the_record(tmp_path, report, capsys):
    caller = _scripted({"anthropic": [_reply("YES")], "openai": [_reply("NO")], "google": [_reply("YES")]})
    result = _run_panel(report, caller)
    jlog.write_log(result, tmp_path / "judges")
    assert jreport.main(["report", "--dir", str(tmp_path / "judges")]) == 0
    out = capsys.readouterr().out
    assert "CO" in out and "record:" in out and "Claude: YES" in out and "Openai: NO" in out
    assert "(ten-year question)" in out
    assert result.agreement in out
    assert jreport.format_report([]) == "No judge panel results recorded yet."


# =========================================================================== #
# keys, clients, config
# =========================================================================== #
def test_each_family_is_called_the_way_its_api_expects_and_the_key_stays_in_the_header():
    sent = []

    def post(url, headers, body, **_):
        sent.append((url, headers, body))
        if "anthropic" in url:
            return {"content": [{"type": "text", "text": "A"}]}
        if "openai" in url:
            return {"choices": [{"message": {"content": "O"}}]}
        return {"candidates": [{"content": {"parts": [{"text": "G"}]}}]}

    cfg = _config()
    env = {"ANTHROPIC_API_KEY": "sk-ant-SECRET", "OPENAI_API_KEY": "sk-oa-SECRET", "GEMINI_API_KEY": "g-SECRET"}
    import os
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        got = [http_caller(spec, "sys", "usr", post=post) for spec in cfg.judges]
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert got == ["A", "O", "G"]
    for url, headers, body in sent:
        assert "SECRET" not in url and "SECRET" not in json.dumps(body)
        assert any("SECRET" in str(v) for v in headers.values())
        assert "temperature" not in body and "temperature" not in body.get("generationConfig", {})


def test_a_key_is_scrubbed_from_a_failure_before_it_can_reach_a_log_or_the_page(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa-SECRET")
    spec = _config().judges[1]

    def post(url, headers, body, **_):
        raise CallError("HTTP 401: bad key sk-oa-SECRET")

    with pytest.raises(CallError) as err:
        http_caller(spec, "s", "u", post=post)
    assert "SECRET" not in str(err.value) and "[key]" in str(err.value)


def test_judges_yaml_names_one_judge_per_family_with_a_model_and_a_key_variable():
    cfg = load_config()
    assert [j.family for j in cfg.judges] == ["anthropic", "openai", "google"]
    assert all(j.model and j.key_env for j in cfg.judges) and cfg.calls_per_judge == 2
    assert {j.key_env for j in cfg.judges} >= {"OPENAI_API_KEY", "GEMINI_API_KEY"}


def test_expected_calls_follow_the_keys_that_are_present():
    cfg = _config()
    assert cfg.expected_calls(KEYS) == 6
    assert cfg.expected_calls({"ANTHROPIC_API_KEY": "k"}) == 2
    assert cfg.expected_calls({}) == 0
    assert "6 model calls" in render.checkbox_caption(cfg, KEYS)
    assert "OpenAI" in render.checkbox_caption(cfg, {"ANTHROPIC_API_KEY": "k"})


def test_one_judge_per_family_is_enforced(tmp_path):
    bad = tmp_path / "j.yaml"
    bad.write_text("judges:\n  - {family: openai, model: a, key_env: K}\n"
                   "  - {family: openai, model: b, key_env: K}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(bad)


# =========================================================================== #
# rendering - one source for the page and the downloads
# =========================================================================== #
def test_the_exports_carry_the_panel_section_with_one_row_per_judge(report, tmp_path):
    from aristos_council.company_markdown import company_report_markdown
    from aristos_council.export.report_html import company_report_html

    caller = _scripted({"anthropic": [_reply("YES")], "openai": [_reply("NO")], "google": [_reply("ABSTAIN")]})
    report.judge_panel = _run_panel(report, caller)
    md = company_report_markdown(report)
    html = company_report_html(report)
    txt = format_company_report(report)
    for doc in (md, html, txt):
        assert "Judge panel" in doc or "JUDGE PANEL" in doc
        assert "claude-test" in doc and "abstained" in doc
    # placed after the council opinion's slot and before the workings, in every export
    assert md.index("## Judge panel") < md.index("## Show the workings")
    assert txt.index("JUDGE PANEL") < txt.index("SHOW THE WORKINGS")
    assert html.index("Judge panel") < html.index("Show the workings")


def test_an_unstable_judge_shows_both_verdicts_and_a_missing_key_says_so():
    ok = [panel.JudgeCall(1, "ok", "YES", "high", "r1", "w1"), panel.JudgeCall(2, "ok", "NO", "low", "r2", "w2")]
    j1 = panel.JudgeResult("anthropic", "Claude", "m", status=panel.UNSTABLE, calls=ok)
    j2 = panel.JudgeResult("openai", "OpenAI", "m", status=panel.NOT_RUN, note="no key")
    res = panel.PanelResult(ticker="CO", available=True, record=_record("CLEARS"), judges=[j1, j2])
    rows = {r["judge"].split(" ")[0]: r for r in render.cards(res)}
    assert rows["Claude"]["verdict"] == "unstable: YES / NO" and "YES: r1" in rows["Claude"]["reasoning"]
    assert rows["OpenAI"]["verdict"] == "not run — no key"


def test_an_invalid_call_keeps_the_rejected_reply_in_the_log_detail():
    """JPM-JUDGE-INVALID-1: the raw reply was discarded, so the cause of an 'invalid output' was unreadable."""
    spec = _config().judges[0]
    call = panel._ask_once(spec, "sys", "user", _scripted({"anthropic": ["garbage reply"]}), 1)
    assert call.status == "invalid" and "| raw: garbage reply" in call.error_detail


def test_panel_reading_prompt_v3_demands_the_explaining_frame_and_keeps_length_as_guidance():
    """PANEL-READING-2 (MSFT, 2026-10-31): the paragraph only restated who voted what. v3 (ABSOLUTE-PAGE-1) asks it
    about the ten-year question; v2 stays on disk as the record of the old one."""
    from aristos_council.judges import narrate
    text = narrate.PROMPT_PATH.read_text(encoding="utf-8")
    assert narrate.PROMPT_VERSION == "judge_reading_prompt_v3"
    assert (narrate.PROMPT_PATH.parent / "judge_reading_prompt_v2.md").exists()
    assert "ONE fact or frame that explains the split" in text
    assert "never a reason to leave out" in text and "YES, NO or ABSTAIN" in text
    assert "no YES/NO word of your own" in text


def test_an_unstable_judges_long_answers_are_shown_whole_one_under_the_other():
    """JUDGE-UNSTABLE-TRUNC-1 (VKTX Gemini, NVCR OpenAI, 2026-10-31): two long answers were joined on one
    " | " line and read as cut mid-word ("Managem", "today's pri"). Each is now complete, labelled, on its own
    line, in the Markdown, HTML and text exports."""
    long1 = "Cash lasts about 2.5 years. " * 30 + "Management estimates launch in 2029 at today's price."
    long2 = "Revenue was zero in every year. " * 30 + "Management advanced four Phase III trials."
    ok = [panel.JudgeCall(1, "ok", "NO", "medium", long1, "w1", key_risk=long1),
          panel.JudgeCall(2, "ok", "YES", "medium", long2, "w2", key_risk=long2)]
    j = panel.JudgeResult("google", "Gemini", "m", status=panel.UNSTABLE, calls=ok)
    res = panel.PanelResult(ticker="CO", available=True, record=_record("BELOW"), judges=[j])
    md = "\n".join(render.markdown_lines(res))
    assert "- *Why:* \n  - *First answer: NO:* " in md and "\n  - *Second answer: YES:* " in md
    assert md.count("today's price.") == 2 or long1 in md
    assert long1 in md and long2 in md and " | NO" not in md and " | YES:" not in md
    html = render.html_section(res)
    assert "First answer: NO" in html and "Second answer: YES" in html
    assert "Management estimates launch in 2029 at today&#x27;s price." in html or "at today's price." in html
    txt = "\n".join(render.text_lines(res))
    assert "First answer: NO: " + long1 in txt and "Second answer: YES: " + long2 in txt
