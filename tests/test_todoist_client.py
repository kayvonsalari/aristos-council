"""SCOUT-HOLDINGS-401 — the generic Todoist delivery extracted from gap_ledger/todoist.py.

Gap Ledger's own extensive Todoist tests (test_gap_ledger_todoist_retry.py,
test_gap_ledger_todoist_v1.py) already exercise the REST client and retry classification
through the re-exported names in gap_ledger.todoist — this file pins the GENERIC entry point
(``deliver_alert`` with an arbitrary project name and dedup prefix) that a caller outside
Gap Ledger, such as the scout job, actually uses. No network is reached: every client here is
a fake.
"""
from __future__ import annotations

from datetime import date

import pytest

from aristos_council.todoist_client import (DeliveryOutcome, TodoistTransient,
                                            TodoistUnavailable, deliver_alert)

DAY = date(2026, 9, 27)


class _Client:
    def __init__(self, *, project_id="p1", existing_task=None, create_project=None,
                fail_create_task=None):
        self.project_id = project_id
        self.existing_task = existing_task
        self._create_project = create_project or []
        self._fail_create_task = list(fail_create_task or [])
        self.created = []
        self.updated = []
        self.found_project_calls = 0

    def find_project(self, name):
        self.found_project_calls += 1
        return None if self._create_project else self.project_id

    def create_project(self, name):
        return self._create_project.pop(0)

    def find_task(self, project_id, *, title_prefix):
        if self.existing_task and self.existing_task[0].startswith(title_prefix):
            return self.existing_task[1]
        return None

    def create_task(self, *, content, description, project_id):
        if self._fail_create_task:
            raise self._fail_create_task.pop(0)
        self.created.append((content, description, project_id))
        return "t1"

    def update_task(self, task_id, *, content, description):
        self.updated.append((task_id, content, description))


def test_a_new_alert_creates_a_task_in_the_named_project():
    client = _Client()
    outcome = deliver_alert(project_name="Aristos Scout", title_prefix=f"Scout {DAY.isoformat()}",
                            content=f"Scout {DAY.isoformat()} — holdings fetch failed",
                            description="HTTP Error 401: Unauthorized", client=client, sleep=lambda s: None)
    assert outcome.sent and outcome.task_id == "t1"
    assert client.created == [(f"Scout {DAY.isoformat()} — holdings fetch failed",
                               "HTTP Error 401: Unauthorized", "p1")]
    assert "Aristos Scout" in outcome.sentence(project_name="Aristos Scout")


def test_a_repeat_for_the_same_prefix_updates_instead_of_creating_a_second_task():
    client = _Client(existing_task=(f"Scout {DAY.isoformat()}", "old-id"))
    outcome = deliver_alert(project_name="Aristos Scout", title_prefix=f"Scout {DAY.isoformat()}",
                            content="new content", client=client, sleep=lambda s: None)
    assert outcome.sent and outcome.task_id == "old-id"
    assert client.updated == [("old-id", "new content", "")]
    assert client.created == []


def test_the_project_is_created_when_it_does_not_exist():
    client = _Client(create_project=["new-project-id"])
    outcome = deliver_alert(project_name="Aristos Scout", title_prefix="Scout",
                            content="x", client=client, sleep=lambda s: None)
    assert outcome.sent and outcome.project_created
    assert client.created[0][2] == "new-project-id"


def test_no_client_is_skipped_not_a_crash():
    outcome = deliver_alert(project_name="Aristos Scout", title_prefix="Scout", content="x",
                            client=None)
    assert outcome.skipped == "delivery switched off" and not outcome.sent


def test_a_transient_failure_is_retried_then_succeeds():
    client = _Client(fail_create_task=[TodoistTransient("connection reset")])
    sleeps = []
    outcome = deliver_alert(project_name="Aristos Scout", title_prefix="Scout", content="x",
                            client=client, sleep=sleeps.append, backoff=(5.0, 10.0))
    assert outcome.sent and outcome.attempts == 2 and sleeps == [5.0]


def test_a_non_transient_failure_is_reported_on_the_first_try_no_retry():
    client = _Client(fail_create_task=[TodoistUnavailable("no token")])
    sleeps = []
    outcome = deliver_alert(project_name="Aristos Scout", title_prefix="Scout", content="x",
                            client=client, sleep=sleeps.append, backoff=(5.0, 10.0))
    assert not outcome.sent and "no token" in outcome.error and outcome.attempts == 1
    assert sleeps == []


def test_sentence_names_the_project_only_when_given_one():
    sent = DeliveryOutcome(sent=True, task_id="t1")
    assert "in '" not in sent.sentence()
    assert "in 'Aristos Scout'" in sent.sentence(project_name="Aristos Scout")
