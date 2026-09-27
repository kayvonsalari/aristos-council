"""BACKTEST-1, commit 1 - AsOfAdapter: what was KNOWN on a past date. Fabricated data only.

The rule under test: a fiscal period is visible on ``as_of`` only if it ENDED on or before
``as_of - lag_days`` (the filing lag); nothing dated after ``as_of`` reaches a price, a dividend or a
scalar; and a name whose accounts cannot be dated or cut abstains rather than reading zeros.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from aristos_council.data.adapter import (DividendEvent, Fundamentals, MarketDataAdapter, PriceBar,
                                          PriceHistory)
from aristos_council.data.asof_adapter import AsOfAdapter, provenance_text
from aristos_council.data.eodhd_adapter import fundamentals_from_payload

AS_OF = date(2020, 6, 30)


def _bars(closes_by_day: dict[date, float]):
    return [PriceBar(day=d, open=c, high=c, low=c, close=c, adj_close=c, volume=1)
            for d, c in sorted(closes_by_day.items())]


def _fundamentals(*, ends, scalars=None, shares=100.0):
    """Two-year-apart statements; values equal the year so a leak is visible in the number."""
    ends = sorted(ends, reverse=True)
    values = [float(e[:4]) for e in ends]
    return Fundamentals(
        ticker="X", name="X Corp", company_name="X Corp", sector="Technology", currency="USD",
        financial_currency="USD", market_cap=9e12, eps=99.0, free_cash_flow=99.0, total_debt=99.0,
        total_cash=99.0, pe_ratio=99.0, dividend_yield=0.99, price_to_book=99.0,
        total_revenue=list(values), ebit=list(values), net_income=list(values),
        free_cash_flow_annual=list(values),
        period_ends={"total_revenue": list(ends), "ebit": list(ends), "net_income": list(ends),
                     "free_cash_flow_annual": list(ends)},
        period_scalars=scalars if scalars is not None else {
            e: {"eps": float(e[:4]) / 100, "free_cash_flow": float(e[:4]), "total_debt": 10.0,
                "total_cash": 5.0, "dividends_paid": 2.0, "operating_cash_flow": 30.0,
                "capital_expenditure": 4.0, "shares_outstanding": shares} for e in ends})


class _Inner(MarketDataAdapter):
    name = "fake"

    def __init__(self, fundamentals, closes=None, dividends=None):
        self._f = fundamentals
        self._closes = closes if closes is not None else {
            date(2020, 6, 26): 50.0, date(2020, 6, 29): 51.0, date(2020, 6, 30): 52.0,
            date(2020, 7, 1): 900.0, date(2021, 1, 4): 1000.0}
        self._divs = dividends or []
        self.calls = []

    def get_fundamentals(self, ticker):
        return self._f

    def get_price_history(self, ticker, *, start, end):
        self.calls.append(("prices", start, end))
        return PriceHistory(ticker=ticker, bars=[b for b in _bars(self._closes) if start <= b.day])

    def get_dividend_history(self, ticker, *, start, end):
        return [d for d in self._divs if start <= d.ex_date]


# =========================================================================== #
# the filing lag
# =========================================================================================== #
def test_a_statement_ten_days_before_as_of_is_excluded_under_the_90_day_lag():
    ten_days = (AS_OF - timedelta(days=10)).isoformat()          # closed 2020-06-20: not yet filed
    hundred_days = (AS_OF - timedelta(days=100)).isoformat()      # closed 2020-03-22: filed
    f = _fundamentals(ends=[ten_days, hundred_days, "2019-06-30"])
    got = AsOfAdapter(_Inner(f), AS_OF, lag_days=90).get_fundamentals("X")
    assert ten_days not in got.period_ends["total_revenue"]
    assert hundred_days in got.period_ends["total_revenue"]
    assert got.period_ends["total_revenue"][0] == hundred_days          # newest SURVIVING period
    assert got.total_revenue == [2020.0, 2019.0]                        # values cut in step


def test_one_hundred_days_before_is_included_and_the_boundary_is_inclusive():
    on_the_line = (AS_OF - timedelta(days=90)).isoformat()
    f = _fundamentals(ends=[on_the_line, "2019-06-30"])
    got = AsOfAdapter(_Inner(f), AS_OF, lag_days=90).get_fundamentals("X")
    assert got.period_ends["ebit"][0] == on_the_line
    one_day_late = (AS_OF - timedelta(days=89)).isoformat()
    f2 = _fundamentals(ends=[one_day_late, "2019-06-30"])
    assert AsOfAdapter(_Inner(f2), AS_OF, lag_days=90).get_fundamentals("X").period_ends[
        "ebit"][0] == "2019-06-30"


def test_the_lag_is_a_parameter():
    f = _fundamentals(ends=["2020-05-31", "2019-12-31"])
    assert AsOfAdapter(_Inner(f), AS_OF, lag_days=90).get_fundamentals("X").period_ends["ebit"][0] \
        == "2019-12-31"
    assert AsOfAdapter(_Inner(f), AS_OF, lag_days=30).get_fundamentals("X").period_ends["ebit"][0] \
        == "2020-05-31"


def test_every_annual_list_is_cut_in_step_and_the_aligned_dicts_keep_their_holes():
    f = _fundamentals(ends=["2020-05-31", "2019-12-31", "2018-12-31"])
    f = Fundamentals(**{**f.__dict__, "aligned_annual": {"net_income": [3.0, None, 1.0]},
                        "aligned_period_ends": {"net_income": ["2020-05-31", "2019-12-31",
                                                               "2018-12-31"]}})
    got = AsOfAdapter(_Inner(f), AS_OF, lag_days=90).get_fundamentals("X")
    assert got.aligned_annual["net_income"] == [None, 1.0]                  # hole stays in place
    assert got.aligned_period_ends["net_income"] == ["2019-12-31", "2018-12-31"]
    for name in ("total_revenue", "ebit", "net_income", "free_cash_flow_annual"):
        assert len(getattr(got, name)) == len(got.period_ends[name]) == 2


# =========================================================================== #
# scalars, market cap, prices
# =========================================================================== #
def test_scalars_are_rederived_from_the_newest_surviving_period_not_todays():
    f = _fundamentals(ends=["2020-06-20", "2019-12-31"])
    got = AsOfAdapter(_Inner(f), AS_OF, lag_days=90).get_fundamentals("X")
    assert got.eps == pytest.approx(20.19)                # 2019-12-31's eps, not today's 99.0
    assert got.free_cash_flow == 2019.0 and got.total_debt == 10.0 and got.total_cash == 5.0
    assert got.dividends_paid == 2.0 and got.operating_cash_flow == 30.0
    assert got.capital_expenditure == 4.0
    assert got.price_to_book is None and got.dividend_yield is None      # today's vendor scalars gone


def test_market_cap_is_the_periods_shares_times_the_as_of_close():
    f = _fundamentals(ends=["2019-12-31"], shares=100.0)
    got = AsOfAdapter(_Inner(f), AS_OF, lag_days=90).get_fundamentals("X")
    assert got.market_cap == pytest.approx(100.0 * 52.0)                  # 6/30 close, not 7/1's 900
    assert got.market_cap != 9e12                                         # never today's cap
    assert got.pe_ratio == pytest.approx(52.0 / 20.19)


def test_no_future_close_leaks_even_when_the_inner_adapter_returns_one():
    inner = _Inner(_fundamentals(ends=["2019-12-31"]))
    adapter = AsOfAdapter(inner, AS_OF, lag_days=90)
    history = adapter.get_price_history("X", start=date(2020, 6, 1), end=date(2021, 12, 31))
    assert history.bars and max(b.day for b in history.bars) == AS_OF
    assert all(b.close < 100 for b in history.bars)
    assert inner.calls[-1][2] == AS_OF                                    # the request itself is capped
    assert adapter.get_price_history("X", start=date(2020, 7, 1), end=date(2021, 1, 1)).bars == []


def test_the_dividend_history_is_cut_and_the_yield_uses_the_as_of_close():
    divs = [DividendEvent(date(2019, 9, 1), 1.0), DividendEvent(date(2020, 3, 1), 1.0),
            DividendEvent(date(2020, 9, 1), 5.0)]                         # the last is in the future
    inner = _Inner(_fundamentals(ends=["2019-12-31"]), dividends=divs)
    adapter = AsOfAdapter(inner, AS_OF, lag_days=90)
    assert [d.ex_date for d in adapter.get_dividend_history("X", start=date(2019, 1, 1),
                                                            end=date(2021, 1, 1))] == [
        date(2019, 9, 1), date(2020, 3, 1)]
    got = adapter.get_fundamentals("X")
    assert got.dividend_per_share == 2.0 and got.dividend_yield == pytest.approx(2.0 / 52.0)
    assert got.dividend_payment_dates == ["2019-09-01", "2020-03-01"]     # nothing after as-of


def test_a_split_sized_jump_in_shares_after_the_period_withholds_the_market_cap():
    scalars = {"2019-12-31": {"eps": 1.0, "shares_outstanding": 100.0},
               "2020-12-31": {"eps": 1.0, "shares_outstanding": 400.0}}     # 4-for-1 afterwards
    f = _fundamentals(ends=["2019-12-31", "2020-12-31"], scalars=scalars)
    got = AsOfAdapter(_Inner(f), AS_OF, lag_days=90).get_fundamentals("X")
    assert got.market_cap is None and "probable split" in got.provenance
    ok = {"2019-12-31": {"eps": 1.0, "shares_outstanding": 100.0},
          "2020-12-31": {"eps": 1.0, "shares_outstanding": 98.0}}            # a buyback is fine
    assert AsOfAdapter(_Inner(_fundamentals(ends=["2019-12-31", "2020-12-31"], scalars=ok)),
                       AS_OF).get_fundamentals("X").market_cap == pytest.approx(5200.0)


# =========================================================================== #
# honest abstention
# =========================================================================== #
def test_a_name_with_no_period_surviving_the_cut_abstains_with_identity_only():
    f = _fundamentals(ends=[(AS_OF - timedelta(days=10)).isoformat()])
    got = AsOfAdapter(_Inner(f), AS_OF, lag_days=90).get_fundamentals("X")
    assert (got.name, got.sector, got.currency) == ("X Corp", "Technology", "USD")
    for name in ("market_cap", "eps", "free_cash_flow", "total_debt", "total_cash", "pe_ratio",
                 "dividend_yield"):
        assert getattr(got, name) is None, name                            # never zero
    assert got.total_revenue == [] and got.ebit == [] and got.period_ends == {}
    assert "no fiscal period had ended on or before 2020-04-01" in got.provenance


def test_an_abstained_name_makes_every_lens_abstain_never_zero():
    from aristos_council.factors import compute_factor_outcomes, gather_factor_inputs
    f = _fundamentals(ends=[(AS_OF - timedelta(days=10)).isoformat()])
    fi = gather_factor_inputs(AsOfAdapter(_Inner(f), AS_OF), "X", today=AS_OF, static_rows={})
    outcomes = compute_factor_outcomes(fi, ["roic", "earnings_yield"])
    assert outcomes["roic"][0] is None and outcomes["earnings_yield"][0] is None


def test_a_provider_that_dates_nothing_cannot_be_cut_and_abstains():
    undated = Fundamentals(ticker="X", name="X Corp", sector="Technology", currency="USD",
                           total_revenue=[3.0, 2.0, 1.0], ebit=[3.0, 2.0, 1.0], market_cap=9e12)
    got = AsOfAdapter(_Inner(undated), AS_OF).get_fundamentals("X")
    assert got.total_revenue == [] and got.market_cap is None
    assert "provider gives no dated fiscal periods" in got.provenance


# =========================================================================== #
# provenance
# =========================================================================== #
def test_provenance_says_as_of_lag_and_restated_accounts():
    assert provenance_text(date(2019, 6, 28), 90) == \
        "as-of 2019-06-28, lag 90 days, restated accounts"
    got = AsOfAdapter(_Inner(_fundamentals(ends=["2019-12-31"])), AS_OF, 90).get_fundamentals("X")
    assert got.provenance.startswith("as-of 2020-06-30, lag 90 days, restated accounts")
    assert "newest period used 2019-12-31" in got.provenance


# =========================================================================== #
# the EODHD mapper keeps the dates (and only on request)
# =========================================================================== #
_PAYLOAD = {
    "General": {"Name": "X", "CurrencyCode": "USD"}, "Highlights": {"MarketCapitalization": 5e9},
    "Financials": {
        "Income_Statement": {"currency_symbol": "USD", "yearly": {
            "2019-12-31": {"totalRevenue": "200", "ebit": "40", "netIncome": "30",
                           "operatingIncome": "44", "incomeTaxExpense": "8", "incomeBeforeTax": "38"},
            "2018-12-31": {"totalRevenue": "180", "ebit": "NA", "netIncome": "25",
                           "operatingIncome": "40", "incomeTaxExpense": "7", "incomeBeforeTax": "35"}}},
        "Balance_Sheet": {"yearly": {
            "2019-12-31": {"commonStockSharesOutstanding": "10", "shortLongTermDebtTotal": "60",
                           "cashAndShortTermInvestments": "15", "netInvestedCapital": "300"},
            "2018-12-31": {"commonStockSharesOutstanding": "11", "shortTermDebt": "5",
                           "longTermDebt": "50", "cash": "9", "netInvestedCapital": "280"}}},
        "Cash_Flow": {"yearly": {
            "2019-12-31": {"freeCashFlow": "20", "dividendsPaid": "-6",
                           "totalCashFromOperatingActivities": "28", "capitalExpenditures": "-8"},
            "2018-12-31": {"freeCashFlow": "18"}}},
    },
}


def test_the_eodhd_mapper_keeps_the_fiscal_period_of_every_value_when_asked():
    f = fundamentals_from_payload("X.US", _PAYLOAD, with_periods=True)
    assert f.total_revenue == [200.0, 180.0]
    assert f.period_ends["total_revenue"] == ["2019-12-31", "2018-12-31"]
    assert f.ebit == [40.0] and f.period_ends["ebit"] == ["2019-12-31"]      # the NA cell dropped from both
    assert f.period_ends["invested_capital"] == ["2019-12-31", "2018-12-31"]
    assert f.period_ends["free_cash_flow_annual"] == ["2019-12-31", "2018-12-31"]
    newest = f.period_scalars["2019-12-31"]
    assert newest == {"eps": 3.0, "free_cash_flow": 20.0, "total_debt": 60.0, "total_cash": 15.0,
                      "dividends_paid": 6.0, "operating_cash_flow": 28.0,
                      "capital_expenditure": -8.0, "shares_outstanding": 10.0}
    older = f.period_scalars["2018-12-31"]
    assert older["total_debt"] == 55.0 and older["total_cash"] == 9.0      # short + long, and plain cash


def test_the_mapper_is_byte_identical_when_not_asked():
    plain = fundamentals_from_payload("X.US", _PAYLOAD)
    assert plain.period_ends == {} and plain.period_scalars == {} and plain.provenance is None
    assert plain.total_revenue == [200.0, 180.0] and plain.ebit == [40.0]


def test_the_cut_composes_with_the_mapper_end_to_end():
    f = fundamentals_from_payload("X.US", _PAYLOAD, with_periods=True)
    inner = _Inner(f, closes={date(2020, 3, 31): 10.0})
    got = AsOfAdapter(inner, date(2020, 3, 31), lag_days=90).get_fundamentals("X.US")
    assert got.total_revenue == [200.0, 180.0]                # 2019-12-31 ended exactly 91 days earlier
    tight = AsOfAdapter(inner, date(2020, 3, 31), lag_days=100).get_fundamentals("X.US")
    assert tight.total_revenue == [180.0]                     # 91 < 100: the 2019 year is not filed yet
    assert tight.eps == pytest.approx(25.0 / 11.0)
