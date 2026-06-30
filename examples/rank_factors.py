"""Rank-based multi-factor screening CLI (Aristos v2) — rank a universe, no LLM.

Computes the proven factors (value/quality/momentum/low-vol) from deterministic data
for each ticker, ranks the universe (Greenblatt / van Vliet-Blitz combine-the-ranks),
and assigns BUY/HOLD/SELL by a quintile (or top-k) cut. NO point weights — the
ranking is the decision. Run the full council on the BUY shortlist for the narrative.

Usage:
    python examples/rank_factors.py META MSFT GOOGL LLY NVO ... --strategy magic_formula_v1
    python examples/rank_factors.py --file pool.txt --strategy magic_formula_v1 --cut top_k --k 5
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

from aristos_council.data.adapter import normalize_ticker
from aristos_council.data.cache import DEFAULT_CACHE_DIR, CachingAdapter
from aristos_council.data.provider import select_market_adapter
from aristos_council.factors import (
    compute_factors,
    gather_factor_inputs,
    is_payout_uncovered,
    is_sector_excluded,
    screen_prefilter_fail,
)
from aristos_council.strategy.loader import load_strategy
from aristos_council.rank_engine import FactorSpec, RankedTicker, rank_universe
from aristos_council.strategy.rank_loader import load_rank_strategy

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = ROOT / "strategies"


def _resolve(arg: str) -> Path:
    if arg.endswith((".yaml", ".yml")):
        return Path(arg)
    return STRATEGIES_DIR / f"{arg}.yaml"


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
    p = argparse.ArgumentParser(description="Rank a universe on proven factors (no LLM).")
    p.add_argument("tickers", nargs="*")
    p.add_argument("--file")
    p.add_argument("--strategy", default="magic_formula_v1")
    p.add_argument("--cut", choices=["quintile", "top_k", "top_percentile"], default=None)
    p.add_argument("--k", type=int, default=None)
    p.add_argument("--csv")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args()

    tickers = _read_tickers(args)
    if not tickers:
        p.error("no tickers given (positional or --file)")
    strat = load_rank_strategy(_resolve(args.strategy))
    cut = args.cut or strat.cut
    k = args.k or strat.k

    today = date.today()
    adapter = select_market_adapter()
    if not args.no_cache:
        adapter = CachingAdapter(adapter, cache_dir=DEFAULT_CACHE_DIR, today=today,
                                 refresh=args.refresh)
    factor_names = [f.name for f in strat.factors]
    # Screen-as-prefilter: rank only names passing the council screen's floors.
    prefilter_criteria = None
    if strat.prefilter_screen and strat.council_screen_strategy:
        prefilter_criteria = load_strategy(
            STRATEGIES_DIR / f"{strat.council_screen_strategy}.yaml").criteria
    print(f"(rank strategy: {strat.id}; provider: {adapter.name}; "
          f"factors: {', '.join(factor_names)}; cut: {cut}"
          + (f"; prefilter: {strat.council_screen_strategy}" if prefilter_criteria else "")
          + ")")

    rows: list[tuple[str, dict]] = []
    excluded_cap: list[str] = []
    excluded_sector: list[tuple[str, str]] = []
    excluded_payout: list[tuple[str, float]] = []
    excluded_screen: list[tuple[str, str]] = []
    for t in tickers:
        fi = gather_factor_inputs(adapter, t, today=today)
        f = fi.fundamentals
        if (strat.min_market_cap is not None and f is not None
                and f.market_cap is not None
                and f.market_cap < strat.min_market_cap):
            excluded_cap.append(t)
            continue
        if f is not None and is_sector_excluded(f.sector, strat.exclude_sectors):
            excluded_sector.append((t, f.sector))   # e.g. ROIC invalid for banks
            continue
        if f is not None and is_payout_uncovered(f.payout_ratio, strat.max_payout_ratio):
            excluded_payout.append((t, f.payout_ratio))   # uncovered dividend = trap
            continue
        if prefilter_criteria is not None:
            reason = screen_prefilter_fail(prefilter_criteria, fi)
            if reason is not None:
                excluded_screen.append((t, reason))
                continue
        rows.append((t, compute_factors(fi, factor_names)))
        print(f"  computed {t}")

    ranked = rank_universe(
        rows,
        [FactorSpec(f.name, f.direction, f.missing) for f in strat.factors],
        cut=cut, k=k, percentile=strat.percentile, missing=strat.missing)

    print(f"\n=== RANKED ({strat.id}, universe {sum(1 for r in ranked if not r.excluded)}) ===")
    for i, r in enumerate([r for r in ranked if not r.excluded], 1):
        print(f"  {i:>2}  {r.ticker:<10} {r.verdict.upper():<5} "
              f"combined {r.combined_rank:>5.0f}   "
              + "  ".join(f"{f}:{rk:.0f}" for f, rk in r.factor_ranks.items()))
    drop = [r for r in ranked if r.excluded]
    if drop or excluded_cap or excluded_sector or excluded_payout or excluded_screen:
        print("\n  Excluded:")
        for t in excluded_cap:
            print(f"      {t:<10} below min market cap")
        for t, sec in excluded_sector:
            print(f"      {t:<10} sector excluded ({sec})")
        for t, pr in excluded_payout:
            print(f"      {t:<10} payout uncovered ({pr:.0%} > {strat.max_payout_ratio:.0%})")
        for t, reason in excluded_screen:
            print(f"      {t:<10} {reason}")
        for r in drop:
            print(f"      {r.ticker:<10} {r.reason}")

    if args.csv:
        path = Path(args.csv)
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["ticker", "verdict", "combined_rank", "excluded", "reason"])
            for r in ranked:
                w.writerow([r.ticker, r.verdict,
                            "" if r.excluded else round(r.combined_rank, 1),
                            r.excluded, r.reason])
        print(f"\n  rows appended -> {args.csv}")


if __name__ == "__main__":
    main()
