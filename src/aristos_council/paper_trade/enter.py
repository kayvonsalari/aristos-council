"""PAPER-TRADE-1 — the ``enter`` command.

Target 09:32 ET. Reads today's Gap Ledger CSV (read-only), trades every IBKR-verified
candidate (gap up -> buy, gap down -> sell short), each a marketable limit sized to a fixed
USD 10,000, cancelled if unfilled after two minutes. ONE connection for the whole run
(connect, act on every name, disconnect) — never one per name, never held past this command's
own return.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Callable, Optional

from . import candidates as candidates_mod
from .config import (ENTER_DEADLINE, ENTER_POLL_SECONDS, ENTER_TARGET, LONG,
                     MARKETABLE_PAD, POSITION_USD, SHORT, UNFILLED_CANCEL_SECONDS,
                     now_ny, paths_for, stopped)
from .ibkr_paper import PaperIBKR
from .records import EntryAttempt, EnterRun, save_enter_run

_log = logging.getLogger(__name__)

KILL_SWITCH_REASON = "kill switch: data/local/paper_trade/STOP exists — nothing placed"


def _wait_until(target: datetime, *, now: Callable[[], datetime], sleep: Callable[[float], None],
                poll_seconds: float) -> None:
    while True:
        remaining = (target - now()).total_seconds()
        if remaining <= 0:
            return
        sleep(min(poll_seconds, remaining))


def run_enter(*, day: Optional[date] = None, root=None, gap_ledger_root=None, ibkr=None,
             now: Callable[[], datetime] = now_ny, sleep: Callable[[float], None] = None,
             wait_for_target: bool = True) -> EnterRun:
    """Run one day's entries. ``ibkr`` is an already-constructed ``PaperIBKR`` (or a fake with
    the same shape) — injected so tests never touch a real Gateway; when None a real one is
    built lazily, on first use, against the paper Gateway only.

    ``wait_for_target``/``now``/``sleep`` are seams for tests: a real run waits in real time
    for 09:32 ET (and, if the CSV is not there yet, polls up to 09:40 ET); a test supplies a
    fake clock and a no-op sleep so none of this blocks.
    """
    import time as _time
    sleep = sleep or _time.sleep
    day = day or now().date()
    paths = paths_for(day, root)
    run = EnterRun(date=day.isoformat())

    if stopped(root):
        run.day_skipped = True
        run.day_skip_reason = KILL_SWITCH_REASON
        save_enter_run(paths, run)
        return run

    target = now().replace(
        hour=ENTER_TARGET.hour, minute=ENTER_TARGET.minute, second=0, microsecond=0)
    deadline = now().replace(
        hour=ENTER_DEADLINE.hour, minute=ENTER_DEADLINE.minute, second=0, microsecond=0)
    if wait_for_target:
        _wait_until(target, now=now, sleep=sleep, poll_seconds=ENTER_POLL_SECONDS)

    path = candidates_mod.gap_ledger_csv_path(day, gap_ledger_root)
    seen_at: Optional[datetime] = now() if path.exists() else None
    while seen_at is None and now() < deadline:
        sleep(ENTER_POLL_SECONDS)
        if path.exists():
            seen_at = now()
    if seen_at is None:
        run.day_skipped = True
        run.day_skip_reason = (
            f"Gap Ledger's CSV for {day.isoformat()} never appeared by "
            f"{deadline.time().isoformat(timespec='minutes')} ET — skipped")
        save_enter_run(paths, run)
        return run
    run.csv_seen_at_et = seen_at.isoformat()

    today_candidates = candidates_mod.read_ibkr_candidates(day, gap_ledger_root)
    if not today_candidates:
        save_enter_run(paths, run)                     # a real, legitimate zero-candidate day
        return run

    client = ibkr if ibkr is not None else PaperIBKR()
    opened_here = ibkr is None
    try:
        client.connect()
        for candidate in today_candidates:
            run.attempts.append(_enter_one(client, candidate, day, now()))
    finally:
        if opened_here:
            client.disconnect()
    save_enter_run(paths, run)
    return run


def _enter_one(client: PaperIBKR, candidate, day: date, attempted_at: datetime) -> EntryAttempt:
    attempt = EntryAttempt(date=day.isoformat(), ticker=candidate.ticker,
                           company=candidate.company, direction=candidate.direction,
                           gap_pct=candidate.gap_pct,
                           attempted_at_et=attempted_at.isoformat())

    if candidate.direction == SHORT:
        reading = client.shortable_for(candidate.ticker)
        attempt.shortable, attempt.shortable_shares = reading.shortable, reading.shares
        if reading.shortable is False:
            attempt.skipped, attempt.skip_reason = True, "not shortable (real-world)"
            return attempt
        if reading.shortable is None:
            attempt.skipped = True
            attempt.skip_reason = "shortable flag undetermined — IBKR did not answer in time"
            return attempt

    quote = client.quote_for(candidate.ticker)
    attempt.quote_bid, attempt.quote_ask = quote.bid, quote.ask
    attempt.quote_data_type = quote.data_type
    reference = quote.ask if candidate.direction == LONG else quote.bid
    if reference is None:
        attempt.skipped, attempt.skip_reason = True, "no usable quote (bid/ask unavailable)"
        return attempt

    shares = int(POSITION_USD // reference)
    if shares < 1:
        attempt.skipped = True
        attempt.skip_reason = (f"one share (${reference:,.2f}) costs more than the "
                               f"${POSITION_USD:,.0f} position size")
        return attempt
    attempt.shares = shares

    pad = 1 + MARKETABLE_PAD if candidate.direction == LONG else 1 - MARKETABLE_PAD
    limit_price = reference * pad
    action = "BUY" if candidate.direction == LONG else "SELL"
    order_ref = f"papertrade-entry-{day.isoformat()}-{candidate.ticker}"
    attempt.limit_price, attempt.order_ref = limit_price, order_ref

    trade = client.place_marketable_limit(candidate.ticker, action=action, shares=shares,
                                          limit_price=limit_price, order_ref=order_ref)
    result = client.wait_for_fill(trade, timeout_seconds=UNFILLED_CANCEL_SECONDS)
    if result.filled:
        attempt.filled = True
        attempt.fill_price = result.fill_price
        attempt.fill_time_et = result.fill_time.isoformat() if result.fill_time else ""
    else:
        client.cancel(trade)
        attempt.filled = False
        attempt.skip_reason = "not filled"
    return attempt
