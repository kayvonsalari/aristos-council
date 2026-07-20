"""ETFCORE-1 — the core-market ETF lens + the euro-investable UCITS core universe.

Rank-first, no screens (house doctrine); three factors (cost, size, trend) — deliberately
NO distribution_yield (core cohorts mix ACC/DIST classes, yield is not the criterion). The
asset-kind gate stays active (a stock mock is rejected), and the sibling etf_dividend /
etf_growth lenses are byte-unchanged by this addition.
"""

from datetime import date
from pathlib import Path

from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory,
)
from aristos_council.demo_surface import is_validation_universe
from aristos_council.pipeline import _rank_stage
from aristos_council.strategy.discovery import visible_rank_strategies
from aristos_council.strategy.rank_loader import load_rank_strategy
from aristos_council.universe import list_universes, load_universe_by_id

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
UNIV_DIR = Path(__file__).resolve().parents[1] / "universes"

# The honesty note the core lens MUST carry verbatim (ETFCORE-1 ITEM 1).
CORE_HONESTY_NOTE = (
    "core funds differ mainly in what they track and what they charge; tracking quality "
    "and index choice are not vendor-measurable — this lens compares cost, scale and "
    "trend among self-declared broad-market funds, nothing more; the fee factor matters "
    "MOST here because these are the largest, longest-held positions")

CORE_UCITS = ["VWCE.DE", "IWDA.AS", "SXR8.DE", "CSPX.L", "VUSA.AS", "EUNL.DE",
              "SPYY.DE", "VGWL.DE"]


# --- ITEM 1: the lens loads, is shaped as specced, carries the honesty note ---- #
def test_core_lens_shape_and_verbatim_honesty_note():
    s = load_rank_strategy(STRAT_DIR / "etf_core_v1.yaml")
    assert s.display_name == "Core Market ETFs"
    assert s.asset_kinds == ["etf"]
    # three factors, cost/size/trend — and DELIBERATELY no distribution_yield
    assert [f.name for f in s.factors] == ["expense_ratio", "fund_size", "momentum_12m"]
    assert "distribution_yield" not in {f.name for f in s.factors}
    assert s.missing == "neutral"                     # abstain, never exclude
    assert s.suggested_universes == ["etf_core_ucits_v1"]
    assert s.council_screen_strategy is None          # rank-first, no screen
    assert s.prefilter_screen is False
    # the honesty note travels with the lens, verbatim
    assert CORE_HONESTY_NOTE in s.rationale


def test_core_lens_is_a_visible_rank_strategy():
    ids = {s.id for s in visible_rank_strategies(STRAT_DIR)}
    assert "etf_core_v1" in ids


# --- ITEM 2: the UCITS core universe is discovered, front-stage, observation-only #
def test_core_ucits_universe_loads_with_all_eight_tickers():
    u = load_universe_by_id("etf_core_ucits_v1", UNIV_DIR)
    assert u.tickers == CORE_UCITS
    assert u.display_name == "Core Market ETFs (UCITS)"
    assert u.role == "euro-investable exploration — observation only"


def test_core_ucits_universe_documents_static_layer_and_mixed_share_classes():
    u = load_universe_by_id("etf_core_ucits_v1", UNIV_DIR)
    text = (u.description + " " + u.rationale).lower()
    assert "euro-investable" in text
    assert "static" in text and "eodhd" in text
    # ACC/DIST deliberately mixed because yield is not ranked
    assert "acc" in text and "dist" in text


def test_core_ucits_universe_is_discovered_front_stage():
    by_id = {u.id: u for u in list_universes(UNIV_DIR)}
    assert "etf_core_ucits_v1" in by_id
    # an "observation only" role is not the "never graded" backstage marker
    assert not is_validation_universe(by_id["etf_core_ucits_v1"])


def test_core_lens_suggests_the_ucits_universe():
    s = load_rank_strategy(STRAT_DIR / "etf_core_v1.yaml")
    assert "etf_core_ucits_v1" in s.suggested_universes
    for uid in s.suggested_universes:
        assert load_universe_by_id(uid, UNIV_DIR).id == uid


# --- the asset-kind gate stays active (a stock mock is rejected) --------------- #
def _rising(n=300, base=100.0):
    return PriceHistory(ticker="X", bars=[
        PriceBar(day=date(2026, 1, 1), open=base, high=base, low=base,
                 close=base + 0.1 * i, adj_close=base + 0.1 * i, volume=10)
        for i in range(n)])


class _KindAdapter(MarketDataAdapter):
    name = "fake"

    def __init__(self, kinds):
        self._kinds = kinds

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, market_cap=5e10,
                            net_expense_ratio=0.05, total_assets=5e10,
                            quote_type=self._kinds[ticker])

    def get_price_history(self, ticker, *, start, end):
        return _rising()

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_core_lens_gates_a_stock_mock_out():
    strat = load_rank_strategy(STRAT_DIR / "etf_core_v1.yaml")
    adapter = _KindAdapter({"AAPL": "EQUITY", "VWCE.DE": "ETF"})
    ranked, excluded, _, _ = _rank_stage(
        ["AAPL", "VWCE.DE"], strat, adapter, today=date(2026, 6, 30))
    # the equity is gated out with the verbatim asset-kind message
    assert ("AAPL", "asset kind 'Equity' outside this strategy's scope") in excluded
    ranked_ids = {r.ticker for r in ranked if not r.excluded}
    assert "VWCE.DE" in ranked_ids           # the ETF is ranked
    assert "AAPL" not in ranked_ids


# --- the sibling lenses are byte-unchanged by this addition -------------------- #
def test_dividend_and_growth_lenses_byte_unchanged():
    div = load_rank_strategy(STRAT_DIR / "etf_dividend_v1.yaml")
    grw = load_rank_strategy(STRAT_DIR / "etf_growth_v1.yaml")
    # dividend lens shape unchanged (still carries distribution_yield first)
    assert [f.name for f in div.factors] == [
        "distribution_yield", "expense_ratio", "fund_size", "momentum_12m"]
    assert div.suggested_universes == ["etf_dividend_us_v1", "etf_dividend_ucits_v1"]
    # growth lens shape unchanged
    assert [f.name for f in grw.factors] == ["expense_ratio", "momentum_12m", "fund_size"]
    assert grw.suggested_universes == ["etf_growth_us_v1", "etf_growth_ucits_v1"]
