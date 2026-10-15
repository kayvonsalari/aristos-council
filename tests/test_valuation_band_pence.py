"""DEFENCE-COHORT-1 hand test: every London name's valuation band read "accounts currency GBP vs price currency GBp -
no rates from GBPGBp=X, GBpGBP=X - band not evaluated" (Rolls-Royce, BAE Systems). Pence and pounds are the same money in
two units: no rate exists and none is needed. Market cap and accounts are both in pounds, so the multiple is right as
computed; only a PRICE stated from the band (the reversion value) has to be scaled back to pence."""
from aristos_council.data.adapter import Fundamentals
from tests.test_valuation_band import TODAY, _bars, _fiscal_years
from aristos_council.tools.reversion import reversion_value
from aristos_council.tools.valuation_band import MINOR_UNITS, valuation_band


def _london(price_ccy="GBp", acct="GBP", years=8):
    """Market cap 1,000 (pounds), 10 shares: a 100-pound share, i.e. 10,000p."""
    ends = _fiscal_years(years)
    aligned = {"ebit": [100.0] * years, "total_debt": [200.0] * years, "cash": [50.0] * years,
               "shares_outstanding": [10.0] * years}
    return Fundamentals(ticker="X", market_cap=1_000.0, aligned_annual=aligned,
                        aligned_period_ends={k: ends[:years] for k in aligned}, currency=price_ccy,
                        financial_currency=acct)


def test_a_pence_quote_on_pound_accounts_is_not_a_cross_currency_band():
    band = valuation_band(_bars([10000.0] * 61), _london(), asof=TODAY)
    assert band.available, band.note
    assert band.percentile == 50.0 and band.quote_unit_scale == 100.0 and band.fx_pair == ""


def test_it_reads_exactly_like_the_same_company_quoted_in_pounds():
    pence = valuation_band(_bars([10000.0] * 61), _london("GBp"), asof=TODAY)
    pounds = valuation_band(_bars([100.0] * 61), _london("GBP"), asof=TODAY)
    assert (pence.percentile, pence.current, pence.median_multiple) == \
        (pounds.percentile, pounds.current, pounds.median_multiple)
    assert pounds.quote_unit_scale == 1.0


def test_the_reversion_price_is_stated_in_pence_beside_a_pence_close():
    f = _london()
    band = valuation_band(_bars([10000.0] * 61), f, asof=TODAY)
    rev = reversion_value(band, f, last_close=10000.0, currency="GBp")
    # median multiple == today's, so the price at the median is today's price: 10,000p, not 100 (pounds)
    assert rev.price is not None and 9000 < rev.price < 11000, rev


def test_a_different_currency_still_abstains_when_no_rate_exists():
    band = valuation_band(_bars([10000.0] * 61), _london("GBp", "USD"), asof=TODAY)
    assert not band.available and "accounts currency USD vs price currency GBp" in band.note


def test_the_minor_unit_table_names_pence_and_pounds_only_for_known_pairs():
    assert MINOR_UNITS["GBp"] == "GBP" and MINOR_UNITS["GBX"] == "GBP" and MINOR_UNITS["ZAc"] == "ZAR"
