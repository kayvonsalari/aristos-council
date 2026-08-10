"""Run NARR-INDEP-TEST for real — the 27 billed narrations (Colab, NOT Claude Code).

Requires:  pip install -e ".[yfinance,llm]"  and ANTHROPIC_API_KEY set.

Per this repo's CLAUDE.md environment rule, ANTHROPIC_API_KEY must NEVER be set in the
Claude Code dev environment; live/billed runs happen in Colab (or any environment you
control) with the key in your own secrets. This script makes real API calls the moment
you run it — read the cost line it prints before confirming.

Usage:
    python experiments/narr_indep_test/run_experiment.py [--reps N] [--out-dir DIR]
                                                          [--dry-run] [--yes]

    --reps N       repetitions per condition (default 3, matching the pre-registered
                   protocol; changing it is a deviation from the frozen protocol and
                   should be noted in the write-up if used for a real run)
    --out-dir DIR  where raw narration JSON + the manifest are written (default:
                   experiments/narr_indep_test/runs/<UTC timestamp>/)
    --dry-run      build every condition and print the cost estimate, make NO API calls
    --yes          skip the interactive confirmation prompt (for non-interactive/CI use)

Colab quickstart (paste into cells):
    !git clone https://github.com/<you>/aristos-council.git
    %cd aristos-council
    !pip install -e ".[yfinance,llm]"
    import os
    os.environ["ANTHROPIC_API_KEY"] = "..."   # from Colab secrets, never hardcoded
    !python experiments/narr_indep_test/run_experiment.py
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from aristos_council.agents.runners import production_runners
from aristos_council.reproducibility import cost_guard_line

from . import analysis, runner
from .conditions import REPS, all_conditions

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = Path(__file__).resolve().parent / "runs"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_experiment.py",
        description="NARR-INDEP-TEST: 27 narrations testing narrator independence "
                    "(VERDICT-BEND, ABLATION, CORRUPTION). Makes REAL, BILLED API calls.")
    p.add_argument("--reps", type=int, default=REPS,
                   help=f"repetitions per condition (protocol default: {REPS})")
    p.add_argument("--out-dir", type=Path, default=None,
                   help="output directory (default: runs/<UTC timestamp>/ under this "
                        "package)")
    p.add_argument("--dry-run", action="store_true",
                   help="build conditions and print the cost estimate; make NO API calls")
    p.add_argument("--yes", action="store_true",
                   help="skip the interactive confirmation prompt")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    conditions = all_conditions()
    n_narrations = len(conditions) * args.reps

    print(f"NARR-INDEP-TEST: {len(conditions)} conditions x {args.reps} reps = "
         f"{n_narrations} narrations")
    for c in conditions:
        print(f"  {c.condition_id:32s} [{c.experiment:10s}] {c.note}")
    print()
    print(cost_guard_line(n_narrations))
    print("(cost model: reproducibility._COST_PER_RUN_USD, one 'run' = one full council "
         "invocation — 4 specialists + critic + decision, at this project's default "
         "tiered models. See agents/runners.py to check/override ARISTOS_MODEL_*.)")

    if args.dry_run:
        print("\n--dry-run: no API calls made.")
        return

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\nANTHROPIC_API_KEY is not set in this environment. Per this repo's "
             "CLAUDE.md, that key must NEVER be set in Claude Code — set it here only "
             "if you are running this in Colab (or another environment you control) "
             "with your own billed key.", file=sys.stderr)
        sys.exit(1)

    if not args.yes:
        reply = input(f"\nThis will make {n_narrations} REAL, BILLED narrations. "
                      f"Continue? [y/N] ").strip().lower()
        if reply != "y":
            print("Aborted — no API calls made.")
            return

    out_dir = args.out_dir or (RUNS_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    manifest_path = out_dir / "manifest.jsonl"
    print(f"\nwriting raw output -> {out_dir}")
    print(f"writing manifest -> {manifest_path}\n")

    runners = production_runners()

    def _progress(msg: str) -> None:
        print(msg, flush=True)

    runner.run_all(conditions, args.reps, runners, out_dir=out_dir,
                   manifest_path=manifest_path, progress=_progress)

    raw = analysis.load_raw_outputs(out_dir)
    n_failed = n_narrations - len(raw)
    print(f"\n{len(raw)}/{n_narrations} narrations saved ok"
         + (f" ({n_failed} failed — see the manifest)" if n_failed else ""))

    report = analysis.full_report(raw)
    print("\n=== Experiment A (VERDICT-BEND):", report["experiment_a"]["verdict"], "===")
    for fund, row in report["experiment_a"]["per_fund"].items():
        print(f"  {fund}: {'PASS' if row['pass'] else 'FAIL'} "
             f"(median value-changed {row['median_value_changed']}, "
             f"median presence-diff {row['median_presence']})")
    print("\n=== Experiment B1 (ABLATION):", report["experiment_b1"]["verdict"], "===")
    invented = [r for r in report["experiment_b1"]["per_narration"] if r["count"] > 0]
    print(f"  {len(invented)} narration(s) with invented quantitative claims")
    print("\n=== Experiment B2 (CORRUPTION):", report["experiment_b2"]["verdict"], "===")
    for kind, row in report["experiment_b2"]["by_kind"].items():
        print(f"  {kind}: stamp rate {row['stamp_rate']} "
             f"({row['stamped_count']}/{row['narrated_count']} narrated corruptions caught)")

    print(f"\nRaw outputs + manifest are the experiment data: {out_dir}")
    print("Next: docs/experiments/NARR-INDEP-TEST.md — append the results tables, "
         "verbatim excerpts, and a verdict per experiment.")


if __name__ == "__main__":
    main()
