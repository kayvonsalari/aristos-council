"""GAP-LEDGER-1 step 5b — the morning's list, delivered to Todoist.

One task per run with candidates, in the project ``Gap Ledger``. Nothing at all on an empty
day: a daily "no candidates" task trains you to ignore the project, which defeats the only
purpose delivery has.

The token comes from ``TODOIST_API_TOKEN`` in the local environment (the repo's ``.env``,
which is gitignored) — nothing personal is written here, and no project id or task id is
committed.

Two deliberate behaviours worth saying out loud:

* **The project is created when it does not exist.** The alternative is a run that screens
  correctly and then fails at the last step because of a project nobody made yet. The
  outcome says which happened, so a created project is visible rather than a surprise.
* **A delivery failure never fails the run.** The CSV is the record; Todoist is a
  convenience. ``deliver`` returns an outcome carrying the error instead of raising, and
  the CLI prints it — an unsent task is reported, never silent.
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

from .ledger import GROUP_CANDIDATE, LedgerRow

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
#     exist" and would silently create a SECOND "Gap Ledger" every morning;
#   * ids are opaque STRINGS now, not numbers — so nothing here coerces them to int.
#
# CREATE endpoints still answer a bare object (``POST /projects`` -> a project,
# ``POST /tasks`` -> a task), which is why only the list path needs unwrapping.
BASE_URL = "https://api.todoist.com/api/v1"
# Page size for the project listing. The API caps it at 200; the cursor loop below means
# the cap is a pacing choice and not a correctness one.
PAGE_LIMIT = 200
PROJECT_NAME = "Gap Ledger"


class TodoistUnavailable(RuntimeError):
    """No token, or the API refused in a way that leaves nothing to deliver to."""


class TodoistTransient(TodoistUnavailable):
    """A failure of the MOMENT, worth asking again: a connection reset or dropped, a timeout, a
    5xx or a 429. GAP-TODOIST-RETRY-1. Everything else (no token, a 4xx such as the retired API's
    410) is a fact about the request and is reported at once, because asking again cannot change
    the answer."""


# GAP-TODOIST-RETRY-1 - three tries over about a minute: now, +20s, +60s. Found on 2026-09-25, when
# the scheduled run screened and logged correctly and then lost its one Todoist request to
# "[WinError 10054] An existing connection was forcibly closed by the remote host", and was not
# asked again. That error is a plain OSError (ConnectionResetError) raised while READING the reply,
# which ``urllib`` does not wrap in URLError, so it was neither classified nor retried.
DELIVERY_BACKOFF = (20.0, 40.0)


# --------------------------------------------------------------------------- #
# the message — pure, so what gets sent is testable without a network
# --------------------------------------------------------------------------- #
def task_title(day: date, candidates: Sequence[LedgerRow]) -> str:
    """The one-line title: the date, the count, and the names."""
    names = ", ".join(row.ticker for row in candidates)
    return f"Gap Ledger {day.isoformat()} — {len(candidates)} name(s): {names}"


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:+.1f}%"


def _times(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.1f}x"


def _short_spread(note: str) -> str:
    """The part of the spread mark that is about this NAME.

    GAP-REPORT-CLARITY-1 (b): "spread unknown — IBKR market-data subscription does not cover
    API streaming quotes" explains the account, not the stock. Everything after the dash is
    dropped here and said once in the footer.
    """
    head = (note or "").split(" — ")[0].strip()
    return "spread n/a" if head in ("", "spread unknown") else head


def task_body(candidates: Sequence[LedgerRow]) -> str:
    """One block per name: gap, relative volume, the flags, and the headline link.

    Markdown, which Todoist renders in a task description. Every figure comes from the
    row — this function computes nothing.
    """
    blocks: list[str] = []
    for row in candidates:
        # GAP-REPORT-CLARITY-1 (b) — the row keeps only what is about the row. The
        # subscription explanation is a fact about the ACCOUNT and is said once at the end,
        # not seventeen times down the task.
        flags = [_short_spread(row.spread_note), row.news_found]
        # A candidate whose relative volume could not be read was selected on its GAP ALONE,
        # and the task has to say so — this is the one place the owner reads in the morning.
        volume = (f"rel. pre-market volume {_times(row.relative_volume)}"
                  if row.relative_volume is not None
                  else f"rel. pre-market volume UNAVAILABLE ({row.relative_volume_note})")
        # Which provider verified this name is the first thing worth knowing about it.
        badge = "IBKR-verified" if row.source == "ibkr" else "yfinance only"
        # The company name beside the ticker: this is what the owner reads at 06:00.
        name = f" ({row.company})" if row.company else ""
        line = (f"**{row.ticker}**{name} [{badge}] — gap {_pct(row.gap_pct)}, {volume}"
                + (f" · {' · '.join(f for f in flags if f)}" if any(flags) else ""))
        parts = [line]
        if row.reason:
            parts.append(f"  {row.reason}")
        if row.news_link:
            headline = row.headline or row.news_link
            where = f" ({row.news_source})" if row.news_source else ""
            parts.append(f"  [{headline}]({row.news_link}){where}")
        blocks.append("\n".join(parts))
    # GAP-IBKR-1 item 3 — the banner belongs here too: the task is what the owner reads in
    # the morning, and a yfinance-only list is a different product from a verified one.
    note = next((row.ibkr_note for row in candidates if row.ibkr_note), "")
    if note:
        blocks.append(f"**{note}** — relative volume was not measured; these names come "
                      f"from yfinance prices with the tape-density trust tests.")
    # GAP-REPORT-CLARITY-1 (b) — the subscription explanation, once, at the end.
    subscription = next((row.spread_note for row in candidates
                         if " — " in (row.spread_note or "")), "")
    if subscription:
        blocks.append(f"_{subscription}._")
    blocks.append("_Screened by maths; no recommendation. Logged in "
                  "data/local/gap_ledger/._")
    return "\n\n".join(blocks)


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


class RestTodoist:
    """Todoist API **v1** over ``urllib``, matching the repo's other outbound calls.

    The class name is kept so the CLI and any saved habit still work; what changed is the
    wire protocol, not the seam. The ``TodoistClient`` Protocol above is untouched.
    """

    def __init__(self, token: Optional[str] = None, *, timeout: float = 15.0) -> None:
        self._token = token
        self.timeout = timeout

    def _require_token(self) -> str:
        token = self._token or os.environ.get("TODOIST_API_TOKEN", "")
        if not token.strip():
            raise TodoistUnavailable(
                "TODOIST_API_TOKEN is not set — put it in the local .env, or run with "
                "`--no-todoist`.")
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

        Paging matters for correctness, not tidiness: a "Gap Ledger" project sitting on
        page two would read as absent, and ``deliver`` would helpfully create a second one
        every single morning.
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

        Name matching is the contract because the id is not something the owner knows or
        can put in a config — the project already exists in their account, made by hand.
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
        # request rather than a second task; the title carries the date and the names, so it is
        # the same across the tries of one delivery and different from any other day's.
        request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"gap-ledger|{project_id}|{content}"))
        created = self._call("tasks", payload=payload, request_id=request_id)
        return str((created or {}).get("id"))


# --------------------------------------------------------------------------- #
# the step
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DeliveryOutcome:
    """What delivery did. ``sent`` False with an empty ``error`` means there was nothing
    to send, which is the intended behaviour on a day with no candidates."""

    sent: bool = False
    task_id: str = ""
    project_created: bool = False
    error: str = ""
    skipped: str = ""
    attempts: int = 1                 # GAP-TODOIST-RETRY-1: how many tries it took (or was given)

    def sentence(self) -> str:
        if self.error:
            return f"Todoist: NOT sent — {self.error}"
        if self.skipped:
            return f"Todoist: not sent — {self.skipped}"
        made = " (project created)" if self.project_created else ""
        retried = f" (after {self.attempts} tries)" if self.attempts > 1 else ""
        return f"Todoist: task {self.task_id} created in '{PROJECT_NAME}'{made}{retried}."


def _is_transient(exc: BaseException) -> bool:
    """A failure worth asking again about. A raw ``OSError`` counts as well as our own class: a
    client that lets a connection reset escape unwrapped is exactly the case that was missed."""
    return isinstance(exc, (TodoistTransient, OSError, http.client.HTTPException))


def deliver(day: date, rows: Sequence[LedgerRow], *,
            client: Optional[TodoistClient], sleep=time.sleep,
            backoff: Sequence[float] = DELIVERY_BACKOFF) -> DeliveryOutcome:
    """Send the day's candidates as one task. Never raises.

    GAP-TODOIST-RETRY-1: a transient failure is asked again after each wait in ``backoff`` (three
    tries in all by default, over about a minute) before it is reported as "NOT sent". Only the
    step that failed is repeated - a project found or created stays found - and any other failure
    is reported on the first try, because waiting cannot change it. The run stays non-fatal
    whatever happens here: the CSV is the record.
    """
    candidates = [r for r in rows if r.group == GROUP_CANDIDATE]
    if not candidates:
        return DeliveryOutcome(skipped="no candidates today")
    if client is None:
        return DeliveryOutcome(skipped="delivery switched off")

    tries = len(backoff) + 1
    project_id: Optional[str] = None
    created = False
    for attempt in range(1, tries + 1):
        try:
            if project_id is None:
                project_id = client.find_project(PROJECT_NAME)
                created = project_id is None
                if project_id is None:
                    project_id = client.create_project(PROJECT_NAME)
            task_id = client.create_task(content=task_title(day, candidates),
                                         description=task_body(candidates),
                                         project_id=project_id)
        except Exception as exc:                         # a convenience, never the record
            if _is_transient(exc) and attempt < tries:
                wait = backoff[attempt - 1]
                _log.warning("gap_ledger: Todoist delivery attempt %d of %d failed (%s); "
                             "retrying in %gs", attempt, tries, exc, wait)
                sleep(wait)
                continue
            _log.warning("gap_ledger: Todoist delivery failed: %s", exc)
            spent = sum(backoff[:attempt - 1])
            after = f" (after {attempt} tries over {spent:g}s)" if attempt > 1 else ""
            return DeliveryOutcome(error=f"{exc}{after}", attempts=attempt)
        return DeliveryOutcome(sent=True, task_id=task_id, project_created=created,
                               attempts=attempt)
    raise AssertionError("unreachable")                  # pragma: no cover
