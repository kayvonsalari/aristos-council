"""ETF-1 ITEM 5 — generate the ranker-only baselines + the two kind-leak mirrors.

Runs OUTSIDE the frozen dev sandbox (needs yfinance network). Emits four exploratory
markdown reports to ``reports/exploratory/``:

  - baseline: etf_dividend_v1 on etf_dividend_us_v1
  - baseline: etf_growth_v1   on etf_growth_us_v1
  - mirror:   magic_formula_momentum_v1 (flagship, equity lens) on etf_dividend_us_v1
              -> expect 0 ranked, 10 kind-gated
  - mirror:   etf_dividend_v1 (ETF lens) on growth_40_v1
              -> expect 0 ranked, all kind-gated, PARA/WBA UNRATEABLE

Usage:
    python examples/etf_baselines.py
    python examples/etf_baselines.py --out-dir reports/exploratory

The formatting + summary math is pure and tested offline (aristos_council.etf_baselines);
this script only drives the shared run_rank_pipeline entrypoint and writes files.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from aristos_council.etf_baselines import (
    format_baseline_markdown,
    format_mirror_markdown,
)
from aristos_council.pipeline import run_rank_pipeline

_DEFAULT_OUT = Path(__file__).resolve().parents[1] / "reports" / "exploratory"

# (rank_strategy_id, universe_id) baselines.
BASELINES = [
    ("etf_dividend_v1", "etf_dividend_us_v1"),
    ("etf_growth_v1", "etf_growth_us_v1"),
]
# (rank_strategy_id, universe_id, expectation) mirrors.
MIRRORS = [
    ("magic_formula_momentum_v1", "etf_dividend_us_v1",
     "flagship equity lens on the dividend-ETF universe -> 0 ranked, 10 kind-gated"),
    ("etf_dividend_v1", "growth_40_v1",
     "ETF dividend lens on the stock universe -> 0 ranked, all kind-gated, "
     "PARA/WBA UNRATEABLE"),
]


def _stamp(today: date) -> str:
    return today.isoformat()


def main() -> None:
    ap = argparse.ArgumentParser(description="ETF-1 ITEM 5 baselines + mirrors")
    ap.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()

    for strat, uni in BASELINES:
        result = run_rank_pipeline(None, strat, universe_id=uni, ranker_only=True)
        md = format_baseline_markdown(result)
        out = args.out_dir / f"etf_baseline_{strat}_{uni}_{_stamp(today)}.md"
        out.write_text(md, encoding="utf-8")
        print(f"wrote {out}  (ranked {len(result.ranked)})")

    for strat, uni, expectation in MIRRORS:
        result = run_rank_pipeline(None, strat, universe_id=uni, ranker_only=True)
        md = format_mirror_markdown(result, expectation=expectation)
        out = args.out_dir / f"etf_mirror_{strat}_on_{uni}_{_stamp(today)}.md"
        out.write_text(md, encoding="utf-8")
        print(f"wrote {out}  (ranked {len(result.ranked)}, "
              f"excluded {len(result.excluded)}, unrateable {len(result.unrateable)})")


if __name__ == "__main__":
    main()
