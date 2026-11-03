"""ABSOLUTE-VALUE-1 (docs/DESIGN_absolute.md sections d to g): free cash flow per share, the own-currency
hurdle, implied growth, the four growth cases, the return range and the new verdict.

Anchored to the design page's Microsoft worked example: base 8.99, price 521.91, bond 4.93%, hurdle 9.93%,
implied growth 25.0%; returns own 5-year 17.2% -> 6.5%, consensus 16.0% -> 6.0%, own 10-year 21.2% -> 8.1%,
downside -> 4.3%; backtest-form downside with 17.2% in years 1 and 2 -> 4.6%. No test reaches a real
adapter: the bond, the exchange rate and the analyst data are fakes.
"""
from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace

import pytest
import yaml

from aristos_council.absolute_value import render as vrender
from aristos_council.absolute_value.definition import (ValueDefinitionError, load_definition,
                                                       parse_definition)
from aristos_council.absolute_value.engine import (BELOW_HURDLE, CLEARS_HURDLE, EXCLUDED, NOT_COVERED,
                                                   NOT_ENOUGH_DATA, build_absolute, free_cash_flow_base,
                                                   price_in_reporting_currency)
from aristos_council.abs_readings import AccountsFx, Reading
from aristos_council.data import bond_yield as by
from aristos_council.data.adapter import Fundamentals
from aristos_council.data.analyst_trend import TrendData, TrendPeriod, parse_trend
from aristos_council.gate.engine import GateResult
from aristos_council.tools import absolute_math as am

TODAY = date(2026, 11, 3)
M = 1e6
DEF = load_definition()


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def msft(**over) -> Fundamentals:
    """Microsoft's three fiscal years from the 10-K (design section e), dated, in dollars."""
    ends = ["2026-06-30", "2025-06-30", "2024-06-30"]
    kw = dict(
        ticker="MSFT", currency="USD", financial_currency="USD", sector="Technology",
        aligned_annual={
            "operating_cash_flow": [182_935 * M, 136_162 * M, 118_548 * M],
            "capital_expenditure": [-115_948 * M, -64_551 * M, -44_477 * M],
            "diluted_shares": [7_453 * M, 7_465 * M, 7_469 * M],
            "depreciation_amortization": [38_534 * M, 29_433 * M, 20_958 * M],
            "diluted_eps": [17.95, 13.64, 11.80]},
        aligned_period_ends={"operating_cash_flow": ends, "capital_expenditure": ends,
                             "diluted_shares": ends, "diluted_eps": ends, "depreciation_amortization": ends},
        capital_expenditure_annual=[-115_948 * M, -64_551 * M, -44_477 * M, -28_107 * M],
        total_revenue=[331_800 * M, 281_700 * M, 245_100 * M, 211_900 * M],
        depreciation_amortization_annual=[38_534 * M, 29_433 * M, 20_958 * M],
        trailing_period_end="2026-06-30", total_debt=56_826 * M, total_cash=76_843 * M)
    kw.update(over)
    return Fundamentals(**kw)


GATE_OK = GateResult(state="qualifies", reason="Qualifies: no exclusion rule failed. No warnings.")


def bond(currency="USD", value=0.04934, when="2026-11-02", symbol="US10Y", name="US ten-year Treasury yield"):
    def source(ccy, *, today):
        return by.BondResult(ccy, by.BondYield(ccy, symbol, name, value, when, 1))
    return source


def record(own5=0.172, own10=0.2116, span5=5, span10=10):
    def leg(rate, window, span):
        return (Reading(value=rate, span=span, label="x") if rate is not None
                else Reading(note="earnings per share was not positive 5 years ago"))
    return SimpleNamespace(eps=SimpleNamespace(cagr={5: leg(own5, 5, span5), 10: leg(own10, 10, span10)}))


def trend(now=20.02, nxt=24.14):
    return TrendData(current=TrendPeriod(period_end="2027-06-30", now=now, analysts=30),
                     next_year=TrendPeriod(period_end="2028-06-30", now=nxt, analysts=28))


def run(f=None, *, gate=GATE_OK, price=521.91, fx=None, rec=None, tr=None, bond_source=None, **kw):
    return build_absolute(f if f is not None else msft(), gate=gate, last_close=price, quote_fx=fx,
                          growth_record=rec if rec is not None else record(),
                          trend=tr if tr is not None else trend(), today=TODAY,
                          bond_source=bond_source or bond(), **kw)


