"""GAP-STATUS-1 — one short Todoist status task for every weekday run, including the ones that fail.

Found 2026-10-06: Todoist had no Gap Ledger task for 09-30, 10-01, 10-02 and 10-05, and nothing
said why. ``todoist.deliver`` posts only when there are candidates ("a daily 'no candidates' task
trains you to ignore the project"), so a run that hung, crashed or never started looked exactly
like a quiet day. This module is the other half of that bargain: a run that tells you NOTHING is
now impossible to mistake for a run that found nothing, because EVERY run leaves one line in
the same project, titled ``Gap Ledger STATUS <date>`` (a different prefix from the candidate list's
``Gap Ledger <date>``, so neither task can overwrite or hide the other).

It is called by ``scripts/run_gap_ledger_timed.ps1`` AFTER the screen process has ended - however
it ended. That is deliberate: a process that hangs or is killed cannot report on itself. The
wrapper owns the watchdog and this module turns "what happened" into text. Delivery and logging
only: it READS the ledger and the run log and decides nothing about screening or scoring.

A run that cannot start at all (machine off or asleep) posts nothing, by definition; the NEXT run's
status lists every NYSE day since the last ledger file or status that has neither (``missed_days``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from .ledger import DEFAULT_ROOT, GROUP_CANDIDATE, GROUP_STATUS, ledger_days, ledger_path, read_day
from .nyse_calendar import is_trading_day, trading_days_between
from .todoist import PROJECT_NAME, TodoistClient
from ..todoist_client import DeliveryOutcome
from ..todoist_client import deliver_alert as _deliver

STATE_FILE = "data/local/gap_ledger_status.json"
TITLE_PREFIX = "Gap Ledger STATUS"
IBKR_UNAVAILABLE_TEXT = "IBKR UNAVAILABLE"
REASON_LINES = 3                      # how much of the log's tail a failure quotes


def title_prefix(day: date) -> str:
    return f"{TITLE_PREFIX} {day.isoformat()}"


# --------------------------------------------------------------------------- #
# what happened - read off the ledger and this run's slice of the log
# --------------------------------------------------------------------------- #
@dataclass
class RunFacts:
    day: date
    exit_code: Optional[int] = None
    timed_out: bool = False
    timeout_minutes: Optional[float] = None
    log_text: str = ""                                 # ONLY this run's part of the log
    explicit_reason: str = ""
    missed: list = field(default_factory=list)         # list[date]


def log_slice(path: str | Path, offset: int) -> str:
    """The part of the run log written since ``offset`` bytes - this run's, not the history.
    Decoded as UTF-8 with replacement; the old UTF-16 tail the previous wrapper left in the file
    is stripped of its NULs so it stays readable."""
    try:
        data = Path(path).read_bytes()[max(0, offset):]
    except OSError:
        return ""
    return data.replace(b"\x00", b"").decode("utf-8", errors="replace")


def _last_lines(text: str, n: int = REASON_LINES) -> list:
    return [line.strip() for line in text.splitlines() if line.strip()][-n:]


def ledger_facts(day: date, root: str | Path = DEFAULT_ROOT) -> Optional[dict]:
    """None when no ledger file exists for ``day``; otherwise the candidate count, how many were
    IBKR-verified, and whether the gateway banner was on the day."""
    if not ledger_path(day, root).exists():
        return None
    rows = read_day(day, root)
    cands = [r for r in rows if r.group == GROUP_CANDIDATE]
    stub = [r for r in rows if r.group == GROUP_STATUS]
    return {"candidates": len(cands),
            "verified": sum(1 for r in cands if r.source == "ibkr"),
            "banner": any(r.ibkr_note for r in rows),
            "stub_reason": next((r.status_reason for r in stub if r.status_reason), "")}


def missed_days(today: date, *, root: str | Path = DEFAULT_ROOT,
                last_status_day: Optional[date] = None) -> list:
    """NYSE days strictly between the newest thing we know happened (a ledger file or a status
    task) and ``today`` that have no ledger file. No anchor at all -> []: nothing to measure from."""
    anchors = [d for d in ledger_days(root) if d < today]
    if last_status_day is not None and last_status_day < today:
        anchors.append(last_status_day)
    if not anchors:
        return []
    start = max(anchors)
    return [d for d in trading_days_between(start, today)
            if d != start and d < today and not ledger_path(d, root).exists()]


def describe(facts: RunFacts, *, root: str | Path = DEFAULT_ROOT) -> tuple[str, str, bool]:
    """(title, description, ran). Pure apart from reading the ledger. ``ran`` is True when the
    day's screen completed and wrote its record."""
    day = facts.day
    led = ledger_facts(day, root)
    text = facts.log_text
    gateway_down = "IB Gateway unreachable after" in text or (
        led is not None and led["stub_reason"] and "ibkr" in led["stub_reason"].lower())
    ran = (led is not None and not gateway_down and not facts.timed_out
           and (facts.exit_code in (0, None)))

    if not is_trading_day(day) and led is None and facts.exit_code in (0, None) \
            and not facts.timed_out:
        head, ibkr_text, detail = "NYSE CLOSED", "", "market closed today, nothing to screen"
    elif ran:
        ibkr_down = bool(led["banner"]) or IBKR_UNAVAILABLE_TEXT in text
        n, v = led["candidates"], led["verified"]
        if ibkr_down:
            ibkr_text = IBKR_UNAVAILABLE_TEXT
        elif n == 0:
            ibkr_text = "IBKR reached" if "IBKR reached a verdict" in text else "IBKR not reported"
        else:
            ibkr_text = f"IBKR verified {v} of {n}"
        head, detail = "RAN", f"{n} candidate(s) found"
    else:
        ibkr_text = IBKR_UNAVAILABLE_TEXT if gateway_down else "IBKR not reached"
        if facts.timed_out:
            why = (f"killed after {facts.timeout_minutes:g} min without finishing"
                   if facts.timeout_minutes else "killed without finishing")
        elif gateway_down:
            why = "IB Gateway unreachable after retrying"
        elif facts.exit_code not in (0, None):
            why = f"process exited with code {facts.exit_code}"
        else:
            why = "process ended without writing the day's ledger file"
        if facts.explicit_reason:
            why += f" ({facts.explicit_reason})"
        head, detail = "FAILED", f"no candidates recorded - {why}"

    title = f"{title_prefix(day)} - {head}: {detail}" + (f"; {ibkr_text}" if ibkr_text else "")
    lines = [f"Date: {day.isoformat()}",
             f"Result: {head}",
             f"Candidates: {led['candidates'] if led else 'none recorded'}",
             f"IBKR: {ibkr_text or 'n/a'}"]
    if head == "FAILED":
        lines.append(f"Reason: {detail.split(' - ', 1)[-1]}")
        tail = _last_lines(text)
        if tail:
            lines.append("Last log lines:\n" + "\n".join(f"    {t[:300]}" for t in tail))
    if facts.missed:
        lines.append("Missed (no run recorded, no ledger file): "
                     + ", ".join(d.isoformat() for d in facts.missed)
                     + " - the machine was off or asleep, or the run could not start.")
        title += f" | MISSED {len(facts.missed)} day(s): " + ", ".join(
            d.strftime("%m-%d") for d in facts.missed)
    lines.append("Log: data/local/gap_ledger_run.log")
    return title, "\n".join(lines), ran


# --------------------------------------------------------------------------- #
# state + delivery
# --------------------------------------------------------------------------- #
def read_state(path: str | Path = STATE_FILE) -> Optional[date]:
    try:
        return date.fromisoformat(json.loads(Path(path).read_text("utf-8"))["last_status_day"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_state(day: date, path: str | Path = STATE_FILE) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"last_status_day": day.isoformat()}), "utf-8")


def post_status(facts: RunFacts, *, client: Optional[TodoistClient],
                root: str | Path = DEFAULT_ROOT, state_path: str | Path = STATE_FILE,
                sleep=None) -> tuple[str, DeliveryOutcome]:
    """Build and send the day's status task; remember the day only if it was delivered, so a
    Todoist outage is reported as missed days by the next status instead of being forgotten.
    Never raises."""
    facts.missed = missed_days(facts.day, root=root, last_status_day=read_state(state_path))
    title, body, _ran = describe(facts, root=root)
    kwargs = {"sleep": sleep} if sleep is not None else {}
    outcome = _deliver(project_name=PROJECT_NAME, title_prefix=title_prefix(facts.day),
                       content=title, description=body, client=client, **kwargs)
    if outcome.sent:
        write_state(facts.day, state_path)
    return title, outcome
