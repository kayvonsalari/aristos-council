"""ABS-READINGS-3 — the long annual history, and what the growth record does with it.

yfinance returns FOUR annual periods, so Coca-Cola — a company with a century of
published accounts — reported a three-year compound rate. EODHD carries far more.

The fabricated responses here copy the shape PROBED from the live API for KO.US on
2026-09-21, including the thing a parser written from the documentation would get wrong:
**every statement figure is a STRING**, not a number.

  Financials::Income_Statement::yearly   41 periods, 1985-12-31 .. 2025-12-31,
                                         keyed by period-end date, 34 fields each
                                         totalRevenue '47941000000.00'
                                         netIncome    '13107000000.00'
                                         NO diluted EPS, NO share count
  outstandingShares::annual              42 entries keyed "0","1",…, 1985..2026,
                                         {"dateFormatted": "2025-12-31",
                                          "shares": 4313000000}

No network: every test builds the payload itself.
"""
from __future__ import annotations

import json
import urllib.parse

import pytest

from aristos_council.abs_readings import growth_record
from aristos_council.cohorts.symbols import SymbolError, eodhd_symbol
from aristos_council.data.adapter import Fundamentals
from aristos_council.growth_history import (GROWTH_FILTER, INCOME_KEY, SHARES_KEY,
                                            GrowthHistory, fetch_growth_history,
                                            parse_growth_history)


def _payload(n_years: int = 41, *, first_year: int = 1985, shares: bool = True,
             revenue0: float = 47_941_000_000.0) -> dict:
    """The probed shape, with figures as STRINGS exactly as EODHD sends them."""
    income, out_shares = {}, {}
    for i in range(n_years):
        year = first_year + i
        # revenue grows 4% a year towards `revenue0` in the newest period
        value = revenue0 / (1.04 ** (n_years - 1 - i))
        income[f"{year}-12-31"] = {
            "date": f"{year}-12-31",
            "totalRevenue": f"{value:.2f}",
            "netIncome": f"{value * 0.25:.2f}",
            "ebit": f"{value * 0.3:.2f}",
        }
        if shares:
            out_shares[str(i)] = {"date": str(year),
                                  "dateFormatted": f"{year}-12-31",
                                  "shares": 4_313_000_000}
    doc = {INCOME_KEY: income}
    if shares:
        doc[SHARES_KEY] = out_shares
    return doc


# =========================================================================== #
# 1. the parser
# =========================================================================== #
def test_the_history_comes_back_newest_first_with_its_years():
    h = parse_growth_history(_payload())
    assert h.available
    assert h.years[0] == "FY2025" and h.years[-1] == "FY1985"
    assert len(h.years) == 41


def test_string_figures_are_coerced():
    """Every statement value EODHD sends is a string. A parser that trusted the type
    would produce nothing at all."""
    h = parse_growth_history(_payload(n_years=3))
    assert all(isinstance(v, float) for v in h.revenue)
    assert h.revenue[0] == pytest.approx(47_941_000_000.0)


def test_eps_is_derived_from_net_income_and_the_annual_share_count():
    """The income-statement block has NO diluted-EPS field and NO share count; the share
    counts come from a second block and are matched BY FISCAL YEAR."""
    h = parse_growth_history(_payload(n_years=2))
    assert h.eps[0] == pytest.approx(47_941_000_000.0 * 0.25 / 4_313_000_000)


def test_without_share_counts_the_revenue_still_parses_and_eps_abstains():
    h = parse_growth_history(_payload(n_years=5, shares=False))
    assert all(v is not None for v in h.revenue)
    assert all(v is None for v in h.eps)


def test_the_tag_names_the_source_and_the_number_of_reports():
    assert parse_growth_history(_payload(n_years=41)).tag() == (
        "source: EODHD, 41 annual reports")
    assert parse_growth_history(_payload(n_years=1)).tag() == (
        "source: EODHD, 1 annual report")


def test_an_empty_or_malformed_payload_is_an_empty_history_not_a_crash():
    for doc in ({}, {INCOME_KEY: {}}, {INCOME_KEY: "nonsense"}, {"other": 1}):
        assert not parse_growth_history(doc).available


