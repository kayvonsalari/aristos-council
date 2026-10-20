"""Batch 27B-2 - ASOF-FILING-1: the as-of adapter's "filing" mode.

A fiscal period is visible when its FILING date is on or before the round date; a period with no
usable filing date falls back to period end + lag and is COUNTED. Lag mode (the default) is unchanged.
Fabricated data only.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from aristos_council.data.adapter import Fundamentals, MarketDataAdapter, PriceBar, PriceHistory
from aristos_council.data.asof_adapter import AsOfAdapter
from aristos_council.data.eodhd_adapter import fundamentals_from_payload

AS_OF = date(2020, 6, 30)


class _Inner(MarketDataAdapter):
    name = "fake"

    def __init__(self, fundamentals):
        self._f = fundamentals

    def get_fundamentals(self, ticker):
        return self._f

    def get_price_history(self, ticker, *, start, end):
        bar = PriceBar(day=date(2020, 6, 29), open=50, high=50, low=50, close=50, adj_close=50, volume=1)
        return PriceHistory(ticker=ticker, bars=[bar])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _f(periods: dict, *, with_filing=True):
    """``periods`` is {period end: filing date or ""}; values equal the year so a leak shows."""
    ends = sorted(periods, reverse=True)
    values = [float(e[:4]) for e in ends]
    names = ("total_revenue", "ebit")
    return Fundamentals(
        ticker="X", name="X Corp", company_name="X Corp", sector="Technology", currency="USD",
        financial_currency="USD", total_revenue=list(values), ebit=list(values),
        period_ends={n: list(ends) for n in names},
        filing_dates=({n: [periods[e] for e in ends] for n in names} if with_filing else {}),
        period_scalars={e: {"eps": 1.0, "free_cash_flow": 1.0, "total_debt": 1.0, "total_cash": 1.0,
                            "dividends_paid": 0.0, "operating_cash_flow": 1.0,
                            "capital_expenditure": 1.0, "shares_outstanding": 100.0} for e in ends})


def _adapt(f, mode="filing", lag=90):
    return AsOfAdapter(_Inner(f), AS_OF, lag, mode)


# the period ending 2020-03-31 is only 91 days old on 2020-06-30: visible under lag 90 either way, so
# the cases below use periods that differ between the two rules.
def test_a_period_filed_early_is_visible_earlier_than_lag_90():
    # FY ended 2020-05-31, filed 2020-06-15: lag-90 would hide it until 2020-08-29
    f = _f({"2020-05-31": "2020-06-15", "2019-12-31": "2020-02-20"})
    lag = _adapt(f, "lag").get_fundamentals("X")
    filing = _adapt(f, "filing").get_fundamentals("X")
    assert lag.period_ends["ebit"] == ["2019-12-31"]
    assert filing.period_ends["ebit"] == ["2020-05-31", "2019-12-31"]
    assert filing.ebit == [2020.0, 2019.0]
    assert filing.filing_dates["ebit"] == ["2020-06-15", "2020-02-20"]        # cut in step


def test_a_period_filed_late_is_visible_later_than_lag_90():
    # FY ended 2019-12-31 but filed 2020-07-20 (a late filer): lag-90 shows it, filing mode must not
    f = _f({"2019-12-31": "2020-07-20", "2018-12-31": "2019-03-01"})
    assert _adapt(f, "lag").get_fundamentals("X").period_ends["ebit"][0] == "2019-12-31"
    got = _adapt(f, "filing").get_fundamentals("X")
    assert got.period_ends["ebit"] == ["2018-12-31"]
    assert got.ebit == [2018.0]


def test_the_filing_day_itself_is_visible():
    f = _f({"2020-05-31": AS_OF.isoformat(), "2019-12-31": "2020-02-20"})
    assert _adapt(f).get_fundamentals("X").period_ends["ebit"][0] == "2020-05-31"
    f2 = _f({"2020-05-31": (AS_OF + timedelta(days=1)).isoformat(), "2019-12-31": "2020-02-20"})
    assert _adapt(f2).get_fundamentals("X").period_ends["ebit"][0] == "2019-12-31"


def test_a_missing_filing_date_falls_back_to_lag_and_is_counted():
    f = _f({"2020-05-31": "", "2019-12-31": "", "2018-12-31": "2019-03-01"})
    ad = _adapt(f)
    got = ad.get_fundamentals("X")
    assert got.period_ends["ebit"] == ["2019-12-31", "2018-12-31"]            # 2020-05-31 is inside the lag
    assert ad.filing_decisions == 3 and ad.filing_date_fallbacks == 2


def test_an_impossible_filing_date_before_the_period_end_is_a_fallback_not_trusted():
    f = _f({"2020-05-31": "2020-01-01", "2019-12-31": "2020-02-20"})        # "filed" before it ended
    ad = _adapt(f)
    assert ad.get_fundamentals("X").period_ends["ebit"] == ["2019-12-31"]
    assert ad.filing_date_fallbacks == 1


def test_a_filing_date_equal_to_the_period_end_is_eodhds_placeholder_and_falls_back():
    """Live 2026-10-20: 1211.HK and RIO.AU carry filing_date == period end for most years. Trusting it
    would show a year the day it closed, months before it was public."""
    f = _f({"2021-12-31": "2021-12-31", "2020-12-31": "2020-12-31"})
    ad = AsOfAdapter(_Inner(f), date(2022, 3, 15), 90, "filing")
    assert ad.get_fundamentals("X").period_ends["ebit"] == ["2020-12-31"]    # same as lag 90, not 2021
    assert ad.filing_date_fallbacks == ad.filing_decisions == 2


def test_a_yfinance_style_name_with_no_filing_dates_is_all_fallback_and_equals_lag_mode():
    f = _f({"2020-05-31": "", "2019-12-31": "", "2018-12-31": ""}, with_filing=False)
    ad = _adapt(f)
    filing = ad.get_fundamentals("X")
    lag = _adapt(f, "lag").get_fundamentals("X")
    assert ad.filing_decisions >= 3 and ad.filing_date_fallbacks == ad.filing_decisions
    assert filing.period_ends == lag.period_ends and filing.ebit == lag.ebit


def test_lag_mode_is_byte_identical_whether_or_not_filing_dates_are_present():
    with_dates = _f({"2020-05-31": "2020-06-15", "2019-12-31": "2020-07-20", "2018-12-31": "2019-03-01"})
    without = _f({"2020-05-31": "", "2019-12-31": "", "2018-12-31": ""}, with_filing=False)
    a = AsOfAdapter(_Inner(with_dates), AS_OF, 90).get_fundamentals("X")
    b = AsOfAdapter(_Inner(without), AS_OF, 90, "lag").get_fundamentals("X")
    assert a.period_ends == b.period_ends and a.ebit == b.ebit and a.total_revenue == b.total_revenue
    ad = AsOfAdapter(_Inner(with_dates), AS_OF, 90)
    ad.get_fundamentals("X")
    assert ad.filing_decisions == 0 and ad.filing_date_fallbacks == 0         # lag mode consults nothing


def test_provenance_names_the_mode():
    f = _f({"2020-05-31": "2020-06-15", "2019-12-31": "2020-02-20"})
    assert "lag 90 days" in _adapt(f, "lag").get_fundamentals("X").provenance
    assert "filing dates" in _adapt(f, "filing").get_fundamentals("X").provenance


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        AsOfAdapter(_Inner(_f({"2019-12-31": ""})), AS_OF, 90, "nonsense")


# --------------------------------------------------------------------------- #
# the EODHD mapper carries filing_date
# --------------------------------------------------------------------------- #
_PAYLOAD = {"General": {"Name": "X", "CurrencyCode": "USD"}, "Highlights": {}, "Financials": {
    "Income_Statement": {"currency_symbol": "USD", "yearly": {
        "2019-12-31": {"totalRevenue": "100", "ebit": "10", "filing_date": "2020-02-20"},
        "2018-12-31": {"totalRevenue": "90", "ebit": "9", "filing_date": "NA"},
        "2017-12-31": {"totalRevenue": "80", "ebit": "8"}}},
    "Balance_Sheet": {"yearly": {}}, "Cash_Flow": {"yearly": {}}}}


def test_the_mapper_carries_filing_dates_parallel_to_period_ends_and_blanks_for_gaps():
    f = fundamentals_from_payload("X.US", _PAYLOAD, with_periods=True)
    assert f.period_ends["total_revenue"] == ["2019-12-31", "2018-12-31", "2017-12-31"]
    assert f.filing_dates["total_revenue"] == ["2020-02-20", "", ""]          # "NA" sentinel and absent -> ""
    assert all(len(f.filing_dates[k]) == len(f.period_ends[k]) for k in f.period_ends)


def test_the_mapper_without_periods_is_unchanged():
    assert fundamentals_from_payload("X.US", _PAYLOAD).filing_dates == {}


# --------------------------------------------------------------------------- #
# the CSV header
# --------------------------------------------------------------------------- #
def test_the_csv_header_records_the_mode_and_the_fallback_count_and_reads_back(tmp_path):
    from aristos_council.backtest import BacktestResult, Round, from_csv, to_csv
    rd = Round(date(2020, 1, 28), 3, 0.1, 0.05, 0.05, ("A", "B", "C"), date(2021, 1, 28), 8, 10)
    res = BacktestResult("Test Cohort", "x_v1", date(2019, 9, 1), date(2020, 9, 1), rounds=[rd],
                         as_of_mode="filing", filing_decisions=200, filing_date_fallbacks=30)
    text = to_csv(res, tmp_path).read_text(encoding="utf-8")
    assert "# as_of_mode: filing" in text
    assert "# filing_date_fallbacks: 30 of 200 fiscal periods (15.0%)" in text
    assert "filing date is on or before the round date" in text
    back = from_csv(to_csv(res, tmp_path))
    assert (back.as_of_mode, back.filing_decisions, back.filing_date_fallbacks) == ("filing", 200, 30)


def test_a_lag_mode_file_says_so_and_an_old_file_without_the_lines_reads_as_lag(tmp_path):
    from aristos_council.backtest import BacktestResult, Round, from_csv, to_csv
    rd = Round(date(2020, 1, 28), 3, 0.1, 0.05, 0.05, ("A", "B", "C"), date(2021, 1, 28), 8, 10)
    res = BacktestResult("Test Cohort", "x_v1", date(2019, 9, 1), date(2020, 9, 1), rounds=[rd])
    path = to_csv(res, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "# as_of_mode: lag" in text and "# filing_date_fallbacks: n/a - lag mode" in text
    path.write_text("\n".join(l for l in text.splitlines()
                              if not l.startswith(("# as_of_mode", "# filing_date_fallbacks"))) + "\n",
                    encoding="utf-8")
    old = from_csv(path)
    assert (old.as_of_mode, old.filing_decisions, old.filing_date_fallbacks) == ("lag", 0, 0)


def test_the_committed_csvs_all_read_as_lag_mode():
    from aristos_council.backtest import _load_backtests
    assert {r.as_of_mode for r in _load_backtests().values()} == {"lag"}


def test_run_lens_backtest_passes_the_mode_to_every_round_adapter_and_stamps_the_result(monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    import test_lens_backtest as t
    calls = []
    result = t._run(monkeypatch, calls=calls, as_of_mode="filing")
    assert result.as_of_mode == "filing" and calls
    assert all(kw["adapter"].mode == "filing" for _u, _l, kw in calls)
    assert "filing date" in "".join(result.caveats)
    default = t._run(monkeypatch, calls=[], lag_days=45)
    assert default.as_of_mode == "lag" and default.filing_decisions == 0


def test_an_unknown_as_of_mode_is_refused_by_the_backtest(monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    import test_lens_backtest as t
    with pytest.raises(ValueError):
        t._run(monkeypatch, as_of_mode="nonsense")
