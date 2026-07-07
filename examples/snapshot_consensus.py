"""Prospective scoreboard — SNAPSHOT job (Aristos v2).

Freeze today's Aristos ranker verdicts AND the street's consensus, same-day, into an
append-only store. Scored later on FORWARD returns (``examples/score_snapshot.py``) —
the only honest "how do we fare vs analysts". NO LLM, $0: this runs the EXISTING
``run_rank_pipeline`` in ranker-only mode.

CLI:
    python examples/snapshot_consensus.py AAPL MSFT GOOGL ... --rank-strategy magic_formula_v1
    python examples/snapshot_consensus.py --file pool.txt --rank-strategy conservative_plus_v1 --out snapshots/

Appends one row per name (ranked, EXCLUDED, and UNRATEABLE — an exclusion is a call
too) to ``<out>/verdict_consensus.csv``, then prints the divergence map. Cadence is
MANUAL / quarterly — no scheduler in scope. Requires the market-data extra; NO keys
(ranker-only + yfinance ``info``).
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from aristos_council.cli_guards import universe_args_error
from aristos_council.data.adapter import normalize_ticker
from aristos_council.data.cache import DEFAULT_CACHE_DIR, CachingAdapter
from aristos_council.data.provider import select_market_adapter
from aristos_council.scoreboard import format_divergence_map, run_snapshot

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = ROOT / "strategies"
UNIVERSES_DIR = ROOT / "universes"
DEFAULT_OUT = ROOT / "snapshots"


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
        if nt and nt not in seen:
            seen.add(nt)
            out.append(nt)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Freeze verdict + street-consensus snapshot.")
    p.add_argument("tickers", nargs="*")
    p.add_argument("--file")
    p.add_argument("--universe-id",
                   help="a manifest under universes/ (recorded by id); "
                        "otherwise the ticker list is recorded as adhoc:<hash>")
    p.add_argument("--rank-strategy", required=True)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args()

    # Guardrails FIRST (the paste-slip lesson): reject an implausible positional token
    # by NAME, and forbid positional tickers together with --universe-id — before any
    # adapter runs or any row is written to the permanent record.
    guard = universe_args_error(args.tickers, args.universe_id)
    if guard:
        p.error(guard)

    universe = _read_tickers(args)
    if not universe and not args.universe_id:
        p.error("no tickers given (positional, --file, or --universe-id)")

    today = date.today()
    adapter = select_market_adapter()
    if not args.no_cache:
        adapter = CachingAdapter(adapter, cache_dir=DEFAULT_CACHE_DIR, today=today)

    rows, path = run_snapshot(universe or None, args.rank_strategy, adapter=adapter,
                              today=today, strategies_dir=STRATEGIES_DIR,
                              out_dir=args.out, universe_id=args.universe_id,
                              universes_dir=UNIVERSES_DIR)

    uid = rows[0].universe_id if rows else (args.universe_id or "—")
    src = "--universe-id" if args.universe_id else f"{len(universe)} positional/-file ticker(s)"
    print(f"Snapshot {today.isoformat()} · strategy {args.rank_strategy} "
          f"(ranker-only, no LLM) · universe {uid} (from {src}) · "
          f"{len(rows)} row(s) appended -> {path}")
    print()
    print(format_divergence_map(rows))


if __name__ == "__main__":
    main()
