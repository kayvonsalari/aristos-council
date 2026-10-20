"""JUDGE-PANEL-1 - read ``judge_summary.csv`` back: per ticker, every dated panel result next to the
verdict of record at that moment. No prices, no scoring - the 12-month scoring is a later job and
this is only the table it will be scored from."""
from __future__ import annotations

import csv
from collections import defaultdict
from typing import Optional

from .log import FAMILY_COLUMNS, SUMMARY_NAME, judges_dir


def read_summary(directory=None) -> list[dict]:
    path = judges_dir(directory) / SUMMARY_NAME
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def format_report(rows: list, *, ticker: Optional[str] = None) -> str:
    if ticker:
        rows = [r for r in rows if r.get("ticker", "").upper() == ticker.upper()]
    if not rows:
        return ("No judge panel results recorded yet." if not ticker
                else f"No judge panel results recorded for {ticker}.")
    by_ticker: dict = defaultdict(list)
    for r in rows:
        by_ticker[r.get("ticker", "?")].append(r)
    out = []
    for name in sorted(by_ticker):
        entries = sorted(by_ticker[name], key=lambda r: r.get("run_date", ""))
        out.append(f"{name}  ({len(entries)} panel{'s' if len(entries) != 1 else ''})")
        for r in entries:
            judges = "  ".join(
                f"{label.capitalize()}: {r.get(f'{label}_verdict') or '-'}"
                + (f" ({r.get(f'{label}_confidence')})" if r.get(f"{label}_confidence") else "")
                for _, label in FAMILY_COLUMNS)
            out.append(f"  {r.get('run_date', '')[:16].replace('T', ' ')}  "
                       f"record: {r.get('verdict_of_record') or '-'} [{r.get('lens_split', '')}]")
            out.append(f"      panel: {judges}")
            out.append(f"      {r.get('panel_split', '')} -> {r.get('panel_agreement', '')}"
                       f"   (facts {r.get('pack_hash', '')})")
        out.append("")
    return "\n".join(out).rstrip()


def main(argv=None) -> int:
    import argparse

    from ..cli_guards import force_utf8_stdout

    parser = argparse.ArgumentParser(
        prog="python -m aristos_council.judges",
        description="JUDGE-PANEL-1: read the blind judge panel's log back.")
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report", help="every dated panel result per ticker, beside the verdict of "
                                        "record at that moment (reads judge_summary.csv)")
    rep.add_argument("--dir", default=None, help="the folder holding judge_summary.csv "
                                                 "(default data/local/judges)")
    rep.add_argument("--ticker", default=None, help="only this ticker")
    sc = sub.add_parser("score", help="score every logged verdict (each judge's and the lenses' record) "
                                      "against its cohort over 3, 6 and 12 months; writes score.csv. "
                                      "Read-only analysis: it changes nothing on the page")
    sc.add_argument("--dir", default=None)
    sc.add_argument("--today", default=None, help="score as of this date (YYYY-MM-DD); default today")
    args = parser.parse_args(argv)
    force_utf8_stdout()
    if args.command == "report":
        print(format_report(read_summary(args.dir), ticker=args.ticker))
    if args.command == "score":
        from datetime import date

        from .score import run_score
        today = date.fromisoformat(args.today) if args.today else None
        _scored, text, path = run_score(args.dir, today=today)
        print(text)
        if path:
            print(f"\nWritten to {path}")
    return 0
