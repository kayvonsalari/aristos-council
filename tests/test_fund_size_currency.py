"""DATA-HYGIENE-1 item 2/3 — fund_size base-currency normalisation.

EODHD reports an ETF's total assets in the FUND'S BASE currency, not the listing's (verified
live: IQQH's 4,172,812,800 == Citywire's "4.2bn USD" while the German factsheets quote EUR),
so a cross-fund ``fund_size`` ranking was comparing USD against EUR amounts. These pin the
four outcomes and the static-CSV schema they rest on:

- **converted** — a known base currency + a dated FX rate -> the value is EUR and its
  provenance receipt names the SOURCE currency, the rate and the rate's date.
- **already EUR** — nothing to convert; the tag is unchanged from before this change.
- **rate unavailable** — currency known, rate missing -> the value is WITHHELD and the
  factor ABSTAINS (never a mixed-currency number in a ranking).
- **currency unknown** — served UNCONVERTED and FLAGGED (the static rows committed before
  the currency column), never silently reinterpreted as EUR.

Abstention semantics are unchanged throughout: a missing fund_size is still simply missing.
"""

from __future__ import annotations

from datetime import date

import pytest

from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.etf_static import StaticRow, apply_static_fill, load_static
from aristos_council.factors import compute_factor_outcomes, gather_factor_inputs
from aristos_council.fund_currency import (
    FUND_SIZE_CCY,
    FX_UNAVAILABLE_NOTE,
    UNVERIFIED_CCY_NOTE,
    compact_amount,
    convert_fund_size,
    needs_conversion,
    normalize_currency_code,
)

TODAY = date(2026, 7, 29)
IQQH_USD = 4_172_812_800.0          # the live-verified USD figure from the issue
USD_EUR = 0.86                      # the pinned FX fixture rate (EUR per 1 USD)
SOURCE = "EODHD fundamentals API"


def _row(**kw) -> StaticRow:
    """A FRESH static row for an ETF (as_of == today, so staleness never interferes)."""
    base = dict(ticker="IQQH.DE", expense_ratio=0.65, fund_size=IQQH_USD,
                distribution_yield=0.005, share_class="dist", domicile="IE",
                source=SOURCE, as_of=TODAY.isoformat(), fund_size_currency="USD")
    base.update(kw)
    return StaticRow(**base)


# --------------------------------------------------------------------------- #
# the pure layer
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    ("usd", "USD"), (" USD ", "USD"), ("EUR", "EUR"),
    ("NA", None), ("$", None), ("EURO", None), ("US", None), ("", None),
    (None, None), (12, None),
])
def test_normalize_currency_code(raw, expected):
    assert normalize_currency_code(raw) == expected


@pytest.mark.parametrize("value,expected", [
    (IQQH_USD, "4.17bn"), (1.5e12, "1.5tn"), (2.5e6, "2.5m"), (12345.0, "12345"),
])
def test_compact_amount(value, expected):
    assert compact_amount(value) == expected


def test_conversion_value_and_receipt():
    conv = convert_fund_size(IQQH_USD, "usd", USD_EUR, "2026-07-29")
    assert conv is not None
    assert conv.from_ccy == "USD" and conv.to_ccy == FUND_SIZE_CCY
    assert conv.value == pytest.approx(IQQH_USD * USD_EUR)
    # the receipt carries SOURCE currency + rate + rate date, so it can be re-checked.
    assert conv.tag == "4.17bn USD @ 0.86 EUR/USD, 2026-07-29"


@pytest.mark.parametrize("value,ccy,rate", [
    (None, "USD", USD_EUR),          # nothing to convert
    (IQQH_USD, None, USD_EUR),       # unknown currency -> never guessed
    (IQQH_USD, "NA", USD_EUR),       # sentinel currency is an absence
    (IQQH_USD, "USD", None),         # no rate -> the caller abstains
    (IQQH_USD, "USD", 0.0),          # nonsense rate
    (IQQH_USD, "USD", -1.0),
])
def test_conversion_refuses_rather_than_guesses(value, ccy, rate):
    assert convert_fund_size(value, ccy, rate, "2026-07-29") is None


def test_needs_conversion_only_for_a_known_non_eur_currency():
    assert needs_conversion("USD") is True
    assert needs_conversion("eur") is False        # already normalised
    assert needs_conversion(None) is False         # unknown -> flagged, not converted
    assert needs_conversion("NA") is False


