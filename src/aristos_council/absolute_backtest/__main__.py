"""python -m aristos_council.absolute_backtest run|status|summarise"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import BACKTEST_DIR, SURVIVORSHIP


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m aristos_council.absolute_backtest",
                                description="ABSOLUTE-BACKTEST-1: ten-year backtest of the absolute method, three forms. "
                                            "No model is called.")
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run every unfinished industry (resumes; one industry at a time)")
    run.add_argument("--budget", type=int, default=None, help="EODHD units this invocation may spend; stops cleanly")
    run.add_argument("--only", nargs="*", default=None, help="industry slugs to run")
    run.add_argument("--end", type=date.fromisoformat, default=None, help="window end (default: last full month end)")
    run.add_argument("--max-rounds", type=int, default=None, help="smoke test: only the first N monthly rounds")
    run.add_argument("--out", default=BACKTEST_DIR)
    run.add_argument("--cohorts-root", default=None)
    run.add_argument("--force", action="store_true", help="redo industries that are already finished")
    run.add_argument("--random-baskets", type=int, default=500)
    st = sub.add_parser("status", help="progress: finished industries, units spent, what is running")
    st.add_argument("--out", default=BACKTEST_DIR)
    st.add_argument("--cohorts-root", default=None)
    sm = sub.add_parser("summarise", help="write SUMMARY.csv and COUNTS.csv from the finished industries")
    sm.add_argument("--out", default=BACKTEST_DIR)
    return p


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    except Exception:                                                  # noqa: BLE001
        pass


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                                  # noqa: BLE001
        pass
    from . import runner, summarise
    if args.cmd == "status":
        print("\n".join(runner.status_lines(args.out, args.cohorts_root)))
        return 0
    if args.cmd == "summarise":
        built = summarise.build(args.out)
        print(SURVIVORSHIP)
        if not built["overall"]:
            print(f"no finished industries under {args.out}")
            return 0
        print("\n".join(summarise.text_table(built["overall"])))
        for f, names in built["passes"].items():
            print(f"form {f} passes the bar (12-month hold) in {len(names)}: {', '.join(names) or 'none'}")
        return 0
    _load_env()
    print(SURVIVORSHIP, flush=True)
    res = runner.run_all(out_root=args.out, end=args.end, budget=args.budget, only=args.only, force=args.force,
                         max_rounds=args.max_rounds, cohorts_root=args.cohorts_root,
                         random_baskets=args.random_baskets, progress=lambda m: print(m, flush=True))
    print(f"ran {len(res['ran'])}, already finished {len(res['skipped'])}, units spent {res['units']}.")
    if res.get("failed"):
        print(f"failed (will be retried on the next run): {', '.join(res['failed'])}")
    if res["stopped"]:
        print(res["stopped"])
    print("\n".join(runner.status_lines(args.out, args.cohorts_root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
