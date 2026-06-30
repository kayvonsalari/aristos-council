"""Integrated pipeline CLI (Aristos v2) — rank a universe, then run the council on the
shortlist as an independent second opinion.

STAGE 1 ranks the universe (free, deterministic) — the ranker verdict is the
verdict-of-record. STAGE 2 runs the LLM council ONLY on the shortlist (default: the
BUY quintile) — analysis + a second opinion, with the ranker-vs-council agreement and
dissent notes surfaced. The council bills API credits; the estimate is printed and
requires --yes above the cap.

Usage:
    python examples/run_pipeline.py --file pool.txt --rank-strategy conservative_plus_v1 \
        --screen-strategy growth_v1 --council-runs-on buy_quintile --yes
    python examples/run_pipeline.py META MSFT GOOGL ... --council-mode narrator --yes

Requires the runtime extras + keys. Do NOT launch from the Claude Code dev env.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

from aristos_council.agents.runners import production_runners
from aristos_council.data.adapter import normalize_ticker
from aristos_council.data.cache import DEFAULT_CACHE_DIR, CachingAdapter
from aristos_council.data.provider import select_market_adapter
from aristos_council.pipeline import (
    agreement_csv_rows,
    agreement_table,
    run_pipeline,
)
from aristos_council.reproducibility import estimate_cost
from aristos_council.strategy.loader import load_strategy
from aristos_council.strategy.rank_loader import load_rank_strategy

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = ROOT / "strategies"
SPEND_CAP_COUNCILS = 8   # require --yes above this many council runs


def _resolve(arg: str) -> Path:
    return Path(arg) if arg.endswith((".yaml", ".yml")) else STRATEGIES_DIR / f"{arg}.yaml"


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
    p = argparse.ArgumentParser(description="Rank -> council second-opinion pipeline.")
    p.add_argument("tickers", nargs="*")
    p.add_argument("--file")
    p.add_argument("--rank-strategy", default="conservative_plus_v1")
    p.add_argument("--screen-strategy", default="growth_v1")
    p.add_argument("--council-runs-on", choices=["buy_quintile", "top_k", "all"],
                   default=None)
    p.add_argument("--council-mode", choices=["second_opinion", "narrator"],
                   default=None)
    p.add_argument("--csv")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--yes", action="store_true", help="approve the council spend")
    args = p.parse_args()

    universe = _read_tickers(args)
    if not universe:
        p.error("no tickers given (positional or --file)")
    rank_strategy = load_rank_strategy(_resolve(args.rank_strategy))
    screen_strategy = load_strategy(_resolve(args.screen_strategy))

    today = date.today()
    adapter = select_market_adapter()
    if not args.no_cache:
        adapter = CachingAdapter(adapter, cache_dir=DEFAULT_CACHE_DIR, today=today)

    # STAGE 1 (free) up front so the spend can be sized before any council runs.
    from aristos_council.pipeline import _rank_stage, _shortlist
    runs_on = args.council_runs_on or rank_strategy.council_runs_on
    ranked, _excl = _rank_stage(universe, rank_strategy, adapter, today=today)
    shortlist = _shortlist(ranked, runs_on, rank_strategy.k)
    est = estimate_cost(len(shortlist))
    print(f"(rank: {rank_strategy.id}; screen: {screen_strategy.id}; "
          f"mode: {args.council_mode or rank_strategy.council_mode}; "
          f"shortlist {len(shortlist)}/{len(universe)}; est ${est:.2f})")
    if len(shortlist) > SPEND_CAP_COUNCILS and not args.yes:
        p.error(f"{len(shortlist)} council runs (> {SPEND_CAP_COUNCILS}) — re-run "
                f"with --yes to approve ~${est:.2f} of spend")

    result = run_pipeline(
        universe=universe, rank_strategy=rank_strategy,
        screen_strategy=screen_strategy, adapter=adapter, runners=production_runners(),
        today=today, council_runs_on=args.council_runs_on,
        council_mode=args.council_mode)

    print(f"\n=== RANKED ({rank_strategy.id}) — verdict-of-record ===")
    for i, r in enumerate([r for r in result.ranked if not r.excluded], 1):
        print(f"  {i:>2}  {r.ticker:<10} {r.verdict.upper():<5} combined {r.combined_rank:>5.0f}")
    print("\n" + agreement_table(result))

    if args.csv:
        path = Path(args.csv)
        new = not path.exists()
        rows = agreement_csv_rows(result)
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else
                               ["ticker", "ranker_verdict", "council_verdict",
                                "agreement", "council_mode", "dissent_notes"])
            if new:
                w.writeheader()
            for row in rows:
                w.writerow(row)
        print(f"\n  agreement rows appended -> {args.csv}")


if __name__ == "__main__":
    main()