# --------------------------------------------------------------------------- #
# the static layer carries the currency through the fill
# --------------------------------------------------------------------------- #
def _etf(**kw) -> Fundamentals:
    return Fundamentals(ticker="IQQH.DE", quote_type="ETF", **kw)


def test_fill_reports_the_currency_of_a_fund_size_it_served():
    _, fill = apply_static_fill(_etf(), kind="etf", row=_row(), today=TODAY)
    assert fill.fund_size_currency == "USD"


def test_fill_reports_no_currency_when_the_vendor_served_the_size():
    # vendor precedence: static did not serve fund_size, so it names no currency for it.
    _, fill = apply_static_fill(_etf(total_assets=9.9e9), kind="etf", row=_row(),
                                today=TODAY)
    assert "total_assets" not in fill.filled
    assert fill.fund_size_currency is None


def test_fill_reports_no_currency_for_a_pre_column_row():
    _, fill = apply_static_fill(_etf(), kind="etf", row=_row(fund_size_currency=None),
                                today=TODAY)
    assert "total_assets" in fill.filled           # the value is still served...
    assert fill.fund_size_currency is None         # ...with no currency -> flagged later


# --------------------------------------------------------------------------- #
# the fetch edge: gather_factor_inputs converts / withholds / flags
# --------------------------------------------------------------------------- #
class _FxAdapter(MarketDataAdapter):
    """A fake adapter serving one fundamentals shell plus pinned FX pair closes. The FX
    rate travels the SAME get_price_history path the production code uses (``USDEUR=X``),
    so no network and no live rate is involved."""

    name = "fake"

    def __init__(self, fundamentals, rates: dict | None = None):
        self._f = fundamentals
        self._rates = rates or {}

    def get_fundamentals(self, ticker):
        return self._f

    def get_price_history(self, ticker, *, start, end):
        rate = self._rates.get(ticker)
        if rate is None:
            return PriceHistory(ticker=ticker, bars=[])
        bar = PriceBar(day=end, open=rate, high=rate, low=rate, close=rate,
                       adj_close=rate, volume=0)
        return PriceHistory(ticker=ticker, bars=[bar])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _gather(row, rates=None, fundamentals=None):
    adapter = _FxAdapter(fundamentals if fundamentals is not None else _etf(), rates)
    rows = {"IQQH.DE": row} if row is not None else {}
    return gather_factor_inputs(adapter, "IQQH.DE", today=TODAY, static_rows=rows)


def _fund_size_outcome(fi):
    return compute_factor_outcomes(fi, ["fund_size"])["fund_size"]


def test_usd_fund_size_is_converted_to_eur_with_a_dated_receipt():
    fi = _gather(_row(), rates={"USDEUR=X": USD_EUR})
    assert fi.fundamentals.total_assets == pytest.approx(IQQH_USD * USD_EUR)
    value, source = _fund_size_outcome(fi)
    assert value == pytest.approx(IQQH_USD * USD_EUR)
    # the static receipt still LEADS the tag (the narrator's static-evidence ledger and the
    # report's provenance badge both match on the "static:" prefix)...
    assert source.startswith(f"static: {TODAY.isoformat()}, {SOURCE}")
    # ...and the conversion rides after it with source currency + rate + date.
    assert "4.17bn USD @ 0.86 EUR/USD, 2026-07-29" in source


def test_eur_fund_size_is_not_converted_and_its_tag_is_unchanged():
    fi = _gather(_row(fund_size=3.0e9, fund_size_currency="EUR"),
                 rates={"USDEUR=X": USD_EUR})
    assert fi.fundamentals.total_assets == 3.0e9
    assert fi.fund_size_fx is None and fi.fund_size_fx_failed is False
    assert fi.fund_size_currency_unverified is False
    assert _fund_size_outcome(fi)[1] == f"static: {TODAY.isoformat()}, {SOURCE}"


def test_unavailable_fx_rate_abstains_instead_of_mixing_currencies():
    fi = _gather(_row(), rates={})                 # currency known, no rate served
    assert fi.fundamentals.total_assets is None    # WITHHELD
    assert fi.fund_size_fx_failed is True
    value, source = _fund_size_outcome(fi)
    assert value is None                           # the factor abstains, as today
    assert source == FX_UNAVAILABLE_NOTE


def test_missing_base_currency_serves_unconverted_and_flags_it():
    # a static row written before the currency column: unchanged value, explicit flag —
    # never relabelled EUR.
    fi = _gather(_row(fund_size_currency=None), rates={"USDEUR=X": USD_EUR})
    assert fi.fundamentals.total_assets == IQQH_USD
    assert fi.fund_size_currency_unverified is True
    value, source = _fund_size_outcome(fi)
    assert value == IQQH_USD
    assert source.startswith(f"static: {TODAY.isoformat()}, {SOURCE}")
    assert UNVERIFIED_CCY_NOTE in source