# --------------------------------------------------------------------------- #
# the two solvers against the worked example
# --------------------------------------------------------------------------- #
def test_implied_growth_is_25_percent_for_the_microsoft_example():
    g = am.implied_growth(8.99, 521.91, 0.0993)
    assert round(100 * g, 1) == 25.0


def test_returns_for_each_growth_case_match_the_design_page():
    ret = lambda rates: am.path_return(8.99, 521.91, rates)
    assert round(100 * ret(am.constant_path(0.172)), 1) == 6.5
    assert round(100 * ret(am.constant_path(0.160)), 1) == 6.0
    # the page shows the own 10-year rate as 21.2%; the unrounded rate behind that display is ~21.16%
    assert round(100 * ret(am.constant_path(0.2116)), 1) == 8.1


def test_downside_case_for_microsoft_is_4_3_and_the_backtest_form_is_4_6():
    consensus = (24.14 / 17.95) ** 0.5 - 1                      # 15.97%, two steps from the last reported year
    down = am.downside_path(consensus, 0.04934)
    assert round(100 * am.path_return(8.99, 521.91, down), 1) == 4.3
    assert round(100 * am.path_return(8.99, 521.91, am.downside_path(0.172, 0.04934)), 1) == 4.6


def test_the_downside_path_is_two_fast_years_then_a_straight_line_to_the_bond_by_year_10():
    p = am.downside_path(0.16, 0.05)
    assert len(p) == 10 and p[0] == p[1] == 0.16 and p[-1] == pytest.approx(0.05)
    steps = [b - a for a, b in zip(p[1:], p[2:])]
    assert all(s == pytest.approx(steps[0]) for s in steps)


def test_a_solver_outside_its_range_says_none_not_a_guess():
    assert am.implied_growth(8.99, 1.0, 0.0993) is None            # a price needing less than -10% a year
    assert am.implied_growth(8.99, 1e6, 0.0993) is None            # more than +40% a year
    assert am.implied_growth(-1.0, 100.0, 0.0993) is None and am.implied_growth(8.99, 100.0, 0.0) is None
    assert am.path_return(8.99, 1e9, am.constant_path(0.1)) is None
    assert am.path_return(0.0, 100.0, am.constant_path(0.1)) is None


def test_the_return_equation_is_consistent_with_the_value_it_inverts():
    r = am.path_return(8.99, 521.91, am.constant_path(0.172))
    assert am.path_value(8.99, am.constant_path(0.172), r) == pytest.approx(521.91, rel=1e-9)


# --------------------------------------------------------------------------- #
# free cash flow per share, the lower-of rule
# --------------------------------------------------------------------------- #
def test_the_microsoft_base_is_the_latest_year_because_it_is_lower_than_the_three_year_average():
    b = free_cash_flow_base(msft(), DEF)
    assert [round(y.fcf_per_share, 2) for y in b.years] == [8.99, 9.59, 9.92]
    assert round(b.average, 2) == 9.50 and round(b.base, 2) == 8.99
    assert b.chosen == "the latest year" and b.share_basis == "diluted shares"
    assert b.years[0].label == "the year to Jun 2026"


def test_the_three_year_average_is_the_base_when_it_is_the_lower_of_the_two():
    f = msft(aligned_annual={**msft().aligned_annual,
                             "operating_cash_flow": [182_935 * M, 70_000 * M, 60_000 * M]})
    b = free_cash_flow_base(f, DEF)
    assert b.latest > b.average and b.base == b.average and b.chosen == "the three-year average"


def test_fewer_than_three_years_uses_the_latest_and_says_so():
    f = msft(aligned_annual={**msft().aligned_annual, "operating_cash_flow": [182_935 * M, None, 118_548 * M]})
    b = free_cash_flow_base(f, DEF)
    assert b.average is None and b.base == b.latest and "needs three years" in b.note


def test_a_missing_latest_line_is_not_enough_data_never_a_zero():
    f = msft(aligned_annual={**msft().aligned_annual, "capital_expenditure": [None, -64_551 * M, -44_477 * M]})
    b = free_cash_flow_base(f, DEF)
    assert b.base is None and "capital spending missing" in b.note and not b.usable


def test_a_company_with_no_diluted_count_uses_basic_shares_and_labels_it():
    aligned = dict(msft().aligned_annual)
    del aligned["diluted_shares"]
    f = msft(aligned_annual=aligned, aligned_period_ends={}, shares_outstanding_annual=[7_430 * M, 7_433 * M, 7_440 * M])
    # EODHD shape: nothing dated, every series positional
    f = msft(aligned_annual={}, aligned_period_ends={}, operating_cash_flow_annual=[182_935 * M, 136_162 * M, 118_548 * M],
             shares_outstanding_annual=[7_430 * M, 7_433 * M, 7_440 * M])
    b = free_cash_flow_base(f, DEF)
    assert b.share_basis == "basic shares" and b.usable
    assert round(b.latest, 2) == round((182_935 - 115_948) / 7_430, 2)


