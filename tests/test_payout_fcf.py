"""Payout-on-FCF criterion (dividend coverage measured against cash, not GAAP EPS).

Resolves the PEP/KMB/MRK-class wrong exclusions (sound payers whose GAAP EPS carries
non-cash charges). FCF <= 0 abstains (never fails); FCF missing falls back to the EPS
basis, MARKED; zero dividends passes trivially; the exclusion line and the aggregate
basis block NAME the basis. Network-free.
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
    effective_free_cash_flow,
    max_payout_fcf_criterion,
)

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


def _c(**fund):
    return max_payout_fcf_criterion(Fundamentals(ticker="T", **fund), max_payout=0.80)


# --------------------------------------------------------------------------- #
# Criterion semantics
# --------------------------------------------------------------------------- #
def test_covered_payer_passes_on_fcf():
    r = _c(dividend_per_share=2.0, dividends_paid=4e9, free_cash_flow=10e9)
    assert r.passed is True and abs(r.observed - 0.40) < 1e-9 and r.basis == "fcf"


def test_stretched_payer_fails_on_fcf():
    r = _c(dividend_per_share=2.0, dividends_paid=9e9, free_cash_flow=10e9)  # 0.90 > 0.80
    assert r.passed is False and r.basis == "fcf"


def test_negative_fcf_is_not_evaluated_never_excluded():
    r = _c(dividend_per_share=2.0, dividends_paid=4e9, free_cash_flow=-1e9)
    assert r.passed is None and r.basis == ""            # abstain (utilities lesson)


def test_fcf_missing_falls_back_to_eps_marked():
    covered = _c(dividend_per_share=2.0, payout_ratio=0.6)     # no cash-flow data
    assert covered.passed is True and covered.basis == "eps"
    stretched = _c(dividend_per_share=2.0, payout_ratio=0.95)
    assert stretched.passed is False and stretched.basis == "eps"


def test_zero_dividends_passes_trivially():
    r = _c(dividend_per_share=0.0)
    assert r.passed is True and r.basis == ""            # non-payers are yield's job


def test_null_dividend_is_a_data_gap_not_evaluated():
    assert _c().passed is None                           # dividend_per_share None


def test_effective_fcf_prefers_direct_else_derives():
    assert effective_free_cash_flow(Fundamentals(
        ticker="T", operating_cash_flow=12e9, capital_expenditure=-2e9)) == 10e9
    assert effective_free_cash_flow(Fundamentals(          # direct FCF wins
        ticker="T", free_cash_flow=8e9, operating_cash_flow=12e9,
        capital_expenditure=-2e9)) == 8e9


# --------------------------------------------------------------------------- #
# Exclusion line + aggregate basis block name the basis
# --------------------------------------------------------------------------- #
_SCREEN = load_strategy(STRAT_DIR / "conservative_screen_v1.yaml").criteria


def _fi(**fund):
    return FactorInputs(
        ticker="X", last_close=100.0, return_12m=0.05,
        fundamentals=Fundamentals(ticker="X", market_cap=1e10, dividend_per_share=2.0,
                                  dividend_yield=0.02, **fund))


def test_exclusion_line_names_the_fcf_basis():
    reason = screen_prefilter_fail(_SCREEN, _fi(dividends_paid=9e9, free_cash_flow=10e9))
    assert reason is not None
    assert "max_payout_ratio_fcf" in reason and "[FCF]" in reason


def test_exclusion_line_names_the_eps_fallback_basis():
    reason = screen_prefilter_fail(_SCREEN, _fi(payout_ratio=0.95))   # no FCF -> EPS
    assert reason is not None
    assert "max_payout_ratio_fcf" in reason and "[EPS fallback]" in reason


def test_screen_evaluate_records_basis_for_a_passing_name():
    _, bases = screen_evaluate(_SCREEN, _fi(dividends_paid=4e9, free_cash_flow=10e9))
    assert bases.get("max_payout_ratio_fcf") == "fcf"    # basis recorded even on a pass


def test_basis_block_counts_and_names_fallbacks():
    sb = {f"F{i}": {"max_payout_ratio_fcf": "fcf"} for i in range(14)}
    sb["KMB"] = {"max_payout_ratio_fcf": "eps"}
    sb["PEP"] = {"max_payout_ratio_fcf": "eps"}
    result = RankPipelineResult(ranked=[], excluded=[], unrateable=[], narratives={},
                                header="", meta={"rank_strategy_id": "s"},
                                screen_bases=sb)
    line = format_screen_basis_entry(screen_basis_integrity(result)[0])
    assert "FCF 14/16" in line
    assert "EPS fallback 2/16 (KMB, PEP)" in line
