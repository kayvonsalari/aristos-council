"""FIGURES-1: wrong figures and blocked verdicts found on six awkward companies, 2026-11-20.

Each test is anchored to what a real page printed that day (Rio Tinto, Fevertree, Revolution Medicines).
No test reaches a provider: the adapter helpers are fed hand-built frames, the rest hand-built accounts.

  1  Rio Tinto     a pence price divided by a pound rate: $7,585.62 a share, P/E 1379
  2  Rio Tinto     pound amounts labelled "GBp"
  3  Rio Tinto     one blank operating-cash-flow cell blocked the gate and the price test
  4  Rio Tinto     yearly lists dropped blanks, so last year's figure was read as the latest
  5  Rio Tinto     Forensic ranked it last of 39 on one factor of three
  6  Fevertree     an empty newest column was "the latest year"
  7  Fevertree     year labels and figures came from lists starting in different years
  8  Fevertree     see tests/test_company_report.py (the ranked group is the group the page shows)
  15 RVMD          two cash runways on one page (48 months and 3.3 years)
  K1 RVMD          a product launch read as "statements not in one currency"
  K2 RVMD          the share price blanked in the price test
  K3 RVMD          the runway on last year's spending, not the latest quarter's
"""
from __future__ import annotations

from datetime import date

import pytest

from aristos_council.abs_readings import debt_and_cash, latest_accounts_fx
from aristos_council.absolute_value import render as value_render
from aristos_council.absolute_value.engine import (PriceReading, free_cash_flow_base,
                                                    price_in_reporting_currency)
from aristos_council.absolute_value.definition import load_definition
from aristos_council.data.adapter import Fundamentals, PriceBar, PriceHistory
from aristos_council.gate import engine as gate_engine
from aristos_council.gate.definition import BACKTEST_GATE_ID, DEFAULT_GATE_ID, load_gate
from aristos_council.rank_engine import FactorSpec, rank_universe
from aristos_council.tools import gate_readings as gr
from aristos_council.tools.screening import period_matched, period_matched_ends

TODAY = date(2026, 11, 20)


def _f(**kw) -> Fundamentals:
    base = dict(ticker="X", name="X Corp", sector="Technology", currency="USD", financial_currency="USD")
    base.update(kw)
    return Fundamentals(**base)


# --------------------------------------------------------------------------- #
# 1. pence are a unit of the pound, not a currency with its own rate
# --------------------------------------------------------------------------- #
class _PoundRate:
    """The feed's answer for USDGBp=X on 2026-11-20: the POUND rate, 0.7606, whatever the 'p' says."""

    name = "fake-fx"

    def get_price_history(self, ticker, *, start, end):
        if ticker != "USDGBp=X":
            raise RuntimeError("no such pair")
        bar = PriceBar(day=date(2026, 11, 19), open=0.7606, high=0.7606, low=0.7606, close=0.7606,
                       adj_close=0.7606, volume=0)
        return PriceHistory(ticker=ticker, bars=[bar])


def test_1_a_dollar_to_pence_rate_is_in_pence():
    fx = latest_accounts_fx(_PoundRate(), "USD", "GBp", TODAY)
    assert fx.rate == pytest.approx(76.06)                     # pence per dollar, not 0.7606
    assert (fx.from_ccy, fx.to_ccy) == ("USD", "GBp")
    assert fx.source == "yfinance USDGBp=X"                    # the pair asked for is unchanged


def test_1_rio_tintos_pence_price_is_about_76_dollars_not_7_586():
    rio = _f(ticker="RIO.L", currency="GBp", financial_currency="USD")
    price = price_in_reporting_currency(rio, 5770.0, latest_accounts_fx(_PoundRate(), "USD", "GBp", TODAY))
    assert price.price == pytest.approx(5770.0 / 76.06)        # $75.86
    assert 75 < price.price < 77
    assert price.currency == "USD" and price.quote_currency == "GBp"


def test_1_a_price_to_earnings_ratio_formed_from_that_rate_is_about_14_not_1379():
    fx = latest_accounts_fx(_PoundRate(), "USD", "GBp", TODAY)
    eps_in_pence = 5.50 * fx.rate                              # $5.50 a share
    assert 5770.0 / eps_in_pence == pytest.approx(13.79, abs=0.01)


