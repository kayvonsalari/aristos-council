"""GAP-TODOIST-RETRY-1 - the morning's Todoist task is asked for again, and the log reads in ASCII.

Found on 2026-09-25: the scheduled run screened and logged correctly, then lost its one Todoist
request to ``[WinError 10054] An existing connection was forcibly closed by the remote host`` and was
not retried. That error is a ``ConnectionResetError`` (a plain ``OSError``) raised while the reply is
READ, which ``urllib`` does not wrap in ``URLError``, so it was neither classified nor retried. The
same run's log showed "â€”" for every dash: UTF-8 bytes read back as cp1252.

No test sleeps for real or reaches the network: ``deliver`` takes an injected ``sleep`` and the wire
tests replace ``urlopen``.
"""
from __future__ import annotations

import json
import logging

import pytest

from aristos_council.gap_ledger import __main__ as cli
from aristos_council.gap_ledger import todoist as td
from aristos_council.gap_ledger.ledger import GROUP_CANDIDATE, LedgerRow
from datetime import date

DAY = date(2026, 9, 25)


def _candidate():
    return LedgerRow(ticker="AMD", group=GROUP_CANDIDATE, gap_pct=0.043, relative_volume=6.4,
                     spread_note="spread ok", news_found="news found", headline="AMD beats",
                     news_source="a.com", news_link="https://a.com/1")


def _reset():
    return ConnectionResetError(10054, "An existing connection was forcibly closed by the "
                                       "remote host")


class _Client:
    """A Todoist whose calls fail on a script, and which records every call."""

    def __init__(self, *, find=None, create_project=None, create_task=None, existing="proj-1"):
        self.script = {"find": list(find or []), "create_project": list(create_project or []),
                       "create_task": list(create_task or [])}
        self.existing, self.calls, self.tasks = existing, [], []

    def _maybe_fail(self, step):
        self.calls.append(step)
        queue = self.script[step]
        if queue:
            failure = queue.pop(0)
            if failure is not None:
                raise failure

    def find_project(self, name):
        self._maybe_fail("find")
        return self.existing

    def create_project(self, name):
        self._maybe_fail("create_project")
        self.existing = "proj-new"
        return self.existing

    def create_task(self, *, content, description, project_id):
        self._maybe_fail("create_task")
        self.tasks.append((content, project_id))
        return f"task-{len(self.tasks)}"


class _Sleeps:
    def __init__(self):
        self.waited = []

    def __call__(self, seconds):
        self.waited.append(seconds)


# =========================================================================== #
# the retry
# =========================================================================== #
def test_the_default_is_three_tries_over_about_a_minute():
    assert td.DELIVERY_BACKOFF == (20.0, 40.0)
    assert 1 + len(td.DELIVERY_BACKOFF) == 3 and sum(td.DELIVERY_BACKOFF) == 60.0


def test_a_connection_reset_is_retried_and_the_task_is_delivered():
    """The incident: one WinError 10054, then Todoist answers."""
    client, sleeps = _Client(create_task=[_reset()]), _Sleeps()
    outcome = td.deliver(DAY, [_candidate()], client=client, sleep=sleeps)
    assert outcome.sent and outcome.task_id == "task-1" and outcome.attempts == 2
    assert sleeps.waited == [20.0]
    assert "(after 2 tries)" in outcome.sentence() and "NOT sent" not in outcome.sentence()


def test_two_resets_are_survived_on_the_third_try():
    client, sleeps = _Client(create_task=[_reset(), _reset()]), _Sleeps()
    outcome = td.deliver(DAY, [_candidate()], client=client, sleep=sleeps)
    assert outcome.sent and outcome.attempts == 3 and sleeps.waited == [20.0, 40.0]


def test_three_failures_report_not_sent_with_the_reason_and_never_raise():
    client = _Client(create_task=[_reset(), _reset(), _reset()])
    sleeps = _Sleeps()
    outcome = td.deliver(DAY, [_candidate()], client=client, sleep=sleeps)   # does not raise
    assert not outcome.sent and outcome.attempts == 3
    assert sleeps.waited == [20.0, 40.0]                   # no wait after the last try
    sentence = outcome.sentence()
    assert sentence.startswith("Todoist: NOT sent") and "10054" in sentence
    assert "after 3 tries over 60s" in sentence
    assert len(client.tasks) == 0


def test_only_the_failed_step_is_repeated_a_found_project_stays_found():
    client = _Client(create_task=[_reset()])
    td.deliver(DAY, [_candidate()], client=client, sleep=_Sleeps())
    assert client.calls == ["find", "create_task", "create_task"]


