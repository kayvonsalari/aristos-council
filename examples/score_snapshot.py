"""Prospective scoreboard — SCORING (Aristos v2).

Grade a past snapshot on FORWARD total return once its horizon has (partly or fully)
elapsed. NO LLM: pure arithmetic over auto-adjusted closes. The PRE-COMMITTED test is
bucket ORDERING (BUY > HOLD > SELL for Aristos; loved > middle > unloved for the
street), NOT any single name — and the street is bucketed by RELATIVE terciles because
absolute rating bands are structurally all-BUY on the observed universes.

CLI:
    python examples/score_snapshot.py --date 2026-07-04
    python examples/score_snapshot.py --date 2026-07-04 --horizon-months 12 --strategy magic_formula_v1

Reads ``<snapshots>/verdict_consensus.csv`` (default ``snapshots/``). Names that stopped
trading in the window are marked UNRESOLVED and reported separately — never dropped,
never assumed -100% (a delisting can be an acquisition). Requires the market-data extra;
NO keys.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from aristos_council.data.cache import DEFAULT_CACHE_DIR, CachingAdapter
from aristos_council.data.provider import select_market_adapter
from aristos_council.scoreboard import (
    format_strategy_score,
    read_rows,
    score_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOTS = ROOT / "snapshots" / "verdict_consensus.csv"


def main() -> None:
    p = argparse.ArgumentParser(description="Score a past verdict+consensus snapshot.")
    p.add_argument("--date", required=True, help="snapshot date, YYYY-MM-DD")
    p.add_argument("--horizon-months", type=int, default=6)
    p.add_argument("--snapshots", default=str(DEFAULT_SNAPSHOTS))
    p.add_argument("--strategy", default=None, help="score only this strategy")
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args()

    snapshot_date = date.fromisoformat(args.date)
    rows = read_rows(args.snapshots, snapshot_date=snapshot_date, strategy=args.strategy)
    if not rows:
        p.error(f"no snapshot rows for {args.date} in {args.snapshots}"
                + (f" (strategy {args.strategy})" if args.strategy else ""))

    today = date.today()
    adapter = select_market_adapter()
    if not args.no_cache:
        adapter = CachingAdapter(adapter, cache_dir=DEFAULT_CACHE_DIR, today=today)

    scores, partial = score_snapshot(rows, adapter=adapter, snapshot_date=snapshot_date,
                                     today=today, horizon_months=args.horizon_months)
    for sid in sorted(scores):
        print(format_strategy_score(scores[sid], snapshot_date=snapshot_date,
                                    horizon_months=args.horizon_months, partial=partial))
        print()


if __name__ == "__main__":
    main()