def test_1_pounds_to_dollars_is_untouched_by_the_unit_fix():
    class Cable:
        def get_price_history(self, ticker, *, start, end):
            if ticker != "GBPUSD=X":
                raise RuntimeError("no such pair")
            bar = PriceBar(day=date(2026, 11, 19), open=1.31, high=1.31, low=1.31, close=1.31,
                           adj_close=1.31, volume=0)
            return PriceHistory(ticker=ticker, bars=[bar])
    assert latest_accounts_fx(Cable(), "GBP", "USD", TODAY).rate == pytest.approx(1.31)


# --------------------------------------------------------------------------- #
# 2. amounts built on the market value are pounds on a pence-quoted line
# --------------------------------------------------------------------------- #
def test_2_the_earnings_power_line_is_labelled_in_pounds_for_a_pence_quote():
    class _Av:
        context = {"epv": 110.4e9, "epv_enterprise_value": 101.9e9}
        price = PriceReading(price=75.86, currency="USD", quote_price=5770.0, quote_currency="GBp")
    (line,) = value_render.context_lines(_Av())
    assert "GBp" not in line
    assert "£110.4bn" in line and "£101.9bn" in line


def test_2_a_dollar_quote_keeps_its_dollar_label():
    class _Av:
        context = {"epv": 941.8e6, "epv_enterprise_value": 6.1e9}
        price = PriceReading(price=20.96, currency="USD", quote_price=20.96, quote_currency="USD")
    (line,) = value_render.context_lines(_Av())
    assert "$941.8m" in line and "$6.1bn" in line


# --------------------------------------------------------------------------- #
# 3, 4, 6. the provider's frames: a blank cell, a blank that used to slide years, an empty newest year
# --------------------------------------------------------------------------- #
pd = pytest.importorskip("pandas")

from aristos_council.data.yfinance_adapter import (  # noqa: E402
    _BALANCE_KEY_ROWS, _CASHFLOW_KEY_ROWS, _INCOME_KEY_ROWS, _aligned_series, _annual_series,
    _cashflow_series, _fill_operating_cash_flow, _latest_quarter_cash_flow, _statements_currency_note,
    _trim_unreported)

NAN = float("nan")
_Y = [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31"), pd.Timestamp("2023-12-31"),
      pd.Timestamp("2022-12-31")]


def _rio_cashflow():
    """Rio Tinto as Yahoo served it: 2025 free cash flow and capital spending present, the 2025
    operating-cash-flow cell empty. $ millions."""
    return pd.DataFrame(
        [[NAN, 15599.0, 15160.0, 16134.0],
         [4497.0, 5978.0, 8074.0, 9384.0],
         [-12335.0, -9621.0, -7086.0, -6750.0]],
        index=["Operating Cash Flow", "Free Cash Flow", "Capital Expenditure"], columns=_Y)


def test_3_a_blank_operating_cash_flow_cell_is_derived_from_free_cash_flow_and_capital_spending():
    filled = _fill_operating_cash_flow(_rio_cashflow())
    assert filled.loc["Operating Cash Flow", _Y[0]] == pytest.approx(4497.0 + 12335.0)     # 16,832
    assert filled.loc["Operating Cash Flow", _Y[1]] == 15599.0                              # reported: untouched
    assert pd.isna(_rio_cashflow().loc["Operating Cash Flow", _Y[0]])                       # the input is not edited


def test_3_the_derived_figure_reaches_the_gate_so_the_verdict_is_no_longer_blocked():
    cf = _fill_operating_cash_flow(_rio_cashflow())
    dates, ocf = _aligned_series(cf, "Operating Cash Flow")
    f = _f(aligned_annual={"operating_cash_flow": ocf, "change_in_working_capital": [-244.0, 57.0, -926.0, -465.0]},
           aligned_period_ends={"operating_cash_flow": dates, "change_in_working_capital": dates})
    reading = gr.cash_flow_before_working_capital(f)
    assert reading.value == pytest.approx(16832.0 + 244.0)
    assert reading.note != "operating cash flow missing"


