"""LENS-EXPAND-1 — the existing lenses' ranks and verdicts are BYTE-IDENTICAL.

Adding the Quality and Earnings Power Value lenses (new factors, new strategy files) must not move
a single rank, verdict, factor value or exclusion of any lens that was already here. The golden file
``tests/fixtures/lens_golden/existing_lenses.json`` was generated from the code as it stood on
``main`` BEFORE those lenses existed (``python tests/test_lens_expand_golden.py --regen`` run from a
clean checkout of that commit), so it is a real before/after comparison, not a snapshot of whatever
the code does today. Never regenerate it to make this test pass: a difference here means an existing
lens changed.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

from aristos_council.data.adapter import (
    DividendEvent, Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.pipeline import run_rank_pipeline

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "fixtures" / "lens_golden" / "existing_lenses.json"
TODAY = date(2026, 6, 30)

# The lenses that existed before LENS-EXPAND-1 (stock and ETF), by id — frozen on purpose.
EXISTING_LENSES = (
    "magic_formula_raw_v1", "magic_formula_momentum_v1", "growth_garp_v2", "conservative_plus_v1",
    "cyclical_income_v1", "financials_v1", "forensic_v1",
    "etf_dividend_v1", "etf_growth_v1", "etf_core_v1",
)
TICKERS = [f"T{i:02d}" for i in range(14)] + ["BANK", "TINY"]


def _fundamentals(i: int, ticker: str) -> Fundamentals:
    """Deterministic, varied accounts: arithmetic on the index only, no randomness."""
    rev0 = 8000.0 + 900.0 * i
    revenue = [rev0 * ((0.86 if i % 2 else 0.97) ** k) for k in range(5)]
    margin = 0.08 + 0.011 * ((i * 7) % 11)
    oi = [r * margin * (1.0 + 0.04 * ((i + k) % 3)) for k, r in enumerate(revenue)]
    pretax = [o * 0.95 for o in oi]
    tax = [p * (0.18 + 0.01 * (i % 6)) for p in pretax]
    cap = 6e9 + 2.5e9 * i
    return Fundamentals(
        ticker=ticker, name=ticker, market_cap=cap if ticker != "TINY" else 8e8,
        sector="Financial Services" if ticker == "BANK" else "Industrials",
        currency="USD", financial_currency="USD", quote_type="EQUITY",
        pe_ratio=9.0 + 1.7 * i, eps=2.0 + 0.1 * i,
        dividend_per_share=0.8 + 0.07 * i, dividend_yield=0.012 + 0.003 * (i % 7),
        payout_ratio=0.25 + 0.04 * (i % 9), free_cash_flow=oi[0] * 0.6,
        total_debt=oi[0] * (0.5 + 0.35 * (i % 8)), total_cash=oi[0] * (0.2 + 0.1 * (i % 5)),
        total_revenue=revenue, operating_income=oi, ebit=oi, pretax_income=pretax,
        tax_provision=tax, invested_capital=[o * (5.0 + (i % 4)) for o in oi],
        net_income=[p - t for p, t in zip(pretax, tax)],
        free_cash_flow_annual=[o * 0.6 for o in oi],
        shareholders_equity=[o * 4.0 for o in oi],
        total_assets_annual=[o * 9.0 for o in oi], gross_profit_annual=[r * 0.4 for r in revenue],
    )


class _Adapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        if ticker not in TICKERS:
            raise KeyError(ticker)
        return _fundamentals(TICKERS.index(ticker), ticker)

    def get_price_history(self, ticker, *, start, end):
        i = TICKERS.index(ticker)
        drift = 0.0004 * ((i * 5) % 9 - 3)
        bars = []
        for d in range(420):
            px = 100.0 * (1.0 + drift) ** d * (1.0 + 0.01 * ((d * (i + 3)) % 7 - 3) / 3.0)
            bars.append(PriceBar(day=TODAY - timedelta(days=419 - d), open=px, high=px * 1.01,
                                 low=px * 0.99, close=px, adj_close=px, volume=1000))
        return PriceHistory(ticker=ticker, bars=bars)

    def get_dividend_history(self, ticker, *, start, end):
        i = TICKERS.index(ticker)
        return [DividendEvent(ex_date=date(y, 3, 1), amount=0.5 + 0.03 * i + 0.02 * (y - 2010))
                for y in range(2010, 2026)]


def build_outputs() -> dict:
    out: dict = {}
    for lens in EXISTING_LENSES:
        r = run_rank_pipeline(list(TICKERS), lens, ranker_only=True, strategies_dir=ROOT / "strategies",
                              adapter=_Adapter(), today=TODAY, use_cache=False)
        out[lens] = {
            "ranked": [{"ticker": x.ticker, "verdict": x.verdict, "combined_rank": x.combined_rank,
                        "position": x.cohort_position, "factor_ranks": x.factor_ranks,
                        "factor_values": x.factor_values, "factor_sources": x.factor_sources}
                       for x in r.ranked],
            "excluded": [list(e) for e in r.excluded],
            "unrateable": [list(u) for u in r.unrateable],
        }
    return json.loads(json.dumps(out, sort_keys=True, default=str))


def test_existing_lenses_are_byte_identical_to_the_pre_lens_expand_golden():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    got = build_outputs()
    assert set(got) == set(golden)
    for lens in EXISTING_LENSES:
        assert got[lens] == golden[lens], f"{lens} no longer matches its pre-LENS-EXPAND-1 output"
    # The universe really exercised the lenses (a golden of empty lists would prove nothing).
    assert sum(len(v["ranked"]) for v in golden.values()) >= 30


if __name__ == "__main__":
    if "--regen" in sys.argv:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(build_outputs(), indent=1, sort_keys=True), encoding="utf-8")
        print("wrote", GOLDEN)
