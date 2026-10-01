"""PAPER-TRADE-1 CLI.

    python -m aristos_council.paper_trade enter    # target 09:32 ET
    python -m aristos_council.paper_trade exit     # target 15:40 ET
    python -m aristos_council.paper_trade record   # after 16:10 ET
    python -m aristos_council.paper_trade report   # any time

Each subcommand connects to the PAPER Gateway (port 4002 by default), acts, and
disconnects — see the package docstring for the guards (port, account id, kill switch).
``--now`` (every subcommand) lets a dry run or a backfill pretend it is a different moment;
without it, real wall-clock New York time is used and ``enter``/``exit`` really do wait for
their target time before doing anything.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from .config import now_ny


def _parse_day(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def _parse_now(text: str | None):
    if not text:
        return now_ny
    from .config import NY
    fixed = datetime.fromisoformat(text)
    if fixed.tzinfo is None:
        fixed = fixed.replace(tzinfo=NY)
    return lambda: fixed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m aristos_council.paper_trade",
        description="Paper-trades Gap Ledger's daily candidates in the IBKR PAPER account "
                    "(port 4002 only) to measure real-world execution. Gap Ledger itself is "
                    "read-only here and never modified.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("enter", "exit", "record", "report"):
        p = sub.add_parser(name)
        p.add_argument("--day", default=None, help="YYYY-MM-DD (default: today, NY time)")
        p.add_argument("--root", default=None, help="output root (default data/local/paper_trade)")
        if name in ("enter", "exit"):
            p.add_argument("--no-wait", action="store_true",
                           help="act immediately instead of waiting for the target NY time "
                                "(manual/backfill use)")
        if name == "report":
            p.add_argument("--gap-ledger-root", default=None,
                           help="Gap Ledger's own CSV root (default data/local/gap_ledger)")

    args = parser.parse_args(argv)
    day = _parse_day(args.day)

    if args.cmd == "enter":
        from .enter import run_enter
        run = run_enter(day=day, root=args.root, wait_for_target=not args.no_wait)
        print(f"enter {run.date}: {len(run.attempts)} candidate(s) attempted"
             + (f" — day skipped: {run.day_skip_reason}" if run.day_skipped else ""))
        for a in run.attempts:
            status = ("filled" if a.filled else
                     ("skipped: " + a.skip_reason if a.skipped else "not filled"))
            print(f"  {a.ticker} ({a.direction}): {status}")
        return 0

    if args.cmd == "exit":
        from .exit import run_exit
        run = run_exit(day=day, root=args.root, wait_for_target=not args.no_wait)
        print(f"exit {run.date}: {len(run.exits)} position(s)"
             + (f" — day skipped: {run.day_skip_reason}" if run.day_skipped else ""))
        for e in run.exits:
            print(f"  {e.ticker}: {'MOC submitted' if e.moc_submitted else 'FAILED: ' + e.error}")
        return 0

    if args.cmd == "record":
        from .record import run_record
        rows = run_record(day=day, root=args.root)
        if rows is None:
            print(f"record: no enter run found for "
                 f"{(day or now_ny().date()).isoformat()} — nothing to record")
            return 1
        print(f"record: wrote {len(rows)} row(s)")
        return 0

    if args.cmd == "report":
        from .report import format_report
        print(format_report(args.root, args.gap_ledger_root))
        return 0

    return 1                                                # pragma: no cover - argparse guards this


if __name__ == "__main__":
    sys.exit(main())