@pytest.mark.parametrize("missing", ["Free Cash Flow", "Capital Expenditure"])
def test_3_a_true_gap_stays_a_gap(missing):
    cf = _rio_cashflow()
    cf.loc[missing, _Y[0]] = NAN
    filled = _fill_operating_cash_flow(cf)
    assert pd.isna(filled.loc["Operating Cash Flow", _Y[0]])
    assert _cashflow_series(filled, "Operating Cash Flow") == []      # the latest year is blank: no list


def test_4_a_blank_latest_year_never_slides_last_year_into_its_place():
    """Before: [15599, 15160, 16134] - 2024's figure in position 0, printed as the latest year's."""
    assert _annual_series(_rio_cashflow(), "Operating Cash Flow") == []


def test_4_a_blank_in_the_middle_ends_the_list_and_is_never_bridged():
    row = pd.DataFrame([[13.0, 12.0, NAN, 10.0]], index=["Operating Income"], columns=_Y)
    assert _annual_series(row, "Operating Income") == [13.0, 12.0]    # index 1 is still 2024; 2022 is not index 2
    dates, values = _aligned_series(row, "Operating Income")           # the dated series keeps every year
    assert values == [13.0, 12.0, None, 10.0] and dates[2] == "2023-12-31"


def test_4_a_complete_row_is_unchanged():
    assert _annual_series(_rio_cashflow(), "Free Cash Flow") == [4497.0, 5978.0, 8074.0, 9384.0]


def _fevertree_income():
    """Fevertree as Yahoo served it: a 2025 column holding a share count and nothing else."""
    return pd.DataFrame(
        [[NAN, 368.5, 364.4, 344.3],
         [NAN, 24.4, 15.4, 24.9],
         [121.4, 117.0, 116.8, 116.8]],
        index=["Total Revenue", "Net Income", "Diluted Average Shares"], columns=_Y)


def test_6_a_newest_column_with_no_revenue_or_profit_is_not_a_reported_year():
    trimmed = _trim_unreported(_fevertree_income(), _INCOME_KEY_ROWS)
    assert list(trimmed.columns) == _Y[1:]                              # 2025 is gone
    dates, shares = _aligned_series(trimmed, "Diluted Average Shares")
    assert dates[0] == "2024-12-31" and shares[0] == 117.0              # the latest year is 2024
    assert _annual_series(trimmed, "Total Revenue")[0] == 368.5


def test_6_a_frame_whose_newest_year_is_real_is_returned_as_it_is():
    cf = _rio_cashflow()
    assert _trim_unreported(cf, _CASHFLOW_KEY_ROWS) is cf
    balance = pd.DataFrame([[100.0, 90.0, 80.0, 70.0]], index=["Total Assets"], columns=_Y)
    assert _trim_unreported(balance, _BALANCE_KEY_ROWS) is balance


def test_6_only_newest_empty_years_are_dropped_and_an_all_empty_frame_is_left_alone():
    gap = pd.DataFrame([[100.0, NAN, 80.0, 70.0]], index=["Total Assets"], columns=_Y)
    assert _trim_unreported(gap, _BALANCE_KEY_ROWS) is gap              # an older gap is not a stub
    empty = pd.DataFrame([[NAN, NAN, NAN, NAN]], index=["Total Assets"], columns=_Y)
    assert _trim_unreported(empty, _BALANCE_KEY_ROWS) is empty
    assert _trim_unreported(None, _BALANCE_KEY_ROWS) is None


# --------------------------------------------------------------------------- #
# 7. a year printed beside a figure is that figure's own year
# --------------------------------------------------------------------------- #
def _fevertree_accounts() -> Fundamentals:
    """The share count carries a 2025 year-end the cash flow statement does not: the lists begin in
    different years, as they did on the page of 2026-11-20. GBP."""
    cf_ends = ["2024-12-31", "2023-12-31", "2022-12-31"]
    return _f(ticker="FEVR.L", currency="GBp", financial_currency="GBP",
              aligned_annual={"operating_cash_flow": [70.2e6, -3.8e6, 8.5e6],
                              "capital_expenditure": [-14.1e6, -9.6e6, -7.1e6],
                              "change_in_working_capital": [30.6e6, -29.6e6, -22.2e6],
                              "diluted_shares": [121.4e6, 117.0e6, 116.8e6, 116.8e6]},
              aligned_period_ends={"operating_cash_flow": cf_ends, "capital_expenditure": cf_ends,
                                   "change_in_working_capital": cf_ends,
                                   "diluted_shares": ["2025-12-31"] + cf_ends})