def test_a_project_created_before_the_failure_is_not_created_twice():
    """No project yet: created once, then the task step is retried - never a second project."""
    client = _Client(existing=None, create_task=[_reset()])
    # find_project returns None the first time only (before create_project sets ``existing``)
    outcome = td.deliver(DAY, [_candidate()], client=client, sleep=_Sleeps())
    assert outcome.sent and outcome.project_created
    assert client.calls == ["find", "create_project", "create_task", "create_task"]


def test_a_failure_at_the_project_lookup_is_retried_too():
    client = _Client(find=[_reset()])
    outcome = td.deliver(DAY, [_candidate()], client=client, sleep=_Sleeps())
    assert outcome.sent and client.calls == ["find", "find", "create_task"]


@pytest.mark.parametrize("failure", [
    RuntimeError("todoist refused the task"),
    td.TodoistUnavailable("Todoist refused GET /projects: HTTP 410"),
    td.TodoistUnavailable("TODOIST_API_TOKEN is not set"),
])
def test_a_failure_that_waiting_cannot_change_is_reported_on_the_first_try(failure):
    client, sleeps = _Client(create_task=[failure]), _Sleeps()
    outcome = td.deliver(DAY, [_candidate()], client=client, sleep=sleeps)
    assert not outcome.sent and outcome.attempts == 1 and sleeps.waited == []
    assert client.calls == ["find", "create_task"]
    assert "tries" not in outcome.error


def test_a_raw_oserror_from_a_client_counts_as_transient():
    """A client that lets the reset escape unwrapped is exactly the case that was missed."""
    client = _Client(find=[TimeoutError("timed out")])
    assert td.deliver(DAY, [_candidate()], client=client, sleep=_Sleeps()).sent


def test_each_retry_is_logged_as_a_warning(caplog):
    caplog.set_level(logging.WARNING)
    td.deliver(DAY, [_candidate()], client=_Client(create_task=[_reset()]), sleep=_Sleeps())
    assert any("attempt 1 of 3 failed" in r.message and "retrying in 20s" in r.message
               for r in caplog.records)


def test_no_candidates_and_switched_off_still_never_retry_or_wait():
    sleeps = _Sleeps()
    assert td.deliver(DAY, [], client=_Client(), sleep=sleeps).skipped == "no candidates today"
    assert td.deliver(DAY, [_candidate()], client=None, sleep=sleeps).skipped
    assert sleeps.waited == []


