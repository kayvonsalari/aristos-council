"""Factor-integrity disclosure (cleanup ITEM 1): the exact computation path each factor
took per ticker is RECORDED at compute time and rendered as a plain-text block — so
EV vs EBIT/mcap proxy vs abstained is never silent again.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.factors import (
    FactorInputs,
    SRC_ABSTAINED,
    SRC_EBIT_MCAP,
    SRC_EV,
    compute_factor_outcomes,
)
from aristos_council.pipeline import (
    RankPipelineResult,
    factor_integrity,
    format_factor_integrity,
    format_integrity_entry,
    run_rank_pipeline,
)
from aristos_council.rank_engine import RankedTicker

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


# --------------------------------------------------------------------------- #
# Source recorded at compute time (the real deliverable)
# --------------------------------------------------------------------------- #
def _fi(**fund):
    return FactorInputs(ticker="T", fundamentals=Fundamentals(ticker="T", **fund))


def test_earnings_yield_source_is_recorded_per_path():
    ev = compute_factor_outcomes(_fi(ebit=[100.0], market_cap=1000.0, total_debt=200.0,
                                     total_cash=100.0), ["earnings_yield"])
    assert ev["earnings_yield"][1] == SRC_EV

    proxy = compute_factor_outcomes(_fi(ebit=[100.0], market_cap=1000.0,
                                        total_debt=200.0), ["earnings_yield"])
    assert proxy["earnings_yield"][1] == SRC_EBIT_MCAP        # no total_cash -> proxy

    net_cash = compute_factor_outcomes(_fi(ebit=[100.0], market_cap=100.0,
                                           total_debt=0.0, total_cash=500.0),
                                       ["earnings_yield"])
    assert net_cash["earnings_yield"] == (None, SRC_ABSTAINED)   # EV<=0 -> abstain


# --------------------------------------------------------------------------- #
# The rendered block (pure)
# --------------------------------------------------------------------------- #
def _rt(ticker, source):
    return RankedTicker(ticker=ticker, factor_ranks={"earnings_yield": 1.0},
                        factor_values={"earnings_yield": 0.1}, combined_rank=1.0,
                        universe_size=6, factor_sources={"earnings_yield": source})


def _result(ranked):
    return RankPipelineResult(ranked=ranked, excluded=[], unrateable=[], narratives={},
                              header="", meta={"rank_strategy_id": "s"})


def test_block_names_fallback_tickers_when_five_or_fewer():
    ranked = ([_rt(f"EV{i}", SRC_EV) for i in range(21)]
              + [_rt("HD", SRC_EBIT_MCAP), _rt("CAT", SRC_EBIT_MCAP)])
    entry = factor_integrity(_result(ranked))[0]
    line = format_integrity_entry(entry)
    assert "EV 21/23" in line
    assert "EBIT/mcap proxy 2/23 (HD, CAT)" in line           # <=5 -> named
    assert "abstained 0" in line


def test_block_counts_fallback_tickers_when_more_than_five():
    ranked = ([_rt("A", SRC_EV)]
              + [_rt(f"P{i}", SRC_EBIT_MCAP) for i in range(6)])   # 6 fallbacks
    line = format_integrity_entry(factor_integrity(_result(ranked))[0])
    assert "EBIT/mcap proxy 6/7" in line and "(" not in line.split("proxy")[1][:12]


def test_all_ev_reads_clean():
    line = format_integrity_entry(
        factor_integrity(_result([_rt(f"E{i}", SRC_EV) for i in range(23)]))[0])
    assert "EV 23/23" in line and "abstained 0" in line


def test_abstention_counted_separately_from_fallback():
    ranked = [_rt("A", SRC_EV), _rt("B", SRC_EBIT_MCAP), _rt("C", SRC_ABSTAINED)]
    line = format_integrity_entry(factor_integrity(_result(ranked))[0])
    assert "EV 1/3" in line
    assert "EBIT/mcap proxy 1/3 (B)" in line
    assert "abstained 1 (C)" in line                          # separate from fallback


# --------------------------------------------------------------------------- #
# End-to-end: the pipeline attaches sources; the CLI report shows the block
# --------------------------------------------------------------------------- #
_BASE = dict(sector="Technology", ebit=[3000.0], operating_income=[3000.0] * 4,
             tax_provision=[600.0] * 4, pretax_income=[2900.0] * 4,
             invested_capital=[5000.0] * 4)      # ROIC ~0.48 -> passes the 12% floor


class _Adapter(MarketDataAdapter):
    name = "fake"
    _F = {
        "EVFULL1": dict(market_cap=2e10, total_debt=1000.0, total_cash=500.0, **_BASE),
        "EVFULL2": dict(market_cap=2e10, total_debt=1000.0, total_cash=500.0, **_BASE),
        "NOCASH1": dict(market_cap=2e10, total_debt=1000.0, **_BASE),          # no cash
        "NOCASH2": dict(market_cap=2e10, total_debt=1000.0, **_BASE),
        "NETCASH": dict(market_cap=5e9, total_debt=0.0, total_cash=6e9, **_BASE),  # EV<0
    }

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, **self._F[ticker])

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_pipeline_attaches_sources_and_report_shows_block():
    result = run_rank_pipeline(
        ["EVFULL1", "EVFULL2", "NOCASH1", "NOCASH2", "NETCASH"], "magic_formula_v1",
        ranker_only=True, strategies_dir=STRAT_DIR, adapter=_Adapter(),
        today=date(2026, 6, 30))
    assert len(result.ranked) == 5
    by = {r.ticker: r.factor_sources["earnings_yield"] for r in result.ranked}
    assert by["EVFULL1"] == SRC_EV and by["NOCASH1"] == SRC_EBIT_MCAP
    assert by["NETCASH"] == SRC_ABSTAINED

    block = "\n".join(format_factor_integrity(result))
    assert "FACTOR INTEGRITY" in block
    assert "EV 2/5" in block
    assert "EBIT/mcap proxy 2/5 (NOCASH1, NOCASH2)" in block
    assert "abstained 1 (NETCASH)" in block