def test_7_period_matched_ends_names_the_year_of_each_matched_row():
    f = _fevertree_accounts()
    keys = ("operating_cash_flow", "capital_expenditure", "diluted_shares", "change_in_working_capital")
    rows = period_matched(f, keys, periods=3)
    ends = period_matched_ends(f, keys, periods=3)
    assert ends == ["2025-12-31", "2024-12-31", "2023-12-31"]
    assert rows[0]["operating_cash_flow"] is None                      # the 2025 row: no cash flow yet
    assert rows[1]["operating_cash_flow"] == 70.2e6                    # and 2024's sits beside "2024"


def test_7_the_base_table_puts_each_figure_under_its_own_year():
    """Before: 2025's empty row was labelled "the year to Dec 2024" and 2024's £0.22 "Dec 2023"."""
    base = free_cash_flow_base(_fevertree_accounts(), load_definition())
    by_year = {y.label: y.fcf_per_share for y in base.years}
    assert list(by_year) == ["the year to Dec 2025", "the year to Dec 2024", "the year to Dec 2023"]
    assert by_year["the year to Dec 2025"] is None
    assert by_year["the year to Dec 2024"] == pytest.approx((70.2 - 30.6 - 14.1) / 117.0, rel=1e-6)   # £0.22
    assert by_year["the year to Dec 2023"] == pytest.approx((-3.8 + 29.6 - 9.6) / 116.8, rel=1e-6)    # £0.14
    assert "for the year to Dec 2025" in base.note                     # the missing year is named correctly


def test_7_a_positional_fixture_with_no_dates_keeps_the_old_labels():
    assert period_matched_ends(_f(operating_cash_flow_annual=[1.0]), ("operating_cash_flow",), periods=2) == []


# --------------------------------------------------------------------------- #
# 5. one measured factor is not a rank on a lens of several
# --------------------------------------------------------------------------- #
_FORENSIC = [FactorSpec("accrual_ratio", "low", missing="neutral"),
             FactorSpec("altman_z", "high", missing="neutral"),
             FactorSpec("piotroski", "high", missing="neutral")]


def _forensic_rows():
    return [("RIO", {"accrual_ratio": None, "altman_z": None, "piotroski": 4.0}),      # one factor of three
            ("TWO", {"accrual_ratio": 0.01, "altman_z": None, "piotroski": 7.0}),      # two of three
            ("AAA", {"accrual_ratio": 0.02, "altman_z": 3.0, "piotroski": 8.0}),
            ("BBB", {"accrual_ratio": 0.05, "altman_z": 2.0, "piotroski": 6.0})]


def test_5_a_name_with_one_measured_factor_of_three_is_not_ranked():
    ranked = {r.ticker: r for r in rank_universe(_forensic_rows(), _FORENSIC, cut="quintile")}
    rio = ranked["RIO"]
    assert rio.excluded and rio.factor_ranks == {}
    assert rio.reason == "only 1 of 3 factors could be measured"
    assert rio.verdict != "buy"
    assert all(r.universe_size == 3 for t, r in ranked.items() if t != "RIO")   # it is not in the cohort


def test_5_two_of_three_is_still_ranked_with_the_third_imputed():
    ranked = {r.ticker: r for r in rank_universe(_forensic_rows(), _FORENSIC, cut="quintile")}
    assert not ranked["TWO"].excluded and ranked["TWO"].imputed_factors == ["altman_z"]


def test_5_a_name_with_no_factor_at_all_says_so():
    rows = _forensic_rows() + [("NIL", {"accrual_ratio": None, "altman_z": None, "piotroski": None})]
    nil = next(r for r in rank_universe(rows, _FORENSIC, cut="quintile") if r.ticker == "NIL")
    assert nil.excluded and nil.reason == "none of the 3 factors could be measured"


