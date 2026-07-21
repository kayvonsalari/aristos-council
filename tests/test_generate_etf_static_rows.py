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
    """An EODHD /fundamentals payload carrying only the ETF_Data fields under test."""
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
    assert draft.notes == []                       # nothing blanked
    assert draft.source == gen.SOURCE_BASE         # no note appended


def test_format_row_matches_csv_column_order():
    draft = gen.build_static_row(
        "vhyl.l",
        _payload(Ongoing_Charge="0.29", TotalAssets="7680000000",
                 Yield="4.63", Domicile="Ireland"),
        as_of=AS_OF)
    line = gen.format_row(draft)
    assert line == f"VHYL.L,0.29,7680000000,0.0463,dist,IE,{gen.SOURCE_BASE},{AS_OF}"


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
# a payload missing ETF_Data entirely degrades gracefully (all guards fire, no crash)
# --------------------------------------------------------------------------- #
def test_empty_payload_produces_a_row_of_blanks_with_notes():
    draft = gen.build_static_row("x.de", {}, as_of=AS_OF)
    assert draft.expense_ratio is None
    assert draft.fund_size is None
    assert draft.distribution_yield is None
    assert draft.share_class is None
    assert any("fake-zero" in n for n in draft.notes)   # fee note fires
    line = gen.format_row(draft)
    assert line == f"X.DE,,,,,,{draft.source},{AS_OF}"
