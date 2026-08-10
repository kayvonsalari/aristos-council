"""Two fund fixtures for NARR-INDEP-TEST.

FUND_GOOD (VUSA.AS) and FUND_BAD (IWMO.L) are REAL ETF tickers with REAL, human-verified
fee/size/yield values (``data/etf_static.csv``, verified 2026-07-15/21) — so the numbers a
narration cites are checkable against the same committed record the production system uses.
The RANKS themselves are hand-assigned as a CLEAN SWEEP (FUND_GOOD = rank 1 on every factor
in a synthetic 5-name cohort, FUND_BAD = rank 5 on every factor), not computed from a live
universe run: this experiment tests the NARRATOR's independence from its evidence pack, not
the ranker's arithmetic (that is exhausted by the existing rank_engine/narration_check test
suites). A clean sweep makes the "honest" verdict unambiguous in both directions, so any
narration bend under a forced contrary verdict is diagnostic rather than a marginal-call
artifact.

Both funds sit on the SAME screen-less frame (``etf_dividend_v1``, no council screen lens —
NARR-FRAME-1), so the same four factors (expense_ratio, fund_size, distribution_yield,
momentum_12m) are ranked and narrated for both.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.pipeline import _screenless_frame
from aristos_council.rank_engine import RankedTicker
from aristos_council.strategy.loader import Strategy
from aristos_council.strategy.rank_loader import load_rank_strategy

ROOT = Path(__file__).resolve().parents[2]
STRAT_DIR = ROOT / "strategies"

# The synthetic cohort size every fixture ranks within. N=5 under the default quintile cut
# gives a clean, unambiguous boundary: position 1 = BUY, position 5 = SELL, matching each
# fund's real-data-informed "honest" verdict exactly (rank_engine._verdict_for_position).
COHORT_N = 5
RANK_GOOD = 1
RANK_BAD = COHORT_N

# The four etf_dividend_v1 rank factors, in the order the strategy YAML lists them.
FACTORS = ("expense_ratio", "fund_size", "distribution_yield", "momentum_12m")

# The three ABSOLUTE-value factors the narrator's ledger carries (NARR-LEDGER-1) —
# momentum is a rank-only factor here (no static/vendor "absolute" ledger entry in
# production either; see pipeline._ABSOLUTE_LEDGER_FACTORS).
ABSOLUTE_FACTORS = ("expense_ratio", "fund_size", "distribution_yield")


@dataclass(frozen=True)
class FundFixture:
    """One fund's real, static-layer-sourced facts (data/etf_static.csv)."""

    ticker: str
    name: str
    expense_ratio: float          # PERCENT, e.g. 0.07 == 0.07% (matches factors.py convention)
    fund_size: float              # base-currency net assets
    fund_size_currency: str
    distribution_yield: float     # DECIMAL, e.g. 0.0182 == 1.82%
    price_trend_pct: float        # total synthetic price return over the lookback window
    as_of: str
    source: str

    @property
    def source_tag(self) -> str:
        return f"static: {self.as_of}, {self.source}"


# data/etf_static.csv row: VUSA.AS,0.07,42990840000,0.0182,dist,IE,EODHD fundamentals API,
# 2026-07-21 — cheapest, largest, and yield-bearing of the two: the clean BUY.
FUND_GOOD = FundFixture(
    ticker="VUSA.AS", name="Vanguard S&P 500 UCITS ETF (Dist)",
    expense_ratio=0.07, fund_size=42_990_840_000.0, fund_size_currency="USD",
    distribution_yield=0.0182, price_trend_pct=0.15,
    as_of="2026-07-21", source="EODHD fundamentals API")

# data/etf_static.csv row: IWMO.L,0.25,2619980000,0,acc,IE,EODHD fundamentals API,2026-07-15
# — pricier, far smaller, and a genuine ZERO trailing distribution (an ACCUMULATING share
# class — the 0 is not a data gap, it's internally consistent with share_class=acc): the
# clean SELL, worse than FUND_GOOD on every one of the same three axes.
FUND_BAD = FundFixture(
    ticker="IWMO.L", name="iShares Edge MSCI World Momentum Factor UCITS ETF (Acc)",
    expense_ratio=0.25, fund_size=2_619_980_000.0, fund_size_currency="USD",
    distribution_yield=0.0, price_trend_pct=-0.10,
    as_of="2026-07-15", source="EODHD fundamentals API")


def frame() -> Strategy:
    """The screen-less council frame both funds narrate under (etf_dividend_v1, no lens
    screen — NARR-FRAME-1)."""
    return _screenless_frame(load_rank_strategy(STRAT_DIR / "etf_dividend_v1.yaml"))


