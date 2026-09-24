"""INDEX-CLASS-SANITY-1 — a fund is not a company, and a label can contradict its own name.

Found in the built table (21,181 rows): EODHD lists Taiwan ETFs as "Common Stock", so they passed
the listing filter, and its classification for them is nonsense —

    0052.TW   "Fubon Taiwan Technology"               -> GicSubIndustry "Pharmaceuticals"
    00939.TW  "China Construction Bank Corp Class H"  -> "Semiconductor Materials & Equipment"

Those rows feed the peer ladder, so a Taiwan ETF became a "semiconductor-equipment peer".

Two problems, handled differently, and each with a way of being wrong that this file pins:

* a FUND is recognised by name and by each exchange's fund code shape — but "Fund", "Trust" and
  "Investment Trust" are also honest words in operating-company names (a Canadian income fund
  that makes chemicals, Northern Trust, a REIT), so a fund word alone must never be enough;
* a SUSPECT label is a POSITIVE contradiction only. A row with no classification contradicts
  nothing (null is not false), and one agreeing field is enough to leave a row alone, because a
  false flag deletes a real peer.

Both kinds stay in the table, are excluded from peer groups, and are counted in ``status``.
"""
from __future__ import annotations

import pytest

from aristos_council.market_index import (CLASSIFICATION_SUSPECT, FUND_NOT_A_COMPANY,
                                          SOURCE_EODHD_LISTING, IndexRow, fund_reason,
                                          is_fund, is_suspect, peers, status, suspect_reason)

SNAPSHOT = "2026-09-20"


def _row(ticker, name, *, sub="", industry="", sector="", gics_sector="", cap=1.0e9,
         market="", exchange="", isin=None, primary=None) -> IndexRow:
    """A fabricated index row. ``cap`` defaults to a value, so the 'no classification AND no cap'
    corroboration is opt-in: pass ``cap=None`` for it."""
    return IndexRow(ticker=ticker, yahoo_ticker=ticker.split(".")[0], name=name,
                    exchange=exchange or market, market=market, currency="USD",
                    sector=sector, industry=industry, gics_sector=gics_sector,
                    gics_industry=industry, gics_subindustry=sub, market_cap=cap,
                    market_cap_usd=cap, market_cap_usd_source="computed",
                    primary_ticker=primary if primary is not None else ticker,
                    isin=isin if isin is not None else f"XX{abs(hash(ticker)) % 10**10:010d}",
                    fetched_at=SNAPSHOT, source=SOURCE_EODHD_LISTING)


# --------------------------------------------------------------------------- #
# the two named examples
# --------------------------------------------------------------------------- #
def test_the_fubon_taiwan_technology_etf_is_a_fund_not_a_pharmaceutical_company():
    """0052.TW: nothing in its NAME says fund. Only the exchange's own code shape does."""
    row = _row("0052.TW", "Fubon Taiwan Technology", sub="Pharmaceuticals", market="TW",
               cap=None)
    assert is_fund(row)
    assert fund_reason(row) == f"{FUND_NOT_A_COMPANY} (TW fund code 0052)"


def test_the_china_construction_bank_etf_line_is_a_fund_not_a_semiconductor_company():
    row = _row("00939.TW", "China Construction Bank Corp Class H",
               sub="Semiconductor Materials & Equipment", market="TW", cap=None)
    assert is_fund(row)
    assert "TW fund code 00939" in fund_reason(row)


def test_the_real_china_construction_bank_is_kept_and_is_not_suspect():
    """0939.HK is the bank itself: a Bank filed under Banks is exactly what it should be."""
    row = _row("0939.HK", "China Construction Bank Corp", sub="Diversified Banks",
               industry="Banks", sector="Financial Services", market="HK", cap=2.5e12)
    assert not is_fund(row) and not suspect_reason(row)


def test_a_bank_filed_under_semiconductors_is_classification_suspect():
    """The classification half of the same defect, on a row that is NOT a Taiwan fund code."""
    row = _row("XYZ.HK", "China Construction Bank Corp Class H",
               sub="Semiconductor Materials & Equipment", industry="Semiconductors",
               sector="Technology", market="HK")
    assert not is_fund(row)
    assert suspect_reason(row).startswith(CLASSIFICATION_SUSPECT)
    assert "named like a bank" in suspect_reason(row)
    assert is_suspect(row)


def test_a_taiwan_fund_is_counted_once_as_a_fund_and_never_also_as_suspect():
    row = _row("00939.TW", "China Construction Bank Corp Class H",
               sub="Semiconductor Materials & Equipment", industry="Semiconductors",
               market="TW", cap=None)
    assert is_fund(row) and not is_suspect(row)