# =========================================================================== #
# 2. the fetch: cached, keyless, and never raising
# =========================================================================== #
def test_no_key_means_no_history_and_no_call(tmp_path, monkeypatch):
    monkeypatch.delenv("EODHD_API_KEY", raising=False)

    def _boom(*a, **k):                            # pragma: no cover - must not run
        raise AssertionError("a call was made without a key")

    assert not fetch_growth_history("KO", cache_dir=tmp_path, opener=_boom).available


def test_a_refused_call_falls_back_quietly(tmp_path):
    def _refuse(*a, **k):
        raise OSError("402 Payment Required")

    out = fetch_growth_history("KO", api_key="k", cache_dir=tmp_path, opener=_refuse)
    assert not out.available           # the growth record falls back to yfinance


def test_the_answer_is_cached_for_the_day_and_the_second_call_makes_no_request(tmp_path):
    from datetime import date

    calls = []

    class _Resp:
        def __init__(self, body):
            self._body = body
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _once(url, timeout=None):
        calls.append(url)
        return _Resp(json.dumps(_payload(n_years=6)).encode())

    day = date(2026, 9, 21)
    first = fetch_growth_history("KO", api_key="k", cache_dir=tmp_path, today=day,
                                 opener=_once)
    second = fetch_growth_history("KO", api_key="k", cache_dir=tmp_path, today=day,
                                  opener=_once)
    assert first.available and second.available
    assert len(calls) == 1, "the second call should have been served from the day cache"
    query = urllib.parse.parse_qs(urllib.parse.urlparse(calls[0]).query)
    assert query["filter"] == [GROWTH_FILTER]   # both blocks, one call
    assert "KO.US" in calls[0]                  # the EODHD spelling, not the Yahoo one
    assert (tmp_path / "eodhd_KO.US_2026_09_21_growth.json").exists()


def test_an_untranslatable_symbol_does_not_reach_the_provider(tmp_path):
    def _boom(*a, **k):                            # pragma: no cover
        raise AssertionError("called with a symbol we cannot spell")

    assert not fetch_growth_history("WHAT.ZZZ", api_key="k", cache_dir=tmp_path,
                                    opener=_boom).available


@pytest.mark.parametrize("yahoo,eodhd", [("KO", "KO.US"), ("NFLX", "NFLX.US"),
                                         ("SHEL.L", "SHEL.LSE"), ("SAP.DE", "SAP.XETRA"),
                                         ("7203.T", "7203.T")])
def test_the_symbol_is_translated_for_eodhd(yahoo, eodhd):
    assert eodhd_symbol(yahoo) == eodhd


def test_an_unknown_suffix_raises_rather_than_guessing():
    with pytest.raises(SymbolError):
        eodhd_symbol("WHAT.ZZZ")


# =========================================================================== #
# 3. the growth record prefers it, and says which it used
# =========================================================================== #
def _four_period_fundamentals():
    return Fundamentals(ticker="KO", aligned_annual={
        "total_revenue": [47e9, 45.8e9, 43e9, 38.7e9],
        "diluted_eps": [2.5, 2.4, 2.2, 1.9]})


def test_eodhd_history_is_PREFERRED_over_the_four_yfinance_periods():
    """The defect: Coca-Cola reporting a three-year compound rate."""
    out = growth_record(_four_period_fundamentals(), parse_growth_history(_payload()))
    assert out.revenue.cagr[5].span == 5
    assert out.revenue.cagr[10].span == 10
    assert "grew in 10 of the 10 years reported" in out.revenue.grew_in.label
    assert out.source_tag == "source: EODHD, 41 annual reports"


def test_yfinance_is_the_FALLBACK_and_is_tagged_as_such():
    out = growth_record(_four_period_fundamentals())
    assert out.source_tag == "source: yfinance, 4 annual reports"
    assert out.revenue.cagr[5].span == 3          # four periods -> a three-year span


def test_an_empty_history_falls_back_rather_than_reporting_nothing():
    out = growth_record(_four_period_fundamentals(), GrowthHistory())
    assert out.source_tag.startswith("source: yfinance")
    assert out.revenue.cagr[5].available


def test_the_source_tag_is_the_LAST_line_on_the_page():
    lines = growth_record(_four_period_fundamentals(),
                          parse_growth_history(_payload())).lines()
    assert lines[-1] == "source: EODHD, 41 annual reports"
    assert sum(1 for l in lines if l.startswith("source:")) == 1
