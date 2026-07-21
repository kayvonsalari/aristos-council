"""NARR-2 ITEM 2 — narration coverage toggle.

The narrator writes prose only for the BUY-tier shortlist by default (``buys_only``,
cheapest). ``narrate_coverage="all"`` narrates EVERY ranked (live) name — for
core/ETF cohorts where the HOLDs are live options being compared, not rejects.

Coverage only: the deterministic ranker verdicts + ordering are byte-unchanged
between the two modes; only WHICH names get an LLM narrative differs.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.agents.schemas import CriticOutput, DecisionOutput, SpecialistOutput
from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.pipeline import run_rank_pipeline
from aristos_council.state import Recommendation, Stance

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"

# A/B/C/D all clear the magic-value prefilter; a clear ROIC + earnings-yield ordering
# spreads them across buy/hold so "all" narrates strictly more than "buys_only".
_FUND = {
    t: dict(market_cap=2e10, sector="Technology", ebit=[ebit], pe_ratio=pe,
            operating_income=[oi, oi, oi, oi], tax_provision=[oi * 0.2] * 4,
            pretax_income=[oi * 0.95] * 4, invested_capital=[5000.0] * 4,
            total_revenue=[200.0, 170, 150, 120])
    for t, ebit, pe, oi in [
        ("A", 3000.0, 8.0, 3000.0), ("B", 2500.0, 12.0, 2500.0),
        ("C", 2000.0, 16.0, 2000.0), ("D", 1500.0, 20.0, 1500.0)]
}


class _Adapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, currency="USD", **_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


class _SpecialistRunner:
    def invoke(self, system, user):
        return SpecialistOutput(stance=Stance.ABSTAIN, confidence=0.0, thesis="n/a")


class _Fixed:
    def __init__(self, out):
        self._out = out

    def invoke(self, system, user):
        return self._out


def _runners():
    return {"specialist": _SpecialistRunner(),
            "critic": _Fixed(CriticOutput(counter_thesis="c")),
            "decision": _Fixed(DecisionOutput(
                recommendation=Recommendation.BUY, confidence=0.8,
                rationale="ranked on ROIC + earnings yield."))}


UNIVERSE = ["A", "B", "C", "D"]


def _run(coverage: str):
    return run_rank_pipeline(
        UNIVERSE, "magic_formula_v1", council_mode="narrator",
        narrate_coverage=coverage, strategies_dir=STRAT_DIR,
        adapter=_Adapter(), runners=_runners(), today=date(2026, 6, 30))


def test_buys_only_is_the_default_and_narrates_only_buy_tier():
    default = _run("buys_only")
    # default value flows into meta
    assert default.meta["narrate_coverage"] == "buys_only"
    buys = [r.ticker for r in default.ranked if r.verdict == "buy"]
    assert buys, "fixture must have at least one BUY"
    # only BUY-tier names got a narrative
    assert set(default.narratives) == set(buys)
    # and NOT every ranked name (the fixture spreads names across tiers)
    assert len(default.narratives) < len(default.ranked)


def test_all_narrates_every_ranked_name():
    full = _run("all")
    assert full.meta["narrate_coverage"] == "all"
    ranked = [r.ticker for r in full.ranked]
    assert set(full.narratives) == set(ranked)
    assert full.meta["shortlist"] == ranked


def test_ranker_verdicts_byte_unchanged_across_coverage():
    buys_only, full = _run("buys_only"), _run("all")

    def _verdicts(res):
        return [(r.ticker, r.verdict, round(r.combined_rank, 6),
                 r.rank_position) for r in res.ranked]

    # The deterministic ranking (order, verdicts, rank-sums) is identical — coverage
    # changes only which names are NARRATED, never the verdict-of-record.
    assert _verdicts(buys_only) == _verdicts(full)
