"""BATCH 7 (2026-09) - orphaned ADRs, honest distinct counts, Korean preference shares.

Measured on the live index (24,999 rows) after batch 6 and the GBX repair:

    AZN.LSE cohort held GSK twice: GSK.LSE ($102.5bn) and GSK.US "GlaxoSmithKline PLC ADR"
    ($100.6bn), which names ITSELF as PrimaryTicker, has its own ISIN, and a different name.
    2330.TW cohort held SK Hynix twice: SKHY.US (ADS, no PrimaryTicker) beside 000660.KO, 1.37x
    apart - beyond the name-link tolerance.
    Samsung Electronics Co Pref (005935.KO) stood beside 005930.KO as a second company.

    PEER-ADR-ALIAS-1     name cleaning + a dated identity-alias file
    PEER-DISTINCT-COUNT-1  the distinct count reads the same grouping as the pool
    PEER-KR-PREF-1       Korean preference shares fold into the ordinary line

Every table here is fabricated; nothing reaches the real adapter or the real index.
"""
from __future__ import annotations

import pytest

from aristos_council.market_index import (SOURCE_EODHD_LISTING, IdentityAlias, IndexRow,
                                          IndexStore, MarketIndexError, _name_key,
                                          apply_identity_aliases, company_groups,
                                          RECEIPT_KR_PREF, distinct_companies, korean_pref_map,
                                          load_identity_aliases, secondary_lines, orphan_depositary_rows,
                                          peer_snapshot, peers, status)

SNAPSHOT = "2026-09-25"


def _row(ticker, name="", *, cap_bn=100.0, sub="Pharmaceuticals", industry="Pharma & Biotech",
         eodhd="Drug Manufacturers", primary=None, isin=None, market="", currency="USD"
         ) -> IndexRow:
    cap = None if cap_bn is None else cap_bn * 1e9
    return IndexRow(
        ticker=ticker, yahoo_ticker=ticker.rpartition(".")[0] or ticker, name=name or ticker,
        exchange=market or ticker.rpartition(".")[2], market=market, currency=currency,
        sector="Healthcare", industry=eodhd, gics_industry=industry, gics_subindustry=sub,
        market_cap=cap, market_cap_usd=cap,
        market_cap_usd_source="computed" if cap is not None else "abstained",
        primary_ticker=ticker if primary is None else primary,
        isin=f"XX{abs(hash(ticker)) % 10**10:010d}" if isin is None else isin,
        fetched_at=SNAPSHOT, source=SOURCE_EODHD_LISTING)


def _fillers(n, *, cap_bn=100.0, **kw):
    return [_row(f"RIV{i:02d}.US", f"Rival Company {i}", cap_bn=cap_bn + i, **kw)
            for i in range(n)]


def _tickers(group):
    return [m.ticker for m in group.members]


# =========================================================================== #
# PEER-ADR-ALIAS-1 (a) - name cleaning
# =========================================================================== #
@pytest.mark.parametrize("name, key", [
    ("GlaxoSmithKline PLC ADR", "glaxosmithkline"),
    ("SK Hynix Inc. American Depositary Shares", "sk hynix"),
    ("Samsung Electronics Co Pref", "samsung electronics"),
    ("Lg Electronics Pref", "lg electronics"),
    ("Doosan Pref Shs", "doosan"),
    ("Yuhan Corp Preferred", "yuhan"),
    ("Fuchs Petrolub SE Preference Shares", "fuchs petrolub"),
    ("ioneer Ltd American Depositary Shares", "ioneer"),
    ("Some Co Global Depositary Receipts", "some"),
])
def test_depositary_and_preference_wording_is_not_part_of_a_company_name(name, key):
    assert _name_key(name) == key


@pytest.mark.parametrize("name, key", [
    ("American Express Company", "american express"),
    ("American Airlines Group Inc.", "american airlines"),
    ("American Financial Group, Inc.", "american financial"),
])
def test_american_alone_is_not_noise(name, key):
    """Only the PHRASE 'american depositary shares/receipts' is stripped."""
    assert _name_key(name) == key


def test_name_cleaning_alone_does_not_join_gsk_or_sk_hynix_to_their_adrs():
    """The brief is explicit: 1a only stops future near-misses. glaxosmithkline is not gsk, and
    1.37x is beyond the name-link tolerance."""
    gsk = _row("GSK.LSE", "GSK plc", cap_bn=102.5, market="LSE", isin="GB00BN7SWP63")
    gsk_adr = _row("GSK.US", "GlaxoSmithKline PLC ADR", cap_bn=100.6, isin="US37733W2044")
    hynix = _row("000660.KO", "SK Hynix Inc", cap_bn=970.1, market="KO", isin="KR7000660001")
    hynix_ads = _row("SKHY.US", "SK Hynix Inc. American Depositary Shares", cap_bn=1331.0,
                     primary="", isin="US78392B2060")
    assert len(company_groups([gsk, gsk_adr], link_by_name=True)) == 2
    assert len(company_groups([hynix, hynix_ads], link_by_name=True)) == 2