def test_a_non_positive_base_reads_not_valued_and_the_verdict_is_not_enough_data():
    f = msft(aligned_annual={**msft().aligned_annual, "operating_cash_flow": [60_000 * M, 50_000 * M, 40_000 * M]})
    av = run(f)
    assert av.base.not_valued and av.base.note == "not valued: no positive free cash flow"
    assert av.verdict.state == NOT_ENOUGH_DATA and "no positive free cash flow" in av.verdict.reason
    assert av.implied_growth is None and all(not c.available for c in av.cases)
    assert "Not valued: no positive free cash flow" in "\n".join(vrender.text_lines(av)) \
        or "not valued: no positive free cash flow" in "\n".join(vrender.text_lines(av))


def test_the_yfinance_labels_for_diluted_shares_and_capital_spending_are_read():
    import pandas as pd

    from aristos_council.data.yfinance_adapter import _aligned_series, _annual_series
    cols = [pd.Timestamp("2026-06-30"), pd.Timestamp("2025-06-30")]
    income = pd.DataFrame([[7_453e6, 7_465e6]], index=["Diluted Average Shares"], columns=cols)
    cash = pd.DataFrame([[-115_948e6, -64_551e6]], index=["Capital Expenditure"], columns=cols)
    assert _annual_series(income, "Diluted Average Shares") == [7_453e6, 7_465e6]
    assert _aligned_series(cash, "Capital Expenditure", "Capital Expenditures")[1] == [-115_948e6, -64_551e6]


# --------------------------------------------------------------------------- #
# the price: exchange rates
# --------------------------------------------------------------------------- #
def test_a_price_in_another_currency_is_converted_with_the_dated_rate_and_the_rate_is_shown():
    f = Fundamentals(ticker="1211.HK", currency="HKD", financial_currency="CNY")
    fx = AccountsFx(from_ccy="CNY", to_ccy="HKD", rate=1.17, as_of="2026-10", source="yfinance CNYHKD=X")
    p = price_in_reporting_currency(f, 117.0, fx)
    assert p.price == pytest.approx(100.0) and p.currency == "CNY"
    assert p.rate_line == "at 1 CNY = 1.1700 HKD, Oct 2026"


def test_no_exchange_rate_is_stated_and_never_guessed():
    f = Fundamentals(ticker="1211.HK", currency="HKD", financial_currency="CNY")
    p = price_in_reporting_currency(f, 117.0, None)
    assert not p.available and p.note == "no exchange rate"
    av = run(msft(currency="HKD", financial_currency="CNY"), fx=None)
    assert av.verdict.state == NOT_ENOUGH_DATA and "no exchange rate" in av.verdict.reason


def test_pence_against_pounds_is_exact_and_needs_no_feed():
    f = Fundamentals(ticker="AZN.L", currency="GBX", financial_currency="GBP")
    assert price_in_reporting_currency(f, 12_000.0, None).price == pytest.approx(120.0)


def test_the_same_currency_needs_no_conversion():
    p = price_in_reporting_currency(Fundamentals(ticker="X", currency="USD", financial_currency="USD"), 10.0, None)
    assert p.price == 10.0 and not p.rate_line


# --------------------------------------------------------------------------- #
# the hurdle: bond mapping, missing series, stale yield
# --------------------------------------------------------------------------- #
def rows(last="2026-11-02", close=4.934):
    return [{"date": "2026-10-30", "close": 4.9}, {"date": last, "close": close}]


def test_the_us_yield_is_read_as_a_fraction_with_its_date_and_series():
    res = by.bond_yield("USD", today=TODAY, rows_fetcher=lambda s, t: rows())
    assert res.available and res.yield_.value == pytest.approx(0.04934) and res.yield_.symbol == "US10Y"
    assert res.yield_.as_of == "2026-11-02"


def test_the_hurdle_is_the_bond_plus_five_points_and_the_page_prints_all_of_it():
    av = run()
    h = av.hurdle
    assert h.hurdle == pytest.approx(0.09934) and h.bond_symbol == "US10Y" and h.bond_date == "2026-11-02"
    line = vrender._hurdle_line(av)
    assert "9.93%" in line and "4.93%" in line and "5.0 points" in line and "2 Nov 2026" in line


