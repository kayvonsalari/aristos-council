"""NARR-INDEP-TEST harness — fixtures.py."""

from __future__ import annotations

from experiments.narr_indep_test.fixtures import (
    COHORT_N, FACTORS, FUND_BAD, FUND_GOOD, RANK_BAD, RANK_GOOD, ablated_adapter,
    ablated_ticker, frame, fund_adapter, ranked_ticker)


def test_fund_good_is_the_clean_sweep_best():
    r = ranked_ticker(FUND_GOOD, rank=RANK_GOOD, verdict="buy")
    assert set(r.factor_ranks) == set(FACTORS)
    assert all(v == 1.0 for v in r.factor_ranks.values())
    assert r.combined_rank == 4.0                 # 4 factors x rank 1
    assert r.cohort_position == 1 and r.rank_position == 1
    assert r.universe_size == COHORT_N


def test_fund_bad_is_the_clean_sweep_worst():
    r = ranked_ticker(FUND_BAD, rank=RANK_BAD, verdict="sell")
    assert all(v == float(COHORT_N) for v in r.factor_ranks.values())
    assert r.combined_rank == 20.0                # 4 factors x rank 5
    assert r.cohort_position == 5


def test_fund_good_beats_fund_bad_on_every_real_static_value():
    assert FUND_GOOD.expense_ratio < FUND_BAD.expense_ratio
    assert FUND_GOOD.fund_size > FUND_BAD.fund_size
    assert FUND_GOOD.distribution_yield > FUND_BAD.distribution_yield


def test_true_values_and_sources_round_trip_onto_the_ticker():
    r = ranked_ticker(FUND_GOOD, rank=RANK_GOOD, verdict="buy")
    assert r.factor_values["expense_ratio"] == FUND_GOOD.expense_ratio
    assert r.factor_values["fund_size"] == FUND_GOOD.fund_size
    assert r.factor_sources["expense_ratio"] == FUND_GOOD.source_tag
    assert r.factor_sources["expense_ratio"].startswith("static: 2026-07-21")


def test_ablated_ticker_carries_no_evidence():
    r = ablated_ticker(FUND_GOOD, verdict="buy")
    assert r.factor_ranks == {} and r.factor_values == {} and r.factor_sources == {}
    assert r.cohort_position is None and r.rank_position is None
    assert "combined rank-sum 0" in r.explain()   # no "#N of M" prefix — nothing assigned


def test_fund_adapter_serves_no_static_fee_fields_directly():
    # the fee/size/yield absolutes must reach the narrator ONLY via static_factor_evidence
    # (the RankedTicker), never via Fundamentals — matching the real ETF static layer.
    fund = fund_adapter(FUND_GOOD).get_fundamentals("VUSA.AS")
    assert fund.net_expense_ratio is None
    assert fund.total_assets is None
    assert fund.dividend_yield is None


def test_ablated_adapter_serves_near_null_fundamentals_and_empty_prices():
    adapter = ablated_adapter(FUND_GOOD)
    fund = adapter.get_fundamentals("VUSA.AS")
    assert fund.market_cap is None and fund.name is None
    prices = adapter.get_price_history("VUSA.AS", start=None, end=None)
    assert prices.bars == []


def test_price_series_trends_match_the_assigned_clean_sweep():
    good_prices = fund_adapter(FUND_GOOD).get_price_history("VUSA.AS", start=None, end=None)
    bad_prices = fund_adapter(FUND_BAD).get_price_history("IWMO.L", start=None, end=None)
    assert good_prices.closes[-1] > good_prices.closes[0]     # uptrend, matches rank 1
    assert bad_prices.closes[-1] < bad_prices.closes[0]         # downtrend, matches rank 5


def test_frame_is_screen_less_etf_dividend():
    f = frame()
    assert f.id == "etf_dividend_v1"
    assert f.criteria == []
