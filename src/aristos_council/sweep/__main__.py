"""``python -m aristos_council.sweep run`` - see ``docs/SWEEP.md``."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .sets import DEFAULT_PATH, SweepSetError, get_set

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT_ROOT = ROOT / "data" / "local" / "sweep"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m aristos_council.sweep")
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run a set of entries end to end")
    run.add_argument("--set", default="awkward", help="set name in data/sweep_sets.yaml (default awkward)")
    run.add_argument("--sets-file", type=Path, default=DEFAULT_PATH, help=argparse.SUPPRESS)
    run.add_argument("--judges", action=argparse.BooleanOptionalAction, default=True,
                     help="run the blind judge panel on company pages (default on; real model calls)")
    run.add_argument("--council", action=argparse.BooleanOptionalAction, default=False,
                     help="run the council opinion on every company page (default off; an entry "
                          "with council: true runs it regardless)")
    run.add_argument("--workers", type=int, default=3, help="company entries run this many at once")
    run.add_argument("--out", type=Path, default=None,
                     help="output folder (default data/local/sweep/<YYYY-MM-DD_HHMM>/)")
    return p


def main(argv=None, *, producers=None) -> int:
    args = build_parser().parse_args(argv)
    from .runner import exit_code, run_sweep, status_line

    try:
        sweep_set = get_set(args.set, args.sets_file)
    except SweepSetError as exc:
        print(f"sweep: {exc}", file=sys.stderr)
        return 2
    if producers is None:
        from .producers import PRODUCERS as producers
    out = args.out or DEFAULT_OUT_ROOT / datetime.now().strftime("%Y-%m-%d_%H%M")
    print(f"sweep '{sweep_set.name}': {len(sweep_set.entries)} entries -> {out}", flush=True)
    manifest, results = run_sweep(sweep_set, out, producers, judges=args.judges,
                                  council=args.council, workers=args.workers,
                                  say=lambda m: print(m, flush=True))
    t = manifest["totals"]
    print(f"\nstatus: {status_line(manifest)}")
    print(f"{t['ok']}/{t['entries']} ok, {t['findings']} finding(s), {t['model_calls']} model calls, "
          f"about ${t['approx_cost_usd']:.2f}\nsummary: {out / 'summary.md'}")
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
