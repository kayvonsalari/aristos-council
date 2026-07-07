"""Company Check — single-name diagnostic CLI (Aristos v2).

"Why isn't X on the list?" for ONE ticker: every lens-screen criterion with values,
the sector/cap gates, each factor's value with NAMED-cohort context, and the
price-vs-fundamentals divergence flag. NO verdict is ever emitted (a rank over a class
of one is fabricated). NO LLM, $0 — deterministic tools only. Cohort context comes from
the latest FROZEN run of the reference universe (offline replay), never a fresh pull.

CLI:
    python examples/company_check.py MU --strategy magic_formula_momentum_v1 \
        --reference growth_40_v1

Requires the market-data extra; NO keys (yfinance ``info`` + price history).
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from aristos_council.cli_guards import force_utf8_stdout, implausible_ticker_reason
from aristos_council.company_check import format_company_check, run_company_check
from aristos_council.data.adapter import normalize_ticker
from aristos_council.data.cache import DEFAULT_CACHE_DIR, CachingAdapter
from aristos_council.data.provider import select_market_adapter

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = ROOT / "strategies"
UNIVERSES_DIR = ROOT / "universes"
RUNS_DIR = ROOT / "runs"


def main() -> None:
    p = argparse.ArgumentParser(
        description="Single-name diagnostic — why isn't this ticker on the list? "
                    "(NO verdict; NO LLM).")
    force_utf8_stdout()          # the '⚠' divergence flag must not crash a cp1252 console
    p.add_argument("ticker", help="the ONE ticker to diagnose")
    p.add_argument("--strategy", default="magic_formula_momentum_v1",
                   help="rank strategy whose lens screen + factors apply "
                        "(default: magic_formula_momentum_v1)")
    p.add_argument("--reference", default="",
                   help="reference universe manifest (under universes/) for cohort "
                        "context; omit for raw values with no cohort position")
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args()

    # Guardrail FIRST (the paste-slip lesson): reject a path / flag / '.py' token by
    # NAME before any fetch — a bogus symbol must never masquerade as a diagnosis.
    why = implausible_ticker_reason(args.ticker)
    if why:
        p.error(f"implausible ticker {args.ticker!r}: {why}")

    ticker = normalize_ticker(args.ticker)
    if not ticker:
        p.error("no ticker given")

    today = date.today()
    adapter = select_market_adapter()
    if not args.no_cache:
        adapter = CachingAdapter(adapter, cache_dir=DEFAULT_CACHE_DIR, today=today)

    result = run_company_check(
        ticker, args.strategy, args.reference, adapter=adapter,
        strategies_dir=STRATEGIES_DIR, universes_dir=UNIVERSES_DIR, runs_dir=RUNS_DIR,
        today=today)
    print(format_company_check(result))


if __name__ == "__main__":
    main()