def test_the_euro_maps_to_the_german_series_and_the_hong_kong_dollar_to_the_us_one_with_a_peg_note():
    table = by.load_series_map()
    assert table["EUR"]["symbol"] == "DE10Y" and table["GBP"]["symbol"] == "UK10Y"
    assert table["JPY"]["symbol"] == "JP10Y"
    assert table["HKD"]["symbol"] == "US10Y" and "pegged" in table["HKD"]["note"]
    got = by.bond_yield("HKD", today=TODAY, rows_fetcher=lambda s, t: rows())
    assert got.yield_.symbol == "US10Y" and "pegged" in got.yield_.note


def test_a_pence_code_looks_up_its_major_currency():
    assert by.bond_yield("GBX", today=TODAY, rows_fetcher=lambda s, t: rows()).yield_.symbol == "UK10Y"


def test_a_currency_with_no_series_has_no_hurdle_and_says_so():
    table = by.load_series_map()
    assert table["CHF"]["symbol"] == "SW10Y"                       # CH10Y is Chile, not Switzerland
    assert "CLP" not in table and "ZAR" not in table
    res = by.bond_yield("ZAR", today=TODAY, rows_fetcher=lambda s, t: rows())
    assert not res.available and "no government bond series is mapped for ZAR" in res.reason
    av = run(bond_source=lambda ccy, *, today: by.bond_yield("ZAR", today=today, rows_fetcher=lambda s, t: rows()))
    assert av.verdict.state == NOT_ENOUGH_DATA and "no hurdle" in av.verdict.reason


def test_a_yield_is_used_for_seven_days_and_then_lapses():
    ok = by.bond_yield("USD", today=date(2026, 11, 9), rows_fetcher=lambda s, t: rows("2026-11-02"))
    assert ok.available                                            # 7 days old
    lapsed = by.bond_yield("USD", today=date(2026, 11, 10), rows_fetcher=lambda s, t: rows("2026-11-02"))
    assert not lapsed.available and "more than 7 days old" in lapsed.reason


def test_a_failing_fetch_or_an_empty_series_is_no_hurdle_not_a_crash():
    def boom(s, t):
        raise OSError("network down")
    assert not by.bond_yield("USD", today=TODAY, rows_fetcher=boom, cache_dir="no/such/dir").available
    assert not by.bond_yield("USD", today=TODAY, rows_fetcher=lambda s, t: []).available
    assert not by.bond_yield("USD", today=TODAY, rows_fetcher=lambda s, t: [{"date": "2026-11-02", "close": -1}]).available


def test_without_a_key_nothing_is_asked_for_and_no_cache_is_read(monkeypatch, tmp_path):
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    (tmp_path / "eodhd_US10Y_2026_11_03_gbond.json").write_text('[{"date": "2026-11-02", "close": 4.9}]')
    res = by.bond_yield("USD", today=TODAY, cache_dir=tmp_path)
    assert not res.available and "EODHD_API_KEY is not set" in res.reason


