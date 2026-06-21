"""EODHD adapter — dividend history + the calendar-year streak it makes possible.

No live API calls: the HTTP boundary is factored out (``dividend_events_from_rows``
is a pure parser, and ``_get_json`` is overridden in a fake), so every test runs
on recorded fixture JSON — the same fake pattern the rest of the suite uses.

The fixtures encode the traps the Nestlé data exposed: split-adjusted vs raw
values, an annual->Interim/Final cadence change, a genuine cut, short history,
and an incomplete current year.
"""

from __future__ import annotations

from datetime import date

import pytest

from aristos_council.data.adapter import DataUnavailable, normalize_ticker
from aristos_council.data.eodhd_adapter import (
    EODHDAdapter,
    dividend_events_from_rows,
)
from aristos_council.tools.screening import (
    dividend_growth_streak_by_calendar_year,
    min_growth_streak_criterion_by_year,
)

FULL_RANGE = dict(start=date(1900, 1, 1), end=date(2100, 1, 1))


# --------------------------------------------------------------------------- #
# Fixture builders (EODHD div-endpoint row shape)
# --------------------------------------------------------------------------- #
def _row(d: str, value: float, *, unadjusted: float | None = None,
         period: str | None = "Final") -> dict:
    return {"date": d, "value": value,
            "unadjustedValue": value if unadjusted is None else unadjusted,
            "currency": "USD", "period": period}


def _annual_rows(start_year: int, values: list[float]) -> list[dict]:
    """One Final payment per calendar year, oldest-first."""
    return [_row(f"{start_year + i}-05-15", v) for i, v in enumerate(values)]


# --------------------------------------------------------------------------- #
# Ticker normalization (the SK Hynix trailing-dot bug)
# --------------------------------------------------------------------------- #
def test_normalize_ticker_strips_trailing_dot():
    assert normalize_ticker("000660.KS.") == "000660.KS"   # the live bug
    assert normalize_ticker("  ko.us  ") == "KO.US"         # trim + upper
    assert normalize_ticker("000660.KS. ") == "000660.KS"   # trailing dot + space
    assert normalize_ticker("NESN.SW") == "NESN.SW"         # internal dot kept
    assert normalize_ticker("aapl") == "AAPL"


# --------------------------------------------------------------------------- #
# Pure parser: adjusted value, ordering, range, robustness
# --------------------------------------------------------------------------- #
def test_parser_uses_adjusted_value_not_unadjusted():
    rows = [_row("2002-05-15", value=0.64, unadjusted=6.40)]  # Nestlé split shape
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    assert len(evs) == 1
    assert evs[0].amount == 0.64           # ADJUSTED value, never unadjustedValue


def test_parser_sorts_oldest_first_and_filters_range():
    rows = [_row("2020-05-15", 2.0), _row("2010-05-15", 1.0), _row("2030-05-15", 3.0)]
    evs = dividend_events_from_rows(
        rows, start=date(2009, 1, 1), end=date(2025, 1, 1))
    assert [e.ex_date.year for e in evs] == [2010, 2020]   # 2030 filtered, sorted


def test_parser_skips_malformed_rows():
    rows = [
        _row("2010-05-15", 1.0),
        {"date": "not-a-date", "value": 2.0},
        {"date": "2011-05-15", "value": None},
        {"date": "2012-05-15", "value": -5.0},   # non-positive -> not a real payment
        "garbage",
    ]
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    assert [e.ex_date.year for e in evs] == [2010]


# --------------------------------------------------------------------------- #
# Streak — the six required scenarios (calendar-year SUM of adjusted values)
# --------------------------------------------------------------------------- #
def test_streak_25_years_increasing_passes_20():
    rows = _annual_rows(1998, [1.0 + 0.1 * i for i in range(27)])  # 27 yrs rising
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    streak, _ = dividend_growth_streak_by_calendar_year(evs, min_years=20)
    assert streak is not None and streak >= 20
    r = min_growth_streak_criterion_by_year(evs, min_years=20)
    assert r.passed is True


def test_cadence_change_annual_to_interim_final_not_falsely_broken():
    # 2000-2019 one payment/yr; 2020-2024 split into Interim+Final whose YEARLY
    # TOTAL keeps rising. Calendar-year grouping must see the totals increase.
    rows = _annual_rows(2000, [1.0 + 0.1 * i for i in range(20)])  # 2000..2019
    year_total = 2.9
    for y in range(2020, 2025):
        year_total += 0.2
        interim, final = year_total * 0.4, year_total * 0.6
        rows.append(_row(f"{y}-03-01", round(interim, 4), period="Interim"))
        rows.append(_row(f"{y}-09-01", round(final, 4), period="Final"))
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    streak, _ = dividend_growth_streak_by_calendar_year(evs, min_years=20)
    assert streak is not None and streak >= 20    # spans the cadence change, no break

    # Contrast: the per-payment MEDIAN method DOES misread the cadence change as a
    # cut (each Interim is far below the prior single annual payment) — which is
    # exactly why EODHD uses the calendar-year method.
    from aristos_council.tools.screening import consecutive_dividend_growth_years
    per_payment, _ = consecutive_dividend_growth_years(evs)
    assert per_payment < streak


