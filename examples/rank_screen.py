"""Fast SCREEN-ONLY ranking CLI — shortlist a pool of tickers WITHOUT the LLM council.

For each ticker: fetch market data (the SAME adapter the council uses, respecting
ARISTOS_MARKET_PROVIDER), run the deterministic screen, and compute the SCREEN-ONLY
matrix score (the real matrix weights, with specialist stances excluded). Sorts the
pool by score descending so the top N are the picks — SECONDS per name instead of
MINUTES. Then run the FULL council (run_council.py) only on the chosen finalists for
the narrative report and the small specialist-stance adjustment.

Usage:
    python examples/rank_screen.py META MSFT GOOGL ADBE --strategy growth_v1
    python examples/rank_screen.py --file pool.txt --strategy growth_v1 --csv rank.csv
    python examples/rank_screen.py NVDA --strategy growth_v1 --refresh   # ignore cache

Requires the data extras + keys; this does NOT call any LLM.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

from aristos_council.data.adapter import normalize_ticker
from aristos_council.data.cache import DEFAULT_CACHE_DIR, CachingAdapter
from aristos_council.data.provider import select_market_adapter
from aristos_council.ranking import rank_ticker, split_and_sort
from aristos_council.strategy.loader import load_strategy

from run_council import resolve_strategy_path  # type: ignore


def _read_tickers(args) -> list[str]:
    raw: list[str] = list(args.tickers)
    if args.file:
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                raw.extend(t.strip() for t in line.replace(",", " ").split())
    seen, out = set(), []
    for t in raw:
        nt = normalize_ticker(t)
        if nt not in seen:
            seen.add(nt)
            out.append(nt)
    return out


def _obs(r, name):
    for c in r.criteria:
        if c["name"] == name:
            o = c["observed"]
            return f"{o:.3g}" if isinstance(o, (int, float)) else "—"
    return "—"


def main() -> None:
    p = argparse.ArgumentParser(description="Fast screen-only ranking (no LLM).")
    p.add_argument("tickers", nargs="*", help="tickers to rank")
    p.add_argument("--file", help="file with tickers (whitespace/comma/newline separated)")
    p.add_argument("--strategy", default=None, help="strategy id or YAML path")
    p.add_argument("--csv", default=None, help="append ranked rows to this CSV")
    p.add_argument("--no-cache", action="store_true", help="do not use the data cache")
    p.add_argument("--refresh", action="store_true", help="ignore cache; fetch fresh")
    p.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    args = p.parse_args()

    tickers = _read_tickers(args)
    if not tickers:
        p.error("no tickers given (positional or --file)")
    strategy = load_strategy(resolve_strategy_path(args.strategy))

    today = date.today()
    adapter = select_market_adapter()
    if not args.no_cache:
        adapter = CachingAdapter(adapter, cache_dir=args.cache_dir, today=today,
                                 refresh=args.refresh)
    print(f"(strategy: {strategy.id}; provider: {adapter.name}; "
          f"{'no cache' if args.no_cache else 'cache ' + args.cache_dir})")

    rankings = []
    for t in tickers:
        try:
            rankings.append(rank_ticker(adapter, strategy, t, today=today))
        except Exception as exc:                       # never let one name abort
            from aristos_council.ranking import ScreenRanking
            rankings.append(ScreenRanking(ticker=t, verdict="error", score=None,
                                          error=str(exc)))
        print(f"  screened {t}")

    scored, gated, other = split_and_sort(rankings)

    print(f"\n=== SCREEN-ONLY RANKING ({strategy.id}) ===")
    print("  NOTE: screen-only score — excludes the small specialist-stance "
          "contribution; run the full council on the shortlist for the narrative "
          "+ the stance adjustment.\n")
    print(f"  {'#':>2}  {'ticker':<10} {'verdict':<8} {'score':>7}  flags")
    for i, r in enumerate(scored, 1):
        flags = []
        if r.degraded:
            flags.append("DEGRADED")
        print(f"  {i:>2}  {r.ticker:<10} {r.verdict.upper():<8} "
              f"{r.score:>+7.1f}  {' '.join(flags)}")

    if gated:
        print("\n  Gated (deterministic, not scored):")
        for r in gated:
            print(f"      {r.ticker:<10} {r.verdict.upper()} (gated)")
    if other:
        print("\n  Could not screen:")
        for r in other:
            print(f"      {r.ticker:<10} {r.error or 'no data'}")

    if args.csv:
        path = Path(args.csv)
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["ticker", "verdict", "score", "gated", "degraded", "error"])
            for r in scored + gated + other:
                w.writerow([r.ticker, r.verdict,
                            "" if r.score is None else round(r.score, 2),
                            r.gated, r.degraded, r.error or ""])
        print(f"\n  rows appended -> {args.csv}")


if __name__ == "__main__":
    main()
