"""GAP-TODOIST-1 — the unified Todoist API v1, and the paginated shape it answers with.

The first live delivery died on ``Todoist refused GET /projects: HTTP 410``: ``/rest/v2``
is retired, not merely deprecated. Three things changed together, and the middle one is the
dangerous one:

* the base URL is ``/api/v1``;
* list endpoints are PAGINATED — ``{"results": [...], "next_cursor": ...}`` rather than a
  bare array. Reading the old shape yields an empty iteration, which looks exactly like
  "the project does not exist", so ``deliver`` would create a SECOND "Gap Ledger" every
  morning. That is the failure these tests exist to prevent;
* ids are opaque strings, so nothing may coerce them to int.

The ``TodoistClient`` Protocol and the test fake are unchanged, so every existing
delivery test still holds — only the wire layer moved. The HTTP is exercised through a
stubbed ``urlopen``: no test reaches the network.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from aristos_council.gap_ledger import todoist as td
from aristos_council.gap_ledger.ledger import GROUP_CANDIDATE, LedgerRow

DAY = date(2026, 9, 22)


# --------------------------------------------------------------------------- #
# the shape unwrapper
# --------------------------------------------------------------------------- #
def test_the_v1_paginated_shape_is_unwrapped():
    rows, cursor = td._results({"results": [{"id": "6cW", "name": "Gap Ledger"}],
                               "next_cursor": "eyJwYWdlIjoy"})
    assert rows == [{"id": "6cW", "name": "Gap Ledger"}]
    assert cursor == "eyJwYWdlIjoy"


def test_a_final_page_reports_no_cursor():
    assert td._results({"results": [{"id": "1"}], "next_cursor": None})[1] is None


def test_a_bare_array_is_still_tolerated():
    """If a proxy unwraps the envelope, ``.get`` on a list would raise and a working
    delivery would become a crash."""
    rows, cursor = td._results([{"id": "1", "name": "x"}])
    assert rows == [{"id": "1", "name": "x"}]
    assert cursor is None


@pytest.mark.parametrize("payload", ["nonsense", 7, None, {"unexpected": True}])
def test_an_unrecognised_shape_yields_nothing_rather_than_looping(payload):
    assert td._results(payload) == ([], None)


# --------------------------------------------------------------------------- #
# a stubbed transport
# --------------------------------------------------------------------------- #
class _Response:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _transport(monkeypatch, routes: dict, calls: list):
    """Answer ``urlopen`` from ``routes`` keyed by "<METHOD> <path-with-query>"."""
    def fake_urlopen(request, timeout=None):
        key = f"{request.method} {request.full_url[len(td.BASE_URL) + 1:]}"
        calls.append((key, json.loads(request.data.decode()) if request.data else None,
                      dict(request.headers)))
        for pattern, body in routes.items():
            if key.startswith(pattern):
                return _Response(json.dumps(body))
        raise AssertionError(f"unrouted request: {key}")

    monkeypatch.setattr(td.urllib.request, "urlopen", fake_urlopen)
    return calls


# --------------------------------------------------------------------------- #
# the client
# --------------------------------------------------------------------------- #
def test_the_base_url_is_the_unified_v1_api():
    assert td.BASE_URL == "https://api.todoist.com/api/v1"
    assert "rest/v2" not in td.BASE_URL


def test_find_project_matches_the_existing_project_by_name(monkeypatch):
    """The project already exists in the owner's account, made by hand — the id is not
    something they know, so name matching is the contract."""
    calls: list = []
    _transport(monkeypatch, {"GET projects": {
        "results": [{"id": "6cWQ1", "name": "Inbox"},
                    {"id": "6cWQ9", "name": "Gap Ledger"}],
        "next_cursor": None}}, calls)
    client = td.RestTodoist(token="tok")
    assert client.find_project("Gap Ledger") == "6cWQ9"


def test_find_project_ignores_case_and_surrounding_space(monkeypatch):
    _transport(monkeypatch, {"GET projects": {
        "results": [{"id": "6cWQ9", "name": "  gap ledger "}], "next_cursor": None}}, [])
    assert td.RestTodoist(token="tok").find_project("Gap Ledger") == "6cWQ9"


def test_find_project_follows_the_cursor_to_a_later_page(monkeypatch):
    """The bug this prevents: a project on page two reads as absent, and every morning
    creates another "Gap Ledger"."""
    calls: list = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        body = ({"results": [{"id": "1", "name": "Inbox"}], "next_cursor": "PAGE2"}
                if "cursor" not in request.full_url
                else {"results": [{"id": "6cWQ9", "name": "Gap Ledger"}],
                      "next_cursor": None})
        return _Response(json.dumps(body))

    monkeypatch.setattr(td.urllib.request, "urlopen", fake_urlopen)
    assert td.RestTodoist(token="tok").find_project("Gap Ledger") == "6cWQ9"
    assert len(calls) == 2
    assert "cursor=PAGE2" in calls[1]


def test_an_absent_project_is_none_not_an_error(monkeypatch):
    _transport(monkeypatch, {"GET projects": {"results": [{"id": "1", "name": "Inbox"}],
                                              "next_cursor": None}}, [])
    assert td.RestTodoist(token="tok").find_project("Gap Ledger") is None


def test_reading_the_old_bare_array_would_not_be_mistaken_for_absence(monkeypatch):
    _transport(monkeypatch, {"GET projects": [{"id": "6cWQ9", "name": "Gap Ledger"}]}, [])
    assert td.RestTodoist(token="tok").find_project("Gap Ledger") == "6cWQ9"


def test_an_opaque_string_id_is_never_coerced_to_a_number(monkeypatch):
    _transport(monkeypatch, {"GET projects": {
        "results": [{"id": "6cWQ9Vx7", "name": "Gap Ledger"}], "next_cursor": None}}, [])
    found = td.RestTodoist(token="tok").find_project("Gap Ledger")
    assert found == "6cWQ9Vx7"
    assert isinstance(found, str)


def test_create_project_posts_a_name_and_reads_a_bare_object(monkeypatch):
    calls: list = []
    _transport(monkeypatch, {"POST projects": {"id": "6cWNEW", "name": "Gap Ledger"}},
               calls)
    assert td.RestTodoist(token="tok").create_project("Gap Ledger") == "6cWNEW"
    assert calls[0][1] == {"name": "Gap Ledger"}


def test_create_task_posts_content_description_and_project_id(monkeypatch):
    calls: list = []
    _transport(monkeypatch, {"POST tasks": {"id": "6cTASK"}}, calls)
    made = td.RestTodoist(token="tok").create_task(content="Gap Ledger 2026-09-22",
                                                   description="**AMD** …",
                                                   project_id="6cWQ9")
    assert made == "6cTASK"
    assert calls[0][1] == {"content": "Gap Ledger 2026-09-22",
                           "description": "**AMD** …", "project_id": "6cWQ9"}


def test_the_token_is_sent_as_a_bearer_header(monkeypatch):
    calls: list = []
    _transport(monkeypatch, {"GET projects": {"results": [], "next_cursor": None}}, calls)
    td.RestTodoist(token="tok").find_project("Gap Ledger")
    headers = {k.lower(): v for k, v in calls[0][2].items()}
    assert headers["Authorization".lower()] == "Bearer tok"


def test_a_missing_token_says_what_to_do_about_it(monkeypatch):
    monkeypatch.delenv("TODOIST_API_TOKEN", raising=False)
    with pytest.raises(td.TodoistUnavailable) as caught:
        td.RestTodoist().find_project("Gap Ledger")
    assert "TODOIST_API_TOKEN" in str(caught.value) and "--no-todoist" in str(caught.value)


def test_an_http_error_names_the_method_and_path(monkeypatch):
    """The 410 that started this: the message has to say which call was refused."""
    def fake_urlopen(request, timeout=None):
        raise td.urllib.error.HTTPError(request.full_url, 410, "Gone", {}, None)

    monkeypatch.setattr(td.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(td.TodoistUnavailable) as caught:
        td.RestTodoist(token="tok").find_project("Gap Ledger")
    assert "GET" in str(caught.value) and "410" in str(caught.value)


# --------------------------------------------------------------------------- #
# end to end through deliver(), against the v1 shape
# --------------------------------------------------------------------------- #
def _candidate() -> LedgerRow:
    return LedgerRow(ticker="AMD", group=GROUP_CANDIDATE, gap_pct=0.043,
                     relative_volume=6.4, spread_note="spread ok",
                     news_found="news found", headline="AMD beats",
                     news_source="a.com", news_link="https://a.com/1")


def test_delivery_reuses_the_existing_project_over_the_v1_wire(monkeypatch):
    """The whole point of item 2: the project exists, so nothing is created."""
    calls: list = []
    _transport(monkeypatch, {
        "GET projects": {"results": [{"id": "6cWQ9", "name": "Gap Ledger"}],
                         "next_cursor": None},
        "POST tasks": {"id": "6cTASK"},
    }, calls)
    outcome = td.deliver(DAY, [_candidate()], client=td.RestTodoist(token="tok"))
    assert outcome.sent and not outcome.project_created
    assert outcome.task_id == "6cTASK"
    assert [c[0].split()[0] for c in calls] == ["GET", "POST"]   # no POST /projects
    assert calls[-1][1]["project_id"] == "6cWQ9"


def test_a_410_is_reported_and_never_raised(monkeypatch):
    """The CSV is the record; Todoist is a convenience. The first live run must have
    survived this, and now it says so."""
    def fake_urlopen(request, timeout=None):
        raise td.urllib.error.HTTPError(request.full_url, 410, "Gone", {}, None)

    monkeypatch.setattr(td.urllib.request, "urlopen", fake_urlopen)
    outcome = td.deliver(DAY, [_candidate()], client=td.RestTodoist(token="tok"))
    assert not outcome.sent
    assert "410" in outcome.error
    assert "NOT sent" in outcome.sentence()