def test_split_divergence_uses_adjusted_no_false_break():
    # Adjusted `value` rises smoothly; unadjustedValue has a 10x split jump down
    # at 2002. Using the adjusted value, there is no phantom break.
    values = [1.0 + 0.1 * i for i in range(25)]       # smooth adjusted series
    rows = []
    for i, v in enumerate(values):
        year = 2000 + i
        # raw 10x higher pre-split (<=2001), normal after — divergence at 2002
        raw = v * 10 if year <= 2001 else v
        rows.append(_row(f"{year}-05-15", round(v, 4), unadjusted=round(raw, 4)))
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    streak, _ = dividend_growth_streak_by_calendar_year(evs, min_years=20)
    assert streak is not None and streak >= 20


def test_genuine_cut_resets_streak_and_fails():
    # 25 yrs of history, but a real cut 3 years before the latest year -> the
    # streak resets at the cut. History is long enough to judge, so this is a
    # genuine FAIL, not NOT-EVAL.
    values = [1.0 + 0.1 * i for i in range(25)]       # 2000..2024 rising...
    values[21] = 0.1                                   # ...except a 2021 cut
    rows = _annual_rows(2000, values)
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    streak, _ = dividend_growth_streak_by_calendar_year(evs, min_years=20)
    assert streak is not None and streak < 20          # reset by the cut
    assert min_growth_streak_criterion_by_year(evs, min_years=20).passed is False


def test_short_history_is_not_eval_not_a_fail():
    rows = _annual_rows(2015, [1.0 + 0.1 * i for i in range(10)])  # only 10 yrs
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    streak, note = dividend_growth_streak_by_calendar_year(evs, min_years=20)
    assert streak is None                              # NOT-EVAL
    assert "insufficient history" in note
    # feeds the INSUFFICIENT_EVIDENCE path: passed is None, never False
    assert min_growth_streak_criterion_by_year(evs, min_years=20).passed is None


def test_incomplete_current_year_excluded_not_read_as_cut():
    # 24 complete increasing years, then a current year with ONLY an Interim
    # payment (well below the prior full-year total). Dropping the latest year
    # means the partial year is NOT read as a cut.
    rows = _annual_rows(2000, [1.0 + 0.1 * i for i in range(24)])  # 2000..2023
    rows.append(_row("2024-03-01", 0.2, period="Interim"))         # partial 2024
    evs = dividend_events_from_rows(rows, **FULL_RANGE)
    streak, _ = dividend_growth_streak_by_calendar_year(evs, min_years=20)
    assert streak is not None and streak >= 20         # partial year did not break it
    assert min_growth_streak_criterion_by_year(evs, min_years=20).passed is True


# --------------------------------------------------------------------------- #
# Adapter: error mapping, never a silent zero
# --------------------------------------------------------------------------- #
class _FakeEODHD(EODHDAdapter):
    """EODHDAdapter with the HTTP boundary stubbed by canned rows."""

    def __init__(self, rows):
        super().__init__(api_key="test-key")
        self._rows = rows

    def _get_json(self, path):           # override the only networked method
        return self._rows


def test_adapter_parses_rows_into_events():
    rows = _annual_rows(2018, [1.0, 1.1, 1.2, 1.3])
    evs = _FakeEODHD(rows).get_dividend_history("KO.US", **FULL_RANGE)
    assert [e.ex_date.year for e in evs] == [2018, 2019, 2020, 2021]
    assert evs[0].amount == 1.0


def test_adapter_empty_array_is_data_unavailable_not_silent_zero():
    with pytest.raises(DataUnavailable):
        _FakeEODHD([]).get_dividend_history("KO.US", **FULL_RANGE)


def test_adapter_missing_key_is_data_unavailable(monkeypatch):
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    with pytest.raises(DataUnavailable, match="EODHD_API_KEY is not set"):
        EODHDAdapter(api_key=None).get_dividend_history("KO.US", **FULL_RANGE)


def test_adapter_price_history_still_deferred():
    # get_price_history raises BEFORE any network call (no key needed).
    a = EODHDAdapter(api_key="k")
    with pytest.raises(NotImplementedError):
        a.get_price_history("KO.US", start=date(2020, 1, 1), end=date(2021, 1, 1))


