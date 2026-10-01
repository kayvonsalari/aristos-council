"""PAPER-TRADE-1 — the ``exit`` command.

Target 15:40 ET — comfortably ahead of NYSE's 15:50 MOC cutoff (no cancel/reduce after
15:45 ET). Submits a market-on-close order for every position ``enter`` actually filled
today; never waits for the fill (the close print does not exist yet) — ``record`` reads
the fill back afterward from IBKR's own execution history.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime
from typing import Callable, Optional

from .config import EXIT_TARGET, ENTER_POLL_SECONDS, LONG, now_ny, paths_for, stopped
from .enter import _wait_until
from .ibkr_paper import PaperIBKR
from .records import ExitAttempt, ExitRun, load_enter_run, save_exit_run

KILL_SWITCH_REASON = "kill switch: data/local/paper_trade/STOP exists — nothing placed"
NO_ENTRIES_REASON = "no entries were recorded for this day (enter was skipped or never ran)"


def run_exit(*, day: Optional[date] = None, root=None, ibkr=None,
            now: Callable[[], datetime] = now_ny, sleep: Callable[[float], None] = None,
            wait_for_target: bool = True) -> ExitRun:
    sleep = sleep or _time.sleep
    day = day or now().date()
    paths = paths_for(day, root)
    run = ExitRun(date=day.isoformat())

    if stopped(root):
        run.day_skipped, run.day_skip_reason = True, KILL_SWITCH_REASON
        save_exit_run(paths, run)
        return run

    enter_run = load_enter_run(paths)
    if enter_run is None or enter_run.day_skipped:
        run.day_skipped, run.day_skip_reason = True, NO_ENTRIES_REASON
        save_exit_run(paths, run)
        return run

    open_positions = [a for a in enter_run.attempts if a.filled]
    if not open_positions:
        save_exit_run(paths, run)                       # nothing filled today; legitimate
        return run

    target = now().replace(hour=EXIT_TARGET.hour, minute=EXIT_TARGET.minute, second=0,
                           microsecond=0)
    if wait_for_target:
        _wait_until(target, now=now, sleep=sleep, poll_seconds=ENTER_POLL_SECONDS)

    client = ibkr if ibkr is not None else PaperIBKR()
    opened_here = ibkr is None
    try:
        client.connect()
        for position in open_positions:
            run.exits.append(_exit_one(client, position, day, now()))
    finally:
        if opened_here:
            client.disconnect()
    save_exit_run(paths, run)
    return run


def _exit_one(client: PaperIBKR, position, day: date, submitted_at: datetime) -> ExitAttempt:
    order_ref = f"papertrade-exit-{day.isoformat()}-{position.ticker}"
    attempt = ExitAttempt(date=day.isoformat(), ticker=position.ticker,
                          direction=position.direction, shares=position.shares,
                          submitted_at_et=submitted_at.isoformat(), order_ref=order_ref)
    closing_action = "SELL" if position.direction == LONG else "BUY"
    try:
        trade = client.place_moc(position.ticker, action=closing_action,
                                 shares=position.shares, order_ref=order_ref)
    except Exception as exc:                              # noqa: BLE001 - reported, not raised
        attempt.moc_submitted, attempt.error = False, str(exc)
        return attempt
    if trade is None:
        attempt.moc_submitted, attempt.error = False, "could not qualify contract"
        return attempt
    attempt.moc_submitted = True
    return attempt
