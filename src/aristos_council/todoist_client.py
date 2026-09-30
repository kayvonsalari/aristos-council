"""Generic Todoist delivery over the v1 REST API — find-or-create a project, post or update ONE
dated task, three tries with backoff on a transient failure (now, +20s, +60s by default).

Extracted from ``gap_ledger/todoist.py`` (SCOUT-HOLDINGS-401) so it has exactly ONE
implementation, used by Gap Ledger's own candidate-list delivery AND by the scout job's
failure alert (``scripts/scout_verdicts.py``) — a pure move, no behaviour change; every test
that pinned the old module still pins the same behaviour through its re-exports. Nothing
project-specific lives here: no candidate rows, no gap-ledger vocabulary, no scout vocabulary.
A caller supplies its own project name, task content/description and a day for the dedup
prefix.

The token comes from ``TODOIST_API_TOKEN`` in the environment (a local ``.env``, or a
GitHub Actions secret for a workflow) — nothing personal is written here, and no project id or
task id is committed.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol, Sequence

_log = logging.getLogger(__name__)

# GAP-TODOIST-1 — the unified API v1. The first live run died on
# "Todoist refused GET /projects: HTTP 410": /rest/v2 is RETIRED, not merely deprecated.
#
# Confirmed against Doist's own Python client (todoist-api-python,
# ``_core/endpoints.py`` + ``api.py``) rather than from memory, because three things
# changed at once and each of them breaks quietly:
#
#   * the base URL is ``/api/v1``;
#   * LIST endpoints are PAGINATED — ``GET /projects`` answers
#     ``{"results": [...], "next_cursor": ...}``, not a bare array. Reading the old shape
#     yields an empty iteration, which would look exactly like "the project does not
#     exist" and would silently create a SECOND project every run;
#   * ids are opaque STRINGS now, not numbers — so nothing here coerces them to int.
#
# CREATE endpoints still answer a bare object (``POST /projects`` -> a project,
# ``POST /tasks`` -> a task), which is why only the list path needs unwrapping.
BASE_URL = "https://api.todoist.com/api/v1"
# Page size for a listing. The API caps it at 200; the cursor loop below means the cap is a
# pacing choice and not a correctness one.
PAGE_LIMIT = 200


class TodoistUnavailable(RuntimeError):
    """No token, or the API refused in a way that leaves nothing to deliver to."""


class TodoistTransient(TodoistUnavailable):
    """A failure of the MOMENT, worth asking again: a connection reset or dropped, a timeout, a
    5xx or a 429. GAP-TODOIST-RETRY-1. Everything else (no token, a 4xx such as the retired API's
    410) is a fact about the request and is reported at once, because asking again cannot change
    the answer."""


# GAP-TODOIST-RETRY-1 — three tries over about a minute: now, +20s, +60s. Found on 2026-09-25,
# when a scheduled run screened and logged correctly and then lost its one Todoist request to
# "[WinError 10054] An existing connection was forcibly closed by the remote host", and was not
# asked again. That error is a plain OSError (ConnectionResetError) raised while READING the
# reply, which ``urllib`` does not wrap in URLError, so it was neither classified nor retried.
DELIVERY_BACKOFF = (20.0, 40.0)


# --------------------------------------------------------------------------- #
# the client
# --------------------------------------------------------------------------- #
def _results(page) -> tuple[list, Optional[str]]:
    """``(rows, next_cursor)`` out of a v1 list response, tolerating the older bare array.

    GAP-TODOIST-1. The tolerance is not politeness: if Todoist ever serves a bare list
    again, or a proxy unwraps it, reading ``.get("results")`` off a list would raise and a
    working delivery would become a crash. A shape we do not recognise yields NO rows and
    NO cursor, which ends the loop rather than looping forever.
    """
    if isinstance(page, dict):
        rows = page.get("results")
        cursor = page.get("next_cursor")
        return (list(rows) if isinstance(rows, list) else []),               (str(cursor) if cursor else None)
    if isinstance(page, list):
        return list(page), None
    return [], None


class TodoistClient(Protocol):
    def find_project(self, name: str) -> Optional[str]:
        """The project id, or None when no project has that name."""

    def create_project(self, name: str) -> str:
        """Create it and return the new id."""

    def create_task(self, *, content: str, description: str, project_id: str) -> str:
        """Create the task and return its id."""

    def find_task(self, project_id: str, *, title_prefix: str) -> Optional[str]:
        """The id of a task in ``project_id`` whose content STARTS WITH ``title_prefix``, or
        None. The dedup primitive: a re-post for the same dated alert finds and updates
        whichever task already carries that prefix instead of creating a second one."""

    def update_task(self, task_id: str, *, content: str, description: str) -> None:
        """Replace an existing task's content and description in place."""


