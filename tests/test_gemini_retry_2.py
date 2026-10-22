"""GEMINI-RETRY-2 - a busy server (503), a rate limit (429) or a timeout is retried once, after a
short pause, for EVERY judge family, before an error is recorded.

Live (2026-10-22 sweep): Gemini returned HTTP 503 on 5 of 7 company pages, rendered as
"Gemini: incomplete - 503". Every call here is a fake and the pause is a recorded no-op."""
from __future__ import annotations

import pytest

from aristos_council.judges import log as jlog
from aristos_council.judges import panel
from aristos_council.judges.clients import CallError
from tests.test_judge_panel import _config, _reply

pytestmark = pytest.mark.min_group(3)

FAMILIES = list(_config(3, 1).judges)
TRANSIENT = [CallError("HTTP 503: The model is overloaded. Please try again later.", status=503),
             CallError("HTTP 429: rate limit, slow down", status=429),
             CallError("TimeoutError: the read operation timed out", timeout=True)]


def _seq(*items):
    queue = list(items)
    calls = []

    def caller(spec, system, user):
        calls.append(spec.family)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    caller.calls = calls
    return caller


@pytest.mark.parametrize("spec", FAMILIES, ids=lambda s: s.family)
@pytest.mark.parametrize("exc", TRANSIENT, ids=["503", "429", "timeout"])
def test_one_transient_failure_then_success_is_one_answer_after_one_retry(spec, exc):
    slept = []
    caller = _seq(exc, _reply("HOLD"))
    call = panel._ask_once(spec, "s", "u", caller, 1, sleep=slept.append)
    assert call.status == "ok" and call.verdict == "HOLD" and call.error == ""
    assert call.attempts == 2 and len(caller.calls) == 2
    assert slept == [panel.TRANSIENT_BACKOFF_SECONDS]
    assert str(exc) in call.error_detail          # kept for the log


@pytest.mark.parametrize("spec", FAMILIES, ids=lambda s: s.family)
@pytest.mark.parametrize("exc, label", list(zip(TRANSIENT, ["503", "429", "timeout"])),
                         ids=["503", "429", "timeout"])
def test_a_second_transient_failure_is_recorded_as_the_error(spec, exc, label):
    slept = []
    caller = _seq(exc, exc, _reply("BUY"))
    call = panel._ask_once(spec, "s", "u", caller, 1, sleep=slept.append)
    assert call.status == "error" and call.error == label and call.error_detail == str(exc)
    assert len(caller.calls) == 2 and len(slept) == 1


@pytest.mark.parametrize("spec", FAMILIES, ids=lambda s: s.family)
@pytest.mark.parametrize("exc, label", [
    (CallError("HTTP 400: invalid argument", status=400), "400"),
    (CallError("HTTP 404: models/x is no longer available", status=404), "model not available"),
    (CallError('HTTP 429: {"error": {"type": "insufficient_quota"}}', status=429), "no credits"),
    (CallError("URLError: dns"), "failed"),
], ids=["400", "404", "no-credits", "dns"])
def test_a_non_transient_failure_is_not_retried(spec, exc, label):
    slept = []
    caller = _seq(exc, _reply("BUY"))
    call = panel._ask_once(spec, "s", "u", caller, 1, sleep=slept.append)
    assert call.status == "error" and call.error == label
    assert len(caller.calls) == 1 and slept == []


def test_the_panel_retries_a_503_for_each_judge_and_settles_stable():
    """Every family fails 503 on its first attempt, then answers: three stable judges, no error."""
    seen: dict = {}

    def caller(spec, system, user):
        seen.setdefault(spec.family, []).append(1)
        if len(seen[spec.family]) == 1:
            raise CallError("HTTP 503: high demand", status=503)
        return _reply("HOLD")

    slept = []
    judges = panel.run_judges("PACK", _config(3, 1), caller,
                              environ={"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o", "GEMINI_API_KEY": "g"},
                              sleep=slept.append)
    assert [j.status for j in judges] == [panel.STABLE] * 3
    assert [j.calls[0].attempts for j in judges] == [2, 2, 2] and len(slept) == 3


def test_a_recovered_retry_is_in_the_log_and_the_shown_verdict_carries_no_error():
    call = panel._ask_once(FAMILIES[2], "s", "u", _seq(TRANSIENT[0], _reply("SELL")), 1,
                           sleep=lambda s: None)
    j = panel.settle(FAMILIES[2], [call])
    res = panel.PanelResult(ticker="CO", available=True, judges=[j])
    (row,) = jlog.log_rows(res)
    row = dict(zip(jlog.LOG_COLUMNS, row))
    assert row["call_status"] == "ok" and row["verdict"] == "SELL"
    assert "503" in row["error_detail"] and j.verdict_text() == "SELL"
