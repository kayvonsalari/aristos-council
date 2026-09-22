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

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol, Sequence

from .ledger import GROUP_CANDIDATE, LedgerRow

_log = logging.getLogger(__name__)

BASE_URL = "https://api.todoist.com/rest/v2"
PROJECT_NAME = "Gap Ledger"


class TodoistUnavailable(RuntimeError):
    """No token, or the API refused in a way that leaves nothing to deliver to."""


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


def task_body(candidates: Sequence[LedgerRow]) -> str:
    """One block per name: gap, relative volume, the flags, and the headline link.

    Markdown, which Todoist renders in a task description. Every figure comes from the
    row — this function computes nothing.
    """
    blocks: list[str] = []
    for row in candidates:
        flags = [row.spread_note, row.news_found]
        # A candidate whose relative volume could not be read was selected on its GAP ALONE,
        # and the task has to say so — this is the one place the owner reads in the morning.
        volume = (f"rel. pre-market volume {_times(row.relative_volume)}"
                  if row.relative_volume is not None
                  else f"rel. pre-market volume UNAVAILABLE ({row.relative_volume_note})")
        line = (f"**{row.ticker}** — gap {_pct(row.gap_pct)}, {volume}"
                + (f" · {' · '.join(f for f in flags if f)}" if any(flags) else ""))
        parts = [line]
        if row.reason:
            parts.append(f"  {row.reason}")
        if row.news_link:
            headline = row.headline or row.news_link
            where = f" ({row.news_source})" if row.news_source else ""
            parts.append(f"  [{headline}]({row.news_link}){where}")
        blocks.append("\n".join(parts))
    blocks.append("_Screened by maths; no recommendation. Logged in "
                  "data/local/gap_ledger/._")
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# the client
# --------------------------------------------------------------------------- #
class TodoistClient(Protocol):
    def find_project(self, name: str) -> Optional[str]:
        """The project id, or None when no project has that name."""

    def create_project(self, name: str) -> str:
        """Create it and return the new id."""

    def create_task(self, *, content: str, description: str, project_id: str) -> str:
        """Create the task and return its id."""


class RestTodoist:
    """Todoist REST v2 over ``urllib``, matching the repo's other outbound calls."""

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

    def _call(self, path: str, *, payload: Optional[dict] = None):
        request = urllib.request.Request(
            f"{BASE_URL}/{path}",
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._require_token()}",
                     "Content-Type": "application/json"},
            method="GET" if payload is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise TodoistUnavailable(
                f"Todoist refused {request.method} /{path}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TodoistUnavailable(f"Todoist unreachable: {exc}") from exc
        return json.loads(body) if body.strip() else {}

    def find_project(self, name: str) -> Optional[str]:
        wanted = name.strip().lower()
        for project in self._call("projects") or []:
            if isinstance(project, dict) and str(project.get("name", "")).strip().lower() == wanted:
                return str(project.get("id"))
        return None

    def create_project(self, name: str) -> str:
        return str((self._call("projects", payload={"name": name}) or {}).get("id"))

    def create_task(self, *, content: str, description: str, project_id: str) -> str:
        payload = {"content": content, "description": description,
                   "project_id": project_id}
        return str((self._call("tasks", payload=payload) or {}).get("id"))


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

    def sentence(self) -> str:
        if self.error:
            return f"Todoist: NOT sent — {self.error}"
        if self.skipped:
            return f"Todoist: not sent — {self.skipped}"
        made = " (project created)" if self.project_created else ""
        return f"Todoist: task {self.task_id} created in '{PROJECT_NAME}'{made}."


def deliver(day: date, rows: Sequence[LedgerRow], *,
            client: Optional[TodoistClient]) -> DeliveryOutcome:
    """Send the day's candidates as one task. Never raises."""
    candidates = [r for r in rows if r.group == GROUP_CANDIDATE]
    if not candidates:
        return DeliveryOutcome(skipped="no candidates today")
    if client is None:
        return DeliveryOutcome(skipped="delivery switched off")
    try:
        project_id = client.find_project(PROJECT_NAME)
        created = project_id is None
        if project_id is None:
            project_id = client.create_project(PROJECT_NAME)
        task_id = client.create_task(content=task_title(day, candidates),
                                     description=task_body(candidates),
                                     project_id=project_id)
    except Exception as exc:                             # a convenience, never the record
        _log.warning("gap_ledger: Todoist delivery failed: %s", exc)
        return DeliveryOutcome(error=str(exc))
    return DeliveryOutcome(sent=True, task_id=task_id, project_created=created)