class RestTodoist:
    """Todoist API **v1** over ``urllib``, matching the repo's other outbound calls.

    The class name is kept (GAP-TODOIST-1) so every existing caller and saved habit still
    works; what changed there was the wire protocol, not the seam.
    """

    def __init__(self, token: Optional[str] = None, *, timeout: float = 15.0) -> None:
        self._token = token
        self.timeout = timeout

    def _require_token(self) -> str:
        token = self._token or os.environ.get("TODOIST_API_TOKEN", "")
        if not token.strip():
            raise TodoistUnavailable(
                "TODOIST_API_TOKEN is not set — put it in the local .env (or, for a GitHub "
                "Actions workflow, add it as a repository secret), or run with `--no-todoist`.")
        return token.strip()

    def _call(self, path: str, *, payload: Optional[dict] = None,
              params: Optional[dict] = None, request_id: str = ""):
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        headers = {"Authorization": f"Bearer {self._require_token()}",
                   "Content-Type": "application/json"}
        if request_id:
            headers["X-Request-Id"] = request_id
        request = urllib.request.Request(
            f"{BASE_URL}/{path}{query}",
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers=headers, method="GET" if payload is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            kind = (TodoistTransient if exc.code in (408, 425, 429) or exc.code >= 500
                    else TodoistUnavailable)
            raise kind(f"Todoist refused {request.method} /{path}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            # OSError covers ConnectionResetError (WinError 10054) and friends, which escape
            # urllib unwrapped when they happen while the reply is being read.
            raise TodoistTransient(f"Todoist unreachable: {exc}") from exc
        except ValueError as exc:                        # a reply cut off mid-body
            raise TodoistTransient(f"Todoist reply unreadable: {exc}") from exc

    def _projects(self):
        """Every project, following ``next_cursor`` to the end.

        Paging matters for correctness, not tidiness: a project sitting on page two would
        read as absent, and ``deliver_alert`` would helpfully create a second one every run.
        """
        cursor: Optional[str] = None
        seen = 0
        while True:
            params = {"limit": PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            page = self._call("projects", params=params)
            rows, cursor = _results(page)
            yield from rows
            seen += len(rows)
            if not cursor or not rows or seen > 10_000:   # a cursor that never ends
                return

    def find_project(self, name: str) -> Optional[str]:
        """The project id, matched on NAME, case- and whitespace-insensitively.

        Name matching is the contract because the id is not something the caller knows or
        can put in a config — the project already exists in the account, made by hand.
        """
        wanted = name.strip().lower()
        for project in self._projects():
            if not isinstance(project, dict):
                continue
            if str(project.get("name", "")).strip().lower() == wanted:
                identifier = project.get("id")
                if identifier is not None:
                    return str(identifier)               # opaque string in v1, never int
        return None

    def create_project(self, name: str) -> str:
        created = self._call("projects", payload={"name": name})
        return str((created or {}).get("id"))

    def create_task(self, *, content: str, description: str, project_id: str) -> str:
        payload = {"content": content, "description": description,
                   "project_id": project_id}
        # A reset can arrive AFTER Todoist has created the task, with the reply lost on the way
        # back. The same request id on every try lets the server treat the repeat as the same
        # request rather than a second task; the content carries the date (and whatever else
        # the caller put in it), so it is the same across the tries of one delivery and
        # different from any other day's.
        request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"todoist-alert|{project_id}|{content}"))
        created = self._call("tasks", payload=payload, request_id=request_id)
        return str((created or {}).get("id"))

    def _tasks(self, project_id: str):
        """Every task in ``project_id``, following ``next_cursor`` — the same pagination
        discipline ``_projects`` uses, for the same reason (GAP-TODOIST-1)."""
        cursor: Optional[str] = None
        seen = 0
        while True:
            params = {"project_id": project_id, "limit": PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            page = self._call("tasks", params=params)
            rows, cursor = _results(page)
            yield from rows
            seen += len(rows)
            if not cursor or not rows or seen > 10_000:
                return

    def find_task(self, project_id: str, *, title_prefix: str) -> Optional[str]:
        for task in self._tasks(project_id):
            if not isinstance(task, dict):
                continue
            if str(task.get("content", "")).startswith(title_prefix):
                identifier = task.get("id")
                if identifier is not None:
                    return str(identifier)
        return None

    def update_task(self, task_id: str, *, content: str, description: str) -> None:
        self._call(f"tasks/{task_id}",
                  payload={"content": content, "description": description})


# --------------------------------------------------------------------------- #
# the alert — ONE dated task, no candidate rows, no project-specific vocabulary
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DeliveryOutcome:
    """What delivery did. ``sent`` False with an empty ``error`` means there was nothing
    to send, or delivery was switched off."""

    sent: bool = False
    task_id: str = ""
    project_created: bool = False
    error: str = ""
    skipped: str = ""
    attempts: int = 1                 # GAP-TODOIST-RETRY-1: how many tries it took (or was given)

    def sentence(self, *, project_name: str = "") -> str:
        if self.error:
            return f"Todoist: NOT sent — {self.error}"
        if self.skipped:
            return f"Todoist: not sent — {self.skipped}"
        made = " (project created)" if self.project_created else ""
        retried = f" (after {self.attempts} tries)" if self.attempts > 1 else ""
        where = f" in '{project_name}'" if project_name else ""
        return f"Todoist: task {self.task_id} created{where}{made}{retried}."


def _is_transient(exc: BaseException) -> bool:
    """A failure worth asking again about. A raw ``OSError`` counts as well as our own class: a
    client that lets a connection reset escape unwrapped is exactly the case that was missed."""
    return isinstance(exc, (TodoistTransient, OSError, http.client.HTTPException))


def _project(client: TodoistClient, project_name: str) -> tuple[str, bool]:
    """``(project_id, created)`` — found or made, once per delivery attempt loop."""
    project_id = client.find_project(project_name)
    if project_id is None:
        return client.create_project(project_name), True
    return project_id, False


def deliver_alert(*, project_name: str, title_prefix: str, content: str, description: str = "",
                  client: Optional[TodoistClient], sleep=time.sleep,
                  backoff: Sequence[float] = DELIVERY_BACKOFF) -> DeliveryOutcome:
    """Send ONE alert task — create it, or UPDATE the existing one whose content already starts
    with ``title_prefix`` (the dedup primitive: a retry or a re-run for the same day finds and
    updates it rather than posting a second one). Never raises.

    Three tries over about a minute on a transient failure (GAP-TODOIST-RETRY-1); any other
    failure (no token, a 4xx) is reported on the first try, because asking again cannot change
    the answer. ``client=None`` means delivery is switched off — reported, never a crash.
    """
    if client is None:
        return DeliveryOutcome(skipped="delivery switched off")

    tries = len(backoff) + 1
    project_id: Optional[str] = None
    created = False
    for attempt in range(1, tries + 1):
        try:
            if project_id is None:
                project_id, created = _project(client, project_name)
            existing = client.find_task(project_id, title_prefix=title_prefix)
            if existing is not None:
                client.update_task(existing, content=content, description=description)
                task_id = existing
            else:
                task_id = client.create_task(content=content, description=description,
                                             project_id=project_id)
        except Exception as exc:                         # a convenience, never the record
            if _is_transient(exc) and attempt < tries:
                wait = backoff[attempt - 1]
                _log.warning("todoist_client: delivery attempt %d of %d failed (%s); "
                             "retrying in %gs", attempt, tries, exc, wait)
                sleep(wait)
                continue
            _log.warning("todoist_client: delivery failed: %s", exc)
            spent = sum(backoff[:attempt - 1])
            after = f" (after {attempt} tries over {spent:g}s)" if attempt > 1 else ""
            return DeliveryOutcome(error=f"{exc}{after}", attempts=attempt)
        return DeliveryOutcome(sent=True, task_id=task_id, project_created=created,
                               attempts=attempt)
    raise AssertionError("unreachable")                  # pragma: no cover