def test_vendor_served_fund_size_is_flagged_currency_unverified():
    # no static row at all: the vendor's total_assets has no known base currency.
    fi = _gather(None, fundamentals=_etf(total_assets=9.9e9))
    assert fi.fundamentals.total_assets == 9.9e9   # behaviour unchanged
    assert fi.fund_size_currency_unverified is True
    assert UNVERIFIED_CCY_NOTE in _fund_size_outcome(fi)[1]


def test_absent_fund_size_stays_absent_and_flags_nothing():
    # the hard constraint: abstention semantics are untouched by this layer.
    fi = _gather(_row(fund_size=None))
    assert fi.fundamentals.total_assets is None
    assert fi.fund_size_fx is None
    assert fi.fund_size_fx_failed is False
    assert fi.fund_size_currency_unverified is False
    value, source = _fund_size_outcome(fi)
    assert value is None and source == "abstained"


# --------------------------------------------------------------------------- #
# the static CSV schema: old rows load flagged, new rows load with a currency
# --------------------------------------------------------------------------- #
_HEADER = ("ticker,expense_ratio,fund_size,distribution_yield,share_class,domicile,"
           "source,as_of,fund_size_currency")


def test_loader_accepts_pre_column_rows_and_new_rows(tmp_path):
    path = tmp_path / "etf_static.csv"
    path.write_text(
        "# a comment line is skipped\n"
        + _HEADER + "\n"
        # a row written BEFORE the currency column existed: 8 cells under a 9-column
        # header. csv maps by POSITION, so appending the column cannot shift its values.
        + f"OLD.DE,0.2,101700000000,0,acc,IE,{SOURCE},2026-07-21\n"
        + f"NEW.DE,0.65,4172812800,0.005,dist,IE,{SOURCE},2026-07-29,usd\n",
        encoding="utf-8")
    rows = load_static(path)
    old = rows["OLD.DE"]
    assert old.fund_size == 1.017e11 and old.distribution_yield == 0.0
    assert old.domicile == "IE" and old.as_of == "2026-07-21"   # nothing shifted
    assert old.fund_size_currency is None                       # -> flagged downstream
    new = rows["NEW.DE"]
    assert new.fund_size == 4_172_812_800.0
    assert new.fund_size_currency == "USD"                      # normalised code


def test_loader_ignores_an_unusable_currency_cell(tmp_path):
    path = tmp_path / "etf_static.csv"
    path.write_text(_HEADER + "\n"
                    + f"X.DE,0.2,1e10,0,acc,IE,{SOURCE},2026-07-29,NA\n", encoding="utf-8")
    # "NA" is a sentinel, not a currency -> unknown, so the row is flagged, not converted.
    assert load_static(path)["X.DE"].fund_size_currency is None


# --------------------------------------------------------------------------- #
# a fund size is never labelled with the LISTING currency
# --------------------------------------------------------------------------- #
def test_fund_size_is_not_labelled_with_the_listing_currency():
    from aristos_council.agents import nodes
    from aristos_council.presentation import format_factor_value

    # The narrator's money label comes from the instrument's LISTING currency, which is not
    # the currency a fund's assets are reported in (and the ranker normalises the served
    # value to EUR), so fund_size / total_assets are no longer labelled from it.
    assert "fund_size" not in nodes._CURRENCY_DISPLAY_FIELDS
    assert "total_assets" not in nodes._CURRENCY_DISPLAY_FIELDS
    assert "market_cap" in nodes._CURRENCY_DISPLAY_FIELDS      # unchanged: price currency
    # unlabelled, never a fabricated unit (the receipt states the currency facts).
    assert format_factor_value("fund_size", 2e10) == "20.0bn"


def test_committed_csv_has_the_currency_column_and_flags_legacy_rows():
    rows = load_static()
    # the format-documenting example row shows the new column filled...
    assert rows["EXMPL"].fund_size_currency == "USD"
    # ...while the human-verified rows committed before it are untouched and unlabelled,
    # so they are FLAGGED (never reinterpreted). Pre-fix snapshots therefore still mix
    # currencies — see the PR's DOCS PROPOSED note.
    assert rows["EUNL.DE"].fund_size == 1.017e11
    assert rows["EUNL.DE"].fund_size_currency is None
