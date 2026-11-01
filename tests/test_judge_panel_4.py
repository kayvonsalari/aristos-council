"""JUDGE-PANEL-3 additions - the panel without a lens vote, the timeout retry, the yaml settings.

Every model call is a fake; nothing here reaches a provider."""
from __future__ import annotations

import csv

import pytest

from aristos_council.judges import log as jlog
from aristos_council.judges import panel, render, score
from aristos_council.judges.clients import CallError, http_caller
from aristos_council.judges.config import JudgeSpec, PanelConfig, load_config
from tests.test_judge_panel import (KEYS, _config, _reply, _run_panel, _scripted,
                                    report)  # noqa: F401  (report is a fixture)

pytestmark = pytest.mark.min_group(3)


# =========================================================================== #
# no lens voted
# =========================================================================== #
def test_no_lens_vote_still_asks_three_judges_and_logs_no_lens_vote(tmp_path, report):
    report.agreement = None
    narrator = []
    caller = _scripted({"anthropic": [_reply("SELL")], "openai": [_reply("SELL")], "google": [_reply("HOLD")]})
    result = _run_panel(report, caller, reader=lambda *a: narrator.append(a))
    assert result.available and len(caller.calls) == 6 and narrator == []
    assert result.agreement == panel.NO_RECORD and result.reading is None
    assert [c["verdict"] for c in render.cards(result)] == ["SELL", "SELL", "HOLD"]     # three judge rows
    folder = jlog.write_log(result, tmp_path / "j")
    log = list(csv.DictReader((folder / jlog.LOG_NAME).open(encoding="utf-8")))
    (summary,) = list(csv.DictReader((folder / jlog.SUMMARY_NAME).open(encoding="utf-8")))
    assert {r["verdict_of_record"] for r in log} == {"no lens vote"} and len(log) == 6
    assert summary["verdict_of_record"] == "no lens vote" and summary["judges_answered"] == "3"


def test_the_agreement_line_without_a_lens_vote(report):
    report.agreement = None
    caller = _scripted({"anthropic": [_reply("SELL")], "openai": [_reply("SELL")], "google": [_reply("HOLD")]})
    result = _run_panel(report, caller)
    # JUDGE-FIGURE-CHECK-1: the scripted answers quote figures the test pack does not hold, so the header also
    # counts the sentences that were marked
    assert render.summary_lines(result) == [
        "Verdict of record: none (no lens voted). Panel: 1 HOLD, 2 SELL.",
        "3 sentences quoted a figure that is not in the facts the judges were given; each is marked where it stands."]
    one = _run_panel(report, _scripted({"anthropic": [_reply("SELL")], "openai": [RuntimeError("x")],
                                        "google": [RuntimeError("x")]}))
    assert render.summary_lines(one)[0].endswith("No panel reading: fewer than two judges answered.")


def test_the_scorer_scores_judges_and_leaves_the_lens_comparison_empty(report):
    row = {"run_date": "2025-01-02T10:00:00+00:00", "ticker": "CO", "verdict_of_record": "no lens vote",
           "claude_verdict": "SELL", "openai_verdict": "HOLD", "gemini_verdict": "ABSTAIN",
           "peers": "A;B;C"}
    out = score.score_rows([row], {}, __import__("datetime").date(2025, 2, 1))
    by = {r["subject"]: r["status"] for r in out if int(r["window_months"]) == 3}
    assert by["lenses"] == score.NOT_SCORED and by["claude"] == score.TOO_EARLY
    assert score.disagreements(out) == []          # nothing to disagree with


def test_the_council_opinion_keeps_its_silence_rule():
    import inspect
    from aristos_council import company_report
    assert "no lens" in inspect.getsource(company_report.run_council_opinion).lower()


# =========================================================================== #
# timeout retry
# =========================================================================== #
def test_a_timeout_is_retried_once_and_the_second_try_can_succeed(report):
    tries = {"n": 0}

    def caller(spec, system, user):
        tries["n"] += 1
        if tries["n"] == 1:
            raise CallError("TimeoutError: timed out", timeout=True)
        return _reply("BUY")

    call = panel._ask_once(_config(1, 1).judges[0], "s", "u", caller, 1)
    assert call.status == "ok" and call.attempts == 2 and call.verdict == "BUY"


def test_two_timeouts_record_error_timeout_with_the_raw_detail():
    n = []

    def caller(spec, system, user):
        n.append(1)
        raise CallError("TimeoutError: the read operation timed out", timeout=True)

    call = panel._ask_once(_config(1, 1).judges[0], "s", "u", caller, 1)
    assert len(n) == 2 and call.status == "error" and call.error == "timeout"
    assert "read operation timed out" in call.error_detail and call.attempts == 2


def test_a_non_timeout_failure_is_still_not_retried():
    # GEMINI-RETRY-2: a 503 is now retried (tests/test_gemini_retry_2.py); a 400 still is not
    n = []

    def caller(spec, system, user):
        n.append(1)
        raise CallError("HTTP 400: bad request", status=400)

    call = panel._ask_once(_config(1, 1).judges[0], "s", "u", caller, 1)
    assert len(n) == 1 and call.error == "400"


def test_a_timeout_then_a_malformed_reply_still_gets_its_one_format_retry():
    seq = iter([CallError("timed out", timeout=True), "not json", _reply("HOLD")])

    def caller(spec, system, user):
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        return item

    call = panel._ask_once(_config(1, 1).judges[0], "s", "u", caller, 1)
    assert call.status == "ok" and call.attempts == 3


# =========================================================================== #
# settings live in data/judges.yaml
# =========================================================================== #
def test_the_yaml_carries_the_timeout_and_gemini_thinking_level():
    cfg = load_config()
    assert cfg.timeout_seconds == 90
    by = {j.family: j for j in cfg.judges}
    assert {j.timeout_seconds for j in cfg.judges} == {90}
    assert by["google"].thinking_level == "low" and by["anthropic"].thinking_level == ""


def test_settings_are_read_from_the_file_with_a_per_judge_override(tmp_path):
    p = tmp_path / "j.yaml"
    p.write_text("calls_per_judge: 2\ntimeout_seconds: 30\njudges:\n"
                 "  - {family: anthropic, label: C, model: m, key_env: A}\n"
                 "  - {family: google, label: G, model: g, key_env: B, timeout_seconds: 45, thinking_level: LOW}\n",
                 encoding="utf-8")
    cfg = load_config(p)
    assert cfg.timeout_seconds == 30
    assert [j.timeout_seconds for j in cfg.judges] == [30, 45] and cfg.judges[1].thinking_level == "low"


def test_the_request_carries_the_timeout_and_the_gemini_thinking_level():
    seen = []

    def post(url, headers, body, **kw):
        seen.append((url, body, kw))
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}], "content": [], "choices": []}

    g = JudgeSpec("google", "G", "gem", "GEMINI_API_KEY", timeout_seconds=90, thinking_level="low")
    a = JudgeSpec("anthropic", "A", "cl", "ANTHROPIC_API_KEY", timeout_seconds=70)
    o = JudgeSpec("openai", "O", "gp", "OPENAI_API_KEY", timeout_seconds=60)
    for spec in (g, a, o):
        http_caller(spec, "s", "u", post=post)
    assert seen[0][1]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert [kw["timeout"] for _, _, kw in seen] == [90, 70, 60]
    assert "thinkingConfig" not in seen[1][1].get("generationConfig", {})
    plain = JudgeSpec("google", "G", "gem", "GEMINI_API_KEY")
    http_caller(plain, "s", "u", post=post)
    assert "thinkingConfig" not in seen[-1][1]["generationConfig"]
