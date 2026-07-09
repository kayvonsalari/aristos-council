"""magic_formula_raw_v1 — canonical Greenblatt + momentum, NO screens (RAW-1).

The raw variant ranks the whole universe on the SAME three factors as the flagship, with
the sector + market-cap gates retained but NO threshold screening. It is the comparison
lens: a name the flagship excludes but the raw method ranks is a name the screens — not
the factors — kept off the list.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.pipeline import run_rank_pipeline
from aristos_council.strategy.detail import strategy_detail
from aristos_council.strategy.discovery import rank_strategies, visible_rank_strategies
from aristos_council.strategy.rank_loader import load_rank_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


def test_raw_is_discovered_visible_and_matches_the_flagship_factors_and_gates():
    visible = {s.id for s in visible_rank_strategies(STRAT_DIR)}
    assert "magic_formula_raw_v1" in visible                          # visible (now four)
    assert len(visible) == 4
    assert any(s.id == "magic_formula_raw_v1" and s.kind == "rank"
               for s in rank_strategies(STRAT_DIR))

    raw = load_rank_strategy(STRAT_DIR / "magic_formula_raw_v1.yaml")
    flagship = load_rank_strategy(STRAT_DIR / "magic_formula_momentum_v1.yaml")
    # factors + verdict cut copied EXACTLY from the flagship
    assert [f.name for f in raw.factors] == [f.name for f in flagship.factors]
    assert raw.cut == flagship.cut and raw.missing == flagship.missing
    # sector + market-cap gates retained exactly
    assert raw.exclude_sectors == flagship.exclude_sectors
    assert raw.min_market_cap == flagship.min_market_cap
    # NO screen: no lens, no prefilter
    assert raw.council_screen_strategy is None
    assert raw.prefilter_screen is False


def test_raw_strategy_tab_renders_with_no_screen_criteria():
    # 4C ITEM 3 guarantee: the generic Strategy-tab builder renders it with zero UI code.
    d = strategy_detail("magic_formula_raw_v1", STRAT_DIR)
    assert d.display_name == "Magic Formula RAW (canonical, no screens)"
    assert d.criteria == [] and d.screen_source == "own criteria"     # no screen
    assert {f.name for f in d.factors} == {"roic", "earnings_yield", "momentum_12m"}
    assert "quintile" in d.cut_rule
    assert any(g.name == "sector" for g in d.gates)                   # gates retained
    assert any(g.name == "min_market_cap" for g in d.gates)


# --------------------------------------------------------------------------- #
# A mocked run: nothing screens out but the sector/cap gates.
# --------------------------------------------------------------------------- #
_F = {
    # HI: high ROIC; LO: low ROIC (~5%, below the flagship's 12% floor);
    # FIN: a financial (sector-gated in both); SMALL: below the market-cap floor.
    "HI":    dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
                  operating_income=[3000.0] * 4, tax_provision=[600.0] * 4,
                  pretax_income=[2900.0] * 4, invested_capital=[5000.0] * 4),
    "LO":    dict(market_cap=2e10, sector="Technology", ebit=[500.0], pe_ratio=40.0,
                  operating_income=[500.0] * 4, tax_provision=[100.0] * 4,
                  pretax_income=[480.0] * 4, invested_capital=[8000.0] * 4),
    "FIN":   dict(market_cap=2e10, sector="Financial Services", ebit=[2000.0],
                  pe_ratio=12.0, operating_income=[2000.0] * 4, tax_provision=[400.0] * 4,
                  pretax_income=[1900.0] * 4, invested_capital=[6000.0] * 4),
    "SMALL": dict(market_cap=1e9, sector="Technology", ebit=[400.0], pe_ratio=15.0,
                  operating_income=[400.0] * 4, tax_provision=[80.0] * 4,
                  pretax_income=[380.0] * 4, invested_capital=[1000.0] * 4),
}


class _Adapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, **_F[ticker])

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_raw_run_has_no_screen_exclusions_only_sector_and_cap_gates():
    r = run_rank_pipeline(
        ["HI", "LO", "FIN", "SMALL"], "magic_formula_raw_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_Adapter(), today=date(2026, 6, 30))

    ranked = {x.ticker for x in r.ranked}
    # the low-ROIC name is RANKED here (the flagship screens it out on min_roic)
    assert "HI" in ranked and "LO" in ranked
    # only the sector + cap gates exclude — never a 'screen:' reason
    reasons = dict(r.excluded)
    assert "sector excluded" in reasons["FIN"]
    assert "market cap" in reasons["SMALL"]
    assert not any("screen:" in why for _, why in r.excluded)

    # contrast: the flagship DOES screen the low-ROIC name out
    fr = run_rank_pipeline(
        ["HI", "LO", "FIN", "SMALL"], "magic_formula_momentum_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_Adapter(), today=date(2026, 6, 30))
    assert any(t == "LO" and "min_roic" in why for t, why in fr.excluded)