def test_name_cleaning_does_join_a_plain_ads_to_its_home_line_by_name_and_size():
    """WeRide: '... American Depositary Shares' now reads as the same name as the HK line."""
    hk = _row("0800.HK", "WeRide Inc.", cap_bn=1.96, market="HK", isin="KYG9525R1007")
    ads = _row("WRD.US", "WeRide Inc. American Depositary Shares", cap_bn=1.81,
               isin="US94776L1052")
    assert len(company_groups([hk, ads], link_by_name=True)) == 1


# =========================================================================== #
# PEER-ADR-ALIAS-1 (b) - the alias file
# =========================================================================== #
def test_the_shipped_alias_file_has_the_two_measured_entries_with_a_date_and_a_reason():
    shipped = {a.ticker: a for a in load_identity_aliases()}
    assert shipped["GSK.US"].primary == "GSK.LSE"
    assert shipped["SKHY.US"].primary == "000660.KO"
    assert all(a.date and a.reason for a in shipped.values())


def test_a_missing_alias_file_is_no_aliases(tmp_path):
    assert load_identity_aliases(tmp_path / "nothing.yaml") == []


@pytest.mark.parametrize("body, needle", [
    ("aliases:\n  - ticker: A.US\n    primary: B.US\n    date: 2026-09-25\n", "reason"),
    ("aliases:\n  - ticker: A.US\n    primary: B.US\n    reason: r\n", "date"),
    ("aliases:\n  - primary: B.US\n    date: 2026-09-25\n    reason: r\n", "ticker"),
    ("aliases:\n  - {ticker: A.US, primary: B.US, date: 2026-09-25, reason: r}\n"
     "  - {ticker: a.us, primary: C.US, date: 2026-09-25, reason: r}\n", "repeats"),
    ("aliases: nope\n", "must be a list"),
])
def test_a_malformed_alias_file_is_an_error_not_a_silent_no_op(tmp_path, body, needle):
    bad = tmp_path / "a.yaml"
    bad.write_text(body, encoding="utf-8")
    with pytest.raises(MarketIndexError, match=needle):
        load_identity_aliases(bad)


def test_an_alias_changes_only_the_primary_ticker_of_a_copy():
    adr = _row("GSK.US", "GlaxoSmithKline PLC ADR", cap_bn=100.6)
    (fixed,), applied = apply_identity_aliases(
        [adr], [IdentityAlias("GSK.US", "GSK.LSE", "2026-09-25", "why")])
    assert fixed.primary_ticker == "GSK.LSE" and adr.primary_ticker == "GSK.US"
    assert (fixed.market_cap_usd, fixed.gics_subindustry, fixed.isin) == \
        (adr.market_cap_usd, adr.gics_subindustry, adr.isin)
    assert list(applied) == ["GSK.US"]


ALIAS_GSK = IdentityAlias("GSK.US", "GSK.LSE", "2026-09-25", "an ADR that names itself")
ALIAS_SKHY = IdentityAlias("SKHY.US", "000660.KO", "2026-09-25", "an ADS with no primary")


def _azn_table():
    azn = _row("AZN.LSE", "AstraZeneca PLC", cap_bn=260.0, market="LSE", isin="GB0009895292")
    gsk = _row("GSK.LSE", "GSK plc", cap_bn=102.5, market="LSE", isin="GB00BN7SWP63")
    gsk_adr = _row("GSK.US", "GlaxoSmithKline PLC ADR", cap_bn=100.6, isin="US37733W2044")
    rivals = [_row(f"RIV{i:02d}.US", f"Rival Company {i}", cap_bn=100.0 + i * 3)
              for i in range(11)]
    return [azn, gsk, gsk_adr, *rivals]


def test_without_the_alias_gsk_stands_in_the_cohort_twice():
    """The defect, reproduced: the ADR names itself, so nothing links it to GSK.LSE."""
    members = _tickers(peers("AZN.LSE", rows=_azn_table(), aliases=[]))
    assert "GSK.LSE" in members and "GSK.US" in members