# --------------------------------------------------------------------------- #
# funds: what counts
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "ETF Opportunities Trust",
    "VanEck Sui ETN A",
    "iShares Core MSCI World",
    "WisdomTree Europe Defence UCITS",
    "Aristotle Funds Series Trust",
    "Listed Funds Trust",
    "Impax Funds Series Trust I",
    "CSOP CSI 300 Index Daily (2x) Leveraged Product",
    "Buena Vista Neos Bitcoin High Income Index Fundo De Indice",
    "BlackRock Municipal Target Term Closed Fund",
    "abrdn Asian Income Investment Trust plc",
    "Amundi Index Solutions - Amundi MSCI Europe SRI",
])
def test_a_strong_name_pattern_is_a_fund_on_its_own(name):
    assert is_fund(_row("F1.US", name, sub="Asset Management & Custody Banks", market="US"))


def test_a_morningstar_fund_id_on_lse_is_a_fund():
    assert is_fund(_row("0P0001J2VW.LSE", "Some Portfolio", market="LSE", cap=None))


def test_the_fund_code_shapes_are_per_exchange():
    """A 00xx code is a Taiwan ETF; the same digits on Hong Kong are just a listing."""
    assert is_fund(_row("0050.TW", "Taiwan Top 50", market="TW"))
    assert not is_fund(_row("0050.HK", "Some Company Ltd", market="HK"))
    assert not is_fund(_row("2330.TW", "Taiwan Semiconductor Manufacturing", market="TW"))


def test_a_fund_word_with_nothing_to_say_otherwise_is_a_fund():
    """No classification and no cap: the provider gave the row nothing that says it operates."""
    assert is_fund(_row("SPIIMA.CO", "Sparinvest INDEX MSCI ACWI", market="CO", cap=None))


def test_a_closed_end_fund_in_asset_management_is_a_fund():
    assert is_fund(_row("BCV.US", "Bancroft Fund Limited",
                        sub="Asset Management & Custody Banks", market="US"))


# --------------------------------------------------------------------------- #
# funds: what must NOT count (each one is a real company)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ticker,name,sub,market", [
    ("CHE-UN.TO", "Chemtrade Logistics Income Fund", "Commodity Chemicals", "TO"),
    ("BPF-UN.TO", "Boston Pizza Royalties Income Fund", "Restaurants", "TO"),
    ("RFF.AU", "Rural Funds Group", "Specialized REITs", "AU"),
    ("NTRS.US", "Northern Trust Corp", "Asset Management & Custody Banks", "US"),
    ("AMD.US", "Advanced Micro Devices", "Semiconductors", "US"),
    ("IVZ.US", "Invesco Ltd", "Asset Management & Custody Banks", "US"),
    ("WT.US", "WisdomTree, Inc.", "Asset Management & Custody Banks", "US"),
])
def test_an_operating_company_with_a_fund_word_is_not_a_fund(ticker, name, sub, market):
    assert not is_fund(_row(ticker, name, sub=sub, market=market))


@pytest.mark.parametrize("ticker,name,sub", [
    ("0823.HK", "Link Real Estate Investment Trust", "Retail REITs"),
    ("FRT.US", "Federal Realty Investment Trust", "Retail REITs"),
    ("PMT.US", "PennyMac Mortgage Investment Trust", "Mortgage REITs"),
    ("REI-UN.TO", "RioCan Real Estate Investment Trust", "Retail REITs"),
])
def test_a_reit_is_not_a_fund_although_it_is_called_an_investment_trust(ticker, name, sub):
    """The first cut of the pattern swallowed 60-odd of them, found by surveying the built table:
    a REIT is an operating property company and a legitimate peer."""
    assert not is_fund(_row(ticker, name, sub=sub, market="HK"))


def test_a_fund_word_alone_is_not_enough_when_the_row_reads_as_a_business():
    """A capped, classified row that merely contains 'Fund' outside asset management."""
    row = _row("X.TO", "Some Income Fund", sub="Oil & Gas Storage & Transportation",
               market="TO")
    assert not is_fund(row)


# --------------------------------------------------------------------------- #
# suspect labels: a POSITIVE contradiction only
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ticker,name,sub,industry", [
    ("0DP0.LSE", "Bank Polska Kasa Opieki SA", "Environmental & Facilities Services", ""),
    ("0UKH.LSE", "Bank of Montreal", "Coal & Consumable Fuels", ""),
    ("0GDR.LSE", "UNIQA Insurance Group AG", "Environmental & Facilities Services", ""),
    ("0HHU.LSE", "Armour Residential REIT Inc.", "Environmental & Facilities Services", ""),
    ("01007T.TW", "Cathay No.2 REIT", "Semiconductor Materials & Equipment", ""),
])
def test_a_name_that_says_bank_insurer_or_reit_under_an_unrelated_label_is_suspect(
        ticker, name, sub, industry):
    assert suspect_reason(_row(ticker, name, sub=sub, industry=industry, market="LSE"))


@pytest.mark.parametrize("name,sub,industry,sector", [
    ("Deutsche Bank AG", "Diversified Banks", "Banks", "Financial Services"),
    ("Bank of New York Mellon", "Asset Management & Custody Banks", "", "Financial Services"),
    ("Allianz Insurance", "Multi-line Insurance", "Insurance", "Financial Services"),
    ("Prologis REIT", "Industrial REITs", "", "Real Estate"),
    ("Annaly Mortgage REIT", "Mortgage REITs", "", "Financial Services"),
])
def test_a_label_consistent_with_the_name_is_not_suspect(name, sub, industry, sector):
    assert not suspect_reason(_row("X.US", name, sub=sub, industry=industry, sector=sector,
                                   market="US"))


