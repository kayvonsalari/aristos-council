"""EBIT/EV earnings yield (hardening ITEM 6, Step 2 — shipped after the 95% diagnostic).

EV = market cap + total debt − cash & short-term investments. Uses EBIT/EV when the
components are present; falls back to EBIT/market_cap when they are missing (a comparable
positive proxy); and ABSTAINS on a net-cash name (EV ≤ 0) rather than emit a negative
rank artifact.
"""

from __future__ import annotations

from aristos_council.data.adapter import Fundamentals
from aristos_council.factors import FactorInputs, _earnings_yield, enterprise_value


def _ey(**fund) -> float | None:
    return _earnings_yield(FactorInputs(ticker="T", fundamentals=Fundamentals(
        ticker="T", **fund)))


# --------------------------------------------------------------------------- #
# enterprise_value
# --------------------------------------------------------------------------- #
def test_enterprise_value_formula():
    f = Fundamentals(ticker="T", market_cap=1000.0, total_debt=200.0, total_cash=100.0)
    assert enterprise_value(f) == 1100.0                 # mcap + debt − cash


def test_enterprise_value_none_when_a_component_missing():
    assert enterprise_value(Fundamentals(ticker="T", market_cap=1000.0,
                                         total_debt=200.0)) is None      # no cash
    assert enterprise_value(Fundamentals(ticker="T", market_cap=1000.0,
                                         total_cash=100.0)) is None      # no debt
    assert enterprise_value(None) is None


# --------------------------------------------------------------------------- #
# earnings_yield: EBIT/EV, fallback, negative-EV guard
# --------------------------------------------------------------------------- #
def test_earnings_yield_uses_ebit_over_ev_when_available():
    # EV = 1000 + 200 − 100 = 1100 -> 100/1100
    ey = _ey(ebit=[100.0], market_cap=1000.0, total_debt=200.0, total_cash=100.0)
    assert abs(ey - 100.0 / 1100.0) < 1e-12


def test_earnings_yield_falls_back_to_ebit_over_market_cap_when_ev_components_missing():
    # no total_cash -> EV not computable -> EBIT/market_cap proxy (unchanged behavior)
    assert _ey(ebit=[100.0], market_cap=1000.0, total_debt=200.0) == 0.1
    assert _ey(ebit=[100.0], market_cap=1000.0) == 0.1


def test_earnings_yield_abstains_on_net_cash_negative_ev():
    # cash (500) > mcap (100) + debt (0) -> EV = −400 -> ABSTAIN, no negative artifact
    assert _ey(ebit=[100.0], market_cap=100.0, total_debt=0.0, total_cash=500.0) is None
    # EV exactly 0 also abstains (division blow-up guarded)
    assert _ey(ebit=[100.0], market_cap=100.0, total_debt=0.0, total_cash=100.0) is None


def test_earnings_yield_pe_fallback_when_ebit_missing():
    assert _ey(market_cap=1000.0, total_debt=200.0, total_cash=100.0,
               pe_ratio=10.0) == 0.1                     # no EBIT -> 1/PE
    assert _ey(pe_ratio=None) is None                    # nothing to go on


def test_net_cash_abstention_is_never_a_negative_value():
    # the guard's whole point: a net-cash name never contributes a negative earnings
    # yield to the rank (which 'high is better' would then sort as worst-of-the-worst).
    ey = _ey(ebit=[50.0], market_cap=200.0, total_debt=10.0, total_cash=900.0)
    assert ey is None or ey >= 0
