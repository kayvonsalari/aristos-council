"""PAPER-TRADE-1 — the ``record`` command.

Runs after 16:10 ET, once MOC fills have had time to settle. Reads back ``enter``'s and
``exit``'s own JSON state (never re-places an order), pulls IBKR's own execution history for
the day (the one source of truth for the EXIT fill price/time, which ``exit`` never waits to
see, and for both legs' commissions), fetches the official 09:30 open per name, and writes
ONE human-facing file: ``data/local/paper_trade/YYYY-MM-DD.csv``.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from .config import NY, RECORD_AFTER, now_ny, paths_for
from .ibkr_paper import PaperIBKR
from .records import (EntryAttempt, entry_cost_vs_open_pct, gross_result, load_enter_run,
                      load_exit_run, net_result)

FIELDS = (
    "date", "ticker", "company", "direction", "skipped", "skip_reason",
    "official_open", "reference_price_at_order", "quote_data_type",
    "shares", "entry_fill_price", "entry_fill_time_et", "entry_cost_vs_open_pct",
    "entry_commission", "exit_fill_price", "exit_fill_time_et", "exit_commission",
    "gross_result", "net_result",
)


@dataclass
class ExecutionReading:
    price: Optional[float] = None
    time_et: str = ""
    commission: Optional[float] = None


def _executions_by_order_ref(client: PaperIBKR, day: date) -> dict:
    """{order_ref: ExecutionReading} — the LAST fill per order ref (a MOC or marketable
    limit fills in one execution in the overwhelming common case; if IBKR splits it into
    several, the last one's price is what the position actually closed/opened at and the
    commissions are summed)."""
    out: dict = {}
    for fill in client.executions_for(day):
        ref = getattr(fill.execution, "orderRef", "") or ""
        if not ref:
            continue
        reading = out.setdefault(ref, ExecutionReading())
        reading.price = fill.execution.price
        raw_time = fill.execution.time
        if raw_time is not None:
            aware = raw_time if raw_time.tzinfo else raw_time.replace(tzinfo=NY)
            reading.time_et = aware.astimezone(NY).isoformat()
        commission = getattr(fill.commissionReport, "commission", None)
        if commission is not None:
            reading.commission = (reading.commission or 0.0) + float(commission)
    return out


def _row_for_attempt(attempt: EntryAttempt, *, official_open: Optional[float],
                     executions: dict, exit_order_ref: str) -> dict:
    entry_exec = executions.get(attempt.order_ref) if attempt.order_ref else None
    exit_exec = executions.get(exit_order_ref) if exit_order_ref else None

    entry_fill = attempt.fill_price if attempt.filled else None
    entry_time = attempt.fill_time_et if attempt.filled else ""
    entry_commission = entry_exec.commission if entry_exec else None
    exit_fill = exit_exec.price if exit_exec else None
    exit_time = exit_exec.time_et if exit_exec else ""
    exit_commission = exit_exec.commission if exit_exec else None

    cost_pct = entry_cost_vs_open_pct(attempt.direction, entry_fill, official_open)
    gross = (gross_result(attempt.direction, entry_fill, exit_fill, attempt.shares)
            if attempt.filled else None)
    net = net_result(gross, entry_commission, exit_commission)

    reference_price = (attempt.quote_ask if attempt.direction == "long" else attempt.quote_bid)
    return {
        "date": attempt.date, "ticker": attempt.ticker, "company": attempt.company,
        "direction": attempt.direction, "skipped": "true" if attempt.skipped else "false",
        "skip_reason": attempt.skip_reason, "official_open": official_open,
        "reference_price_at_order": reference_price,
        "quote_data_type": attempt.quote_data_type, "shares": attempt.shares,
        "entry_fill_price": entry_fill, "entry_fill_time_et": entry_time,
        "entry_cost_vs_open_pct": cost_pct, "entry_commission": entry_commission,
        "exit_fill_price": exit_fill, "exit_fill_time_et": exit_time,
        "exit_commission": exit_commission, "gross_result": gross, "net_result": net,
    }


def run_record(*, day: Optional[date] = None, root=None, ibkr=None,
               now=now_ny) -> Optional[list]:
    """Returns the rows written, or None when there was nothing to record (no ``enter`` run
    exists for the day at all — never written as an empty file, which would be
    indistinguishable from "recorded, zero candidates")."""
    day = day or now().date()
    paths = paths_for(day, root)
    enter_run = load_enter_run(paths)
    if enter_run is None:
        return None
    exit_run = load_exit_run(paths)
    exit_by_ticker = {e.ticker: e for e in (exit_run.exits if exit_run else [])}

    tickers_needing_open = {a.ticker for a in enter_run.attempts}
    executions: dict = {}
    opens: dict = {}
    if tickers_needing_open:
        client = ibkr if ibkr is not None else PaperIBKR()
        opened_here = ibkr is None
        try:
            client.connect()
            executions = _executions_by_order_ref(client, day)
            for ticker in sorted(tickers_needing_open):
                opens[ticker] = client.official_open(ticker, day)
        finally:
            if opened_here:
                client.disconnect()

    rows = []
    for attempt in enter_run.attempts:
        exit_attempt = exit_by_ticker.get(attempt.ticker)
        exit_order_ref = exit_attempt.order_ref if exit_attempt else ""
        rows.append(_row_for_attempt(attempt, official_open=opens.get(attempt.ticker),
                                     executions=executions, exit_order_ref=exit_order_ref))

    paths.root.mkdir(parents=True, exist_ok=True)
    with paths.record_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(FIELDS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return rows