def test_with_the_alias_gsk_is_in_azns_cohort_once_as_the_home_line():
    group = peers("AZN.LSE", rows=_azn_table(), aliases=[ALIAS_GSK])
    members = _tickers(group)
    assert "GSK.LSE" in members and "GSK.US" not in members
    assert group.distinct_companies == len(members) == 12
    line = "identity aliased: GSK.US -> GSK.LSE (2026-09-25: an ADR that names itself)"
    assert line in group.reasons
    assert group.aliased == [{"ticker": "GSK.US", "primary": "GSK.LSE",
                              "role": "duplicate collapsed into GSK.LSE",
                              "date": "2026-09-25", "reason": "an ADR that names itself"}]
    assert peer_snapshot(group)["identity_aliases"] == group.aliased


def test_the_adrs_size_is_never_used_the_home_line_wins():
    """GSK.LSE is kept by _listing_rank; only its own $102.5bn is ever in the report."""
    group = peers("AZN.LSE", rows=_azn_table(), aliases=[ALIAS_GSK])
    kept = next(m for m in group.members if m.name == "GSK plc")
    assert kept.market_cap_usd == 102.5e9


def test_an_alias_is_not_reported_by_a_cohort_it_played_no_part_in():
    """Rolls-Royce's cohort has no reason to mention GSK's alias."""
    rr = _row("RR.LSE", "Rolls-Royce", cap_bn=160.0, sub="Aerospace", industry="Aero",
              eodhd="Aerospace", market="LSE")
    peers_ = [_row(f"AERO{i:02d}.US", f"Aero {i}", cap_bn=150.0 + i, sub="Aerospace",
                   industry="Aero", eodhd="Aerospace") for i in range(12)]
    group = peers("RR.LSE", rows=[rr, *peers_, *_azn_table()], aliases=[ALIAS_GSK])
    assert group.available and group.aliased == []
    assert not any("identity aliased" in r for r in group.reasons)


def test_looking_the_adr_up_answers_for_the_home_line_and_reports_the_alias():
    group = peers("GSK.US", rows=_azn_table(), aliases=[ALIAS_GSK])
    assert group.subject.ticker == "GSK.LSE"
    assert any("GSK.US is a" in r and "listing of GSK.LSE" in r for r in group.reasons)
    assert [a["role"] for a in group.aliased] == ["line the reader looked up"]


def test_an_adr_with_no_primary_and_a_size_beyond_the_name_link_is_aliased_too():
    """SKHY.US: no PrimaryTicker, 1.37x from 000660.KO."""
    tsmc = _row("2330.TW", "TSMC", cap_bn=2000.0, sub="Semiconductors", industry="Semis",
                eodhd="Semiconductors", market="TW", currency="TWD")
    hynix = _row("000660.KO", "SK Hynix Inc", cap_bn=970.1, sub="Semiconductors",
                 industry="Semis", eodhd="Semiconductors", market="KO", isin="KR7000660001")
    ads = _row("SKHY.US", "SK Hynix Inc. American Depositary Shares", cap_bn=1331.0,
               sub="Semiconductors", industry="Semis", eodhd="Semiconductors", primary="",
               isin="US78392B2060")
    rivals = [_row(f"SEMI{i:02d}.US", f"Semi Co {i}", cap_bn=1500.0 + i, sub="Semiconductors",
                   industry="Semis", eodhd="Semiconductors") for i in range(12)]
    rows = [tsmc, hynix, ads, *rivals]
    assert {"000660.KO", "SKHY.US"} <= set(_tickers(peers("2330.TW", rows=rows, aliases=[])))
    group = peers("2330.TW", rows=rows, aliases=[ALIAS_SKHY])
    assert "000660.KO" in _tickers(group) and "SKHY.US" not in _tickers(group)
    assert "identity aliased: SKHY.US -> 000660.KO" in " ".join(group.reasons)


def test_aliases_are_never_applied_to_a_table_handed_in_directly_by_default():
    """A fabricated table must not meet a real correction by accident (GSK.US is a real key)."""
    members = _tickers(peers("AZN.LSE", rows=_azn_table()))
    assert "GSK.US" in members


def test_the_alias_is_applied_before_grouping_so_every_company_rule_sees_it():
    rows, _ = apply_identity_aliases(_azn_table(), [ALIAS_GSK])
    assert len(company_groups([r for r in rows if r.ticker.startswith("GSK")])) == 1


