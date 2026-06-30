"""Backtest CLI — validate a rank strategy on free price data, honestly (Phase 3).

Runs the point-in-time backtest engine over a universe and date range. ONLY the
price-derived factor sleeve (momentum, low-vol) is backtested — historical
point-in-time FUNDAMENTALS aren't available on free data, so fundamental factors are
DROPPED from the backtest and FLAGGED loudly (they need an EODHD fundamental tier /
estimates feed). conservative_plus is fully backtestable here; magic_formula's
value+quality legs are data-gated and reported as such.

Usage:
    python examples/backtest.py --file pool.txt --strategy conservative_plus_v1 \
        --start 2015-01-01 --end 2025-01-01 --rebalance-days 365 --top-k 5

Requires the data extras + keys. Spends NO LLM credits.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from aristos_council.backtest import rebalance_dates, run_backtest
from aristos_council.data.adapter import DataUnavailable, normalize_ticker
from aristos_council.data.provider import select_market_adapter
from aristos_council.factors import PRICE_DERIVED_FACTORS, price_factors_from_closes
from aristos_council.rank_engine import FactorSpec
from aristos_council.strategy.rank_loader import load_rank_strategy

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = ROOT / "strategies"


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
    p = argparse.ArgumentParser(description="Backtest a rank strategy (price sleeve, honest).")
    p.add_argument("tickers", nargs="*")
    p.add_argument("--file")
    p.add_argument("--strategy", default="conservative_plus_v1")
    p.add_argument("--start", required=True, help="ISO date, e.g. 2015-01-01")
    p.add_argument("--end", required=True, help="ISO date")
    p.add_argument("--rebalance-days", type=int, default=365)
    p.add_argument("--top-k", type=int, default=5)
    args = p.parse_args()

    universe = _read_tickers(args)
    if not universe:
        p.error("no tickers given (positional or --file)")
    strat = load_rank_strategy(_resolve(args.strategy))
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    # Partition factors: price-derived (backtestable) vs fundamental (data-gated).
    sleeve = [f for f in strat.factors if f.name in PRICE_DERIVED_FACTORS]
    dropped = [f.name for f in strat.factors if f.name not in PRICE_DERIVED_FACTORS]
    if not sleeve:
        p.error(f"strategy {strat.id} has NO price-derived factors — it cannot be "
                f"backtested on free data (needs point-in-time fundamentals: "
                f"{', '.join(dropped)})")
    caveats = []
    if dropped:
        caveats.append(
            "FUNDAMENTAL factors DROPPED (not point-in-time on free data): "
            + ", ".join(dropped)
            + ". This backtests only the price sleeve "
            + ", ".join(f.name for f in sleeve)
            + " — a PARTIAL validation; the full strategy needs a point-in-time "
              "fundamentals feed (EODHD).")

    # Fetch historical prices (a year of warm-up before `start` for momentum/vol).
    adapter = select_market_adapter()
    price_data: dict[str, list[tuple[date, float]]] = {}
    for t in universe:
        try:
            ph = adapter.get_price_history(t, start=start - timedelta(days=400), end=end)
            price_data[t] = [(b.day, b.adj_close) for b in ph.bars]
            print(f"  fetched {t} ({len(price_data[t])} bars)")
        except DataUnavailable as exc:
            print(f"  SKIP {t}: {exc}")

    dates = rebalance_dates(start, end, args.rebalance_days)
    specs = [FactorSpec(f.name, f.direction) for f in sleeve]
    result = run_backtest(
        universe, price_data, dates=dates,
        factor_fn=lambda closes: price_factors_from_closes(
            closes, [f.name for f in sleeve]),
        rank_specs=specs, cut="top_k", top_k=args.top_k, missing="exclude",
        extra_caveats=caveats)

    print(f"\n=== BACKTEST: {strat.id} (price sleeve: "
          f"{', '.join(f.name for f in sleeve)}) ===")
    print(f"  periods: {result.n_periods}  ({args.start} -> {args.end}, "
          f"rebalance {args.rebalance_days}d, top {args.top_k})")
    print(f"  annualized return : {result.annualized_return:+.2%}")
    print(f"  benchmark (eq-wt) : {result.annualized_benchmark:+.2%}")
    print(f"  excess (alpha)    : {result.annualized_return - result.annualized_benchmark:+.2%}")
    print(f"  Sharpe            : {result.sharpe:.2f}")
    print(f"  max drawdown      : {result.max_drawdown:.2%}")
    print(f"  hit rate vs bench : {result.hit_rate:.0%}")
    print("\n  Per-period holdings:")
    for pr in result.periods:
        print(f"    {pr.rebalance_date} -> {pr.next_date}: "
              f"{pr.portfolio_return:+.1%} (bench {pr.benchmark_return:+.1%})  "
              f"{', '.join(pr.holdings) or '(none)'}")
    print("\n  CAVEATS:")
    for c in result.caveats:
        print(f"    - {c}")


if __name__ == "__main__":
    main()
