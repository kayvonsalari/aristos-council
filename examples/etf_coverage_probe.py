"""ETF field-coverage probe (ETF-1 ITEM 1) — the FETCHING CLI.

Fetches the ITEM-4 ETF universes via the existing market adapter and reports, per line,
which candidate factor fields exist: expense ratio (netExpenseRatio /
annualReportExpenseRatio), fund size (totalAssets), distribution/dividend yield,
quoteType, and 12m price history. Then applies the ≥80%-per-universe rule
(``tools.etf_coverage.coverage_decision``) to decide which fields are IN each lens.

Run OUTSIDE the frozen dev sandbox (it needs yfinance network access):

    python examples/etf_coverage_probe.py
    python examples/etf_coverage_probe.py --out reports/exploratory/etf_coverage_probe.md

The decision math is pure and lives in ``aristos_council.tools.etf_coverage`` (tested
offline); this script only fetches and prints, so the ≥80% rule is reproducible.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from aristos_council.data.cache import DEFAULT_CACHE_DIR, CachingAdapter
from aristos_council.data.provider import select_market_adapter
from aristos_council.tools.etf_coverage import (
    PROBE_FIELDS,
    coverage_decision,
    format_coverage_table,
)

# The ITEM-4 universes, inlined so the probe is self-contained (it runs before the
# universe manifests are wired). Kept in sync with universes/etf_*_us_v1.yaml.
DIVIDEND_SET = ["VIG", "VYM", "SCHD", "DVY", "SDY", "NOBL", "HDV", "SPYD", "DGRO", "FVD"]
GROWTH_SET = ["VUG", "QQQ", "IWF", "SPYG", "SCHG", "VONG", "MGK", "IWY"]


def _probe_ticker(adapter, ticker: str, *, today: date) -> dict:
    """One line's probe row: the raw field values + a 12m close count. Any per-source
    failure degrades that field to absent (None / 0), never aborts the probe."""
    f = None
    try:
        f = adapter.get_fundamentals(ticker)
    except Exception:
        pass
    n_closes = 0
    try:
        ph = adapter.get_price_history(
            ticker, start=today - timedelta(days=400), end=today)
        n_closes = len(ph.closes) if ph and ph.closes else 0
    except Exception:
        pass
    return {
        "ticker": ticker,
        "net_expense_ratio": getattr(f, "net_expense_ratio", None),
        "total_assets": getattr(f, "total_assets", None),
        "dividend_yield": getattr(f, "dividend_yield", None),
        "quote_type": getattr(f, "quote_type", None),
        "price_history_12m": n_closes,
    }


def _line_table(label: str, rows: list[dict]) -> str:
    lines = [f"### {label} — per-line fields", "",
             "| ticker | expense_ratio | fund_size | yield | quoteType | 12m_closes |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        def _s(v):
            return "—" if v is None else (f"{v}" if not isinstance(v, float) else f"{v:.4g}")
        lines.append(
            f"| {r['ticker']} | {_s(r['net_expense_ratio'])} | "
            f"{_s(r['total_assets'])} | {_s(r['dividend_yield'])} | "
            f"{_s(r['quote_type'])} | {r['price_history_12m']} |")
    return "\n".join(lines)


def run_probe(*, today: date | None = None, adapter=None) -> str:
    today = today or date.today()
    if adapter is None:
        adapter = CachingAdapter(select_market_adapter(),
                                 cache_dir=DEFAULT_CACHE_DIR, today=today)
    out = ["# ETF field-coverage probe (ETF-1 ITEM 1)", "",
           f"_Fields probed: {', '.join(PROBE_FIELDS)}. A field is IN a lens when "
           "present for ≥ 80% of that universe's lines._", ""]
    for label, universe in (("Dividend set", DIVIDEND_SET), ("Growth set", GROWTH_SET)):
        rows = [_probe_ticker(adapter, t, today=today) for t in universe]
        coverage = coverage_decision(rows)
        out += [_line_table(label, rows), "", format_coverage_table(label, coverage), ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="ETF field-coverage probe (ETF-1 ITEM 1)")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the markdown report here (else print to stdout)")
    args = ap.parse_args()
    report = run_probe()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
