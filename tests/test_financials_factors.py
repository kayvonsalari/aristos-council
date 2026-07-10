"""Financials-lens factors: price_to_book + return_on_equity (FIN-1 ITEM 1).

The measures banks & insurers are actually priced by. Each has a vendor-value primary
path, a derived-series fallback, and an honest abstention (never an exclusion) when book
equity is non-positive or the income is missing. Direction is verified through the rank
engine — a LOWER P/B must earn a BETTER rank; a HIGHER ROE must earn a better rank.
Absurd vendor values route through the VERIFY-2 ITEM 4 sanity flags (flagged, withheld
from narration, never used to fail a name).
"""

from __future__ import annotations

from aristos_council.data.adapter import Fundamentals, implausible_fields
from aristos_council.factors import (
    FACTOR_REGISTRY, FactorInputs, _price_to_book, _return_on_equity,
)
from aristos_council.rank_engine import FactorSpec, rank_universe


def _fi(ticker="X", **kw) -> FactorInputs:
    return FactorInputs(ticker=ticker, fundamentals=Fundamentals(ticker=ticker, **kw))


# --- price_to_book: computed / fallback / abstain --------------------------- #
def test_price_to_book_uses_vendor_value():
    assert _price_to_book(_fi(price_to_book=1.4)) == 1.4


def test_price_to_book_falls_back_to_market_cap_over_equity():
    # no vendor scalar -> market_cap / closing (latest) shareholders' equity
    v = _price_to_book(_fi(market_cap=200.0, shareholders_equity=[100.0, 90.0]))
    assert v == 2.0


def test_price_to_book_abstains_on_nonpositive_book():
    # vendor value implies negative book (≤ 0) -> abstain, and the fallback equity ≤ 0
    # abstains too rather than printing a meaningless negative ratio.
    assert _price_to_book(_fi(price_to_book=-3.0,
                              market_cap=200.0, shareholders_equity=[-50.0])) is None


def test_price_to_book_abstains_when_missing():
    assert _price_to_book(_fi(market_cap=200.0)) is None       # no equity, no vendor


# --- return_on_equity: computed / fallback / abstain ------------------------ #
def test_return_on_equity_uses_vendor_value():
    assert _return_on_equity(_fi(return_on_equity=0.18)) == 0.18


def test_return_on_equity_falls_back_to_income_over_mean_equity():
    # net_income[0] / mean(opening+closing equity) = 20 / ((110+90)/2) = 0.20
    v = _return_on_equity(_fi(net_income=[20.0], shareholders_equity=[110.0, 90.0]))
    assert abs(v - 0.20) < 1e-12


def test_return_on_equity_fallback_single_year_uses_closing_equity():
    v = _return_on_equity(_fi(net_income=[20.0], shareholders_equity=[100.0]))
    assert abs(v - 0.20) < 1e-12


def test_return_on_equity_abstains_on_nonpositive_equity():
    assert _return_on_equity(_fi(net_income=[20.0],
                                 shareholders_equity=[-100.0])) is None


def test_return_on_equity_abstains_when_income_missing():
    assert _return_on_equity(_fi(shareholders_equity=[100.0])) is None


# --- direction correctness (through the rank engine) ------------------------ #
def test_lower_price_to_book_earns_a_better_rank():
    assert FACTOR_REGISTRY["price_to_book"].direction == "low"
    rows = [("CHEAP", {"price_to_book": 0.9}), ("DEAR", {"price_to_book": 3.0})]
    ranked = {r.ticker: r for r in rank_universe(rows, [FactorSpec("price_to_book")])}
    # lower P/B -> lower (better) combined rank -> better position
    assert ranked["CHEAP"].combined_rank < ranked["DEAR"].combined_rank
    assert ranked["CHEAP"].rank_position < ranked["DEAR"].rank_position


def test_higher_return_on_equity_earns_a_better_rank():
    assert FACTOR_REGISTRY["return_on_equity"].direction == "high"
    rows = [("STRONG", {"return_on_equity": 0.22}), ("WEAK", {"return_on_equity": 0.05})]
    ranked = {r.ticker: r for r in rank_universe(rows, [FactorSpec("return_on_equity")])}
    assert ranked["STRONG"].combined_rank < ranked["WEAK"].combined_rank


# --- sanity flags route (VERIFY-2 ITEM 4) ----------------------------------- #
def test_absurd_vendor_values_are_flagged():
    flags = implausible_fields(Fundamentals(
        ticker="X", price_to_book=250.0, return_on_equity=5.0))
    assert "price_to_book" in flags and "return_on_equity" in flags


def test_structurally_high_network_pb_is_not_flagged():
    # V/MA carry a real, structurally high P/B (~15-60) — the lens's documented odd
    # corner. It must NOT be flagged/withheld (that would hide a genuine value).
    assert "price_to_book" not in implausible_fields(
        Fundamentals(ticker="V", price_to_book=18.0))


def test_normal_bank_roe_is_not_flagged():
    assert "return_on_equity" not in implausible_fields(
        Fundamentals(ticker="JPM", return_on_equity=0.16))