# --------------------------------------------------------------------------- #
# PART 1 — provider-declared streak dispatch (Option A)
# --------------------------------------------------------------------------- #
from aristos_council.data.adapter import MarketDataAdapter           # noqa: E402
from aristos_council.data.yfinance_adapter import YFinanceAdapter     # noqa: E402
from aristos_council.tools.screening import (                         # noqa: E402
    consecutive_dividend_growth_years,
    min_growth_streak_criterion,
    streak_by_method,
)


def test_adapters_declare_their_streak_shape():
    assert MarketDataAdapter.dividend_streak_method == "per_payment_median"  # default
    assert YFinanceAdapter().dividend_streak_method == "per_payment_median"
    assert EODHDAdapter(api_key="k").dividend_streak_method == "calendar_year_sum"


def test_dispatch_routes_each_method_to_its_function():
    divs = dividend_events_from_rows(
        _annual_rows(1998, [1.0 + 0.1 * i for i in range(27)]), **FULL_RANGE)
    assert (streak_by_method("per_payment_median", divs, min_years=20)
            == consecutive_dividend_growth_years(divs))
    assert (streak_by_method("calendar_year_sum", divs, min_years=20)
            == dividend_growth_streak_by_calendar_year(divs, min_years=20))


def test_dispatch_unknown_method_raises():
    with pytest.raises(ValueError, match="unknown dividend_streak_method"):
        streak_by_method("bogus", [], min_years=20)


def test_streak_note_records_the_method_used():
    divs = dividend_events_from_rows(
        _annual_rows(1998, [1.0 + 0.1 * i for i in range(27)]), **FULL_RANGE)
    r_eodhd = min_growth_streak_criterion(divs, min_years=20, method="calendar_year_sum")
    assert "calendar_year_sum" in r_eodhd.note and "EODHD shape" in r_eodhd.note
    r_yf = min_growth_streak_criterion(divs, min_years=20, method="per_payment_median")
    assert "per_payment_median" in r_yf.note and "yfinance shape" in r_yf.note


def test_eodhd_routing_does_not_false_break_on_cadence_change():
    # The PART 1.5 mirror of the PG per-payment guard: an EODHD-shaped cadence
    # change (annual -> Interim/Final) routed through calendar_year_sum must NOT
    # false-break, while per_payment_median (yfinance's method) WOULD.
    rows = _annual_rows(2000, [1.0 + 0.1 * i for i in range(20)])
    year_total = 2.9
    for y in range(2020, 2025):
        year_total += 0.2
        rows.append(_row(f"{y}-03-01", round(year_total * 0.4, 4), period="Interim"))
        rows.append(_row(f"{y}-09-01", round(year_total * 0.6, 4), period="Final"))
    divs = dividend_events_from_rows(rows, **FULL_RANGE)
    eodhd = min_growth_streak_criterion(divs, min_years=20, method="calendar_year_sum")
    yfin = min_growth_streak_criterion(divs, min_years=20, method="per_payment_median")
    assert eodhd.passed is True                 # cadence change handled
    assert yfin.observed < eodhd.observed        # per-payment misreads it as a cut


def test_evidence_threads_method_into_the_screen():
    # End to end through the registry: Evidence carries the provider's declared
    # method, the streak criterion picks it up and records it.
    from aristos_council.tools.criteria.registry import (
        CriterionSelection, Evidence, run_screen)
    rows = _annual_rows(1998, [1.0 + 0.1 * i for i in range(27)])
    divs = dividend_events_from_rows(rows, **FULL_RANGE)
    ev = Evidence(dividends=divs, streak_method="calendar_year_sum")
    res = run_screen([CriterionSelection("min_dividend_growth_streak", 20)],
                     ev, ticker="NESN.SW")
    streak_crit = res.criteria[0]
    assert streak_crit.passed is True
    assert "calendar_year_sum" in streak_crit.note


# --------------------------------------------------------------------------- #
# PART 2 — EODHD get_fundamentals (fixture, no network)
# --------------------------------------------------------------------------- #
from aristos_council.data.eodhd_adapter import fundamentals_from_payload  # noqa: E402
from aristos_council.tools.screening import min_market_cap_criterion      # noqa: E402

