"""growth_screen_v2 / growth_garp_v2 — non-gating momentum (4C-FIX-1).

v1's growth screen gated on min_price_momentum, which double-counted the momentum_12m
rank factor and hard-vetoed dip names (ADBE, -41% 12m). v2 removes the momentum GATE:
dip names are dragged down the order by the factor, not screened out.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.pipeline import run_rank_pipeline
from aristos_council.strategy.detail import strategy_detail
from aristos_council.strategy.discovery import visible_rank_strategies
from aristos_council.strategy.loader import load_strategy
from aristos_council.strategy.rank_loader import load_rank_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


def test_growth_screen_v2_is_v1_minus_the_momentum_gate():
    v1 = load_strategy(STRAT_DIR / "growth_screen_v1.yaml")
    v2 = load_strategy(STRAT_DIR / "growth_screen_v2.yaml")
    v1_names = [c.name for c in v1.criteria]
    v2_names = [c.name for c in v2.criteria]
    # exactly one change: min_price_momentum removed
    assert "min_price_momentum" in v1_names
    assert "min_price_momentum" not in v2_names
    assert v2_names == [n for n in v1_names if n != "min_price_momentum"]
    # v1 is untouched semantically (still gates momentum)
    assert v1.id == "growth_screen_v1" and v2.id == "growth_screen_v2" and v2.version == 2
    # the other four criteria keep their thresholds byte-for-byte
    v1_by = {c.name: c.threshold for c in v1.criteria}
    for c in v2.criteria:
        assert c.threshold == v1_by[c.name]


# --------------------------------------------------------------------------- #
# ITEM 2 — growth_garp_v2 supersedes v1; v1 hidden; v2 visible + rendered.
# --------------------------------------------------------------------------- #
def test_v2_is_visible_v1_hidden_and_lens_points_to_v2():
    visible = {s.id for s in visible_rank_strategies(STRAT_DIR)}
    # ETF-1 ITEM 3 added the two visible ETF lenses to the set; ETFCORE-1 ITEM 1 the core.
    assert visible == {"conservative_plus_v1", "magic_formula_momentum_v1",
                       "growth_garp_v2", "magic_formula_raw_v1", "financials_v1",
                       "etf_dividend_v1", "etf_growth_v1", "etf_core_v1"}
    v2 = load_rank_strategy(STRAT_DIR / "growth_garp_v2.yaml")
    v1 = load_rank_strategy(STRAT_DIR / "growth_garp_v1.yaml")
    assert v2.council_screen_strategy == "growth_screen_v2" and v2.version == 2
    assert v2.created == "2026-07-09"
    assert v1.ui == "hidden"                                  # v1 still loadable, unlisted
    # factors unchanged from v1 (the quad)
    assert [f.name for f in v2.factors] == [f.name for f in v1.factors]


def test_v2_renders_in_the_strategy_tab_with_zero_ui_changes():
    d = strategy_detail("growth_garp_v2", STRAT_DIR)
    assert d.display_name == "Growth at a Reasonable Price (GARP)" and d.version == 2
    assert d.screen_source == "lens: growth_screen_v2"
    # the lens has NO momentum criterion in v2
    assert "min_price_momentum" not in {c.name for c in d.criteria}
    assert {f.name for f in d.factors} == {"revenue_growth", "roic", "earnings_yield",
                                           "momentum_12m"}


# --------------------------------------------------------------------------- #
# A dip name (negative 12m momentum, everything else passing) is RANKED under v2,
# EXCLUDED under v1 (the momentum gate).
# --------------------------------------------------------------------------- #
_GROWTH = dict(
    market_cap=2e10, sector="Technology",
    total_revenue=[200.0, 174, 151, 131],          # ~15%/yr top-line growth
    operating_income=[120.0, 104, 90, 78],          # ~15%/yr OI growth (PEG denominator)
    ebit=[120.0], tax_provision=[24.0, 21, 18, 16],
    pretax_income=[115.0, 100, 87, 75], invested_capital=[500.0] * 4,
    pe_ratio=18.0, total_debt=1e9, total_cash=2e9)


class _Adapter(MarketDataAdapter):
    """DIP: prices fall (~-38% 12m). UP1/UP2: prices rise. All pass the growth criteria."""

    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, **_GROWTH)

    def get_price_history(self, ticker, *, start, end):
        if ticker == "DIP":
            closes = [200.0 - 0.3 * i for i in range(260)]      # declining -> neg momentum
        else:
            closes = [100.0 + 0.2 * i for i in range(260)]      # rising -> pos momentum
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=c, high=c, low=c, close=c,
                     adj_close=c, volume=10) for c in closes])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _run(strategy):
    return run_rank_pipeline(
        ["DIP", "UP1", "UP2"], strategy, ranker_only=True, strategies_dir=STRAT_DIR,
        adapter=_Adapter(), today=date(2026, 6, 30))


def test_dip_name_is_gated_out_under_v1_but_ranked_under_v2():
    v1 = _run("growth_garp_v1")
    assert "DIP" not in {r.ticker for r in v1.ranked}         # gated out under v1
    assert any(t == "DIP" and "min_price_momentum" in why for t, why in v1.excluded)

    v2 = _run("growth_garp_v2")
    assert "DIP" in {r.ticker for r in v2.ranked}             # ranked under v2
    assert not any(t == "DIP" for t, _ in v2.excluded)
    # the momentum factor still drags it: DIP is last by combined rank (worst momentum)
    order = [r.ticker for r in v2.ranked]
    assert order[-1] == "DIP"