# =========================================================================== #
# PEER-ADR-ALIAS-1 (c) - status shows the next orphan
# =========================================================================== #
def _status_rows():
    return [
        _row("GSK.US", "GlaxoSmithKline PLC ADR", market="US"),                    # orphan
        _row("ORPH.US", "Orphan Holdings ADS", market="US", primary=""),           # orphan
        _row("TWIN.US", "Twin Corp American Depositary Shares", market="US"),      # has a twin
        _row("TWIN.HK", "Twin Corp", market="HK"),
        _row("HOMED.US", "Homed Inc ADR", market="US", primary="HOMED.LSE"),       # names a home
        _row("PLAIN.US", "Plain Inc", market="US"),                                # not an ADR
        _row("ORPH.HK", "Some Depositary Thing", market="HK"),                     # not US
    ]


def test_status_lists_adr_rows_that_nothing_links_to_a_home(tmp_path):
    assert [r.ticker for r in orphan_depositary_rows(_status_rows())] == ["GSK.US", "ORPH.US"]
    store = IndexStore(tmp_path)
    store.save(_status_rows())
    out = status(store, aliases=[])
    assert out.orphan_adrs == 2 and out.orphan_adr_examples == ["GSK.US", "ORPH.US"]
    assert "2 US ADR/ADS row(s) name no home" in " ".join(out.lines())


def test_status_counts_aliased_rows_and_stops_calling_them_orphans(tmp_path):
    store = IndexStore(tmp_path)
    store.save(_status_rows())
    out = status(store, aliases=[ALIAS_GSK, ALIAS_SKHY])          # SKHY.US is not in this table
    assert out.aliased == 1 and out.aliased_examples == ["GSK.US"]
    assert out.orphan_adrs == 1 and out.orphan_adr_examples == ["ORPH.US"]
    assert "1 row(s) carry an identity alias" in " ".join(out.lines())


# =========================================================================== #
# PEER-DISTINCT-COUNT-1
# =========================================================================== #
def _twin_table(*, second_name="Twin Company", second_cap=180.0):
    subject = _row("SUBJ.US", "Subject Pharma", cap_bn=120.0)
    a = _row("TWIN.US", "Twin Company", cap_bn=100.0, isin="US0000000011")
    b = _row("TWIN2.XETRA", second_name, cap_bn=second_cap, isin="DE0000000022", market="XETRA")
    return [subject, a, b, *_fillers(11, cap_bn=110.0)]


def test_two_members_that_share_a_name_and_no_handle_are_counted_once_and_still_listed():
    """1.8x apart: beyond the name-link tolerance, so both survive the pool - and the count says so."""
    group = peers("SUBJ.US", rows=_twin_table(), aliases=[])
    members = _tickers(group)
    assert "TWIN.US" in members and "TWIN2.XETRA" in members         # never dropped silently
    assert len(members) == 13 and group.distinct_companies == 12
    assert "1 member(s) share a company name with another member: counted once, listed twice"         in group.reasons
    assert "12 distinct companies" in group.sentence()


def test_a_cohort_whose_names_are_all_different_reports_no_sharing():
    group = peers("SUBJ.US", rows=_twin_table(second_name="Another Company"), aliases=[])
    assert group.distinct_companies == len(group.members) == 13
    assert not any("share a company name" in r for r in group.reasons)


def test_members_with_no_usable_name_are_never_merged_by_name():
    a = _row("A.US", "", cap_bn=100.0)
    b = _row("B.US", "Ordinary Fully Paid Deferred Settlement", cap_bn=100.0)
    c = _row("C.US", "Ordinary Fully Paid Deferred Settlement", cap_bn=180.0)
    assert distinct_companies([a, b, c]) == (3, 0)


def test_the_count_reads_the_same_grouping_as_the_pool_including_aliases():
    """Aliases are applied before grouping, so an aliased pair is one company here too."""
    rows, _ = apply_identity_aliases(_azn_table(), [ALIAS_GSK])
    gsk = [r for r in rows if r.ticker.startswith("GSK")]
    assert distinct_companies(gsk) == (1, 1)


def test_the_distinct_count_and_the_pool_agree_on_the_live_shapes():
    """Preference share and ordinary share, 1.29x apart, beyond the link: two rows, one company."""
    ordinary = _row("005930.KO", "Samsung Electronics Co Ltd", cap_bn=1376.0, market="KO")
    pref = _row("005935.KO", "Samsung Electronics Co Pref", cap_bn=1065.4, market="KO")
    assert len(company_groups([ordinary, pref], link_by_name=True)) == 2
    assert distinct_companies([ordinary, pref]) == (1, 1)


# =========================================================================== #
# PEER-KR-PREF-1
# =========================================================================== #
def _kr(code, name, cap_bn, market="KO", **kw):
    return _row(f"{code}.{market}", name, cap_bn=cap_bn, market=market, currency="KRW", **kw)