def test_5_a_single_factor_lens_is_untouched():
    rows = [("A", {"epv": 1.0}), ("B", {"epv": 2.0}), ("C", {"epv": None})]
    ranked = {r.ticker: r for r in rank_universe(rows, [FactorSpec("epv", "high")], cut="quintile",
                                                 missing="worst")}
    assert not ranked["C"].excluded                                     # ranked worst, as before


def test_5_the_rule_holds_for_every_missing_mode():
    for mode in ("worst", "neutral"):
        specs = [FactorSpec("a", "high"), FactorSpec("b", "high")]
        rows = [("ONE", {"a": 1.0, "b": None}), ("X", {"a": 2.0, "b": 1.0}), ("Y", {"a": 3.0, "b": 2.0})]
        one = next(r for r in rank_universe(rows, specs, cut="quintile", missing=mode) if r.ticker == "ONE")
        assert one.excluded and one.reason == "only 1 of 2 factors could be measured"


# --------------------------------------------------------------------------- #
# K1. a product launch is not a currency mix-up
# --------------------------------------------------------------------------- #
def test_k1_revolution_medicines_first_sales_do_not_read_as_two_currencies():
    """Annual sales 0, 0, $11.6m, $35.4m; trailing $197.6m. The old test skipped the two zero years and
    called $197.6m against $11.6m "17.1x apart (not in one currency)"."""
    annual = [0.0, 0.0, 11.58e6, 35.38e6]
    quarters = [95.0e6, 70.0e6, 32.6e6, 0.0]                 # the last four quarters sum to the trailing figure
    assert _statements_currency_note(197.6e6, annual, "USD", quarterly_revenue=quarters) == ""


def test_k1_with_no_quarterly_statements_a_zero_latest_year_is_no_test_not_an_older_year():
    assert _statements_currency_note(197.6e6, [0.0, 0.0, 11.58e6, 35.38e6], "USD") == ""


def test_k1_vale_is_still_caught_on_the_quarters():
    """Headline in reais (218bn), every statement in dollars (38.4bn a year, 9.6bn a quarter)."""
    note = _statements_currency_note(218.0e9, [38.4e9, 41.8e9], "BRL", quarterly_revenue=[9.6e9] * 4)
    assert "5.7x apart (not in one currency)" in note


def test_k1_vale_is_still_caught_on_the_annual_figure_when_there_are_no_quarters():
    assert "5.7x apart" in _statements_currency_note(218.0e9, [38.4e9, 41.8e9], "BRL")
    assert "5.7x apart" in _statements_currency_note(218.0e9, [38.4e9], "BRL", quarterly_revenue=[9.6e9, None])


def test_k1_an_ordinary_company_passes_either_way():
    assert _statements_currency_note(105.0, [100.0, 90.0], "USD") == ""
    assert _statements_currency_note(105.0, [100.0, 90.0], "USD", quarterly_revenue=[27.0, 26.0, 26.0, 26.0]) == ""


# --------------------------------------------------------------------------- #
# K2. the quoted price is shown even when the test cannot use it
# --------------------------------------------------------------------------- #
def test_k2_the_price_line_shows_the_quoted_price_when_the_test_abstains():
    f = _f(ticker="RVMD", statements_currency_note="headline revenue $197.6m vs latest statement $11.6m")
    price = price_in_reporting_currency(f, 159.43, None)
    assert not price.available and price.quote_price == 159.43

    class _Av:
        pass
    av = _Av()
    av.price = price
    line = value_render._price_line(av)
    assert "$159.43" in line
    assert "not enough data" not in line.split("$159.43")[0]


def test_k2_with_no_price_at_all_the_line_still_says_so():
    class _Av:
        price = PriceReading(note="no price")
    assert value_render._price_line(_Av()) == "Price: not enough data: no price."


