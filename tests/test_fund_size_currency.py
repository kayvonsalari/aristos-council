"""DATA-HYGIENE-1 — EODHD null sentinels + fund_size base-currency normalisation.

Two live findings are pinned here:

- **EODHD serves the literal string ``"NA"`` as a null** on XETRA ETF records (observed on
  ``General::ISIN``). Every string cell is cleaned at the adapter boundary, so a sentinel
  lands as ``None`` and abstains exactly as a real null — never as the text "NA" leaking
  into a factor, a CSV cell or a report line.
- **EODHD reports an ETF's total assets in the FUND'S BASE currency** (verified live:
  IQQH.DE's 4,172,812,800 is USD while the fund lists in EUR on XETRA), so ranking
  fund_size across a mixed cohort silently compared different currencies. It is now
  normalised to EUR with a dated FX receipt, and ABSTAINS when the base currency is
  unknown — the listing currency is never borrowed as a substitute.
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
from aristos_council.data.eodhd_adapter import (
    NULL_SENTINELS,
    clean_sentinel,
    clean_str,
    fund_base_currency,
    fundamentals_from_payload,
    sentinel_fields,
)
from aristos_council.etf_static import (
    DEFAULT_STATIC_PATH,
    StaticRow,
    apply_static_fill,
    load_static,
)
from aristos_council.factors import (
    FUND_SIZE_CCY_UNKNOWN,
    FUND_SIZE_CURRENCY,
    FundSizeConversion,
    compute_factor_outcomes,
    fund_size_currency,
    gather_factor_inputs,
    normalize_fund_size,
)
from aristos_council.pipeline import format_integrity_entry

TODAY = date(2026, 7, 29)
FX_USD_EUR = 0.86           # EUR per 1 USD — pinned, never fetched in a test


# --------------------------------------------------------------------------- #
# 1. the sentinel helper (table-driven, incl. mixed case + whitespace)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", [
    "NA", "na", "Na", " NA ", "\tNA\n", "N/A", "n/a", " N/a ", "NONE", "none", "None",
    "-", " - ", "", "   ",
])
def test_sentinels_become_none(raw):
    assert clean_sentinel(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("USD", "USD"), (" USD ", "USD"), ("Ireland", "Ireland"), ("0.0463", "0.0463"),
    (" 4.63 ", "4.63"), ("NAV", "NAV"), ("NASDAQ", "NASDAQ"), ("Nano", "Nano"),
])
def test_real_strings_survive_trimmed(raw, expected):
    """A value that merely CONTAINS a sentinel's letters is not a sentinel — only the
    whole trimmed cell counts (NASDAQ / NAV must not be nulled)."""
    assert clean_sentinel(raw) == expected


@pytest.mark.parametrize("raw", [None, 0, 0.0, 4172812800, {"a": 1}, [], False])
def test_non_strings_pass_through_untouched(raw):
    assert clean_sentinel(raw) is raw or clean_sentinel(raw) == raw


def test_sentinel_set_is_the_documented_five():
    assert NULL_SENTINELS == frozenset({"NA", "N/A", "NONE", "-", ""})


def test_clean_str_handles_absent_objects_and_keys():
    assert clean_str({"ISIN": "IE00B4L5Y983"}, "ISIN") == "IE00B4L5Y983"
    assert clean_str({"ISIN": "NA"}, "ISIN") is None
    assert clean_str({}, "ISIN") is None
    assert clean_str(None, "ISIN") is None
    assert clean_str("not-a-dict", "ISIN") is None


# --------------------------------------------------------------------------- #
# 2. the live shape: General::ISIN == "NA" reads as null, not "NA"
# --------------------------------------------------------------------------- #
_XETRA_ETF = {
    # The observed XETRA ETF record shape: ISIN sentinel-filled, yield/expense absent.
    "General": {"Name": "iShares Global Clean Energy UCITS ETF", "ISIN": "NA",
                "CurrencyCode": "EUR", "Sector": "NA"},
    "Highlights": {"MarketCapitalization": "NA", "DividendYield": "NA"},
    "ETF_Data": {"TotalAssets": "4172812800", "Currency": "USD",
                 "Yield": "NA", "Ongoing_Charge": "NA", "Domicile": "Ireland"},
}


def test_isin_sentinel_reads_as_null():
    assert clean_str(_XETRA_ETF["General"], "ISIN") is None
    assert _XETRA_ETF["General"]["ISIN"] == "NA"        # the fixture is the raw provider


def test_sentinel_fields_reports_every_affected_path():
    assert sentinel_fields(_XETRA_ETF) == [
        "ETF_Data.Ongoing_Charge", "ETF_Data.Yield",
        "General.ISIN", "General.Sector",
        "Highlights.DividendYield", "Highlights.MarketCapitalization",
    ]
    assert sentinel_fields({}) == []
    assert sentinel_fields("not-a-dict") == []


def test_fundamentals_from_payload_cleans_sentinels():
    f = fundamentals_from_payload("IQQH.XETRA", _XETRA_ETF)
    assert f.sector is None                            # "NA" -> None, never "NA"
    assert f.market_cap is None                        # numeric sentinel -> None
    assert f.dividend_yield is None
    assert f.name == "iShares Global Clean Energy UCITS ETF"
    assert f.currency == "EUR"                         # listing currency, carried verbatim


def test_fundamentals_carries_fund_base_currency_not_the_listing_currency():
    f = fundamentals_from_payload("IQQH.XETRA", _XETRA_ETF)
    assert f.fund_currency == "USD"                    # from ETF_Data
    assert f.currency == "EUR"                         # the LISTING currency differs
    # and with no fund-currency field, it abstains rather than borrowing EUR:
    no_ccy = {"General": {"CurrencyCode": "EUR"}, "ETF_Data": {"TotalAssets": "1e10"}}
    assert fundamentals_from_payload("X.XETRA", no_ccy).fund_currency is None


def test_fund_base_currency_names_the_field_it_used():
    ccy, field = fund_base_currency(_XETRA_ETF)
    assert (ccy, field) == ("USD", "ETF_Data::Currency")
    assert fund_base_currency({"General": {"CurrencyCode": "EUR"}}) == (None, None)
    assert fund_base_currency("not-a-dict") == (None, None)


# --------------------------------------------------------------------------- #
# 3. normalize_fund_size — conversion, native, and the two abstentions
# --------------------------------------------------------------------------- #
def _lookup(rate):
    return lambda ccy: rate


def test_converts_to_eur_with_a_pinned_rate():
    value, receipt = normalize_fund_size(4_172_812_800.0, "USD",
                                         rate_lookup=_lookup(FX_USD_EUR),
                                         as_of="2026-07-29")
    assert value == pytest.approx(4_172_812_800.0 * FX_USD_EUR)
    assert receipt.ok and not receipt.native
    # the receipt shows its work: source amount + currency, rate, rate date.
    assert receipt.tag == "4.2bn USD @ 0.86 EUR/USD, 2026-07-29"


def test_eur_native_is_not_converted_and_says_so():
    value, receipt = normalize_fund_size(1.0e10, "eur",
                                         rate_lookup=_lookup(None), as_of="2026-07-29")
    assert value == 1.0e10                             # untouched, no FX fetched
    assert receipt.ok and receipt.native
    assert receipt.tag == "10.0bn EUR native"


def test_unknown_base_currency_abstains():
    for ccy in (None, "", "   "):
        value, receipt = normalize_fund_size(1.0e10, ccy, rate_lookup=_lookup(FX_USD_EUR),
                                             as_of="2026-07-29")
        assert value == 1.0e10                         # the raw number is not destroyed…
        assert not receipt.ok                          # …but the factor withholds it
        assert receipt.note == FUND_SIZE_CCY_UNKNOWN


@pytest.mark.parametrize("rate", [None, 0.0, -0.5])
def test_unavailable_or_absurd_rate_abstains(rate):
    _, receipt = normalize_fund_size(1.0e10, "USD", rate_lookup=_lookup(rate),
                                     as_of="2026-07-29")
    assert not receipt.ok
    assert "USD→EUR rate unavailable" in receipt.note


def test_no_fund_size_is_untouched_no_new_note():
    # HARD CONSTRAINT: a null abstains EXACTLY as before — no receipt, no note.
    assert normalize_fund_size(None, "USD", rate_lookup=_lookup(FX_USD_EUR),
                              as_of="2026-07-29") == (None, None)


def test_abstaining_receipt_renders_no_tag():
    assert FundSizeConversion(note="x").tag == ""
    assert FUND_SIZE_CURRENCY == "EUR"


# --------------------------------------------------------------------------- #
# 4. which currency a name's fund size is in (static row vs provider)
# --------------------------------------------------------------------------- #
def _etf(**kw):
    return Fundamentals(ticker="X", quote_type="ETF", **kw)


_ROW = StaticRow(ticker="X", expense_ratio=0.2, fund_size=1.0e10,
                 distribution_yield=0.0, share_class="acc", domicile="IE",
                 source="factsheet", as_of=TODAY.isoformat(), fund_size_currency="USD")
_LEGACY_ROW = StaticRow(ticker="X", expense_ratio=0.2, fund_size=1.0e10,
                        distribution_yield=0.0, share_class="acc", domicile="IE",
                        source="factsheet", as_of=TODAY.isoformat())


def test_static_filled_fund_size_uses_the_rows_currency():
    f, fill = apply_static_fill(_etf(), kind="etf", row=_ROW, today=TODAY)
    assert fill.fund_size_currency == "USD"
    assert fund_size_currency(f, fill) == "USD"


def test_vendor_served_fund_size_does_not_borrow_the_static_rows_currency():
    # The vendor already served a plausible fund size, so static did NOT fill it — its
    # currency belongs to a number that was never used.
    f, fill = apply_static_fill(_etf(total_assets=9.0e9), kind="etf", row=_ROW,
                                today=TODAY)
    assert "total_assets" not in fill.filled
    assert fill.fund_size_currency is None
    assert fund_size_currency(f, fill) is None         # -> abstain, never assume USD


def test_provider_stated_fund_currency_is_used_when_static_did_not_fill():
    f = _etf(total_assets=9.0e9, fund_currency="USD")
    assert fund_size_currency(f, None) == "USD"


# --------------------------------------------------------------------------- #
# 5. end-to-end through gather_factor_inputs (the ranked value + its receipt)
# --------------------------------------------------------------------------- #
class _Adapter(MarketDataAdapter):
    """Serves one ETF plus a PINNED FX pair close, so the EUR normalisation is
    deterministic offline. ``fx`` False makes the pair fetch fail (rate unavailable)."""

    name = "fake"

    def __init__(self, fundamentals, *, fx=True):
        self._f = fundamentals
        self._fx = fx

    def get_fundamentals(self, ticker):
        return self._f

    def get_price_history(self, ticker, *, start, end):
        if ticker.endswith("=X"):
            if not self._fx:
                return PriceHistory(ticker=ticker, bars=[])
            return PriceHistory(ticker=ticker, bars=[
                PriceBar(day=date(2026, 7, 28), open=FX_USD_EUR, high=FX_USD_EUR,
                         low=FX_USD_EUR, close=FX_USD_EUR, adj_close=FX_USD_EUR,
                         volume=0)])
        return PriceHistory(ticker=ticker, bars=[])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _fund_size_outcome(fundamentals, *, rows=None, fx=True):
    fi = gather_factor_inputs(_Adapter(fundamentals, fx=fx), "X", today=TODAY,
                              static_rows=rows if rows is not None else {})
    return compute_factor_outcomes(fi, ["fund_size"])["fund_size"]


def test_gather_ranks_the_eur_value_and_discloses_the_receipt():
    value, source = _fund_size_outcome(
        _etf(total_assets=4_172_812_800.0, fund_currency="USD"))
    assert value == pytest.approx(4_172_812_800.0 * FX_USD_EUR)
    assert source == "computed, 4.2bn USD @ 0.86 EUR/USD, 2026-07-29"


def test_gather_abstains_when_the_base_currency_is_unknown():
    value, source = _fund_size_outcome(_etf(total_assets=4_172_812_800.0))
    assert value is None                               # withheld, never ranked in mixed ccy
    assert source == f"abstained: {FUND_SIZE_CCY_UNKNOWN}"


def test_gather_abstains_when_the_fx_rate_cannot_be_fetched():
    value, source = _fund_size_outcome(
        _etf(total_assets=1.0e10, fund_currency="USD"), fx=False)
    assert value is None
    assert source.startswith("abstained: USD→EUR rate unavailable")


def test_gather_leaves_a_legacy_static_row_unnormalised_and_flagged():
    value, source = _fund_size_outcome(_etf(), rows={"X": _LEGACY_ROW})
    assert value is None
    assert source == f"abstained: {FUND_SIZE_CCY_UNKNOWN}"


def test_gather_normalises_a_static_row_that_declares_its_currency():
    value, source = _fund_size_outcome(_etf(), rows={"X": _ROW})
    assert value == pytest.approx(1.0e10 * FX_USD_EUR)
    assert source == (f"static: {TODAY.isoformat()}, factsheet, "
                     "10.0bn USD @ 0.86 EUR/USD, 2026-07-29")


def test_a_name_with_no_fund_size_is_byte_unchanged():
    value, source = _fund_size_outcome(_etf(net_expense_ratio=0.2))
    assert value is None
    assert source == "abstained"                       # the pre-fix tag, no new note


# --------------------------------------------------------------------------- #
# 6. a REASONED abstention still counts as abstained in factor integrity
# --------------------------------------------------------------------------- #
def test_reasoned_abstentions_are_counted_as_abstentions():
    entry = {"factor": "fund_size", "total": 3,
             "by_source": {"computed, 4.2bn USD @ 0.86 EUR/USD, 2026-07-29": ["A"],
                           f"abstained: {FUND_SIZE_CCY_UNKNOWN}": ["B"],
                           "abstained": ["C"]}}
    rendered = format_integrity_entry(entry)
    assert "abstained 2 (B, C)" in rendered             # ONE bucket, both names
    assert "abstained: fund base currency" not in rendered


# --------------------------------------------------------------------------- #
# 7. the static CSV: old rows accepted with an explicit flag, new rows with a currency
# --------------------------------------------------------------------------- #
_HEADER_OLD = ("ticker,expense_ratio,fund_size,distribution_yield,share_class,"
               "domicile,source,as_of")
_HEADER_NEW = _HEADER_OLD + ",fund_size_currency"


def test_loader_accepts_old_rows_and_flags_their_missing_currency(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text(f"# comment\n{_HEADER_NEW}\n"
                 "OLD,0.2,1.0e10,0.02,acc,IE,factsheet,2026-07-01\n"          # 8 cells
                 "NEW,0.2,1.0e10,0.02,acc,IE,factsheet,2026-07-01,usd\n",     # 9 cells
                 encoding="utf-8")
    rows = load_static(p)
    old, new = rows["OLD"], rows["NEW"]
    # the short (pre-column) row still parses cell-for-cell — nothing shifted…
    assert (old.fund_size, old.distribution_yield, old.as_of) == (1.0e10, 0.02, "2026-07-01")
    assert old.fund_size_currency is None
    assert old.fund_size_currency_missing is True       # the explicit flag
    # …and a new row carries the currency, normalised to upper case.
    assert new.fund_size_currency == "USD"
    assert new.fund_size_currency_missing is False


def test_loader_reads_a_file_written_without_the_column_at_all(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text(f"{_HEADER_OLD}\nOLD,0.2,1.0e10,0.02,acc,IE,factsheet,2026-07-01\n",
                 encoding="utf-8")
    row = load_static(p)["OLD"]
    assert row.fund_size == 1.0e10 and row.fund_size_currency is None
    assert row.fund_size_currency_missing is True


def test_a_row_without_a_fund_size_is_not_flagged():
    assert StaticRow(ticker="X", expense_ratio=0.2, fund_size=None,
                     distribution_yield=None, share_class=None, domicile=None,
                     source="s", as_of="2026-07-01").fund_size_currency_missing is False


def test_committed_file_declares_the_column_and_flags_its_legacy_rows():
    """The committed rows all predate the column, so each one's fund size is FLAGGED as
    un-normalisable until a human fills the currency from the factsheet. Pinned so the
    remediation is visible rather than silent."""
    rows = load_static(DEFAULT_STATIC_PATH)
    assert rows["EXMPL"].fund_size_currency == "USD"    # the format-documenting example
    flagged = sorted(t for t, r in rows.items() if r.fund_size_currency_missing)
    assert "EUNL.DE" in flagged and "VHYL.L" in flagged
    assert "EXMPL" not in flagged
    # CNDX.L carries no fund size at all (implausible AUM blanked), so it is not flagged.
    assert "CNDX.L" not in flagged