def test_a_row_with_no_classification_contradicts_nothing():
    """Null is not false: a missing label is not a wrong one."""
    assert not suspect_reason(_row("X.US", "Some Bank Holdings", market="US"))


def test_a_label_of_other_is_no_label_at_all():
    """Granite REIT's US line reads 'Other'. That is an absence, not a contradiction."""
    assert not suspect_reason(_row("GRP-U.US", "Granite Real Estate Investment Trust",
                                   sub="Other", market="US"))


def test_one_agreeing_field_is_enough_to_leave_a_row_alone():
    """A sub-industry that is wrong while the sector is right is not worth deleting a peer for."""
    row = _row("X.US", "Example Bank", sub="Semiconductors", sector="Financial Services",
               market="US")
    assert not suspect_reason(row)


def test_a_non_financial_business_with_bank_in_a_word_is_not_matched():
    """Word boundaries: 'Riverbank' and 'Banking' are not 'bank'."""
    assert not suspect_reason(_row("X.US", "Riverbankshire Steel", sub="Steel", market="US"))


# --------------------------------------------------------------------------- #
# peers: kept in the index, never a peer
# --------------------------------------------------------------------------- #
def _pharma(ticker, name="", **kw) -> IndexRow:
    return _row(ticker, name or f"{ticker} Pharma Inc", sub="Pharmaceuticals",
                industry="Pharmaceuticals", sector="Healthcare", market="US", cap=10e9, **kw)


def _universe() -> list:
    rows = [_pharma(f"P{i:02d}.US") for i in range(1, 16)]
    rows.append(_row("0052.TW", "Fubon Taiwan Technology", sub="Pharmaceuticals",
                     industry="Pharmaceuticals", sector="Healthcare", market="TW", cap=10e9))
    rows.append(_row("BNK.HK", "Nowhere Bank Corp", sub="Pharmaceuticals",
                     industry="Pharmaceuticals", sector="Healthcare", market="HK", cap=10e9))
    return rows


def test_a_taiwan_etf_and_a_suspect_bank_are_never_pharma_peers():
    """Both carry the Pharmaceuticals label and a peer-sized cap. Without this they would be
    peers of every pharma company in the index."""
    group = peers("P01.US", rows=_universe())
    tickers = {r.ticker for r in group.members}
    assert group.available
    assert "0052.TW" not in tickers and "BNK.HK" not in tickers
    assert len(tickers) == 14


def test_the_peer_reasons_count_what_was_left_out_and_why():
    group = peers("P01.US", rows=_universe())
    assert f"1 candidate(s) skipped: {FUND_NOT_A_COMPANY}" in group.reasons
    assert any(f"1 candidate(s) skipped: {CLASSIFICATION_SUSPECT}" in r for r in group.reasons)


def test_the_excluded_rows_are_still_in_the_table():
    """Kept, not deleted: the index mirrors the provider, the way cross-listings are kept."""
    rows = _universe()
    peers("P01.US", rows=rows)
    assert {"0052.TW", "BNK.HK"} <= {r.ticker for r in rows}


def test_a_fund_subject_gets_no_peer_group_and_says_why():
    group = peers("0052.TW", rows=_universe())
    assert not group.available
    assert any(FUND_NOT_A_COMPANY in r and "no peer group" in r for r in group.reasons)


def test_a_suspect_subject_gets_no_peer_group_and_says_why():
    group = peers("BNK.HK", rows=_universe())
    assert not group.available
    assert any(CLASSIFICATION_SUSPECT in r for r in group.reasons)


# --------------------------------------------------------------------------- #
# status: counted, and named
# --------------------------------------------------------------------------- #
class _Store:
    def __init__(self, rows):
        self._rows = rows
        self.path = "memory"

    def load(self):
        return list(self._rows)


def test_status_counts_funds_and_suspects_separately():
    out = status(_Store(_universe()))
    assert out.funds == 1 and out.suspect == 1
    assert out.fund_examples == ["0052.TW"] and out.suspect_examples == ["BNK.HK"]


def test_status_says_fund_not_a_company_in_those_words():
    text = "\n".join(status(_Store(_universe())).lines())
    assert "1 row(s) are fund, not a company, excluded from peer groups (e.g. 0052.TW)" in text
    assert "1 row(s) classification suspect - kept, excluded from peer groups (e.g. BNK.HK)" \
        in text


def test_status_on_an_index_with_neither_says_zero_not_nothing():
    text = "\n".join(status(_Store([_pharma("P01.US")])).lines())
    assert "0 row(s) are fund, not a company" in text
    assert "0 row(s) classification suspect" in text


def test_status_only_names_a_handful_of_examples():
    rows = [_row(f"00{n}.TW", f"ETF {n}", market="TW") for n in range(50, 70)]
    assert len(status(_Store(rows)).fund_examples) == 5
