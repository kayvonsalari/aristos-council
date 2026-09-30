"""GAP-LEDGER-1 step 5b — the morning's list, delivered to Todoist.

One task per run with candidates, in the project ``Gap Ledger``. Nothing at all on an empty
day: a daily "no candidates" task trains you to ignore the project, which defeats the only
purpose delivery has.

SCOUT-HOLDINGS-401: the generic wire protocol (the v1 REST client, its retry/backoff
classification, and the find-or-create-project / find-or-update-task alert primitive) moved to
``aristos_council.todoist_client`` so the scout job's own failure alert could reuse it without
importing Gap Ledger (GAP-LEDGER-1's boundary: no other Aristos surface imports or links to it).
This module re-exports every name it used to define itself, so nothing else in Gap Ledger or its
tests changed — a pure move, not a behaviour change.

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
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from ..todoist_client import (BASE_URL, DELIVERY_BACKOFF, PAGE_LIMIT, DeliveryOutcome,
                              RestTodoist, TodoistClient, TodoistTransient, TodoistUnavailable,
                              _is_transient, _results)
from ..todoist_client import deliver_alert as _generic_deliver_alert
from .ledger import GROUP_CANDIDATE, LedgerRow

_log = logging.getLogger(__name__)

PROJECT_NAME = "Gap Ledger"

__all__ = ["BASE_URL", "PAGE_LIMIT", "DELIVERY_BACKOFF", "PROJECT_NAME", "TodoistUnavailable",
          "TodoistTransient", "TodoistClient", "RestTodoist", "DeliveryOutcome", "task_title",
          "task_body", "find_task_for_day", "deliver", "deliver_alert"]


# --------------------------------------------------------------------------- #
# the message — pure, so what gets sent is testable without a network
# --------------------------------------------------------------------------- #
def task_title(day: date, candidates: Sequence[LedgerRow], *,
              backfilled_on: Optional[date] = None) -> str:
    """The one-line title: the date, the count, and the names.

    GAP-BACKFILL-1 — every title for a given ``day`` STARTS WITH ``f"Gap Ledger {day}"``
    regardless of ``backfilled_on``, which is the whole of ``find_task_for_day``'s dedup
    mechanism (7c): a live task, a gateway-down alert, and a backfilled task for the same day
    are all found by the same prefix search, so a later post updates whichever came before it
    instead of creating a second one.
    """
    names = ", ".join(row.ticker for row in candidates)
    base = f"Gap Ledger {day.isoformat()}"
    if backfilled_on is not None:
        base += f" (backfilled on {backfilled_on.isoformat()})"
    return f"{base} — {len(candidates)} name(s): {names}"


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


def task_body(candidates: Sequence[LedgerRow], *, backfilled_on: Optional[date] = None) -> str:
    """One block per name: gap, relative volume, the flags, and the headline link.

    Markdown, which Todoist renders in a task description. Every figure comes from the
    row — this function computes nothing.
    """
    blocks: list[str] = []
    if backfilled_on is not None:
        # GAP-BACKFILL-1 3 — said in the FIRST line, not buried: a backfilled day lacks a
        # spread reading (a book is a snapshot of now) and its news matching runs after the
        # fact, which is weaker than live's same-morning attribution.
        blocks.append(
            f"_Backfilled on {backfilled_on.isoformat()}. No spread reading (a book cannot be "
            f"read retroactively); news matching is after-the-fact and weaker than live. See "
            f"docs/GAP_LEDGER.md, Catch-up and backfill section._")
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
# the step
# --------------------------------------------------------------------------- #
def find_task_for_day(client: TodoistClient, project_id: str, day: date) -> Optional[str]:
    """GAP-BACKFILL-1 7c — the existing task for ``day``, in whichever of its three possible
    forms it was last posted as (a live list, a gateway-down alert, an earlier backfill)."""
    return client.find_task(project_id, title_prefix=f"Gap Ledger {day.isoformat()}")


def deliver(day: date, rows: Sequence[LedgerRow], *,
            client: Optional[TodoistClient], sleep=time.sleep,
            backoff: Sequence[float] = DELIVERY_BACKOFF,
            backfilled_on: Optional[date] = None) -> DeliveryOutcome:
    """Send the day's candidates as one task. Never raises.

    GAP-TODOIST-RETRY-1: a transient failure is asked again after each wait in ``backoff`` (three
    tries in all by default, over about a minute) before it is reported as "NOT sent". Only the
    step that failed is repeated - a project found or created stays found - and any other failure
    is reported on the first try, because waiting cannot change it. The run stays non-fatal
    whatever happens here: the CSV is the record.

    ``backfilled_on`` (GAP-BACKFILL-1): set on a backfill delivery. It changes the title/body
    (see ``task_title``/``task_body``) AND the create-vs-update decision — a backfill looks for
    an existing task for ``day`` first (7c) and UPDATES it rather than posting a second one. A
    live delivery (``backfilled_on=None``) always creates, exactly as before this change.
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
                if project_id is None:
                    project_id, created = client.create_project(PROJECT_NAME), True
            content = task_title(day, candidates, backfilled_on=backfilled_on)
            description = task_body(candidates, backfilled_on=backfilled_on)
            existing = (find_task_for_day(client, project_id, day)
                       if backfilled_on is not None else None)
            if existing is not None:
                client.update_task(existing, content=content, description=description)
                task_id = existing
            else:
                task_id = client.create_task(content=content, description=description,
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


def deliver_alert(day: date, message: str, *, client: Optional[TodoistClient],
                  sleep=time.sleep,
                  backoff: Sequence[float] = DELIVERY_BACKOFF) -> DeliveryOutcome:
    """GAP-BACKFILL-1 7a — one alert task with no candidate rows behind it (e.g. "Gap Ledger
    <date> NOT RUN: IB Gateway unreachable"). Same dedup as a backfilled day's task (7c): if
    ``day`` already has a task under any title, this UPDATES it rather than posting a second
    one — including the reverse case, where a later successful backfill finds and upgrades
    this very alert into the day's real candidate list. Never raises; never retried beyond
    ``backoff``, same as ``deliver``.

    A thin wrapper over the generic ``todoist_client.deliver_alert`` (SCOUT-HOLDINGS-401):
    Gap Ledger's own dedup prefix (``f"Gap Ledger {day}"``, unchanged) and project name.
    """
    return _generic_deliver_alert(project_name=PROJECT_NAME, title_prefix=f"Gap Ledger {day.isoformat()}",
                                  content=message, description="", client=client, sleep=sleep,
                                  backoff=backoff)