# =========================================================================== #
# the wire: what counts as transient, and the idempotency key
# =========================================================================== #
class _Response:
    def __init__(self, body="{}", read_error=None):
        self._body, self._read_error = body.encode("utf-8"), read_error

    def read(self):
        if self._read_error:
            raise self._read_error
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _wire(monkeypatch, script):
    """``urlopen`` answering from a script of responses / exceptions, recording every request."""
    seen = []

    def fake_urlopen(request, timeout=None):
        seen.append(request)
        step = script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step
    monkeypatch.setattr(td.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_a_reset_while_reading_the_reply_is_transient_not_a_raw_exception(monkeypatch):
    """The exact incident: connect fine, reply cut off with WinError 10054."""
    _wire(monkeypatch, [_Response(read_error=_reset())])
    with pytest.raises(td.TodoistTransient, match="10054"):
        td.RestTodoist(token="tok").find_project("Gap Ledger")


@pytest.mark.parametrize("failure", [
    ConnectionResetError(10054, "reset"), ConnectionAbortedError("aborted"),
    TimeoutError("timed out"), td.urllib.error.URLError("no route"),
    td.http.client.RemoteDisconnected("closed without response"),
    td.http.client.IncompleteRead(b"par"),
])
def test_connection_level_failures_are_transient(monkeypatch, failure):
    _wire(monkeypatch, [failure])
    with pytest.raises(td.TodoistTransient):
        td.RestTodoist(token="tok").find_project("Gap Ledger")


@pytest.mark.parametrize("code, transient", [
    (500, True), (502, True), (503, True), (429, True), (408, True),
    (400, False), (401, False), (403, False), (404, False), (410, False)])
def test_http_status_decides_whether_it_is_worth_asking_again(monkeypatch, code, transient):
    _wire(monkeypatch, [td.urllib.error.HTTPError("https://x", code, "why", {}, None)])
    expected = td.TodoistTransient if transient else td.TodoistUnavailable
    with pytest.raises(td.TodoistUnavailable) as caught:
        td.RestTodoist(token="tok").find_project("Gap Ledger")
    assert isinstance(caught.value, td.TodoistTransient) is transient
    assert isinstance(caught.value, expected)


def test_a_reply_cut_off_mid_body_is_transient(monkeypatch):
    _wire(monkeypatch, [_Response(body='{"results": [')])
    with pytest.raises(td.TodoistTransient, match="unreadable"):
        td.RestTodoist(token="tok").find_project("Gap Ledger")


def test_end_to_end_two_resets_then_success_over_the_wire(monkeypatch):
    seen = _wire(monkeypatch, [
        _Response(json.dumps({"results": [{"id": "P1", "name": "Gap Ledger"}],
                              "next_cursor": None})),
        _Response(read_error=_reset()),                      # POST /tasks: reply lost
        td.urllib.error.URLError(_reset()),                  # retry: connection refused
        _Response(json.dumps({"id": "T1"}))])
    sleeps = _Sleeps()
    outcome = td.deliver(DAY, [_candidate()], client=td.RestTodoist(token="tok"), sleep=sleeps)
    assert outcome.sent and outcome.task_id == "T1" and outcome.attempts == 3
    assert [r.method for r in seen] == ["GET", "POST", "POST", "POST"]     # the project: once
    assert sleeps.waited == [20.0, 40.0]


def test_the_task_post_carries_the_same_request_id_on_every_try_and_gets_carry_none(monkeypatch):
    """A reset can land AFTER Todoist created the task; the repeat must not become a second one."""
    seen = _wire(monkeypatch, [
        _Response(json.dumps({"results": [{"id": "P1", "name": "Gap Ledger"}],
                              "next_cursor": None})),
        _Response(read_error=_reset()), _Response(json.dumps({"id": "T1"}))])
    td.deliver(DAY, [_candidate()], client=td.RestTodoist(token="tok"), sleep=_Sleeps())
    get, first, second = seen
    assert get.get_header("X-request-id") is None
    assert first.get_header("X-request-id") and \
        first.get_header("X-request-id") == second.get_header("X-request-id")


def test_the_request_id_differs_between_days():
    ids = []
    for day in (date(2026, 9, 25), date(2026, 9, 26)):
        captured = {}

        class Recorder(td.RestTodoist):
            def _call(self, path, *, payload=None, params=None, request_id=""):
                captured["id"] = request_id
                return {"id": "T"}
        Recorder(token="t").create_task(content=td.task_title(day, [_candidate()]),
                                        description="d", project_id="P1")
        ids.append(captured["id"])
    assert ids[0] and ids[1] and ids[0] != ids[1]


# =========================================================================== #
# the log reads the same in any editor
# =========================================================================== #
def test_ascii_safe_spells_out_the_dashes_that_showed_up_as_mojibake():
    assert cli.ascii_safe("Todoist: NOT sent — boom") == "Todoist: NOT sent - boom"
    assert cli.ascii_safe("a – b … c") == "a - b ... c"
    # the exact mojibake the scheduled log showed is UTF-8 read as cp1252:
    assert "—".encode("utf-8").decode("cp1252") == "â€”"


def test_ascii_safe_drops_accents_and_never_raises_on_an_unknown_character():
    assert cli.ascii_safe("Hermès S.A. × 2, ≤ 5") == "Hermes S.A. x 2, <= 5"
    assert cli.ascii_safe("中国") == "??"
    assert cli.ascii_safe("plain ascii stays") == "plain ascii stays"
    assert cli.ascii_safe("") == ""


def test_redirected_output_is_ascii_and_so_is_the_reports_delivery_line(capsys):
    """capsys is not a terminal: this is what the scheduled run's log file receives."""
    outcome = td.DeliveryOutcome(error="Todoist unreachable: [WinError 10054] reset (after 3 "
                                       "tries over 60s)")
    cli._say(outcome.sentence())
    cli._say("GAP LEDGER — 2026-09-25 · spread ≥ 1%")
    out = capsys.readouterr().out
    assert "—" not in out and "â€" not in out
    assert "Todoist: NOT sent - Todoist unreachable" in out
    assert "GAP LEDGER - 2026-09-25 - spread >= 1%" in out
    assert all(ord(ch) < 128 for ch in out)


def test_a_real_console_keeps_its_glyphs(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_is_console", lambda stream: True)
    cli._say("a — b")
    assert capsys.readouterr().out == "a — b\n"


def test_every_non_ascii_character_the_gap_ledger_sources_contain_has_a_spelling():
    """The table is checked against what the sources actually contain, so a new glyph in a report
    line with no ASCII spelling fails here instead of turning into '?' in a log nobody reads."""
    import pathlib
    root = pathlib.Path(cli.__file__).parent
    used = set()
    for source in root.glob("*.py"):
        used |= {ch for ch in source.read_text(encoding="utf-8") if ord(ch) > 127}
    # the a-circumflex and euro sign also appear as the mojibake quoted in the comment above the table
    unspelled = {ch for ch in used if ch not in cli._ASCII_PUNCTUATION} - {"â"}
    assert unspelled == set(), [f"U+{ord(c):04X}" for c in unspelled]