def _korea():
    return [
        _kr("005930", "Samsung Electronics Co Ltd", 1376.0),
        _kr("005935", "Samsung Electronics Co Pref", 1065.4),
        _kr("066570", "LG Electronics Inc", 12.0),
        _kr("066575", "Lg Electronics Pref", 6.0),
        _kr("091165", "Lone Pref Series Co", 1.0),                       # ends in 5, no 0-line
    ]


def test_samsung_and_lg_preference_lines_fold_into_their_ordinary_line():
    rows = _korea()
    assert korean_pref_map(rows) == {"005935.KO": "005930.KO", "066575.KO": "066570.KO"}
    found = secondary_lines(rows)
    assert found == {"005935.KO": RECEIPT_KR_PREF, "066575.KO": RECEIPT_KR_PREF}


def test_the_second_and_third_series_end_in_7_and_9():
    rows = [_kr("005380", "Hyundai Motor Co Ltd", 40.0), _kr("005385", "Hyundai Motor Co Pref", 10.0),
            _kr("005387", "Hyundai Motor Co Pfd", 5.0), _kr("005389", "Hyundai Motor Co Prf", 4.0)]
    assert set(korean_pref_map(rows)) == {"005385.KO", "005387.KO", "005389.KO"}


def test_a_code_ending_in_5_with_no_ordinary_line_is_left_alone():
    assert "091165.KO" not in secondary_lines(_korea())


def test_a_code_ending_in_5_whose_ordinary_line_has_a_different_name_is_left_alone():
    """Null is not a match: same code shape, but the names do not agree, so nothing folds."""
    rows = [_kr("000150", "Doosan Bobcat Inc", 3.0), _kr("000155", "Doosan Pref Shs", 6.0)]
    assert secondary_lines(rows) == {}


def test_the_ordinary_line_must_be_on_the_same_exchange():
    rows = [_kr("005930", "Samsung Electronics Co Ltd", 1376.0, market="KO"),
            _kr("005935", "Samsung Electronics Co Pref", 1065.4, market="KQ")]
    assert secondary_lines(rows) == {}


def test_a_korean_ordinary_line_and_non_korean_codes_are_never_touched():
    rows = [_kr("005930", "Samsung Electronics Co Ltd", 1376.0),
            _row("00590.TW", "Some Taiwan Co", market="TW"),
            _row("AAPL.US", "Apple Inc", market="US")]
    assert secondary_lines(rows) == {}


def _samsung_world():
    subject = _kr("005930", "Samsung Electronics Co Ltd", 1376.0, sub="Semiconductors",
                  industry="Semis", eodhd="Semiconductors")
    pref = _kr("005935", "Samsung Electronics Co Pref", 1065.4, sub="Semiconductors",
               industry="Semis", eodhd="Semiconductors")
    rivals = [_row(f"SEMI{i:02d}.US", f"Semi Co {i}", cap_bn=1200.0 + i * 20, sub="Semiconductors",
                   industry="Semis", eodhd="Semiconductors") for i in range(13)]
    return [subject, pref, *rivals]


def test_a_preference_line_is_never_a_peer_and_is_counted():
    rivals = _fillers(13, cap_bn=1100.0, sub="Semiconductors", industry="Semis",
                      eodhd="Semiconductors")
    subject = _row("SUBJ.US", "Chip Subject", cap_bn=1200.0, sub="Semiconductors",
                   industry="Semis", eodhd="Semiconductors")
    rows = [subject, *_samsung_world()[:2], *rivals]
    group = peers("SUBJ.US", rows=rows, aliases=[])
    members = _tickers(group)
    assert "005930.KO" in members and "005935.KO" not in members
    assert any("Korean preference share 1" in r for r in group.reasons)


def test_looking_up_the_preference_line_answers_for_the_ordinary_line():
    group = peers("005935.KO", rows=_samsung_world(), aliases=[])
    assert group.subject.ticker == "005930.KO"
    assert any("005935.KO is a Korean preference share; peers computed for 005930.KO"
               in r for r in group.reasons)
    assert "005935.KO" not in _tickers(group) and group.available


def test_status_counts_korean_preference_shares_as_their_own_kind(tmp_path):
    store = IndexStore(tmp_path)
    store.save(_korea())
    out = status(store, aliases=[])
    assert out.receipts == {RECEIPT_KR_PREF: 2}
    assert out.receipts_sole == 0                      # each has its ordinary line here
    assert f"{RECEIPT_KR_PREF}: 2 (e.g. 005935.KO, 066575.KO)" in " ".join(out.lines())
