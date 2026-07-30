"""DATA-HYGIENE-1 item 1 — EODHD "NA" null-sentinel cleaning at the adapter boundary.

EODHD writes a literal placeholder STRING where a field has no value instead of JSON null
(confirmed live on ``General::ISIN`` for XETRA ETF records; suspected on the fund yield /
expense fields — SPYD.DE distribution_yield and EUDF.DE expense_ratio both abstained). A
placeholder is an ABSENCE, so ONE helper maps that family to None BEFORE any parsing, and
it is applied uniformly to every string field and inside the numeric coercion.

What is pinned here:
- the helper's table: which strings are sentinels (case-insensitive, trimmed) and which are
  NOT (``"0"`` is a value, not an absence),
- non-strings pass through untouched (so the helper is safe to apply everywhere),
- the ISIN field comes back None, never the string "NA", from a recorded-shape fixture,
- a sentinel in a numeric or currency field abstains — it never becomes a phantom 0.
"""

from __future__ import annotations

from datetime import date

import pytest

from aristos_council.data.eodhd_adapter import (
    clean_sentinel,
    dividend_events_from_rows,
    fundamentals_from_payload,
)


# --------------------------------------------------------------------------- #
# the helper's table
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", [
    "NA", "na", "Na", " NA ", "\tNA\n",          # the observed live sentinel, any case
    "N/A", "n/a", " N/a ",
    "NONE", "None", "none", " none ",
    "-", " - ",
    "", "   ",                                    # empty / whitespace-only
])
def test_sentinels_become_none(raw):
    assert clean_sentinel(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("USD", "USD"),
    (" USD ", "USD"),                             # a real value comes back trimmed
    ("IE00B4L5Y983", "IE00B4L5Y983"),
    ("0", "0"),                                   # a ZERO is a value, never an absence
    ("0.0000", "0.0000"),
    ("Nasdaq", "Nasdaq"),                         # merely starts like "NA" — kept
    ("NAV", "NAV"),
    ("--", "--"),                                 # only a single dash is the sentinel
])
def test_values_pass_through(raw, expected):
    assert clean_sentinel(raw) == expected


@pytest.mark.parametrize("raw", [None, 0, 0.0, 1.5, -3, False, True])
def test_non_strings_pass_through_unchanged(raw):
    assert clean_sentinel(raw) is raw


def test_containers_pass_through_unchanged():
    payload = {"a": 1}
    rows = ["NA"]
    assert clean_sentinel(payload) is payload      # never recurses into a container
    assert clean_sentinel(rows) is rows


# --------------------------------------------------------------------------- #
# the fixture: a XETRA ETF-shaped payload with "NA" in the observed places
# --------------------------------------------------------------------------- #
def _payload(**general) -> dict:
    base = {"Code": "SPYD", "Name": "SPDR S&P Global Dividend Aristocrats",
            "CurrencyCode": "EUR", "Sector": "Financial Services", "ISIN": "NA"}
    base.update(general)
    return {"General": base,
            "Highlights": {"MarketCapitalization": "NA", "DividendYield": "NA",
                           "PayoutRatio": "NA", "EarningsShare": "NA", "PERatio": "NA"},
            "Financials": {"Income_Statement": {"currency_symbol": "NA", "yearly": {}}}}


def test_isin_sentinel_returns_none_not_the_string():
    f = fundamentals_from_payload("SPYD.XETRA", _payload())
    assert f.isin is None                          # the live-observed case
    assert f.isin != "NA"


def test_isin_real_value_is_carried():
    f = fundamentals_from_payload("EUNL.XETRA", _payload(ISIN="IE00B4L5Y983"))
    assert f.isin == "IE00B4L5Y983"


def test_numeric_sentinels_abstain_and_are_never_zero():
    f = fundamentals_from_payload("SPYD.XETRA", _payload())
    for value in (f.market_cap, f.dividend_yield, f.payout_ratio, f.eps, f.pe_ratio):
        assert value is None                       # NOT-EVAL, never a phantom 0


def test_string_sentinels_abstain_in_string_fields():
    f = fundamentals_from_payload("X.XETRA", _payload(Name="NA", Sector="N/A",
                                                     CurrencyCode="NA"))
    assert f.name is None
    assert f.sector is None
    assert f.currency is None
    assert f.financial_currency is None            # sentinel + sentinel fallback


def test_currency_falls_back_past_a_sentinel_statement_currency():
    # Income_Statement::currency_symbol is "NA" -> fall back to the listing currency.
    f = fundamentals_from_payload("EUNL.XETRA", _payload())
    assert f.currency == "EUR"
    assert f.financial_currency == "EUR"


def test_dividend_rows_with_sentinels_are_skipped_not_zeroed():
    rows = [{"date": "2026-03-05", "value": "NA"},        # sentinel amount -> skipped
            {"date": "NA", "value": "0.55"},             # sentinel date -> skipped
            {"date": "2026-06-05", "value": "0.61"}]      # the one real payment
    events = dividend_events_from_rows(rows, start=date(2026, 1, 1), end=date(2026, 12, 31))
    assert [(e.ex_date, e.amount) for e in events] == [(date(2026, 6, 5), 0.61)]
