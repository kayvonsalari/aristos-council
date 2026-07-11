"""Sector INCLUSION gate + the financials_v1 lens (FIN-1 ITEM 2).

The include_sectors knob is the mirror of exclude_sectors: financials_v1 admits ONLY
financials (P/B and ROE are their yardstick), gating a confirmed out-of-scope sector
with "sector '<X>' outside this strategy's scope" while never gating a missing sector
(confirmed-only, the never-drop-on-unknown discipline). The existing exclude gate is
untouched. financials_v1 becomes the fifth visible rank strategy and renders on the
Strategy tab with zero UI-code changes.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.company_check import run_company_check
from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory,
)
from aristos_council.factors import is_sector_excluded, is_sector_out_of_scope
from aristos_council.pipeline import _rank_stage
from aristos_council.strategy.detail import strategy_detail
from aristos_council.strategy.discovery import visible_rank_strategies
from aristos_council.strategy.rank_loader import load_rank_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
UNIV_DIR = Path(__file__).resolve().parents[1] / "universes"
RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"


# --- the pure inclusion-gate helper ----------------------------------------- #
def test_include_gate_admits_a_financial_sector():
    assert is_sector_out_of_scope("Financial Services",
                                  ["Financial Services", "Financials"]) is False


def test_include_gate_gates_a_non_financial_sector():
    assert is_sector_out_of_scope("Technology",
                                  ["Financial Services", "Financials"]) is True


def test_include_gate_never_gates_a_missing_sector():
    # confirmed-only: absent provider data can't silently drop a name
    assert is_sector_out_of_scope(None, ["Financial Services"]) is False


def test_empty_include_list_scopes_nothing():
    assert is_sector_out_of_scope("Technology", []) is False


def test_exclude_gate_is_untouched():
    # the existing exclusion gate still behaves exactly as before
    assert is_sector_excluded("Financial Services", ["Financial Services"]) is True
    assert is_sector_excluded("Technology", ["Financial Services"]) is False


# --- the gate in the pipeline rank stage ------------------------------------ #
def _rising(n=300, base=100.0):
    return PriceHistory(ticker="X", bars=[
        PriceBar(day=date(2026, 1, 1), open=base, high=base, low=base,
                 close=base + 0.1 * i, adj_close=base + 0.1 * i, volume=10)
        for i in range(n)])


class _TwoSectorAdapter(MarketDataAdapter):
    """JPM = Financial Services (in scope); MSFT = Technology (out of scope)."""

    name = "fake"
    _F = {
        "JPM": dict(name="JPMorgan", sector="Financial Services", market_cap=6e11,
                    price_to_book=1.8, return_on_equity=0.16),
        "MSFT": dict(name="Microsoft", sector="Technology", market_cap=3e12,
                     price_to_book=12.0, return_on_equity=0.40),
    }

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, **self._F[ticker])

    def get_price_history(self, ticker, *, start, end):
        return _rising()

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_pipeline_gates_out_of_scope_and_ranks_in_scope():
    fin = load_rank_strategy(STRAT_DIR / "financials_v1.yaml")
    ranked, excluded, _, _ = _rank_stage(
        ["JPM", "MSFT"], fin, _TwoSectorAdapter(), today=date(2026, 6, 30))
    # MSFT (Technology) gated out of scope with the exact message
    assert ("MSFT", "sector 'Technology' outside this strategy's scope") in excluded
    # JPM (Financial Services) is ranked, not gated
    ranked_ids = {r.ticker for r in ranked if not r.excluded}
    assert "JPM" in ranked_ids
    assert all(r.ticker != "MSFT" for r in ranked if not r.excluded)


# --- discovery: fifth visible rank strategy --------------------------------- #
def test_financials_v1_is_the_fifth_visible_rank_strategy():
    vis = visible_rank_strategies(STRAT_DIR)
    ids = [s.id for s in vis]
    assert "financials_v1" in ids
    assert len(vis) == 5


# --- Strategy tab renders it with zero UI changes --------------------------- #
def test_strategy_tab_renders_financials_lens():
    detail = strategy_detail("financials_v1", STRAT_DIR)
    factor_names = {f.name for f in detail.factors}
    assert factor_names == {"price_to_book", "return_on_equity", "momentum_12m"}
    # the inclusion gate row renders with its rationale (mirror of the exclusion row)
    scope = [g for g in detail.gates if g.name == "sector_scope"]
    assert scope and "admits only" in scope[0].value
    assert "priced by" in scope[0].rationale       # the P/B-and-ROE rationale text


# --- UNI-1 ITEM 3: Strategy tab shows the suggested-universe pairing --------- #
def test_strategy_tab_shows_suggested_universes_as_display_names():
    detail = strategy_detail("financials_v1", STRAT_DIR)
    # resolved to the universe DISPLAY NAME, not the raw id
    assert detail.suggested_universes == ["Financials 16"]


def test_strategy_tab_omits_suggested_universes_when_field_absent():
    # magic_formula_v1 declares no suggested_universes -> empty (header line not rendered)
    detail = strategy_detail("magic_formula_v1", STRAT_DIR)
    assert detail.suggested_universes == []


# --- Company Check GATES surfaces the scope gate (mirror parity) ------------- #
class _OneName(MarketDataAdapter):
    name = "fake"

    def __init__(self, fundamentals):
        self._f = fundamentals

    def get_fundamentals(self, ticker):
        return self._f

    def get_price_history(self, ticker, *, start, end):
        return _rising()

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_company_check_shows_out_of_scope_gate():
    tech = Fundamentals(ticker="MSFT", company_name="Microsoft", sector="Technology",
                        market_cap=3e12, price_to_book=12.0, return_on_equity=0.40)
    r = run_company_check(
        "MSFT", "financials_v1", "", adapter=_OneName(tech),
        strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, runs_dir=RUNS_DIR,
        today=date(2026, 6, 30))
    scope = [g for g in r.gates if g.name == "sector_scope"]
    assert scope and scope[0].status == "FAIL"
    assert "outside this strategy's scope" in scope[0].detail