# --------------------------------------------------------------------------- #
# 15, K3. one runway, on the latest burn
# --------------------------------------------------------------------------- #
def _rvmd(**kw) -> Fundamentals:
    """Revolution Medicines, Sep 2026: $3,651m cash, $639m debt, free cash flow -$913.7m in 2025."""
    base = dict(ticker="RVMD", total_cash=3651.4e6, total_debt=639.1e6, trailing_period_end="2026-09-30",
                free_cash_flow=-913.7e6, operating_cash_flow=-897.7e6,
                free_cash_flow_annual=[-913.7e6], operating_cash_flow_annual=[-897.7e6],
                change_in_working_capital_annual=[86.8e6],
                aligned_annual={"operating_cash_flow": [-897.7e6], "free_cash_flow": [-913.7e6],
                                "change_in_working_capital": [86.8e6]},
                aligned_period_ends={"operating_cash_flow": ["2025-12-31"], "free_cash_flow": ["2025-12-31"],
                                     "change_in_working_capital": ["2025-12-31"]})
    base.update(kw)
    return _f(**base)


def test_the_page_evaluates_gate_version_3_and_the_backtest_stays_on_version_2():
    assert DEFAULT_GATE_ID == "absolute_gate_v3" and BACKTEST_GATE_ID == "absolute_gate_v2"
    v3, v2 = load_gate(), load_gate(BACKTEST_GATE_ID)
    assert (v3.version, v3.runway_burn_basis) == (3, "latest_quarter")
    assert (v2.version, v2.runway_burn_basis) == (2, "latest_year")
    # nothing else moved between the two versions
    for name in ("accrual_exclude_above", "solvency_exclude_below", "f_exclude_at_or_below",
                 "runway_exclude_below_months", "runway_warn_below_months", "leverage_warn_above",
                 "z_warn_below", "z_grey_to", "cover_not_covered_below", "cover_years"):
        assert getattr(v3, name) == getattr(v2, name), name


def test_k3_the_runway_uses_the_latest_quarters_burn_put_on_a_yearly_footing():
    f = _rvmd(latest_quarter_free_cash_flow=-400.0e6, latest_quarter_end="2026-09-30", latest_quarter_months=3)
    r = gr.cash_runway(f, gr.BURN_LATEST_QUARTER)
    assert r.burn == pytest.approx(1600.0e6) and r.basis == gr.BURN_LATEST_QUARTER
    assert r.months == pytest.approx(12 * 3651.4 / 1600.0)             # 27 months, not 48


def test_k3_a_half_year_statement_is_doubled_not_quadrupled():
    f = _rvmd(latest_quarter_free_cash_flow=-400.0e6, latest_quarter_end="2026-06-30", latest_quarter_months=6)
    assert gr.cash_runway(f, gr.BURN_LATEST_QUARTER).burn == pytest.approx(800.0e6)


@pytest.mark.parametrize("quarter", [dict(), dict(latest_quarter_free_cash_flow=25.0e6, latest_quarter_months=3),
                                     dict(latest_quarter_free_cash_flow=-400.0e6, latest_quarter_months=None)])
def test_k3_without_a_usable_interim_statement_the_fiscal_year_is_the_fallback(quarter):
    r = gr.cash_runway(_rvmd(**quarter), gr.BURN_LATEST_QUARTER)
    assert r.basis == gr.BURN_LATEST_YEAR and r.burn == pytest.approx(913.7e6)
    assert r.months == pytest.approx(12 * 3651.4 / 913.7)              # 48 months


def test_k3_gate_version_2_still_reads_the_fiscal_year_whatever_the_quarter_says():
    f = _rvmd(latest_quarter_free_cash_flow=-400.0e6, latest_quarter_end="2026-09-30", latest_quarter_months=3)
    v2 = gate_engine._cash_runway(f, load_gate(BACKTEST_GATE_ID))
    v3 = gate_engine._cash_runway(f, load_gate())
    assert v2.value == pytest.approx(12 * 3651.4 / 913.7)
    assert v3.value == pytest.approx(12 * 3651.4 / 1600.0)


