"""Integrated pipeline CLI (Aristos v2) — rank a universe, then narrate the
shortlist with the LLM council.

STAGE 1 ranks the universe (free, deterministic) — the ranker verdict is the
verdict-of-record. STAGE 2 runs the LLM council ONLY on the shortlist (default: the
BUY quintile) as a NARRATOR (or an independent second opinion behind the flag). The
council bills API credits; the estimate is printed and requires --yes above the cap.

This script is a THIN WRAPPER: all orchestration lives in
``aristos_council.pipeline.run_rank_pipeline`` (the SAME entrypoint Council Station's
Universe Run tab calls) and the console report is ``format_cli_report`` of its result.

Usage:
    python examples/run_pipeline.py --file pool.txt --rank-strategy conservative_plus_v1 \
        --screen-strategy growth_v1 --council-runs-on buy_quintile --yes
    python examples/run_pipeline.py META MSFT GOOGL ... --council-mode narrator --yes
    python examples/run_pipeline.py --file pool.txt --ranker-only   # free, no LLM

Requires the runtime extras + keys (except --ranker-only). Do NOT launch from the
Claude Code dev env.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from aristos_council.cli_guards import force_utf8_stdout
from aristos_council.data.adapter import normalize_ticker
from aristos_council.data.cache import DEFAULT_CACHE_DIR, CachingAdapter
from aristos_council.data.provider import select_market_adapter
from aristos_council.pipeline import format_cli_report, run_rank_pipeline

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = ROOT / "strategies"
RUNS_DIR = ROOT / "runs"                  # frozen run records (ITEM 4)
SPEND_CAP_COUNCILS = 8   # require --yes above this many council runs


def _read_tickers(args) -> list[str]:
    raw = list(args.tickers)
    if args.file:
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                raw.extend(line.replace(",", " ").split())
    seen, out = set(), []
    for t in raw:
        nt = normalize_ticker(t)
        if nt not in seen:
            seen.add(nt)
            out.append(nt)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Rank -> council narrator pipeline.")
    force_utf8_stdout()          # the '⚠' divergence flag must not crash a cp1252 console
    p.add_argument("tickers", nargs="*")
    p.add_argument("--file")
    p.add_argument("--rank-strategy", default="conservative_plus_v1")
    p.add_argument("--screen-strategy", default=None,
                   help="council lens; defaults to the rank strategy's "
                        "council_screen_strategy (same philosophy)")
    p.add_argument("--council-runs-on", choices=["buy_quintile", "top_k", "all"],
                   default=None)
    p.add_argument("--council-mode", choices=["second_opinion", "narrator"],
                   default=None)
    p.add_argument("--csv")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--ranker-only", action="store_true",
                   help="STAGE 1 only — deterministic ranking, no LLM, no spend")
    p.add_argument("--replay", help="replay a frozen run record (runs/<run_id>) "
                                    "OFFLINE — no network; universe read from its manifest")
    p.add_argument("--no-freeze", action="store_true",
                   help="do not freeze this run's inputs to runs/")
    p.add_argument("--yes", action="store_true", help="approve the council spend")
    args = p.parse_args()

    today = date.today()
    if args.replay:
        import json
        manifest = json.loads(
            (RUNS_DIR / args.replay / "manifest.json").read_text(encoding="utf-8"))
        universe = manifest["tickers"]
    else:
        universe = _read_tickers(args)
    if not universe:
        p.error("no tickers given (positional, --file, or --replay)")

    # Replay serves a frozen record (no network). A live run builds the caching
    # adapter ONCE so the (free) sizing pass and the full run share a cache.
    common = dict(strategies_dir=STRATEGIES_DIR, today=today,
                  screen_strategy_id=args.screen_strategy,
                  council_runs_on=args.council_runs_on,
                  council_mode=args.council_mode)
    if args.replay:
        common["replay_run_id"] = args.replay
        common["freeze_dir"] = RUNS_DIR                  # locates the record
    else:
        adapter = select_market_adapter()
        if not args.no_cache:
            adapter = CachingAdapter(adapter, cache_dir=DEFAULT_CACHE_DIR, today=today)
        common["adapter"] = adapter

    # STAGE 1 sizing (free) up front so the spend can be gated before any council.
    sizing = run_rank_pipeline(universe, args.rank_strategy, ranker_only=True, **common)
    n_short = len(sizing.meta["shortlist"])
    if not args.ranker_only and n_short > SPEND_CAP_COUNCILS and not args.yes:
        p.error(f"{n_short} council runs (> {SPEND_CAP_COUNCILS}) — re-run with --yes "
                f"to approve ~${sizing.meta['est_cost']:.2f} of spend")
    # The ACTUAL post-screen shortlist cost, before any narrator spend (ITEM 4).
    if not args.ranker_only:
        print(f"  shortlist: {n_short} name(s) → ${sizing.meta['est_cost']:.2f} "
              "(actual, post-screen)")

    runners = None
    if not args.ranker_only:
        from aristos_council.agents.runners import production_runners
        runners = production_runners()

    # A live full run FREEZES its inputs (unless --no-freeze); replay does not.
    full_kw = dict(common)
    if not args.replay and not args.no_freeze:
        full_kw["freeze_dir"] = RUNS_DIR

    result = run_rank_pipeline(
        universe, args.rank_strategy, ranker_only=args.ranker_only,
        csv_path=args.csv, runners=runners, **full_kw)
    print(format_cli_report(result))
    if result.meta.get("run_id"):
        print(f"\n  run {'replayed' if args.replay else 'frozen'}: "
              f"{result.meta['run_id']}  (runs/{result.meta['run_id']})")
    if args.csv and not args.ranker_only and result.council_mode != "narrator":
        print(f"\n  agreement rows appended -> {args.csv}")


if __name__ == "__main__":
    main()
