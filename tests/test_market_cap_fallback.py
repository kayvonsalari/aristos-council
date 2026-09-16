"""MCAP-NA-1 — "market cap unavailable" on companies worth tens of billions.

Six names in the oil cohort — IMO, WDS, KEY.TO, IOC.NS, BPT.AX, STO.AX — came back with
``marketCap`` absent, so every lens's size floor abstained and the reports read "market cap
unavailable" beside companies worth $2bn to $65bn.

The figure was there the whole time, under ``nonDilutedMarketCap``. The brief proposed
deriving it from ``sharesOutstanding x price``, and that fallback is kept for names where
shares are present — but it would have rescued NONE of these six, because their share count
is missing too. The vendor had simply moved the key.
"""

from __future__ import annotations

import pytest

from aristos_council.data.yfinance_adapter import _market_cap
from aristos_council.tools.screening import min_market_cap_criterion
from aristos_council.data.adapter import Fundamentals


# --------------------------------------------------------------------------- #
# The fallback chain
# --------------------------------------------------------------------------- #
def test_the_diluted_figure_is_preferred_when_present():
    assert _market_cap({"marketCap": 5e9, "nonDilutedMarketCap": 4e9}) == 5e9


def test_the_non_diluted_figure_rescues_the_six():
    """The shape all six actually had: no marketCap, no shares, only this."""
    assert _market_cap({"nonDilutedMarketCap": 65_486_230_831}) == 65_486_230_831
    assert _market_cap({"marketCap": None,
                        "nonDilutedMarketCap": 2_053_200_290}) == 2_053_200_290


def test_shares_times_price_is_the_last_resort():
    assert _market_cap({"sharesOutstanding": 1_000, "regularMarketPrice": 12.5}) == 12_500
    # ...and it accepts the other price fields the provider fills instead
    assert _market_cap({"sharesOutstanding": 1_000, "currentPrice": 3.0}) == 3_000
    assert _market_cap({"sharesOutstanding": 1_000, "previousClose": 2.0}) == 2_000


def test_shares_alone_or_price_alone_derives_nothing():
    """Half of a product is not an estimate of it — no share count is ever invented."""
    assert _market_cap({"sharesOutstanding": 1_000}) is None
    assert _market_cap({"regularMarketPrice": 12.5}) is None
    assert _market_cap({}) is None


def test_the_fallback_would_not_have_saved_the_six_on_its_own():
    """The finding worth keeping: the brief's proposed fix could not have worked here,
    because these names are missing sharesOutstanding as well as marketCap."""
    as_observed = {"marketCap": None, "sharesOutstanding": None,
                   "regularMarketPrice": 129.96}
    assert _market_cap(as_observed) is None
    assert _market_cap({**as_observed, "nonDilutedMarketCap": 65_486_230_831})


# --------------------------------------------------------------------------- #
# A name with no figure at all is NOT excluded
# --------------------------------------------------------------------------- #
def test_an_unsized_name_is_not_tested_rather_than_failed():
    """Already true before this item, and pinned here so it stays true: a missing figure is
    never a phantom fail (house rule 3), so the name proceeds to the screen and the rank."""
    r = min_market_cap_criterion(Fundamentals(ticker="X"), min_market_cap=5e9)
    assert r.passed is None and r.observed is None
    assert "unavailable" in r.note


def test_the_rank_stage_floor_never_excludes_an_unsized_name():
    from datetime import date

    from aristos_council.pipeline import run_rank_pipeline
    from tests.test_multi_strategy_run import RAW, STRAT_DIR, TODAY, _Adapter

    class _Unsized(_Adapter):
        def get_fundamentals(self, ticker):
            f = super().get_fundamentals(ticker)
            return Fundamentals(**{**f.__dict__, "market_cap": None})

    res = run_rank_pipeline(["A", "B"], RAW, strategies_dir=STRAT_DIR, ranker_only=True,
                            adapter=_Unsized(), today=TODAY)
    assert not any("min market cap" in why for _, why in res.excluded)


def test_a_non_usd_market_cap_still_abstains_on_the_usd_threshold():
    """IOC.NS is now sized (1.86e12 INR) and must STILL abstain: the threshold is USD and
    no FX is applied. Sizing a name is not the same as being able to compare it."""
    r = min_market_cap_criterion(
        Fundamentals(ticker="IOC.NS", market_cap=1.864e12, currency="INR"),
        min_market_cap=5e9)
    assert r.passed is None
    assert "not USD" in r.note