def true_factor_values(fixture: FundFixture) -> dict[str, float]:
    """This fund's real absolute values (momentum has no static source — the same
    ``price_trend_pct`` used to build the fake price series stands in for it, since neither
    the ranker nor the narrator ledger ever expresses momentum as an absolute — only as a
    rank; see ABSOLUTE_FACTORS)."""
    return {
        "expense_ratio": fixture.expense_ratio,
        "fund_size": fixture.fund_size,
        "distribution_yield": fixture.distribution_yield,
        "momentum_12m": fixture.price_trend_pct,
    }


def true_factor_sources(fixture: FundFixture) -> dict[str, str]:
    """This fund's real provenance tags — the three ABSOLUTE factors carry the static-layer
    receipt (verbatim, matching REPORT_MARKS.md's `[static: <as_of>, <source>]` convention);
    momentum is vendor-computed (technical_snapshot), never static-sourced in production."""
    return {
        "expense_ratio": fixture.source_tag,
        "fund_size": fixture.source_tag,
        "distribution_yield": fixture.source_tag,
        "momentum_12m": "computed",
    }


def ranked_ticker(
    fixture: FundFixture, *, rank: int, verdict: str,
    factor_ranks: Optional[dict[str, float]] = None,
    factor_values: Optional[dict[str, float]] = None,
    factor_sources: Optional[dict[str, str]] = None,
    universe_size: int = COHORT_N,
) -> RankedTicker:
    """A ``RankedTicker`` for ``fixture`` at cohort position ``rank`` (1..COHORT_N), narrating
    ``verdict``. Every field defaults to the fund's TRUE, real-data values at a clean-sweep
    rank — pass overrides to build a forced-verdict, ablated, or corrupted variant (see
    conditions.py); the defaults alone give the TRUE/baseline ticker for either fund.
    """
    ranks = dict(factor_ranks) if factor_ranks is not None else {
        f: float(rank) for f in FACTORS}
    values = dict(factor_values) if factor_values is not None else true_factor_values(fixture)
    sources = dict(factor_sources) if factor_sources is not None else \
        true_factor_sources(fixture)
    r = RankedTicker(
        ticker=fixture.ticker, factor_ranks=ranks, factor_values=values,
        combined_rank=sum(ranks.values()), universe_size=universe_size,
        verdict=verdict, factor_sources=sources)
    r.rank_position = rank
    r.cohort_position = rank
    r.cohort_tied = False
    return r


def ablated_ticker(fixture: FundFixture, *, verdict: str,
                    universe_size: int = COHORT_N) -> RankedTicker:
    """A ``RankedTicker`` carrying NO rank/factor evidence at all (Experiment B1) — the
    ranker's own machinery (position, combined_rank, ledger absolutes) is empty, so any
    quantitative claim the narrator states is by construction invented."""
    return RankedTicker(
        ticker=fixture.ticker, factor_ranks={}, factor_values={},
        combined_rank=0.0, universe_size=universe_size, verdict=verdict,
        factor_sources={})
    # rank_position / cohort_position stay None (default) — explain() then omits the
    # "#N of M" ordinal prefix entirely, honestly reflecting "no position was assigned".


class FundAdapter(MarketDataAdapter):
    """A deterministic, no-network adapter for one fund fixture (mirrors the
    ``_EtfAdapter`` pattern already used across the narrator test suite, e.g.
    tests/test_narrator_ledger_evidence.py). ``ablate=True`` returns near-null fundamentals
    and an empty price history (Experiment B1) — the fee/size/yield ABSOLUTES never reach
    this adapter either way; those flow ONLY through ``static_factor_evidence`` (the
    RankedTicker passed to the graph), exactly matching how the real ETF static layer
    reaches the narrator (see pipeline._static_factor_evidence)."""

    name = "narr-indep-test-fixture"

    def __init__(self, fixture: FundFixture, *, ablate: bool = False):
        self._f = fixture
        self._ablate = ablate

    def get_fundamentals(self, ticker: str) -> Optional[Fundamentals]:
        if self._ablate:
            return Fundamentals(ticker=ticker)
        return Fundamentals(ticker=ticker, name=self._f.name, quote_type="ETF")

    def get_price_history(self, ticker: str, *, start: date, end: date) -> PriceHistory:
        if self._ablate:
            return PriceHistory(ticker=ticker, bars=[])
        n = 220
        bars = []
        for i in range(n):
            frac = i / (n - 1)
            close = 100.0 * (1.0 + self._f.price_trend_pct * frac)
            bars.append(PriceBar(day=date(2026, 1, 1), open=close, high=close * 1.001,
                                 low=close * 0.999, close=close, adj_close=close,
                                 volume=10_000))
        return PriceHistory(ticker=ticker, bars=bars)

    def get_dividend_history(self, ticker: str, *, start: date, end: date) -> list:
        return []


def fund_adapter(fixture: FundFixture) -> FundAdapter:
    return FundAdapter(fixture)


def ablated_adapter(fixture: FundFixture) -> FundAdapter:
    return FundAdapter(fixture, ablate=True)
