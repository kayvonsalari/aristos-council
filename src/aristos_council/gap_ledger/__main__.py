"""GAP-LEDGER-1 CLI — ``python -m aristos_council.gap_ledger <run|outcomes|score>``.

Spelled to match the package the way ``aristos_council.cohorts`` and
``aristos_council.market_index`` are: this is the only import path that exists, so it is the
only command spelled here.

Three verbs, in the order a day uses them:

``run``       the pre-market screen. Writes the day's CSV, optionally posts to Todoist.
``outcomes``  after the close, fill the four readings for a day (or every unfilled day).
``score``     the whole record, candidates against the control group.

The two network-shaped switches are OFF-by-default in the direction that costs money:
``--explain`` must be asked for, and ``--no-news`` / ``--no-todoist`` are there to turn the
charged and the outward-facing steps off.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

from .. import market_index
from .bars import YFinanceBars
from .ibkr import IBKRBars, IBKRUnavailable
from .config import DEFAULT_CONFIG, DEFAULT_ROOT, DEFAULT_RUN_TIME, GapConfig, at_ny, now_ny
from .explain import build_runner
from .ledger import ledger_days, read_all, read_day
from .news import EODHDNews
from .outcomes import SessionNotClosed, fill_day, is_complete
from .run import format_report, run_screen
from .score import score
from .todoist import RestTodoist
from .universe import (UniverseUnavailable, names_from_index, pool_from_index,
                       read_ticker_file)


def _say(message: str) -> None:
    """Print, on a console that may not be able to spell what we want to say.

    Windows defaults to cp1252 and dies on an em dash. Losing a character is acceptable;
    losing the run because of a character is not. (Lifted from ``cohorts.__main__``, which
    learned it the same way.)
    """
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", "") or "ascii"
        print(message.encode(encoding, "replace").decode(encoding, "replace"), flush=True)


def _parse_time(text: str) -> time:
    try:
        return datetime.strptime(text.strip(), "%H:%M").time()
    except ValueError as exc:
        raise SystemExit(f"error: --at wants HH:MM in New York time, got {text!r}") from exc


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError as exc:
        raise SystemExit(f"error: --date wants YYYY-MM-DD, got {text!r}") from exc


def _config(args) -> GapConfig:
    """The thresholds for this run. Only the pacing knobs are exposed on the command line —
    the screen's thresholds live in ``config.py`` so a day's record cannot be quietly
    rescreened at a different bar from the shell."""
    return GapConfig(chunk_size=args.chunk_size,
                     chunk_pause_seconds=args.chunk_pause)


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def cmd_run(args) -> int:
    config = _config(args)
    day = _parse_date(args.date) if args.date else now_ny().date()
    run_at = at_ny(day, _parse_time(args.at) if args.at else DEFAULT_RUN_TIME)

    try:
        if args.tickers:
            pool = read_ticker_file(args.tickers)
            pool_source = f"{args.tickers} ({len(pool)} tickers)"
        else:
            pool = pool_from_index(config_path=args.index_config)
            pool_source = f"the market index at {args.index_config}"
    except UniverseUnavailable as exc:
        _say(f"error: {exc}")
        return 2
    if args.limit:
        pool = pool[:args.limit]
        pool_source += f", first {len(pool)} by ticker (--limit)"

    # The index's company names, for the news matcher (GAP-NEWS-MATCH-1). Read even for a
    # --tickers run: if a listed name happens to be in the index, its name helps; if the
    # index is absent this is empty and the matcher falls back to ticker + primary symbol.
    company_names = names_from_index(config_path=args.index_config)

    bars = YFinanceBars(config)
    # GAP-IBKR-1 — built here and handed in, so the run entry stays factory-free. An
    # unreachable gateway is NOT fatal: the adapter connects lazily, so construction cannot
    # fail on a missing gateway, and the run reports the banner when a request does.
    ibkr = None if args.no_ibkr else IBKRBars()
    news_source = None if args.no_news else EODHDNews()
    runner = build_runner() if args.explain else None
    client = None if args.no_todoist else RestTodoist()

    try:
        result = run_screen(pool=pool, pool_source=pool_source, daily=bars, intraday=bars,
                            day=day, run_at=run_at, config=config, root=args.root,
                            news_source=news_source, company_names=company_names,
                            ibkr=ibkr, explain_runner=runner, todoist=client,
                            write=not args.dry_run, refresh=args.refresh, progress=_say)
    finally:
        # Hang up whatever happened. A gateway session left open blocks the client id for
        # the next run, and ``disconnect`` never raises.
        if ibkr is not None:
            ibkr.disconnect()
    _say("")
    _say(format_report(result))
    return 0


# --------------------------------------------------------------------------- #
# outcomes
# --------------------------------------------------------------------------- #
def _unfilled_days(root: str) -> list[date]:
    """Every logged day holding at least one row without all four readings."""
    return [day for day in ledger_days(root)
            if any(not is_complete(row) for row in read_day(day, root))]


def cmd_outcomes(args) -> int:
    days = ([_parse_date(args.date)] if args.date else _unfilled_days(args.root))
    if not days:
        _say(f"Nothing to fill: every logged day under {args.root} already carries all "
             f"four readings (or no day is logged yet).")
        return 0
    bars = YFinanceBars(_config(args))
    failures = 0
    for day in days:
        try:
            report = fill_day(day, daily=bars, intraday=bars, root=args.root)
        except SessionNotClosed as exc:
            _say(f"{day}: skipped — {exc}")
            failures += 1
            continue
        _say(report.sentence())
        for note in report.notes:
            _say(f"  note: {note}")
    return 1 if failures and len(days) == failures else 0


# --------------------------------------------------------------------------- #
# score
# --------------------------------------------------------------------------- #
def cmd_score(args) -> int:
    days = read_all(args.root)
    if not days:
        _say(f"No days logged under {args.root} yet — run the screen first.")
        return 0
    card = score(days, config=_config(args))
    _say(f"Gap Ledger scorecard — {args.root}")
    _say("")
    for line in card.lines():
        _say(line)
    return 0


# --------------------------------------------------------------------------- #
# the parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m aristos_council.gap_ledger",
        description="Gap Ledger — a pre-market news-movers screener that keeps score of "
                    "itself. Maths picks the names; no recommendations are made.")
    parser.add_argument("--root", default=DEFAULT_ROOT,
                        help=f"where the day's CSVs live (default: {DEFAULT_ROOT})")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CONFIG.chunk_size,
                        help="tickers per provider request")
    parser.add_argument("--chunk-pause", type=float,
                        default=DEFAULT_CONFIG.chunk_pause_seconds,
                        help="seconds to wait between requests")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="the pre-market screen")
    p_run.add_argument("--date", help="the market date to screen (default: today, ET)")
    p_run.add_argument("--at", help="the run time in New York time, HH:MM "
                                    f"(default: {DEFAULT_RUN_TIME.strftime('%H:%M')})")
    p_run.add_argument("--tickers", help="screen this file (one ticker per line) instead "
                                         "of the market index")
    p_run.add_argument("--index-config", default=str(market_index.DEFAULT_CONFIG),
                       help="where to read the market index location from")
    p_run.add_argument("--limit", type=int, default=0,
                       help="screen only the first N names of the pool (for a smoke test)")
    p_run.add_argument("--explain", action="store_true",
                       help="one cheap LLM call for one plain-English line per name "
                            "(off by default; bills API credits)")
    p_run.add_argument("--no-news", action="store_true",
                       help="skip EODHD news; every name is marked 'news not fetched'")
    p_run.add_argument("--no-todoist", action="store_true",
                       help="do not create a Todoist task")
    p_run.add_argument("--no-ibkr", action="store_true",
                       help="skip Interactive Brokers verification; screen on yfinance "
                            "prices with the tape-density trust tests (the report says so)")
    p_run.add_argument("--refresh", action="store_true",
                       help="ignore the cached daily bars and refetch them")
    p_run.add_argument("--dry-run", action="store_true",
                       help="screen and print, but write no CSV")
    p_run.set_defaults(func=cmd_run)

    p_out = sub.add_parser("outcomes", help="after the close, fill open/10:00/11:30/close")
    p_out.add_argument("--date", help="one market date (default: every unfilled day)")
    p_out.set_defaults(func=cmd_outcomes)

    p_score = sub.add_parser("score", help="candidates vs control group, over all days")
    p_score.set_defaults(func=cmd_score)
    return parser


def _load_env() -> None:
    """Pick up EODHD_API_KEY / TODOIST_API_TOKEN / ANTHROPIC_API_KEY from the local .env,
    the way ``app.py`` and ``cohorts.__main__`` do at start.

    Guarded: python-dotenv is a ``ui`` extra and the keys may already be exported. Under
    pytest this is a no-op by design — TEST-ISOLATION-1 silences ``load_dotenv`` for the
    whole suite, so a test can never screen against the owner's real key.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    except Exception:
        pass


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:                                          # UTF-8 where the console allows it
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    _load_env()
    return args.func(args)


if __name__ == "__main__":                        # pragma: no cover
    raise SystemExit(main())
