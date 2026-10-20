"""JUDGE-PANEL-2 - error labels, judges-not-calls, and the two-judge rule (all model calls mocked)."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from aristos_council.judges import log as jlog
from aristos_council.judges import panel, render
from aristos_council.judges.clients import CallError, error_label
from tests.test_judge_panel import (_config, _record, _reply, _rows, _run_panel, _scripted,
                                    _stable, report)  # noqa: F401  (report is a fixture)

pytestmark = pytest.mark.min_group(3)


@pytest.mark.parametrize("exc, label", [
    (CallError('HTTP 429: {"error": {"type": "insufficient_quota"}}', status=429), "no credits"),
    (CallError("HTTP 429: You have no credits remaining", status=429), "no credits"),
    (CallError("HTTP 429: slow down", status=429), "429"),
    (CallError("HTTP 404: models/x is no longer available", status=404), "model not available"),
    (CallError("TimeoutError: timed out", timeout=True), "timeout"),
    (CallError("HTTP 503: high demand", status=503), "503"),
    (CallError("URLError: dns"), "failed"),
])
def test_a_failed_call_is_labelled_short_and_the_raw_body_is_kept_apart(exc, label):
    assert error_label(exc) == label
    spec = _config().judges[0]
    call = panel._ask_once(spec, "s", "u", _scripted({"anthropic": [exc]}), 1)
    assert call.error == label and call.error_detail == str(exc)


def test_an_error_row_shows_a_short_label_in_the_verdict_column_and_nothing_else():
    err = [panel.JudgeCall(1, "error", error="no credits", error_detail='{"secret": "raw body"}'),
           panel.JudgeCall(2, "error", error="no credits", error_detail='{"secret": "raw body"}')]
    j = panel.settle(_config().judges[1], err)
    assert j.status == panel.ERROR and j.verdict_text() == "error: no credits"
    res = panel.PanelResult(ticker="CO", available=True, record=_record("BUY"), judges=[j],
                            agreement=panel.NO_READING)
    row = render.cards(res)[0]
    assert row["verdict"] == "error: no credits"
    assert not any(row[k] for k in ("confidence", "driving_fact", "reasoning", "key_risk",
                                    "unexplained", "would_change_my_mind"))
    shown = "\n".join(render.text_lines(res) + render.markdown_lines(res))
    assert "raw body" not in shown and "secret" not in shown
    bad = panel.settle(_config().judges[1], [panel.JudgeCall(1, "invalid", error="invalid output")] * 2)
    assert bad.verdict_text() == "error: invalid output"


def test_the_raw_error_body_goes_to_the_log_in_error_detail(tmp_path, report):
    caller = _scripted({"anthropic": [_reply("BUY")],
                        "openai": [CallError('HTTP 429: {"type": "insufficient_quota"}', status=429)],
                        "google": [_reply("BUY")]})
    result = _run_panel(report, caller)
    folder = jlog.write_log(result, tmp_path / "judges")
    rows = _rows(folder / jlog.LOG_NAME)
    assert "error_detail" in jlog.LOG_COLUMNS
    bad = [r for r in rows if r["judge_family"] == "openai"]
    assert len(bad) == 2 and all("insufficient_quota" in r["error_detail"] for r in bad)
    assert all(r["verdict"] == "ERROR" for r in bad)
    assert all(r["error_detail"] == "" for r in rows if r["judge_family"] == "anthropic")


def test_the_split_counts_judges_not_calls():
    def j(status, v=""):
        r = panel.JudgeResult("f", "L", "m", status=status)
        r.calls = ([panel.JudgeCall(1, "ok", v), panel.JudgeCall(2, "ok", v)] if v
                   else [panel.JudgeCall(1, "error", error="x"), panel.JudgeCall(2, "error", error="x")])
        return r
    assert panel.split_line([j(panel.STABLE, "HOLD"), j(panel.ERROR), j(panel.ERROR)]) == "1 HOLD, 2 error"
    assert panel.split_line([j(panel.STABLE, "HOLD"), j(panel.UNSTABLE), j(panel.ERROR)]) == \
        "1 HOLD, 1 unstable, 1 error"


def test_a_panel_is_only_read_when_two_judges_answered():
    rec = _record("BUY")
    assert panel.panel_agreement(_stable("BUY"), rec) == panel.NO_READING
    assert panel.agreement_line(panel.NO_READING, rec) == "No panel reading: fewer than two judges answered."
    assert panel.panel_agreement(_stable("BUY", "BUY"), rec) == panel.AGREES
    assert panel.judges_answered(_stable("ABSTAIN", "BUY")) == 2          # an abstention is an answer


def test_one_error_one_unstable_one_stable_gives_no_panel_reading_and_no_narrator_call(report):
    caller = _scripted({"anthropic": [_reply("HOLD")],                        # stable
                        "openai": [_reply("BUY"), _reply("SELL")],              # unstable
                        "google": [CallError("HTTP 503: busy", status=503)]})   # error
    asked = []

    def reader(result, rep, pack):
        asked.append(1)
        return panel.PanelReading(paragraph="should never be written", calls=1)

    result = _run_panel(report, caller, reader=reader)
    by = {j.family: j.status for j in result.judges}
    assert by == {"anthropic": panel.STABLE, "openai": panel.UNSTABLE, "google": panel.ERROR}
    assert result.agreement == panel.NO_READING and asked == [] and result.reading is None
    assert result.split_line == "1 HOLD, 1 unstable, 1 error"
    assert "No panel reading: fewer than two judges answered." in "\n".join(render.text_lines(result))
    row = dict(zip(jlog.SUMMARY_COLUMNS, jlog.summary_row(result)))
    assert row["judges_answered"] == 1 and row["panel_agreement"] == panel.NO_READING


def test_two_stable_judges_are_a_panel_even_when_the_third_failed(report):
    caller = _scripted({"anthropic": [_reply("SELL")], "openai": [_reply("SELL")],
                        "google": [CallError("HTTP 404: gone", status=404)]})
    result = _run_panel(report, caller)
    assert panel.judges_answered(result.judges) == 2 and result.agreement != panel.NO_READING


def test_an_older_log_with_the_previous_header_is_upgraded_not_refused(tmp_path, report):
    folder = tmp_path / "judges"
    folder.mkdir()
    (folder / jlog.LOG_NAME).write_text(
        ",".join(jlog._LOG_V1) + "\nold,row," + ",".join("x" * 12) + "\n", encoding="utf-8")
    (folder / jlog.SUMMARY_NAME).write_text(
        ",".join(jlog._SUMMARY_V1) + "\n" + ",".join("v" for _ in jlog._SUMMARY_V1) + "\n",
        encoding="utf-8")
    result = _run_panel(report, _scripted({"anthropic": [_reply()], "openai": [_reply()],
                                           "google": [_reply()]}))
    jlog.write_log(result, folder)
    log, summ = _rows(folder / jlog.LOG_NAME), _rows(folder / jlog.SUMMARY_NAME)
    assert tuple(log[0].keys()) == jlog.LOG_COLUMNS and log[0]["run_date"] == "old"
    assert log[0]["error_detail"] == ""
    assert len(log) == 7 and tuple(summ[0].keys()) == jlog.SUMMARY_COLUMNS
    assert summ[0]["judges_answered"] == "" and summ[1]["judges_answered"] == "3"


def test_gemini_is_configured_with_the_current_mid_tier_model():
    from aristos_council.judges.config import load_config
    google = next(j for j in load_config().judges if j.family == "google")
    assert google.model == "gemini-3.8-flash"
