"""ETFCORE-2 ITEM 2 — the static-row generator's pure row-building logic.

These pin the accumulated sanity guards against mocked EODHD payloads; NO live network is
touched (only ``build_static_row`` / ``format_row`` are exercised, never ``fetch_payload``).
The script lives under ``scripts/`` (not on the src import path), so it is loaded by file
path.
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_etf_static_rows.py"
_spec = importlib.util.spec_from_file_location("generate_etf_static_rows", SCRIPT)
gen = importlib.util.module_from_spec(_spec)
# Register before exec so the @dataclass decorator can resolve the module via
# sys.modules during class creation (dataclasses._is_type calls
# sys.modules.get(cls.__module__)); an unregistered module -> AttributeError.
sys.modules[_spec.name] = gen
_spec.loader.exec_module(gen)

AS_OF = date(2026, 7, 21).isoformat()


def _payload(**etf_data):
    """An EODHD /fundamentals payload carrying only the ETF_Data fields under test.

    ``Currency`` defaults to USD so the fund-base-currency guard (DATA-HYGIENE-1) is
    satisfied and the OTHER guards can be pinned in isolation; the currency guard has its
    own cases below, which pass ``Currency=None`` to remove it."""
    etf_data = {"Currency": "USD", **etf_data}
    etf_data = {k: v for k, v in etf_data.items() if v is not None}
    return {"General": {"Name": "Test Fund"}, "ETF_Data": dict(etf_data)}


# --------------------------------------------------------------------------- #
# a well-formed payload -> a correct row
# --------------------------------------------------------------------------- #
def test_wellformed_payload_produces_correct_row():
    draft = gen.build_static_row(
        "vhyl.l",
        _payload(Ongoing_Charge="0.2900", TotalAssets="7680000000.00",
                 Yield="4.6300", Domicile="Ireland"),
        as_of=AS_OF)
    assert draft.ticker == "VHYL.L"                 # normalized (upper-cased)
    assert draft.expense_ratio == 0.29             # from Ongoing_Charge, kept as percent
    assert draft.fund_size == 7680000000.0
    assert draft.distribution_yield == 0.0463      # percent -> fraction
    assert draft.share_class == "dist"             # positive yield
    assert draft.domicile == "IE"                  # country name -> code
    assert draft.fund_size_currency == "USD"       # fund BASE currency, from ETF_Data
    assert draft.notes == []                       # nothing blanked
    assert draft.source == gen.SOURCE_BASE         # no note appended


def test_format_row_matches_csv_column_order():
    draft = gen.build_static_row(
        "vhyl.l",
        _payload(Ongoing_Charge="0.29", TotalAssets="7680000000",
                 Yield="4.63", Domicile="Ireland"),
        as_of=AS_OF)
    line = gen.format_row(draft)
    # fund_size_currency is the LAST cell (appended, never inserted — old rows must keep
    # parsing cell-for-cell).
    assert line == (f"VHYL.L,0.29,7680000000,0.0463,dist,IE,{gen.SOURCE_BASE},{AS_OF},USD")
    assert gen.COLUMNS[-1] == "fund_size_currency"


# --------------------------------------------------------------------------- #
# fee fake-zero: Ongoing_Charge only; a fake zero is skipped with a note
# --------------------------------------------------------------------------- #
def test_fee_uses_ongoing_charge_and_ignores_net_expense_ratio():
    # NetExpenseRatio is the notorious fake zero — the fee must come from Ongoing_Charge.
    draft = gen.build_static_row(
        "x.de",
        _payload(NetExpenseRatio="0.0000", Ongoing_Charge="0.20"),
        as_of=AS_OF)
    assert draft.expense_ratio == 0.20
    assert draft.notes == []


def test_fake_zero_fee_is_skipped_with_a_note():
    # Ongoing_Charge itself absent/zero -> fee blanked (never a phantom 0%) + a note.
    draft = gen.build_static_row(
        "x.de",
        _payload(NetExpenseRatio="0.0000", Ongoing_Charge="0.0000"),
        as_of=AS_OF)
    assert draft.expense_ratio is None
    assert any("fake-zero" in n for n in draft.notes)
    assert "fake-zero" in draft.source
    assert gen.format_row(draft).startswith("X.DE,,")   # empty expense_ratio cell


# --------------------------------------------------------------------------- #
# implausible fund size blanked with a note (the CNDX 270B lesson)
# --------------------------------------------------------------------------- #
def test_implausible_fund_size_is_blanked_with_a_note():
    draft = gen.build_static_row(
        "cndx.l",
        _payload(Ongoing_Charge="0.30", TotalAssets="270000000000000",  # 2.7e14
                 Yield="0"),
        as_of=AS_OF)
    assert draft.fund_size is None
    assert any("implausible" in n for n in draft.notes)
    assert "implausible" in draft.source


def test_plausible_boundary_fund_sizes_are_kept():
    lo = gen.build_static_row("a.de", _payload(Ongoing_Charge="0.1", TotalAssets="1e7"),
                              as_of=AS_OF)
    hi = gen.build_static_row("b.de", _payload(Ongoing_Charge="0.1", TotalAssets="1.5e12"),
                              as_of=AS_OF)
    assert lo.fund_size == 1e7 and hi.fund_size == 1.5e12
    assert lo.notes == [] and hi.notes == []
    too_small = gen.build_static_row(
        "c.de", _payload(Ongoing_Charge="0.1", TotalAssets="9e6"), as_of=AS_OF)
    assert too_small.fund_size is None
    assert any("implausible" in n for n in too_small.notes)


# --------------------------------------------------------------------------- #
# yield percent -> fraction, and dist/acc inference
# --------------------------------------------------------------------------- #
def test_percent_yield_converts_to_fraction():
    draft = gen.build_static_row("x.l", _payload(Ongoing_Charge="0.3", Yield="9.23"),
                                 as_of=AS_OF)
    assert draft.distribution_yield == 0.0923
    assert draft.share_class == "dist"


def test_true_zero_yield_infers_acc():
    draft = gen.build_static_row("x.l", _payload(Ongoing_Charge="0.15", Yield="0"),
                                 as_of=AS_OF)
    assert draft.distribution_yield == 0.0
    assert draft.share_class == "acc"


def test_missing_yield_leaves_share_class_and_yield_blank():
    draft = gen.build_static_row("x.l", _payload(Ongoing_Charge="0.15"), as_of=AS_OF)
    assert draft.distribution_yield is None
    assert draft.share_class is None
    assert gen.format_row(draft).split(",")[3:5] == ["", ""]   # yield + share_class blank


# --------------------------------------------------------------------------- #
# domicile mapping
# --------------------------------------------------------------------------- #
def test_domicile_maps_known_names_and_passes_unknown_through():
    known = gen.build_static_row("x.as", _payload(Ongoing_Charge="0.3",
                                                  Domicile="Netherlands"), as_of=AS_OF)
    assert known.domicile == "NL"
    unknown = gen.build_static_row("x.de", _payload(Ongoing_Charge="0.3",
                                                    Domicile="Narnia"), as_of=AS_OF)
    assert unknown.domicile == "Narnia"              # omit-don't-invent: surfaced raw
    absent = gen.build_static_row("x.de", _payload(Ongoing_Charge="0.3"), as_of=AS_OF)
    assert absent.domicile is None


# --------------------------------------------------------------------------- #
# EODHD exchange-suffix translation (query only — the emitted row keeps .DE)
# --------------------------------------------------------------------------- #
def test_eodhd_query_symbol_translates_xetra_suffix():
    # EODHD's API 404s on .DE (confirmed live) and only recognizes .XETRA.
    assert gen.eodhd_query_symbol("vwce.de") == "VWCE.XETRA"
    assert gen.eodhd_query_symbol("SXR8.DE") == "SXR8.XETRA"


def test_eodhd_query_symbol_leaves_other_suffixes_unchanged():
    assert gen.eodhd_query_symbol("vhyl.l") == "VHYL.L"
    assert gen.eodhd_query_symbol("vusa.as") == "VUSA.AS"


def test_eodhd_query_symbol_handles_no_suffix():
    assert gen.eodhd_query_symbol("smh") == "SMH"


# --------------------------------------------------------------------------- #
# a payload missing ETF_Data entirely degrades gracefully (all guards fire, no crash)
# --------------------------------------------------------------------------- #
def test_empty_payload_produces_a_row_of_blanks_with_notes():
    draft = gen.build_static_row("x.de", {}, as_of=AS_OF)
    assert draft.expense_ratio is None
    assert draft.fund_size is None
    assert draft.distribution_yield is None
    assert draft.share_class is None
    assert draft.fund_size_currency is None
    assert any("fake-zero" in n for n in draft.notes)   # fee note fires
    # no fund size -> no currency note either (nothing to normalise, nothing to flag)
    assert not any("base currency" in n for n in draft.notes)
    line = gen.format_row(draft)
    assert line == f"X.DE,,,,,,{draft.source},{AS_OF},"


# --------------------------------------------------------------------------- #
# fund base currency + null sentinels (DATA-HYGIENE-1)
# --------------------------------------------------------------------------- #
def test_fund_base_currency_read_from_etf_data():
    draft = gen.build_static_row(
        "iqqh.de", _payload(Ongoing_Charge="0.65", TotalAssets="4172812800",
                            Currency="usd", Yield="0"),
        as_of=AS_OF)
    assert draft.fund_size_currency == "USD"           # upper-cased
    assert draft.notes == []


def test_missing_fund_base_currency_blanks_the_cell_with_a_note():
    # No fund-currency field: the cell stays BLANK and the row says why. The listing
    # currency is NOT borrowed (that is the IQQH mislabelling this fix prevents).
    payload = {"General": {"Name": "F", "CurrencyCode": "EUR"},
               "ETF_Data": {"Ongoing_Charge": "0.20", "TotalAssets": "1.0e10"}}
    draft = gen.build_static_row("eunl.de", payload, as_of=AS_OF)
    assert draft.fund_size_currency is None
    assert any("fund base currency not reported" in n for n in draft.notes)
    assert "fund base currency not reported" in draft.source
    assert gen.format_row(draft).endswith(f",{AS_OF},")   # blank currency cell


def test_na_sentinels_are_cleaned_not_pasted_into_the_row():
    # EODHD's literal "NA" on XETRA records must land as an ABSENT cell, never the text.
    payload = {"General": {"Name": "F", "ISIN": "NA"},
               "ETF_Data": {"Ongoing_Charge": "NA", "TotalAssets": " NA ",
                            "Yield": "n/a", "Domicile": "NA", "Currency": "-"}}
    draft = gen.build_static_row("spyd.de", payload, as_of=AS_OF)
    assert draft.expense_ratio is None
    assert draft.fund_size is None
    assert draft.distribution_yield is None
    assert draft.share_class is None
    assert draft.domicile is None                      # NOT the string "NA"
    assert draft.fund_size_currency is None
    # every data cell (all but the free-text source note) is blank — no sentinel text
    cells = gen.format_row(draft).split(",")
    assert cells[1:6] == ["", "", "", "", ""] and cells[-1] == ""


def test_padded_numeric_strings_still_parse():
    draft = gen.build_static_row(
        "x.de", _payload(Ongoing_Charge=" 0.20 ", TotalAssets=" 1.0e10 ",
                         Yield=" 4.63 "),
        as_of=AS_OF)
    assert draft.expense_ratio == 0.20
    assert draft.fund_size == 1.0e10
    assert draft.distribution_yield == 0.0463