def test_15_the_gate_and_the_debt_and_cash_line_give_the_same_runway():
    """Before: 48 months in the gate, "about 3.3 years" (40 months) four sections lower."""
    for f in (_rvmd(), _rvmd(latest_quarter_free_cash_flow=-400.0e6, latest_quarter_end="2026-09-30",
                             latest_quarter_months=3)):
        gate_months = gate_engine._cash_runway(f, load_gate()).value
        line = debt_and_cash(f).cash_runway
        assert line.available and line.value * 12 == pytest.approx(gate_months)


def test_15_the_line_names_the_burn_it_used():
    quarter = debt_and_cash(_rvmd(latest_quarter_free_cash_flow=-400.0e6, latest_quarter_end="2026-09-30",
                                  latest_quarter_months=3))
    assert quarter.cash_runway.label == ("at the spending of the three months to Sep 2026, on a yearly footing "
                                         "($1.6bn) that lasts about 2.3 years (balance sheet of Sep 2026)")
    year = debt_and_cash(_rvmd())
    assert year.cash_runway.label == ("at FY2025's spending ($913.7m) that lasts about 4.0 years "
                                      "(balance sheet of Sep 2026)")


def test_k3_the_latest_interim_statement_is_read_with_its_length():
    q = [pd.Timestamp("2026-09-30"), pd.Timestamp("2026-06-30"), pd.Timestamp("2026-03-31")]
    quarterly = pd.DataFrame([[-400.0, -300.0, -250.0]], index=["Free Cash Flow"], columns=q)
    assert _latest_quarter_cash_flow(quarterly) == (-400.0, "2026-09-30", 3)
    h = [pd.Timestamp("2026-06-30"), pd.Timestamp("2025-12-31")]
    half = pd.DataFrame([[10.0, 30.0], [-4.0, -9.0]], index=["Operating Cash Flow", "Capital Expenditure"],
                        columns=h)
    assert _latest_quarter_cash_flow(half) == (6.0, "2026-06-30", 6)   # derived, and six months long
    assert _latest_quarter_cash_flow(None) == (None, None, None)
    one = pd.DataFrame([[-400.0]], index=["Free Cash Flow"], columns=q[:1])
    assert _latest_quarter_cash_flow(one) == (None, None, None)        # its length cannot be known


# --------------------------------------------------------------------------- #
# the twelve-month burn: available as a basis, not the one version 3 uses
# --------------------------------------------------------------------------- #
def test_one_quarter_can_be_far_from_typical_so_the_twelve_month_burn_is_also_carried():
    """Revolution Medicines' interim free cash flow, newest first: -63.6, -745.3, -355.7, -276.2 ($m)."""
    q = [pd.Timestamp("2026-09-30"), pd.Timestamp("2026-06-30"), pd.Timestamp("2026-03-31"),
         pd.Timestamp("2025-12-31"), pd.Timestamp("2025-09-30")]
    frame = pd.DataFrame([[-63.6, -745.3, -355.7, -276.2, -210.7]], index=["Free Cash Flow"], columns=q)
    from aristos_council.data.yfinance_adapter import _trailing_interim_free_cash_flow
    assert _trailing_interim_free_cash_flow(frame, 3) == pytest.approx(-1440.8)
    frame.loc["Free Cash Flow", q[2]] = NAN
    assert _trailing_interim_free_cash_flow(frame, 3) is None           # an incomplete year is not summed
    assert _trailing_interim_free_cash_flow(frame, None) is None


def test_the_twelve_month_basis_reads_that_sum_and_falls_back_to_the_fiscal_year_without_it():
    f = _rvmd(latest_quarter_free_cash_flow=-63.6e6, latest_quarter_end="2026-09-30", latest_quarter_months=3,
              trailing_interim_free_cash_flow=-1440.8e6)
    r = gr.cash_runway(f, gr.BURN_TRAILING_YEAR)
    assert r.basis == gr.BURN_TRAILING_YEAR and r.months == pytest.approx(12 * 3651.4 / 1440.8)   # 30 months
    assert gr.cash_runway(f, gr.BURN_LATEST_QUARTER).months == pytest.approx(12 * 3651.4 / 254.4)  # 172 months
    assert gr.cash_runway(_rvmd(), gr.BURN_TRAILING_YEAR).basis == gr.BURN_LATEST_YEAR
