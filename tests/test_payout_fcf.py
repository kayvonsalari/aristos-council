"""Payout-on-FCF criterion, THROUGH-CYCLE (dividend coverage vs cash, smoothed like ROIC).

Current-year dividends_paid / MEAN free cash flow over the last up-to-4 fiscal years
(≥2 required, else the EPS basis as a marked fallback). Single-year FCF carries one-off
cash events (KO fairlife earnout) exactly as GAAP earnings carried non-cash charges
(ABBV) — the through-cycle mean dampens both. Numerator stays current-year. Network-free.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.data.adapter import Fundamentals
from aristos_council.factors import (
    FactorInputs,
    screen_evaluate,
    screen_prefilter_fail,
)
from aristos_council.pipeline import (
    RankPipelineResult,
    format_screen_basis_entry,
    screen_basis_integrity,
)
from aristos_council.strategy.loader import load_strategy
from aristos_council.tools.screening import (
    fcf_annual_series,
    max_payout_fcf_criterion,
    through_cycle_fcf,
)

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


def _c(**fund):
    return max_payout_fcf_criterion(Fundamentals(ticker="T", **fund), max_payout=0.80)


# --------------------------------------------------------------------------- #
# Through-cycle criterion semantics
# --------------------------------------------------------------------------- #
def test_covered_payer_passes_on_through_cycle_mean():
    r = _c(dividend_per_share=2.0, dividends_paid=4e9, free_cash_flow_annual=[10e9] * 4)
    assert r.passed is True and abs(r.observed - 0.40) < 1e-9 and r.basis == "fcf"


def test_stretched_payer_fails():
    r = _c(dividend_per_share=2.0, dividends_paid=9e9, free_cash_flow_annual=[10e9] * 4)
    assert r.passed is False and r.basis == "fcf"


def test_through_cycle_mean_smooths_one_crushed_year():
    # a single crushed FCF year (2B) alone -> payout 3.0 (fail); the 4y mean (8B) -> 0.75
    r = _c(dividend_per_share=2.0, dividends_paid=6e9,
           free_cash_flow_annual=[2e9, 10e9, 10e9, 10e9])
    assert r.passed is True and abs(r.observed - 6e9 / 8e9) < 1e-6 and r.basis == "fcf"


def test_numerator_is_current_dividends_not_a_mean():
    # dividends_paid is the CURRENT scalar; only the FCF denominator is averaged.
    r = _c(dividend_per_share=2.0, dividends_paid=4e9,
           free_cash_flow_annual=[10e9, 10e9, 10e9, 10e9])
    assert abs(r.observed - 0.40) < 1e-9        # 4B / 10B mean, numerator NOT averaged


def test_negative_mean_fcf_abstains_never_excludes():
    r = _c(dividend_per_share=2.0, dividends_paid=4e9, free_cash_flow_annual=[-1e9, -2e9])
    assert r.passed is None and r.basis == "abstained"


def test_single_year_history_falls_back_to_eps_marked():
    covered = _c(dividend_per_share=2.0, dividends_paid=4e9,
                 free_cash_flow_annual=[10e9], payout_ratio=0.6)     # <2 years
    assert covered.passed is True and covered.basis == "eps"
    stretched = _c(dividend_per_share=2.0, payout_ratio=0.95)        # no FCF at all
    assert stretched.passed is False and stretched.basis == "eps"


def test_zero_dividends_passes_trivially():
    assert _c(dividend_per_share=0.0).passed is True


def test_fcf_series_prefers_direct_else_derives_from_ocf_capex():
    f = Fundamentals(ticker="T", operating_cash_flow_annual=[12e9, 11e9],
                     capital_expenditure_annual=[-2e9, -1e9])
    assert fcf_annual_series(f) == [10e9, 10e9]                      # ocf + capex per year
    f2 = Fundamentals(ticker="T", free_cash_flow_annual=[8e9],
                      operating_cash_flow_annual=[12e9], capital_expenditure_annual=[-2e9])
    assert fcf_annual_series(f2) == [8e9]                            # direct row wins
    mean, n = through_cycle_fcf(f)
    assert mean == 10e9 and n == 2


# --------------------------------------------------------------------------- #
# Exclusion line + aggregate basis block name the (through-cycle) basis
# --------------------------------------------------------------------------- #
_SCREEN = load_strategy(STRAT_DIR / "conservative_screen_v1.yaml").criteria


def _fi(**fund):
    return FactorInputs(
        ticker="X", last_close=100.0, return_12m=0.05,
        fundamentals=Fundamentals(ticker="X", market_cap=1e10, dividend_per_share=2.0,
                                  dividend_yield=0.02, **fund))


def test_exclusion_line_names_the_through_cycle_fcf_basis():
    reason = screen_prefilter_fail(
        _SCREEN, _fi(dividends_paid=9e9, free_cash_flow_annual=[10e9] * 4))
    assert reason is not None
    assert "max_payout_ratio_fcf" in reason and "FCF (4y mean)" in reason


def test_exclusion_line_names_the_eps_fallback_basis():
    reason = screen_prefilter_fail(_SCREEN, _fi(payout_ratio=0.95))   # no FCF -> EPS
    assert reason is not None and "EPS fallback" in reason


def test_screen_evaluate_records_basis_for_a_passing_name():
    _, bases, _ = screen_evaluate(
        _SCREEN, _fi(dividends_paid=4e9, free_cash_flow_annual=[10e9] * 4))
    assert bases.get("max_payout_ratio_fcf") == "fcf"


def test_ranked_table_marks_a_screen_abstention():
    from datetime import date

    from aristos_council.data.adapter import (
        MarketDataAdapter, PriceBar, PriceHistory)
    from aristos_council.pipeline import (
        format_cli_report, ranked_abstention_footnotes, run_rank_pipeline)

    class _A(MarketDataAdapter):
        name = "fake"
        _F = {"NORMAL": dict(free_cash_flow_annual=[10e9] * 4, dividends_paid=4e9),
              "UTIL": dict(free_cash_flow_annual=[-1e9] * 4, dividends_paid=4e9)}

        def get_fundamentals(self, t):
            return Fundamentals(ticker=t, name=t, market_cap=2e10,
                                dividend_per_share=2.0, dividend_yield=0.02,
                                total_debt=1e9, **self._F[t])

        def get_price_history(self, t, *, start, end):
            return PriceHistory(ticker=t, bars=[
                PriceBar(day=date(2026, 1, 1 + (i % 27)), open=100, high=101, low=99,
                         close=100 + 0.05 * i, adj_close=100 + 0.05 * i, volume=10)
                for i in range(260)])

        def get_dividend_history(self, t, *, start, end):
            return []

    result = run_rank_pipeline(
        ["NORMAL", "UTIL"], "conservative_plus_v1", ranker_only=True,
        strategies_dir=STRAT_DIR, adapter=_A(), today=date(2026, 6, 30))
    by = {r.ticker: r for r in result.ranked}
    assert "UTIL" in by and "NORMAL" in by                          # both pass the screen
    assert "max_payout_ratio_fcf" in by["UTIL"].screen_abstentions  # abstained -> flagged
    assert by["NORMAL"].screen_abstentions == {}                    # fully evaluated -> none
    foots = ranked_abstention_footnotes(result)
    assert any("UTIL" in f and "max_payout_ratio_fcf" in f for f in foots)
    assert not any("NORMAL" in f for f in foots)
    report = format_cli_report(result)
    assert "UTIL†" in report and "NORMAL†" not in report            # dagger on the abstainer


def test_basis_block_counts_names_and_abstentions():
    sb = {f"F{i}": {"max_payout_ratio_fcf": "fcf"} for i in range(11)}
    sb["KMB"] = {"max_payout_ratio_fcf": "eps"}
    sb["UTIL"] = {"max_payout_ratio_fcf": "abstained"}
    result = RankPipelineResult(ranked=[], excluded=[], unrateable=[], narratives={},
                                header="", meta={"rank_strategy_id": "s"},
                                screen_bases=sb)
    line = format_screen_basis_entry(screen_basis_integrity(result)[0])
    assert "FCF (4y mean) 11/13" in line
    assert "EPS fallback 1/13 (KMB)" in line
    assert "abstained 1 (UTIL)" in line
