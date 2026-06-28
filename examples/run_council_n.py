"""Reproducibility harness CLI — run a council N times and report verdict stability.

A single council run is NOT a trustworthy verdict for a borderline, screen-PASSING
name (temperature 0 reduced but did not remove LLM verdict wobble). This runs the
council n times and prints the verdict DISTRIBUTION + a stability flag, so a 6/4
BUY/HOLD split reads as borderline instead of a misleading lone "BUY". Gated
outcomes (SELL cap / INSUFFICIENT_EVIDENCE) are deterministic and short-circuit to
one run.

Usage:
    python examples/run_council_n.py GOOGL --strategy growth_v1 -n 5
    python examples/run_council_n.py LMT   --strategy growth_v1 -n 5 --csv runs.csv

Requires the runtime extras + keys (see CLAUDE.md). Do NOT launch from the Claude
Code dev environment — this spends API credits.
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

from aristos_council.agents.runners import production_runners
from aristos_council.data.adapter import normalize_ticker
from aristos_council.data.provider import select_market_adapter
from aristos_council.reproducibility import (
    build_run_one,
    cost_guard_line,
    format_stability,
    run_council_n,
    stability_csv_row,
)
from aristos_council.strategy.loader import load_strategy

# Reuse the strategy resolver from the single-run CLI so id/path handling matches.
from run_council import resolve_strategy_path  # type: ignore


def append_csv_row(path: Path, row: dict) -> None:
    """Append one stability row to a CSV (Drive-friendly), writing a header once."""
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
        if new:
            writer.writeheader()
        writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a council N times; report stability.")
    parser.add_argument("ticker")
    parser.add_argument("--strategy", default=None, help="strategy id or YAML path")
    parser.add_argument("-n", type=int, default=5, help="runs (default 5)")
    parser.add_argument("--csv", default=None, help="append the stability row to this CSV")
    args = parser.parse_args()

    ticker = normalize_ticker(args.ticker)
    strategy = load_strategy(resolve_strategy_path(args.strategy))

    sentiment = None
    sentiment_missing_key = False
    if os.environ.get("FINNHUB_API_KEY"):
        from aristos_council.data.finnhub_adapter import FinnhubAdapter
        sentiment = FinnhubAdapter()
    else:
        sentiment_missing_key = True
        print("(sentiment: no FINNHUB_API_KEY — Sentiment specialist will abstain)")

    adapter = select_market_adapter()
    runners = production_runners()
    print(f"(strategy: {strategy.id}; market provider: {adapter.name})")

    # Cost guard: n is explicit and the spend is printed BEFORE running.
    print(cost_guard_line(args.n))

    run_one = build_run_one(
        ticker=ticker, strategy=strategy, adapter=adapter, runners=runners,
        sentiment_adapter=sentiment, sentiment_missing_key=sentiment_missing_key)
    report = run_council_n(run_one, ticker=ticker, n=args.n)

    print("\n=== Reproducibility ===")
    print(format_stability(report))
    if report.veto_union:
        print(f"  vetoes seen across runs: {', '.join(report.veto_union)}")

    if args.csv:
        append_csv_row(Path(args.csv), stability_csv_row(report))
        print(f"  stability row appended -> {args.csv}")


if __name__ == "__main__":
    main()