def test_a_transient_failure_is_retried_and_a_clean_refusal_is_not(tmp_path):
    calls = {"n": 0}

    class Resp:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.body

    def flaky(url, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("connection reset: timeout")
        return Resp(b'[{"date": "2026-11-02", "close": 4.9}]')

    res = by.bond_yield("USD", today=TODAY, api_key="k", cache_dir=tmp_path, opener=flaky, sleep=lambda s: None)
    assert res.available and calls["n"] == 3
    assert any("gbond" in p.name for p in tmp_path.iterdir())      # cached for the day

    calls["n"] = 0

    def refused(url, timeout):
        calls["n"] += 1
        raise OSError("HTTP Error 404: Not Found")
    res = by.bond_yield("USD", today=date(2026, 11, 4), api_key="k", cache_dir=tmp_path / "other",
                        opener=refused, sleep=lambda s: None)
    assert not res.available and calls["n"] == 1                   # no wasted retries


def test_the_bond_file_maps_each_currency_once_to_a_symbol_ending_gbond_ready():
    table = by.load_series_map()
    assert {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "SEK", "NOK", "DKK", "KRW", "TWD", "CNY", "INR", "BRL",
            "SGD", "HKD", "CHF"} <= set(table)
    assert all(re.fullmatch(r"[A-Z]{2}10Y", v["symbol"]) for v in table.values())


# --------------------------------------------------------------------------- #
# the growth cases and the 18% mark
# --------------------------------------------------------------------------- #
def test_the_four_cases_and_the_verdict_for_microsoft_are_the_design_pages():
    av = run()
    by_key = {c.key: c for c in av.cases}
    assert round(100 * by_key["own_5_year"].ret, 1) == 6.5
    assert round(100 * by_key["consensus"].growth, 1) == 16.0 and round(100 * by_key["consensus"].ret, 1) == 6.0
    assert round(100 * by_key["own_10_year"].ret, 1) == 8.1
    assert round(100 * by_key["downside"].ret, 1) == 4.3
    assert round(100 * av.implied_growth, 1) == 25.0
    assert av.verdict.state == BELOW_HURDLE and av.verdict.count_text() == "0 of 4 cases clear"
    assert (round(100 * av.verdict.low, 1), round(100 * av.verdict.high, 1)) == (4.3, 8.1)
    bt = av.backtest_verdict
    assert bt.state == BELOW_HURDLE and bt.count_text() == "0 of 3 cases clear"
    assert round(100 * av.backtest_cases[2].ret, 1) == 4.6
    assert [c.key for c in av.backtest_cases] == ["own_5_year", "own_10_year", "downside"]
    assert round(100 * av.yield_on_base, 1) == 1.7


def test_the_consensus_is_two_steps_from_the_last_reported_year_with_both_years_named():
    av = run()
    c = av.cases[2]
    assert c.not_testable and "two steps" in c.note
    assert "the year to Jun 2026 ($17.95)" in c.note and "the year to Jun 2028 ($24.14)" in c.note


def test_a_growth_rate_above_18_percent_carries_the_mark_and_is_still_shown():
    av = run()
    marks = {c.key: c.high_growth for c in av.cases}
    assert marks == {"own_5_year": False, "own_10_year": True, "consensus": False, "downside": False}
    text = "\n".join(vrender.text_lines(av))
    assert "above 18%" in text and "few companies have grown faster than 18% a year for ten years" in text


def test_a_case_is_unavailable_when_the_start_or_end_earnings_are_not_positive():
    av = run(rec=record(own5=None))
    c = {x.key: x for x in av.cases}
    assert not c["own_5_year"].available and "not positive" in c["own_5_year"].reason
    assert c["downside"].available and "consensus" in c["downside"].growth_text      # falls on the consensus rate
    assert av.verdict.of == 3


def test_a_ten_year_rate_built_from_fewer_years_of_accounts_is_not_called_a_ten_year_rate():
    av = run(rec=record(span10=3))
    assert not {c.key: c for c in av.cases}["own_10_year"].available


def test_the_consensus_case_is_unavailable_without_both_forecast_years():
    only_current = TrendData(current=TrendPeriod(period_end="2027-06-30", now=20.02))
    av = run(tr=only_current)
    c = {x.key: x for x in av.cases}
    assert not c["consensus"].available
    assert "own 5-year" in c["downside"].growth_text                                # fallback for the downside
    none = run(tr=SimpleNamespace(current=None, next_year=None, note="EODHD has no block"))
    assert not {x.key: x for x in none.cases}["consensus"].available


def test_a_consensus_from_a_loss_year_is_not_defined():
    f = msft(aligned_annual={**msft().aligned_annual, "diluted_eps": [-1.0, 13.64, 11.80]})
    assert not {c.key: c for c in run(f).cases}["consensus"].available


def test_the_consensus_parser_keeps_the_year_ago_figure():
    doc = {"2027-06-30": {"date": "2027-06-30", "period": "0y", "earningsEstimateAvg": "20.02",
                          "earningsEstimateYearAgoEps": "17.95", "earningsEstimateNumberOfAnalysts": "30"},
           "2028-06-30": {"date": "2028-06-30", "period": "+1y", "earningsEstimateAvg": "24.14"}}
    t = parse_trend(doc, today=TODAY)
    assert t.current.year_ago_eps == 17.95 and t.next_year.now == 24.14


def test_fewer_than_two_cases_is_not_enough_data():
    av = run(rec=record(own5=None, own10=None), tr=SimpleNamespace(current=None, next_year=None, note="none"))
    assert av.verdict.state == NOT_ENOUGH_DATA and "fewer than 2" in av.verdict.reason


# --------------------------------------------------------------------------- #
# the four verdict states
# --------------------------------------------------------------------------- #
def test_qualifies_clears_hurdle_needs_every_available_case_including_the_downside_to_reach_it():
    av = run(price=60.0)                                           # a low price: every case returns more
    assert av.verdict.state == CLEARS_HURDLE and av.verdict.count_text() == "4 of 4 cases clear"
    assert all(c.clears(av.hurdle.hurdle) for c in av.cases)


def test_one_case_below_the_hurdle_keeps_it_below_and_counts_the_ones_that_clear():
    av = run(price=300.0)                                          # own 5-year 10.0% clears, the downside 7.1% does not
    assert av.verdict.state == BELOW_HURDLE
    assert 0 < av.verdict.clearing < av.verdict.of


def test_the_gate_decides_excluded_not_covered_and_not_enough_data():
    ex = run(gate=GateResult(state="excluded", reason="Excluded: Solvency (cash flow is 2.0% of debt)."))
    assert ex.verdict.state == EXCLUDED and "Solvency" in ex.verdict.reason
    assert ex.backtest_verdict.state == EXCLUDED
    nc = run(gate=GateResult(state="not_covered", reason="Not covered: banks and insurers are outside this gate."))
    assert nc.verdict.state == NOT_COVERED and not nc.shown and not nc.cases
    ne = run(gate=GateResult(state="not_enough_data", reason="Not enough data: accrual ratio (x)."))
    assert ne.verdict.state == NOT_ENOUGH_DATA and "accrual ratio" in ne.verdict.reason
    assert run(gate=None).verdict.state == NOT_ENOUGH_DATA


def test_a_bank_gets_one_line_and_no_return_figure_in_any_renderer():
    av = run(gate=GateResult(state="not_covered", reason="Not covered: banks and insurers."))
    text = "\n".join(vrender.text_lines(av))
    assert "banks and insurers are outside this path" in text and "Return" not in text.split("\n", 2)[2]
    assert "%" not in vrender.NOT_COVERED_LINE


def test_a_missing_gate_input_does_not_hide_the_readings_it_does_not_block():
    av = run(gate=GateResult(state="not_enough_data", reason="Not enough data: solvency."))
    assert av.hurdle.available and av.cases[0].available and av.verdict.state == NOT_ENOUGH_DATA


# --------------------------------------------------------------------------- #
# context readings
# --------------------------------------------------------------------------- #
def test_investment_intensity_prints_capex_over_depreciation_and_warns_above_the_frozen_boundary():
    av = run()
    assert av.context["capex_over_depreciation"] == pytest.approx(115_948 / 38_534)
    f = msft(capital_expenditure_annual=[-300_000 * M, -64_551 * M, -44_477 * M, -28_107 * M])
    hot = run(f)
    assert hot.context["abnormal_investment"] > 0.2676 and hot.context["investment_warning"] == \
        "investment well above its own history"
    assert not av.context.get("investment_warning") or av.context["abnormal_investment"] > 0.2676


def test_the_boundary_comes_from_the_gate_file_not_from_the_valuation_file():
    doc = yaml.safe_load(open("valuation/absolute_value_v1.yaml", encoding="utf-8"))
    assert "0.2676" not in open("valuation/absolute_value_v1.yaml", encoding="utf-8").read()
    assert doc["investment_intensity"]["boundary_from"] == "gates/absolute_gate_v2.yaml"


def test_the_caller_supplied_context_is_printed():
    av = run(context={"ebit_over_ev": 0.031, "ebit_over_ev_label": "Operating profit over enterprise value",
                      "epv": 1.9e12, "epv_enterprise_value": 3.6e12})
    text = "\n".join(vrender.text_lines(av))
    assert "Operating profit over enterprise value: 3.1%" in text
    assert "Earnings power value at 9%" in text and "an enterprise value" in text


# --------------------------------------------------------------------------- #
# the definition file
# --------------------------------------------------------------------------- #
def test_the_shipped_definition_holds_every_number_the_design_names():
    assert DEF.margin_points == 5.0 and DEF.horizon_years == 10 and DEF.average_years == 3
    assert DEF.implied_growth_range == (-0.10, 0.40) and DEF.return_range == (0.0001, 0.60)
    assert DEF.high_growth_mark == 0.18 and DEF.staleness_days == 7 and DEF.fast_years == 2
    assert DEF.own_windows == (5, 10) and DEF.status == "frozen"
    assert set(DEF.verdict_states) == {"excluded", "not_covered", "not_enough_data", "qualifies_below_hurdle",
                                       "qualifies_clears_hurdle"}


def test_a_definition_missing_a_number_is_refused_at_load():
    doc = yaml.safe_load(open("valuation/absolute_value_v1.yaml", encoding="utf-8"))
    del doc["hurdle"]["margin_points"]
    with pytest.raises(ValueDefinitionError):
        parse_definition(doc)


def test_the_stale_limit_in_the_definition_matches_the_bond_module():
    assert DEF.staleness_days == by.STALENESS_DAYS


# --------------------------------------------------------------------------- #
# display rules, the page, the downloads and the saved record
# --------------------------------------------------------------------------- #
def test_the_section_uses_the_owners_wording_and_never_the_forbidden_words():
    av = run()
    text = "\n".join(vrender.text_lines(av, outside_range=True)).lower()
    assert "long-run owner's return if the growth holds" in text
    assert "they are not forecasts" in text and "the gate and the cash yield carry the evidence" in text
    assert "outside the tested range" in text
    for word in ("expected return", "target", "proven"):
        assert word not in text
    from aristos_council.report_sweep import internal_id_findings
    assert internal_id_findings("\n".join(vrender.text_lines(av)), "section") == []


def test_the_verdict_names_the_state_the_count_and_the_range():
    text = "\n".join(vrender.text_lines(run()))
    assert "Verdict: Qualifies, below hurdle, 0 of 4 cases clear. The range is 4.3% to 8.1% a year, " \
           "against a hurdle of 9.93%." in text
    assert "Backtest form" in text and "0 of 3 cases clear" in text


def test_the_other_lease_liabilities_line_is_worded_as_agreed():
    from aristos_council.abs_readings import debt_and_cash
    f = Fundamentals(ticker="MSFT", financial_currency="USD", total_debt=56.8e9, total_cash=76.8e9,
                     lease_liabilities=72.0e9, operating_cash_flow=180e9, trailing_period_end="2026-06-30")
    lines = debt_and_cash(f).lines()
    assert "other lease liabilities of $72.0bn are not in that figure" in lines
    assert not any("not counted as debt" in ln for ln in lines)


def test_the_debt_sentence_is_one_sentence_with_the_balance_sheet_date():
    from aristos_council.tools.gate_readings import debt_definition_line
    assert debt_definition_line(msft()) == ("Debt = borrowings plus the long-term operating lease line, "
                                            "from the balance sheet of Jun 2026.")
    assert av_debt(run()) == debt_definition_line(msft())


def av_debt(av):
    return av.debt_line


@pytest.mark.min_group(3)
def test_the_section_reaches_the_text_markdown_and_html_and_adds_one_key_to_the_record(tmp_path):
    from aristos_council.company_markdown import company_report_markdown
    from aristos_council.company_report import format_company_report, report_record
    from aristos_council.export.report_html import company_report_html
    from tests.test_company_report import RAW, _run

    report = _run([RAW], tmp_path=tmp_path, save=False)
    assert report.check.absolute is not None
    text, md, html = format_company_report(report), company_report_markdown(report), company_report_html(report)
    assert vrender.SECTION_TITLE.upper() in text
    assert f"## {vrender.SECTION_TITLE}" in md and 'id="absolute-value"' in html and vrender.SECTION_TITLE in html
    assert text.index("GATE (ABSOLUTE-1") < text.index(vrender.SECTION_TITLE.upper()) < text.index("Vote or mark")
    with_block = report_record(report)
    assert with_block["absolute"]["verdict"]["state"] == report.check.absolute.verdict.state
    report.check.absolute = None
    without = report_record(report)
    assert "absolute" not in without
    assert {k: v for k, v in with_block.items() if k != "absolute"} == without      # nothing else moved


def test_the_first_screen_budget_does_not_count_the_skippable_section():
    from aristos_council.report_sweep import first_screen_lines
    body = "\n".join(["The answer", "line"] + vrender.text_lines(run()) + ["", "Lens table"])
    assert first_screen_lines(body) <= 4


def test_a_negative_operating_cash_flow_with_no_capital_spending_line_is_still_not_valued():
    """Capital spending can only lower free cash flow (VKTX: a loss-maker with no capex row)."""
    aligned = {**msft().aligned_annual, "operating_cash_flow": [-300 * M, -200 * M, -100 * M],
               "capital_expenditure": [None, None, None]}
    b = free_cash_flow_base(msft(aligned_annual=aligned), DEF)
    assert b.not_valued and b.note == "not valued: no positive free cash flow"
    assert "not valued: no positive free cash flow" in "\n".join(vrender.text_lines(run(msft(aligned_annual=aligned))))
    # ...but a missing capital-spending line with POSITIVE operating cash flow stays unknown
    unknown = {**msft().aligned_annual, "capital_expenditure": [None, -64_551 * M, -44_477 * M]}
    assert not free_cash_flow_base(msft(aligned_annual=unknown), DEF).not_valued


# --------------------------------------------------------------------------- #
# ABSOLUTE-VALUE-2: the consensus case starts and ends on the same per-share basis
# --------------------------------------------------------------------------- #
def payload(year_ago=66.25, first=110.55, second=149.18, ccy=""):
    return TrendData(current=TrendPeriod(period_end="2026-12-31", now=first, analysts=30, year_ago_eps=year_ago),
                     next_year=TrendPeriod(period_end="2027-12-31", now=second, analysts=28), eps_currency=ccy)


def tw(**kw):   # statements 65.47 (diluted), a December year end
    aligned = {**msft().aligned_annual, "diluted_eps": [65.47, 50.0, 40.0]}
    ends = {**msft().aligned_period_ends, "diluted_eps": ["2025-12-31"] * 3}
    return msft(aligned_annual=aligned, aligned_period_ends=ends, **kw)


def cons(av):
    return {c.key: c for c in av.cases}["consensus"]


def test_the_start_is_the_analyst_payloads_own_last_reported_figure_and_the_source_is_printed():
    c = cons(run(tw(), tr=payload()))
    assert c.available and round(100 * c.growth, 1) == round(100 * ((149.18 / 66.25) ** 0.5 - 1), 1)
    assert "start: the analyst data's own last reported figure" in c.note and "$66.25" in c.note
    assert "the year to Dec 2025" in c.note and "the year to Dec 2027" in c.note and "two steps" in c.note


def test_a_big_first_step_is_not_a_mismatch_when_both_ends_come_from_the_payload():
    # 66.25 -> 110.55 is +67%, but the payload's own actual is the start, so there is no basis to doubt
    assert cons(run(tw(), tr=payload())).available


def test_the_statement_start_is_the_fallback_and_says_so():
    c = cons(run(tw(), tr=payload(year_ago=None, first=70.0, second=80.0)))
    assert c.available and "start: the statements' diluted EPS" in c.note and "$65.47" in c.note


def test_a_first_forecast_year_over_40_percent_from_the_statement_is_a_suspected_basis_mismatch():
    av = run(tw(), tr=payload(year_ago=None, first=130.0, second=150.0))
    c = cons(av)
    assert not c.available and c.reason.startswith("unavailable: per-share basis mismatch suspected")
    assert "$65.47" in c.reason and "$130.00" in c.reason and "+99%" in c.reason
    assert av.verdict.of == 3                                      # left out of the count
    assert "own 5-year" in {x.key: x for x in av.cases}["downside"].growth_text   # and out of the downside
    low = cons(run(tw(), tr=payload(year_ago=None, first=30.0, second=80.0)))
    assert not low.available and "-54%" in low.reason


def test_a_step_of_exactly_40_percent_passes():
    assert cons(run(tw(), tr=payload(year_ago=None, first=65.47 * 1.4, second=120.0))).available


def test_analyst_figures_in_another_currency_are_converted_with_the_fx_pair_and_it_is_said():
    t = payload(year_ago=None, first=100.0, second=110.0, ccy="EUR")        # EUR figures, USD statements
    fx = AccountsFx(from_ccy="USD", to_ccy="EUR", rate=0.9, as_of="2026-10", source="yfinance USDEUR=X")
    c = cons(run(tw(), tr=t, fx_lookup=lambda frm, to: fx if (frm, to) == ("USD", "EUR") else None))
    # 100 EUR / 0.9 = $111.11 against a $65.47 statement is +70%: converted, said so, and still a mismatch
    assert not c.available and "$111.11" in c.reason and "converted from EUR to USD" in c.reason


def test_a_converted_pair_that_agrees_is_used_and_one_without_a_rate_is_unavailable():
    t = payload(year_ago=None, first=60.0, second=66.0, ccy="EUR")           # 60 EUR / 0.9 = $66.67 (+1.8%)
    fx = AccountsFx(from_ccy="USD", to_ccy="EUR", rate=0.9, as_of="2026-10")
    ok = cons(run(tw(), tr=t, fx_lookup=lambda frm, to: fx))
    assert ok.available and "converted from EUR to USD" in ok.note
    none = cons(run(tw(), tr=t, fx_lookup=lambda frm, to: None))
    assert not none.available and "no exchange rate between EUR and USD" in none.reason


def test_the_payload_start_needs_no_conversion_because_both_ends_share_a_currency():
    c = cons(run(tw(), tr=payload(ccy="EUR")))                                # statements USD, payload EUR
    assert c.available and "converted" not in c.note


def test_a_payload_whose_listing_currency_differs_prints_its_figures_bare_not_in_a_guessed_currency():
    c = cons(run(tw(), tr=payload(ccy="HKD")))
    assert c.available and "66.25" in c.note and "$66.25" not in c.note and "HKD" not in c.note
    assert "currency of the analyst figures not stated by the source" in c.note
