"""ETF-1 ITEM 2 — the asset-kind gate: the wall between asset classes.

Confirmed-only (a missing quoteType never gates, mirroring the sector gate); fires
BEFORE any screen/factor path; equity lenses stay byte-unchanged on equities.
"""

from datetime import date
from pathlib import Path

from aristos_council.company_check import run_company_check
from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory,
)
from aristos_council.factors import (
    asset_kind_display,
    is_asset_kind_out_of_scope,
    normalize_asset_kind,
)
from aristos_council.pipeline import _rank_stage
from aristos_council.strategy.rank_loader import (
    RankFactorSpec, RankStrategy, load_rank_strategy,
)

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
UNIV_DIR = Path(__file__).resolve().parents[1] / "universes"
RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"

# Every existing STOCK rank lens must declare equity-only scope (5 visible + 2 hidden).
STOCK_LENSES = [
    "conservative_plus_v1", "financials_v1", "growth_garp_v1", "growth_garp_v2",
    "magic_formula_momentum_v1", "magic_formula_raw_v1", "magic_formula_v1",
]


# --- the pure helper -------------------------------------------------------- #
def test_normalize_asset_kind():
    assert normalize_asset_kind("EQUITY") == "equity"
    assert normalize_asset_kind("ETF") == "etf"
    assert normalize_asset_kind("  etf ") == "etf"
    assert normalize_asset_kind(None) is None
    assert normalize_asset_kind("") is None
    assert normalize_asset_kind("MUTUALFUND") == "mutualfund"


def test_asset_kind_display_verbatim():
    assert asset_kind_display("ETF") == "ETF"
    assert asset_kind_display("EQUITY") == "Equity"
    assert asset_kind_display(None) == ""


def test_gate_gates_an_etf_in_an_equity_lens():
    assert is_asset_kind_out_of_scope("ETF", ["equity"]) is True


def test_gate_admits_an_equity_in_an_equity_lens():
    assert is_asset_kind_out_of_scope("EQUITY", ["equity"]) is False


def test_gate_admits_an_etf_in_an_etf_lens():
    assert is_asset_kind_out_of_scope("ETF", ["etf"]) is False


def test_gate_never_gates_a_missing_kind():
    # confirmed-only: absent provider data can't silently drop a name
    assert is_asset_kind_out_of_scope(None, ["equity"]) is False
    assert is_asset_kind_out_of_scope("", ["etf"]) is False


def test_empty_asset_kinds_scopes_nothing():
    assert is_asset_kind_out_of_scope("ETF", []) is False


# --- the gate in the pipeline rank stage ------------------------------------ #
def _rising(n=300, base=100.0):
    return PriceHistory(ticker="X", bars=[
        PriceBar(day=date(2026, 1, 1), open=base, high=base, low=base,
                 close=base + 0.1 * i, adj_close=base + 0.1 * i, volume=10)
        for i in range(n)])


class _KindAdapter(MarketDataAdapter):
    """Serves whatever quoteType the fixture maps per ticker, all with rising prices."""

    name = "fake"

    def __init__(self, kinds: dict):
        self._kinds = kinds        # ticker -> quoteType (or None)

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, market_cap=5e10,
                            quote_type=self._kinds[ticker])

    def get_price_history(self, ticker, *, start, end):
        return _rising()

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _equity_only_strategy():
    return RankStrategy(id="kind_test_v1", name="kind test", version=1,
                        asset_kinds=["equity"],
                        factors=[RankFactorSpec(name="momentum_12m")])


def test_pipeline_gates_etf_out_of_an_equity_lens_with_exact_message():
    strat = _equity_only_strategy()
    adapter = _KindAdapter({"AAPL": "EQUITY", "QQQ": "ETF"})
    ranked, excluded, _, _ = _rank_stage(
        ["AAPL", "QQQ"], strat, adapter, today=date(2026, 6, 30))
    # QQQ (ETF) gated out with the verbatim message
    assert ("QQQ", "asset kind 'ETF' outside this strategy's scope") in excluded
    # AAPL (equity) is ranked, not gated — byte-unchanged behaviour on an equity
    ranked_ids = {r.ticker for r in ranked if not r.excluded}
    assert "AAPL" in ranked_ids
    assert "QQQ" not in ranked_ids


def test_pipeline_gates_equity_out_of_an_etf_lens():
    strat = RankStrategy(id="etf_kind_test_v1", name="etf test", version=1,
                         asset_kinds=["etf"],
                         factors=[RankFactorSpec(name="momentum_12m")])
    adapter = _KindAdapter({"AAPL": "EQUITY", "QQQ": "ETF"})
    _, excluded, _, _ = _rank_stage(
        ["AAPL", "QQQ"], strat, adapter, today=date(2026, 6, 30))
    assert ("AAPL", "asset kind 'Equity' outside this strategy's scope") in excluded


def test_pipeline_confirmed_only_missing_kind_never_gates():
    strat = _equity_only_strategy()
    adapter = _KindAdapter({"AAPL": None})       # no quoteType reported
    ranked, excluded, _, _ = _rank_stage(
        ["AAPL"], strat, adapter, today=date(2026, 6, 30))
    assert not any("asset kind" in why for _, why in excluded)
    assert "AAPL" in {r.ticker for r in ranked if not r.excluded}


# --- every stock lens declares equity-only scope ---------------------------- #
def test_all_stock_lenses_declare_equity_scope():
    for sid in STOCK_LENSES:
        s = load_rank_strategy(STRAT_DIR / f"{sid}.yaml")
        assert s.asset_kinds == ["equity"], sid


# --- Company Check surfaces the asset-kind gate ----------------------------- #
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


def test_company_check_shows_asset_kind_gate_fail_for_an_etf():
    etf = Fundamentals(ticker="QQQ", company_name="Invesco QQQ", quote_type="ETF",
                       market_cap=5e11)
    r = run_company_check(
        "QQQ", "magic_formula_raw_v1", "", adapter=_OneName(etf),
        strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, runs_dir=RUNS_DIR,
        today=date(2026, 6, 30))
    kind = [g for g in r.gates if g.name == "asset_kind"]
    assert kind and kind[0].status == "FAIL"
    assert "outside this strategy's scope" in kind[0].detail
    assert "'ETF'" in kind[0].detail


def test_company_check_asset_kind_gate_passes_for_an_equity():
    eq = Fundamentals(ticker="AAPL", company_name="Apple", quote_type="EQUITY",
                      market_cap=3e12)
    r = run_company_check(
        "AAPL", "magic_formula_raw_v1", "", adapter=_OneName(eq),
        strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR, runs_dir=RUNS_DIR,
        today=date(2026, 6, 30))
    kind = [g for g in r.gates if g.name == "asset_kind"]
    assert kind and kind[0].status == "PASS"