_EODHD_FUNDAMENTALS = {
    "General": {"Name": "Coca-Cola", "CurrencyCode": "USD"},
    "Highlights": {
        "MarketCapitalization": 2.6e11, "EarningsShare": 2.47, "PERatio": 24.3,
        "DividendShare": 1.94, "DividendYield": 0.031, "PayoutRatio": 0.74,
    },
    "Financials": {
        "Income_Statement": {
            "currency_symbol": "USD",
            # keys deliberately OUT of order -> parser must sort newest-first
            "yearly": {
                "2021-12-31": {"totalRevenue": "38655000000",
                               "operatingIncome": "10300000000", "ebit": "10300000000",
                               "incomeTaxExpense": "1500000000",
                               "incomeBeforeTax": "10000000000"},
                "2023-12-31": {"totalRevenue": "45754000000",
                               "operatingIncome": "11300000000", "ebit": "11300000000",
                               "incomeTaxExpense": "2249000000",
                               "incomeBeforeTax": "12000000000"},
                "2022-12-31": {"totalRevenue": "43004000000",
                               "operatingIncome": "10900000000", "ebit": "10900000000",
                               "incomeTaxExpense": "1700000000",
                               "incomeBeforeTax": "11600000000"},
            },
        },
        "Balance_Sheet": {"currency_symbol": "USD", "yearly": {
            "2023-12-31": {"netInvestedCapital": "40000000000"},
            "2022-12-31": {"netInvestedCapital": "38000000000"},
        }},
        "Cash_Flow": {"yearly": {
            "2023-12-31": {"freeCashFlow": "9500000000"},
            "2022-12-31": {"freeCashFlow": "9000000000"},
        }},
    },
}


def test_fundamentals_parse_scalar_fields():
    f = fundamentals_from_payload("KO.US", _EODHD_FUNDAMENTALS)
    assert f.ticker == "KO.US"
    assert f.name == "Coca-Cola"
    assert f.market_cap == 2.6e11
    assert f.eps == 2.47 and f.pe_ratio == 24.3
    assert f.dividend_yield == 0.031 and f.dividend_per_share == 1.94
    assert f.payout_ratio == 0.74
    assert f.free_cash_flow == 9.5e9            # newest year's FCF


def test_fundamentals_currency_fields_populated_no_conversion():
    f = fundamentals_from_payload("KO.US", _EODHD_FUNDAMENTALS)
    assert f.currency == "USD" and f.financial_currency == "USD"


def test_fundamentals_annual_series_newest_first():
    f = fundamentals_from_payload("KO.US", _EODHD_FUNDAMENTALS)
    # newest-first, matching the yfinance adapter ordering (CAGR/ROIC depend on it)
    assert f.total_revenue == [45754000000.0, 43004000000.0, 38655000000.0]
    assert f.operating_income == [11300000000.0, 10900000000.0, 10300000000.0]
    assert f.tax_provision == [2249000000.0, 1700000000.0, 1500000000.0]
    assert f.invested_capital == [40000000000.0, 38000000000.0]


def test_fundamentals_missing_fields_stay_none():
    f = fundamentals_from_payload("X", {"General": {}, "Highlights": {}})
    assert f.market_cap is None and f.eps is None and f.free_cash_flow is None
    assert f.total_revenue == [] and f.invested_capital == []


def test_non_usd_currency_leaves_market_cap_not_eval():
    # SK Hynix shape: KRW market cap must NOT be silently compared to a USD floor.
    payload = {"General": {"Name": "SK Hynix", "CurrencyCode": "KRW"},
               "Highlights": {"MarketCapitalization": 1.69e15}, "Financials": {}}
    f = fundamentals_from_payload("000660.KS", payload)
    assert f.currency == "KRW"
    r = min_market_cap_criterion(f, min_market_cap=1.0e10)
    assert r.passed is None                      # NOT-EVAL, never a wrong-currency pass


def test_get_fundamentals_empty_payload_is_data_unavailable():
    class _FakeFund(EODHDAdapter):
        def __init__(self):
            super().__init__(api_key="k")
        def _get_json(self, path):
            return {}
    with pytest.raises(DataUnavailable):
        _FakeFund().get_fundamentals("KO.US")


# --------------------------------------------------------------------------- #
# PART 3 — provider selection
# --------------------------------------------------------------------------- #
from aristos_council.data.provider import select_market_adapter        # noqa: E402


def test_provider_selection_eodhd(monkeypatch):
    monkeypatch.setenv("ARISTOS_MARKET_PROVIDER", "eodhd")
    assert select_market_adapter().name == "eodhd"


def test_provider_selection_default_is_yfinance(monkeypatch):
    pytest.importorskip("yfinance")
    monkeypatch.delenv("ARISTOS_MARKET_PROVIDER", raising=False)
    assert select_market_adapter().name == "yfinance"


def test_provider_selection_explicit_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("ARISTOS_MARKET_PROVIDER", "yfinance")
    assert select_market_adapter("eodhd").name == "eodhd"   # arg wins


def test_provider_selection_unknown_raises(monkeypatch):
    monkeypatch.setenv("ARISTOS_MARKET_PROVIDER", "bloomberg")
    with pytest.raises(ValueError, match="unknown ARISTOS_MARKET_PROVIDER"):
        select_market_adapter()
