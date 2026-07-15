"""ETF-1 ITEM 3 — the two ETF lenses (rank-first, no screens) and their factors."""

from datetime import date
from pathlib import Path

from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory,
)
from aristos_council.factors import FACTOR_REGISTRY
from aristos_council.pipeline import run_rank_pipeline
from aristos_council.strategy.discovery import visible_rank_strategies
from aristos_council.strategy.rank_loader import load_rank_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"

# The honesty note the growth lens MUST carry verbatim (ETF-1 ITEM 3).
GROWTH_HONESTY_NOTE = (
    "a growth ETF's real quality is its index methodology, which no vendor field "
    "captures — this lens compares costs, size and trend among self-declared growth "
    "funds, nothing more.")


# --- the three new factors -------------------------------------------------- #
def test_etf_factors_registered_with_directions():
    assert FACTOR_REGISTRY["distribution_yield"].direction == "high"
    assert FACTOR_REGISTRY["expense_ratio"].direction == "low"   # cost — lower is better
    assert FACTOR_REGISTRY["fund_size"].direction == "high"


def test_etf_factors_read_the_right_fields():
    from aristos_council.factors import FactorInputs
    fi = FactorInputs(ticker="X", fundamentals=Fundamentals(
        ticker="X", dividend_yield=0.033, net_expense_ratio=0.06, total_assets=9.5e10))
    assert FACTOR_REGISTRY["distribution_yield"].fn(fi) == 0.033
    assert FACTOR_REGISTRY["expense_ratio"].fn(fi) == 0.06
    assert FACTOR_REGISTRY["fund_size"].fn(fi) == 9.5e10
    # missing fields abstain (None), never crash
    empty = FactorInputs(ticker="Y", fundamentals=Fundamentals(ticker="Y"))
    assert FACTOR_REGISTRY["distribution_yield"].fn(empty) is None
    assert FACTOR_REGISTRY["expense_ratio"].fn(empty) is None
    assert FACTOR_REGISTRY["fund_size"].fn(empty) is None


# --- the two lenses load and are shaped as specced -------------------------- #
def test_etf_dividend_lens_shape():
    s = load_rank_strategy(STRAT_DIR / "etf_dividend_v1.yaml")
    assert s.display_name == "Dividend ETFs"
    assert s.asset_kinds == ["etf"]
    assert [f.name for f in s.factors] == [
        "distribution_yield", "expense_ratio", "fund_size", "momentum_12m"]
    assert s.missing == "neutral"                     # abstain, never exclude
    assert s.suggested_universes == ["etf_dividend_us_v1"]
    assert s.council_screen_strategy is None          # rank-first, no screen
    assert s.prefilter_screen is False


def test_etf_growth_lens_shape_and_verbatim_honesty_note():
    s = load_rank_strategy(STRAT_DIR / "etf_growth_v1.yaml")
    assert s.display_name == "Growth ETFs"
    assert s.asset_kinds == ["etf"]
    assert [f.name for f in s.factors] == ["expense_ratio", "momentum_12m", "fund_size"]
    assert s.missing == "neutral"
    assert s.suggested_universes == ["etf_growth_us_v1"]
    # the honesty note travels with the lens, verbatim
    assert GROWTH_HONESTY_NOTE in s.rationale


def test_both_lenses_are_visible_rank_strategies():
    ids = {s.id for s in visible_rank_strategies(STRAT_DIR)}
    assert {"etf_dividend_v1", "etf_growth_v1"} <= ids


# --- a ranker-only run over ETFs ranks them all (no screens, no exclusions) --- #
def _flat(n=300, base=100.0):
    return PriceHistory(ticker="X", bars=[
        PriceBar(day=date(2026, 1, 1), open=base, high=base, low=base,
                 close=base + 0.1 * i, adj_close=base + 0.1 * i, volume=10)
        for i in range(n)])


