"""ETF-1 ITEM 5 — baseline + mirror acceptance, offline with mocked adapters.

The live baselines/mirror reports are generated and committed from the runtime side
(this sandbox has no network); these tests exercise the SAME machinery + the REAL
pipeline with mocked adapters, so the acceptance (0 ranked, all kind-gated, delisted
UNRATEABLE) is verified deterministically here.
"""

from datetime import date
from pathlib import Path

from aristos_council.data.adapter import (
    DataUnavailable, Fundamentals, MarketDataAdapter, PriceBar, PriceHistory,
)
from aristos_council.etf_baselines import (
    format_baseline_markdown,
    format_mirror_markdown,
    mirror_summary,
)
from aristos_council.pipeline import run_rank_pipeline
from aristos_council.universe import load_universe_by_id

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
UNIV_DIR = Path(__file__).resolve().parents[1] / "universes"

_DIV = {
    "VIG": (0.04, 129462992896, 0.0151), "VYM": (0.04, 96168181760, 0.023),
    "SCHD": (0.06, 95734071296, 0.033), "DVY": (0.38, 22900787200, 0.0337),
    "SDY": (0.35, 21393180672, 0.0245), "NOBL": (0.35, 11533949952, 0.0207),
    "HDV": (0.08, 13659947008, 0.029), "SPYD": (0.07, 7374172160, 0.0426),
    "DGRO": (0.08, 41227829248, 0.0195), "FVD": (0.62, 8036226560, 0.0232),
}


def _rising(n=300, base=100.0):
    return PriceHistory(ticker="X", bars=[
        PriceBar(day=date(2026, 1, 1), open=base, high=base, low=base,
                 close=base + 0.1 * i, adj_close=base + 0.1 * i, volume=10)
        for i in range(n)])


class _EtfAdapter(MarketDataAdapter):
    """Every name is a rateable ETF (quoteType ETF, rising prices)."""

    name = "fake"

    def get_fundamentals(self, ticker):
        er, ta, dy = _DIV.get(ticker, (0.1, 1e10, 0.02))
        return Fundamentals(ticker=ticker, company_name=ticker, quote_type="ETF",
                            market_cap=5e11, dividend_yield=dy,
                            net_expense_ratio=er, total_assets=ta)

    def get_price_history(self, ticker, *, start, end):
        return _rising()

    def get_dividend_history(self, ticker, *, start, end):
        return []


class _StockAdapter(MarketDataAdapter):
    """Every name is a rateable EQUITY, EXCEPT delisted names that raise (no data)."""

    name = "fake"

    def __init__(self, delisted):
        self._delisted = set(delisted)

    def get_fundamentals(self, ticker):
        if ticker in self._delisted:
            raise DataUnavailable(ticker)
        return Fundamentals(ticker=ticker, company_name=ticker, quote_type="EQUITY",
                            market_cap=5e10, sector="Technology",
                            ebit=[1e9], total_debt=1e9, total_cash=1e9,
                            operating_income=[1e9], tax_provision=[2e8],
                            pretax_income=[1e9], invested_capital=[5e9])

    def get_price_history(self, ticker, *, start, end):
        if ticker in self._delisted:
            raise DataUnavailable(ticker)
        return _rising()

    def get_dividend_history(self, ticker, *, start, end):
        return []


# --- baseline: the ETF lens ranks its whole universe ------------------------ #
def test_baseline_dividend_lens_ranks_all_ten():
    result = run_rank_pipeline(
        list(_DIV), "etf_dividend_v1", strategies_dir=STRAT_DIR,
        ranker_only=True, adapter=_EtfAdapter(), today=date(2026, 6, 30))
    assert len(result.ranked) == 10
    assert result.excluded == [] and result.unrateable == []
    md = format_baseline_markdown(result)
    assert "ETF baseline — etf_dividend_v1" in md
    assert "Sanity anchors" in md
    assert "expense ratio range" in md


# --- mirror A: flagship equity lens on the dividend-ETF universe ------------ #
def test_mirror_flagship_on_dividend_etfs_all_kind_gated():
    result = run_rank_pipeline(
        list(_DIV), "magic_formula_momentum_v1", strategies_dir=STRAT_DIR,
        ranker_only=True, adapter=_EtfAdapter(), today=date(2026, 6, 30))
    s = mirror_summary(result)
    assert s.ranked == 0
    assert s.kind_gated == 10
    assert s.unrateable == 0
    # every exclusion is the asset-kind wall, verbatim
    assert all(w == "asset kind 'ETF' outside this strategy's scope"
               for _, w in result.excluded)
    md = format_mirror_markdown(result, expectation="0 ranked, 10 kind-gated")
    assert "ranked: **0**" in md and "kind-gated: **10**" in md


# --- mirror B: ETF dividend lens on the stock universe ---------------------- #
def test_mirror_etf_lens_on_stock_universe():
    universe = load_universe_by_id("growth_40_v1", UNIV_DIR)
    delisted = ["PARA", "WBA"]
    result = run_rank_pipeline(
        list(universe.tickers), "etf_dividend_v1", strategies_dir=STRAT_DIR,
        ranker_only=True, adapter=_StockAdapter(delisted), today=date(2026, 6, 30))
    s = mirror_summary(result)
    assert s.ranked == 0
    # PARA/WBA are UNRATEABLE (no data -> no confirmed kind, so NOT kind-gated)
    assert set(s.unrateable_names) == set(delisted)
    # every other name (the 38 equities) is kind-gated with the verbatim message
    assert s.kind_gated == len(universe.tickers) - len(delisted)
    assert all(w == "asset kind 'Equity' outside this strategy's scope"
               for t, w in result.excluded if t not in delisted)