# ITEM-1 probe values for the dividend set (expense_ratio, total_assets, yield).
_DIV = {
    "VIG": (0.04, 129462992896, 0.0151), "VYM": (0.04, 96168181760, 0.023),
    "SCHD": (0.06, 95734071296, 0.033), "DVY": (0.38, 22900787200, 0.0337),
    "SDY": (0.35, 21393180672, 0.0245), "NOBL": (0.35, 11533949952, 0.0207),
    "HDV": (0.08, 13659947008, 0.029), "SPYD": (0.07, 7374172160, 0.0426),
    "DGRO": (0.08, 41227829248, 0.0195), "FVD": (0.62, 8036226560, 0.0232),
}


class _EtfAdapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        er, ta, dy = _DIV[ticker]
        return Fundamentals(ticker=ticker, company_name=ticker, quote_type="ETF",
                            dividend_yield=dy, net_expense_ratio=er, total_assets=ta)

    def get_price_history(self, ticker, *, start, end):
        return _flat()

    def get_dividend_history(self, ticker, *, start, end):
        return []


def test_dividend_lens_ranks_all_ten_etfs_none_excluded():
    result = run_rank_pipeline(
        list(_DIV), "etf_dividend_v1", strategies_dir=STRAT_DIR,
        ranker_only=True, adapter=_EtfAdapter(), today=date(2026, 6, 30))
    assert len(result.ranked) == 10
    assert result.excluded == []
    assert result.unrateable == []
    # distribution_yield direction respected: SPYD (0.0426, highest yield) ranks better
    # on that factor than VIG (0.0151, lowest). Lower factor-rank number == better.
    ranks = {r.ticker: r.factor_ranks["distribution_yield"] for r in result.ranked}
    assert ranks["SPYD"] < ranks["VIG"]


# --------------------------------------------------------------------------- #
# ETFCHK-1 — Company Check renders the TRUE ETF distribution yield, and an absent
# yield renders NOT-EVALUATED (never a defaulted 0). The universe-run path (above) is
# byte-unchanged: these render tests exercise the single-name diagnostic only.
# --------------------------------------------------------------------------- #
def _etf_company_check(dividend_yield):
    from aristos_council.company_check import run_company_check
    from aristos_council.data.adapter import MarketDataAdapter, PriceBar, PriceHistory

    class _OneEtf(MarketDataAdapter):
        name = "fake"

        def get_fundamentals(self, ticker):
            return Fundamentals(
                ticker=ticker, company_name="Schwab US Dividend Equity ETF",
                quote_type="ETF", dividend_yield=dividend_yield,
                net_expense_ratio=0.06, total_assets=9.5e10)

        def get_price_history(self, ticker, *, start, end):
            return _flat()

        def get_dividend_history(self, ticker, *, start, end):
            return []

    return run_company_check(
        "SCHD", "etf_dividend_v1", "", adapter=_OneEtf(),
        strategies_dir=STRAT_DIR, universes_dir=STRAT_DIR.parent / "universes",
        runs_dir=STRAT_DIR.parent / "runs", today=date(2026, 6, 30))


def test_company_check_renders_true_etf_distribution_yield():
    from aristos_council.company_check import format_company_check

    r = _etf_company_check(0.033)
    dy = next(fc for fc in r.factors if fc.factor == "distribution_yield")
    assert dy.value == 0.033 and dy.source == "computed"
    text = format_company_check(r)
    assert "0.033" in text
    assert "distribution_yield" not in r.data_integrity.not_evaluated_factors


def test_company_check_absent_etf_yield_is_not_evaluated_never_zero():
    from aristos_council.company_check import format_company_check

    r = _etf_company_check(None)
    dy = next(fc for fc in r.factors if fc.factor == "distribution_yield")
    assert dy.value is None and dy.source == "abstained"        # NOT-EVAL, not a 0
    assert "distribution_yield" in r.data_integrity.not_evaluated_factors
    # the rendered yield line must NOT fabricate a 0 [computed]; it abstains instead
    text = format_company_check(r)
    assert "Distribution yield (distribution_yield): 0 [computed]" not in text
    yield_line = next(ln for ln in text.splitlines()
                      if "(distribution_yield):" in ln)
    assert "[abstained]" in yield_line and "[computed]" not in yield_line
