"""BATCH 6 (2026-09) - who may stand in a peer group, and who they are.

A read-only cohort test on 2026-09-25 (Siemens Energy, Eaton, TSMC) found the same company counted
several times, secondary trading lines with the wrong label and size, and rivals hidden by a wrong
label. The fixtures below are the REAL shapes measured on the 22,209-row table, so each test is
anchored to a row that misbehaved rather than to an invented one.

    PEER-DEDUP-1          identity: PrimaryTicker first, then ISIN, transitively
    PEER-RECEIPTS-1       pools: receipts and foreign trading lines are not peers
    PEER-SIZE-SANITY-1    a size far from the company's other listings is not believed
    PEER-LABEL-MATCH-1    GICS against GICS, EODHD against EODHD, never a coincidence of wording
    PEER-LABEL-RECALL-1   either label system may find a rival; a small override file fixes labels
"""
from __future__ import annotations

from aristos_council.market_index import (RECEIPT_BDR, RECEIPT_BR_FRACTIONAL, RECEIPT_CDR,
                                          RECEIPT_LSE_GDR, RECEIPT_LSE_LINE, RECEIPT_SWISS_LINE,
                                          SOURCE_EODHD_LISTING, IndexRow, IndexStore,
                                          MarketIndexError, apply_label_overrides,
                                          company_groups, company_key, one_row_per_company,
                                          load_config, load_label_overrides, peer_snapshot, peers, receipt_kind,
                                          secondary_lines,
                                          size_disputes, size_suspects, status)

SNAPSHOT = "2026-09-25"


def _row(ticker, name="", *, cap_bn=100.0, sub="Semiconductors",
         industry="Semiconductors & Semiconductor Equipment", eodhd="Semiconductors",
         primary=None, isin=None, market="", currency="USD", country="") -> IndexRow:
    """A fabricated index row. ``primary`` / ``isin`` default to the row's own ticker / a unique
    ISIN; pass ``""`` for the no-identity rows the provider serves for receipts."""
    cap = None if cap_bn is None else cap_bn * 1e9
    code = ticker.rpartition(".")[0] or ticker
    return IndexRow(
        ticker=ticker, yahoo_ticker=code, name=name or ticker,
        exchange=market or ticker.rpartition(".")[2], market=market, country=country,
        currency=currency, sector="Technology", industry=eodhd, gics_industry=industry,
        gics_subindustry=sub, market_cap=cap, market_cap_usd=cap,
        market_cap_usd_source="computed" if cap is not None else "abstained",
        primary_ticker=ticker if primary is None else primary,
        isin=f"XX{abs(hash(ticker)) % 10**10:010d}" if isin is None else isin,
        fetched_at=SNAPSHOT, source=SOURCE_EODHD_LISTING)


def _fillers(n=14, *, cap_bn=100.0, **kw) -> list[IndexRow]:
    """``n`` unrelated single-line companies in the subject's own sub-industry."""
    return [_row(f"PEER{i:02d}.US", f"Peer Company {i}", cap_bn=cap_bn, **kw) for i in range(n)]


def _tickers(group) -> list[str]:
    return [m.ticker for m in group.members]


# =========================================================================== #
# PEER-DEDUP-1
# =========================================================================== #
def _tsmc_lines() -> list[IndexRow]:
    """Probed 2026-09-25: the US ADR carries its OWN ISIN but names its home line."""
    return [_row("2330.TW", "Taiwan Semiconductor Manufacturing Co. Ltd.", cap_bn=2000,
                 isin="TW0002330008", market="TW", currency="TWD"),
            _row("TSM.US", "Taiwan Semiconductor Manufacturing Co", cap_bn=2250,
                 primary="2330.TW", isin="US8740391003", market="US")]


def test_an_adr_with_its_own_isin_but_the_home_primary_is_the_same_company():
    """ISIN-first kept TSM.US apart from 2330.TW, so TSMC was its own peer."""
    tw, us = _tsmc_lines()
    assert company_key(tw) == company_key(us)
    kept, dropped = one_row_per_company([tw, us])
    assert [r.ticker for r in kept] == ["2330.TW"] and dropped == 1


def test_tsmcs_cohort_holds_each_company_once_and_never_tsmc():
    """The named acceptance case, with NVIDIA's several lines as they appear in the index."""
    tw, adr = _tsmc_lines()
    nvidia = [_row("NVDA.US", "NVIDIA Corporation", cap_bn=2300, isin="US67066G1040"),
              _row("NVDA.XETRA", "NVIDIA Corporation", cap_bn=2290, primary="NVDA.US",
                   isin="US67066G1040", market="XETRA"),
              _row("NVDA.MX", "NVIDIA Corporation", cap_bn=2310, primary="NVDA.US",
                   isin="MXP000000001", market="MX")]
    rivals = [_row(f"RIV{i:02d}.US", f"Rival {i}", cap_bn=2000 + i * 20) for i in range(12)]
    group = peers("2330.TW", rows=[tw, adr, *nvidia, *rivals])

    assert group.available
    members = _tickers(group)
    assert "TSM.US" not in members and "2330.TW" not in members     # never TSMC
    assert sum(1 for m in members if m.startswith("NVDA")) == 1     # NVIDIA once
    assert len(set(members)) == len(members)
    assert any("never its own peer" in r for r in group.reasons)


def test_looking_the_cohort_up_by_the_adr_gives_the_same_company_the_same_way():
    tw, adr = _tsmc_lines()
    rows = [tw, adr, *[_row(f"RIV{i:02d}.US", cap_bn=2000 + i * 20) for i in range(12)]]
    by_adr = peers("TSM.US", rows=rows)
    assert by_adr.subject.ticker == "2330.TW"
    assert "TSM.US" not in _tickers(by_adr)


def test_hong_kong_zero_padded_twins_are_one_company():
    """0013.HK and 13.HK each name themselves as primary and share one ISIN (real, Hutchmed),
    and its US ADR names 0013.HK with an ISIN of its own. All three are one company."""
    padded = _row("0013.HK", isin="KYG4672N1198", market="HK")
    bare = _row("13.HK", isin="KYG4672N1198", market="HK")
    adr = _row("HCM.US", primary="0013.HK", isin="US44842L1035", market="US")
    groups = company_groups([padded, bare, adr])
    assert len(groups) == 1 and len(groups[0]) == 3


def test_a_row_linked_only_by_isin_joins_the_group_a_primary_ticker_built():
    """The reason grouping is transitive: one row names the home, a sibling has only the ISIN."""
    home = _row("ACME.US", isin="US1111111111")
    adr = _row("ACME.MX", primary="ACME.US", isin="MX2222222222")
    isin_only = _row("ACME.F", primary="", isin="MX2222222222")
    assert len(company_groups([home, adr, isin_only])) == 1


def test_unrelated_companies_are_never_merged():
    a, b = _row("AAA.US"), _row("BBB.US")
    assert len(company_groups([a, b])) == 2


def test_a_row_with_no_identity_at_all_is_its_own_company():
    """AMD.TO carries neither field: it must not be merged with a stranger by default."""
    lone = _row("LONE.TO", primary="", isin="")
    assert company_key(lone) == "ticker:LONE.TO"
    assert len(company_groups([lone, _row("OTHER.US")])) == 2


def test_the_primary_ticker_outranks_the_isin_as_a_row_key():
    row = _row("TSM.US", primary="2330.TW", isin="US8740391003")
    assert company_key(row) == "primary:2330.TW"


# =========================================================================== #
# PEER-RECEIPTS-1
# =========================================================================== #
# Real rows, as served: a receipt usually has NO PrimaryTicker and NO ISIN.
def _bdr(code="E1TN34", name="Eaton Corporation plc", cap_bn=170.0, **kw):
    return _row(f"{code}.SA", name, cap_bn=cap_bn, primary="", isin="", market="SA",
                currency="BRL", **kw)


def test_the_brazilian_receipt_shapes_are_recognised():
    for code in ("E1TN34", "A1MD34", "AVGO34", "TSMC34", "NVDC34", "AURA33", "MUTC34"):
        assert receipt_kind(_bdr(code)) == RECEIPT_BDR, code


def test_a_bdr_is_also_recognised_by_its_isin_when_the_code_is_ordinary():
    row = _row("XXXX3.SA", "Some Receipt", primary="", isin="BRA1MDBDR002", market="SA")
    assert receipt_kind(row) == RECEIPT_BDR


def test_a_real_brazilian_company_is_not_a_receipt():
    """WEG, Petrobras and a unit line end 3, 4 or 11 - never 32-39."""
    for code, name in (("WEGE3", "WEG S.A."), ("PETR4", "Petroleo Brasileiro"),
                       ("BPAC11", "Banco BTG Pactual"), ("TF533", "Some Fund Unit")):
        assert receipt_kind(_row(f"{code}.SA", name, market="SA")) == "", code


def test_a_brazilian_fractional_lot_line_is_a_secondary_line():
    assert receipt_kind(_row("AALR3F.SA", "AALR3F", primary="", isin="", market="SA")
                        ) == RECEIPT_BR_FRACTIONAL


def test_a_canadian_cdr_is_recognised_by_its_name_and_a_canadian_company_is_not():
    cdr = _row("AMD.TO", "Advanced Micro Devices CDR (CAD Hedged)", primary="", isin="",
               market="TO", currency="CAD")
    assert receipt_kind(cdr) == RECEIPT_CDR
    assert receipt_kind(_row("SHOP.TO", "Shopify Inc.", market="TO")) == ""


def test_london_0xxx_lines_are_secondary_and_a_uk_ticker_is_not():
    """0A0D / 0NMK / 0QMI are the international order book lines: 2,371 of the 3,834 LSE rows."""
    for code in ("0NMK", "0QMI", "0A0D", "0SEA"):
        assert receipt_kind(_row(f"{code}.LSE", "Some Foreign Co", market="LSE")
                            ) == RECEIPT_LSE_LINE, code
    for code in ("BP", "VOD", "3IN", "RR"):
        assert receipt_kind(_row(f"{code}.LSE", "A UK Company", market="LSE")) == "", code


def test_a_london_gdr_is_a_receipt():
    """Ming Yang's London line: a GDR with no identity, which the index served at $1,000bn."""
    gdr = _row("MYSE.LSE", "Ming Yang Smart Energy Group Ltd. GDR", primary="", isin="",
               market="LSE", cap_bn=999.6)
    assert receipt_kind(gdr) == RECEIPT_LSE_GDR


def test_a_swiss_line_that_names_a_foreign_home_is_secondary():
    assert receipt_kind(_row("LLY.SW", "Eli Lilly and Company", primary="LLY.US",
                             market="SW")) == RECEIPT_SWISS_LINE
    assert receipt_kind(_row("UHRN.SW", "Swatch Group", primary="UHR.SW", market="SW")) == ""


def test_a_swiss_row_with_no_identity_is_a_line_only_when_its_name_is_listed_elsewhere():
    """NVDA.SW names nothing, but NVIDIA is in the index with an identity. CENTIEL is a real Swiss
    company and has no such twin, so it stays."""
    nvda_sw = _row("NVDA.SW", "NVIDIA Corporation", primary="", isin="", market="SW")
    nvda_us = _row("NVDA.US", "NVIDIA Corporation", market="US")
    centiel = _row("CNTL.SW", "CENTIEL N AG", primary="", isin="", market="SW")
    found = secondary_lines([nvda_sw, nvda_us, centiel])
    assert found == {"NVDA.SW": RECEIPT_SWISS_LINE}


def _vestas_and_wind_rivals():
    home = _row("VWS.CO", "Vestas Wind Systems A/S", cap_bn=31.5, sub="Heavy Electrical Equipment",
                isin="DK0061539921", market="CO", currency="DKK")
    london = _row("0NMK.LSE", "Vestas Wind Systems A/S", cap_bn=5.2,
                  sub="Heavy Electrical Equipment", primary="VWS.CO", isin="DK0061539921",
                  market="LSE")
    return home, london


def test_vestas_london_line_never_appears_as_a_peer():
    home, london = _vestas_and_wind_rivals()
    subject = _row("SUBJ.US", "Wind Subject", cap_bn=30.0, sub="Heavy Electrical Equipment")
    rivals = _fillers(13, cap_bn=30.0, sub="Heavy Electrical Equipment")
    group = peers("SUBJ.US", rows=[subject, home, london, *rivals])
    assert "0NMK.LSE" not in _tickers(group)
    assert "VWS.CO" in _tickers(group)          # ...but the company itself still is


def test_eatons_brazilian_receipt_never_stands_in_for_eaton():
    """E1TN34.SA has no identity, so nothing merged it into ETN.US: it was a second Eaton, and
    in Siemens Energy's cohort it stood as an Eaton of its own."""
    eaton = _row("ETN.US", "Eaton Corporation PLC", cap_bn=165.0, sub="Industrial Machinery")
    receipt = _bdr("E1TN34", "Eaton Corporation plc", cap_bn=169.8, sub="Industrial Machinery")
    subject = _row("SUBJ.XETRA", "Machinery Subject", cap_bn=140.0, sub="Industrial Machinery")
    rivals = _fillers(12, cap_bn=140.0, sub="Industrial Machinery")
    members = _tickers(peers("SUBJ.XETRA", rows=[subject, eaton, receipt, *rivals]))
    assert "ETN.US" in members and "E1TN34.SA" not in members


def test_a_receipt_looked_up_by_its_own_symbol_is_answered_for_the_company():
    eaton = _row("ETN.US", "Eaton Corporation PLC", cap_bn=165.0)
    receipt = _bdr("E1TN34", "Eaton Corporation plc", cap_bn=169.8)
    rows = [eaton, receipt, *_fillers(13, cap_bn=165.0)]
    group = peers("E1TN34.SA", rows=rows)
    assert group.subject.ticker == "ETN.US"
    assert any("E1TN34.SA is a Brazilian receipt (BDR)" in r for r in group.reasons)
    assert "E1TN34.SA" not in _tickers(group)


def test_the_cohort_report_counts_skipped_receipts_by_kind():
    subject = _row("SUBJ.US", "Subject", cap_bn=100.0)
    rows = [subject, *_fillers(13), _bdr("A1MD34", "Some Co"), _bdr("AVGO34", "Other Co"),
            _row("AMD.TO", "AMD CDR (CAD Hedged)", primary="", isin="", market="TO")]
    line = next(r for r in peers("SUBJ.US", rows=rows).reasons if "secondary trading line" in r)
    assert line.startswith("3 candidate(s) skipped")
    assert f"{RECEIPT_BDR} 2" in line and f"{RECEIPT_CDR} 1" in line


def test_status_counts_receipts_by_kind_and_says_which_leave_a_company_out(tmp_path):
    store = IndexStore(tmp_path)
    eaton = _row("ETN.US", "Eaton Corporation PLC")
    twin = _bdr("E1TN34", "Eaton Corporation plc")               # its company has an own line
    orphan = _bdr("A1OS34", "A. O. Smith Corporation")           # the ONLY line of its company
    store.save([eaton, twin, orphan])
    out = status(store)
    assert out.receipts == {RECEIPT_BDR: 2}
    assert out.receipts_sole == 1
    text = " ".join(out.lines())
    assert "2 secondary trading line(s)" in text and "1 of them are the ONLY line" in text
    assert f"{RECEIPT_BDR}: 2" in text


# =========================================================================== #
# PEER-SIZE-SANITY-1
# =========================================================================== #
def _lines(company="Vestas Wind Systems A/S", **caps_bn):
    """One company, several lines, each ``TICKER=cap_bn``; the first is the home line."""
    rows = []
    home = next(iter(caps_bn)).replace("_", ".")
    for i, (ticker, cap) in enumerate(caps_bn.items()):
        code = ticker.replace("_", ".")
        rows.append(_row(code, company, cap_bn=cap, primary=home,
                         isin=f"{home}-ISIN{i:04d}", market=code.rpartition(".")[2]))
    return rows


def test_a_row_far_from_a_consensus_of_the_companys_other_lines_is_size_suspect():
    """Vestas: $31.5bn at home and $31.0bn on Xetra, $5.2bn on a third non-receipt line."""
    rows = _lines(VWS_CO=31.5, VWSB_XETRA=31.0, VWDRY_US=5.2)
    flagged = size_suspects(rows)
    assert list(flagged) == ["VWDRY.US"]
    assert "size suspect" in flagged["VWDRY.US"] and "$5.2bn" in flagged["VWDRY.US"]


def test_two_lines_that_disagree_cannot_say_which_is_wrong_so_neither_is_flagged():
    """The real shape: RR.LSE (GBX) reads $1.6bn and RRU.XETRA $157.9bn. Rolls-Royce is about
    GBP 119bn, so it is the HOME line that is wrong - flagging the non-home line, as the first
    version of this rule did, excluded the correct row for every UK company."""
    rows = _lines("Rolls-Royce Holdings PLC", RR_LSE=1.6, RRU_XETRA=157.9)
    assert size_suspects(rows) == {}
    assert size_disputes(rows) == [["RR.LSE", "RRU.XETRA"]]


def test_three_figures_that_all_disagree_flag_nothing_but_are_reported_as_disputed():
    rows = _lines(A_US=1.0, B_US=10.0, C_US=100.0)
    assert size_suspects(rows) == {}
    assert len(size_disputes(rows)) == 1


def test_a_company_with_one_sized_line_is_never_flagged_or_disputed():
    (only,) = _lines(VWS_CO=31.5)
    assert size_suspects([only]) == {} and size_disputes([only]) == []


def test_a_line_with_no_size_is_not_a_witness():
    """No cap is not a small cap: it neither votes nor is judged (null is not false)."""
    rows = _lines(VWS_CO=31.5, VWSB_XETRA=31.0, VWDRY_US=None)
    assert size_suspects(rows) == {}


def test_receipts_do_not_vote():
    """Two London 0xxx lines that share one error must not outvote the correct home line."""
    home = _row("VWS.CO", "Vestas Wind Systems A/S", cap_bn=31.5, primary="VWS.CO",
                isin="DK0061539921", market="CO")
    xetra = _row("VWSB.XETRA", "Vestas Wind Systems A/S", cap_bn=31.0, primary="VWS.CO",
                 isin="DK0061539921", market="XETRA")
    bad_a = _row("0NMK.LSE", "Vestas Wind Systems A/S", cap_bn=5.2, primary="VWS.CO",
                 isin="DK0061539921", market="LSE")
    bad_b = _row("0NML.LSE", "Vestas Wind Systems A/S", cap_bn=5.1, primary="VWS.CO",
                 isin="DK0061539921", market="LSE")
    assert size_suspects([home, xetra, bad_a, bad_b]) == {}
    assert size_disputes([home, xetra, bad_a, bad_b]) == []       # they are simply not evidence


def test_the_factor_is_configurable():
    rows = _lines(A_US=100.0, B_US=100.0, C_US=17.0)          # 5.9x apart
    assert list(size_suspects(rows)) == ["C.US"]
    assert size_suspects(rows, factor=7.0) == {}


def test_the_default_factor_is_five_and_the_config_carries_it():
    assert load_config()["size_suspect_factor"] == 5.0
    assert "size_suspect_factor" in open("market_index.yaml", encoding="utf-8").read()


def test_a_size_suspect_row_is_kept_out_of_the_pool_and_counted_in_the_report():
    subject = _row("SUBJ.US", "Subject", cap_bn=30.0)
    lines = _lines("Wind Co", VWS_CO=31.5, VWSB_XETRA=31.0, VWDRY_US=5.2)
    group = peers("SUBJ.US", rows=[subject, *lines, *_fillers(13, cap_bn=30.0)])
    members = _tickers(group)
    assert "VWDRY.US" not in members and "VWS.CO" in members
    assert any("1 candidate(s) skipped: size suspect" in r for r in group.reasons)


def test_a_size_suspect_subject_is_kept_but_its_doubtful_size_is_said():
    lines = _lines("Wind Co", VWS_CO=5.2, VWSB_XETRA=31.0, VWDRY_US=31.5)   # the HOME line is odd
    rows = [*lines, *_fillers(13, cap_bn=5.0)]
    group = peers("VWS.CO", rows=rows)
    assert group.subject.ticker == "VWS.CO" and group.available
    assert any("VWS.CO: size suspect" in r and "as it stands" in r for r in group.reasons)


def test_status_counts_size_suspect_rows_and_undecidable_companies(tmp_path):
    store = IndexStore(tmp_path)
    store.save([*_lines("Wind Co", VWS_CO=31.5, VWSB_XETRA=31.0, VWDRY_US=5.2),
                *_lines("Two Line Co", RR_LSE=1.6, RRU_XETRA=157.9)])
    out = status(store)
    assert out.size_suspect == 1 and out.size_suspect_examples == ["VWDRY.US"]
    assert out.size_disputed == 1
    text = " ".join(out.lines())
    assert "1 row(s) size suspect" in text and "cannot be adjudicated" in text


# =========================================================================== #
# PEER-LABEL-MATCH-1
# =========================================================================== #
def _no_gics(ticker, name="", *, eodhd="Semiconductors", cap_bn=100.0):
    """A row with an EODHD industry and NO GICS label - what the provider serves for many
    secondary and thinly-covered lines (NVDA.SW read 'Semiconductors' and no GICS at all)."""
    return _row(ticker, name, sub="", industry="", eodhd=eodhd, cap_bn=cap_bn)


def test_a_row_with_no_gics_label_is_not_matched_against_gics_names_by_wording():
    """'Semiconductors' is BOTH an EODHD industry and a GICS sub-industry. The ladder used to fall
    back from one to the other, so these 14 GICS-less rows counted as GICS semiconductors."""
    subject = _row("SUBJ.US", "Subject", sub="Semiconductors", eodhd="Chip Design")
    look_alikes = [_no_gics(f"LOOK{i:02d}.US") for i in range(14)]
    group = peers("SUBJ.US", rows=[subject, *look_alikes])
    assert not group.available
    assert all(m.ticker not in _tickers(group) for m in look_alikes)


def test_a_subject_with_no_gics_label_is_matched_on_eodhd_labels_only():
    subject = _no_gics("SUBJ.US", "Subject")
    eodhd_peers = [_no_gics(f"EOD{i:02d}.US") for i in range(12)]
    gics_only = [_row(f"GICS{i:02d}.US", sub="Semiconductors", industry="Semiconductors",
                      eodhd="") for i in range(5)]
    group = peers("SUBJ.US", rows=[subject, *eodhd_peers, *gics_only])
    assert group.available
    assert set(_tickers(group)) == {m.ticker for m in eodhd_peers}
    assert set(group.matched_on.values()) == {"EODHD"}
    assert any("EODHD industry label only" in r for r in group.reasons)


def test_a_member_matched_on_gics_alone_says_so():
    subject = _row("SUBJ.US", "Subject", eodhd="Chip Design")
    group = peers("SUBJ.US", rows=[subject, *_fillers(12)])       # fillers: EODHD "Semiconductors"
    assert group.available and set(group.matched_on.values()) == {"GICS"}


def test_the_providers_other_is_not_a_label():
    """EODHD files 1,400-odd unrelated rows under industry 'Other'; two of them are not peers."""
    subject = _no_gics("SUBJ.US", "Subject", eodhd="Other")
    others = [_no_gics(f"OTH{i:02d}.US", eodhd="Other") for i in range(14)]
    group = peers("SUBJ.US", rows=[subject, *others])
    assert not group.available


def test_the_report_states_the_step_and_the_distinct_company_count():
    subject = _row("SUBJ.US", "Subject")
    group = peers("SUBJ.US", rows=[subject, *_fillers(13)])
    assert group.step == 1 and group.distinct_companies == 13
    sentence = group.sentence()
    assert "found at step 1 of 3" in sentence and "13 distinct companies" in sentence


def test_a_cohort_found_by_a_wider_rung_names_that_step():
    """Fillers at 8x the subject's size: outside the tight band (step 1), inside the wide one."""
    subject = _row("SUBJ.US", "Subject", cap_bn=10.0)
    group = peers("SUBJ.US", rows=[subject, *_fillers(13, cap_bn=80.0)])
    assert group.step == 2 and "found at step 2 of 3" in group.sentence()


def test_the_industry_rung_is_step_three():
    subject = _row("SUBJ.US", "Subject", sub="Alpha", industry="Group", eodhd="Alpha Ind")
    cousins = [_row(f"COUS{i:02d}.US", sub=f"Beta{i}", industry="Group", eodhd=f"Beta Ind {i}",
                    cap_bn=100.0) for i in range(13)]
    group = peers("SUBJ.US", rows=[subject, *cousins])
    assert group.step == 3 and group.rung.startswith("industry")


def test_the_distinct_count_is_by_company_not_by_ticker():
    """A cohort's count is companies: after dedup no two members share an identity."""
    subject = _row("SUBJ.US", "Subject")
    twin_a = _row("TWIN.US", "Twin Co", primary="TWIN.US", isin="US0000000001")
    twin_b = _row("TWIN.XETRA", "Twin Co", primary="TWIN.US", isin="DE0000000001",
                  market="XETRA")
    group = peers("SUBJ.US", rows=[subject, twin_a, twin_b, *_fillers(12)])
    assert len(group.members) == 13 and group.distinct_companies == 13
    assert sum(1 for m in _tickers(group) if m.startswith("TWIN")) == 1


def test_the_snapshot_records_the_step_and_the_distinct_count():
    subject = _row("SUBJ.US", "Subject")
    snap = peer_snapshot(peers("SUBJ.US", rows=[subject, *_fillers(13)]))
    assert snap["step"] == 1 and snap["distinct_companies"] == 13


# =========================================================================== #
# PEER-LABEL-RECALL-1
# =========================================================================== #
SIM = "Specialty Industrial Machinery"        # EODHD's industry for the whole electrical family
MACH = "Industrial Machinery & Supplies & Components"
HEAVY = "Heavy Electrical Equipment"
COMPONENTS = "Electrical Components & Equipment"


def _co(ticker, name, cap_bn, *, sub, gics_industry, eodhd=SIM, **kw):
    return _row(ticker, name, cap_bn=cap_bn, sub=sub, industry=gics_industry, eodhd=eodhd, **kw)


def test_a_wrong_label_in_one_system_does_not_hide_a_rival_the_other_system_finds():
    """The Schneider / Siemens shape: GICS files them as machinery, EODHD files them with Eaton."""
    subject = _co("SUBJ.US", "Subject", 165, sub=COMPONENTS, gics_industry="Electrical Equipment")
    gics_only = [_co(f"GICS{i}.US", f"Gics Rival {i}", 100 + i * 20, sub=COMPONENTS,
                     gics_industry="Electrical Equipment", eodhd="Electrical Parts")
                 for i in range(6)]
    eodhd_only = [_co(f"EOD{i}.US", f"Eodhd Rival {i}", 100 + i * 20, sub=MACH,
                      gics_industry="Machinery") for i in range(6)]
    group = peers("SUBJ.US", rows=[subject, *gics_only, *eodhd_only])
    assert group.available and group.step == 1 and len(group.members) == 12
    assert {group.matched_on[m] for m in _tickers(group)} == {"GICS", "EODHD"}
    assert any("matched on: GICS only 6, EODHD label only 6, both 0" in r
               for r in group.reasons)


def _eaton_world():
    eaton = _co("ETN.US", "Eaton Corporation PLC", 165.0, sub=COMPONENTS,
                gics_industry="Electrical Equipment", market="US")
    same_gics = [_co(f"{c}.US", n, cap, sub=COMPONENTS, gics_industry="Electrical Equipment",
                     eodhd="Electrical Parts", market="US")
                 for c, n, cap in (("EMR", "Emerson Electric", 75), ("HUBB", "Hubbell", 52),
                                   ("AME", "Ametek", 45), ("ROK", "Rockwell Automation", 42),
                                   ("PWR", "Quanta", 60), ("VRT", "Vertiv", 55))]
    same_eodhd = [_co("SU.PA", "Schneider Electric S.E.", 190.8, sub=MACH,
                      gics_industry="Machinery", market="PA"),
                  _co("SIE.XETRA", "Siemens Aktiengesellschaft", 236.4, sub=MACH,
                      gics_industry="Machinery", market="XETRA"),
                  *[_co(f"{c}.ST", n, cap, sub=MACH, gics_industry="Machinery", market="ST")
                    for c, n, cap in (("ATCO-A", "Atlas Copco AB Series A", 103.6),
                                      ("SAND", "Sandvik AB", 49.1),
                                      ("PH", "Parker-Hannifin", 119.0),
                                      ("ITW", "Illinois Tool Works", 76.7))]]
    return [eaton, *same_gics, *same_eodhd]


def test_eatons_cohort_reaches_twelve_with_schneider_and_siemens_present():
    """Six by GICS and six by EODHD label: neither system fills the floor alone, together they
    do, at the tight step, and the two names the owner looked for are in it."""
    rows = _eaton_world()
    only_gics = [r for r in rows if r.gics_subindustry == COMPONENTS and r.ticker != "ETN.US"]
    assert len(only_gics) == 6                              # the floor is 12: GICS alone fails
    group = peers("ETN.US", rows=rows, overrides=load_label_overrides())
    members = _tickers(group)
    assert group.available and len(members) >= 12 and group.step == 1
    assert "SU.PA" in members and "SIE.XETRA" in members
    assert group.distinct_companies == len(members)


def test_siemens_energys_cohort_holds_ge_vernova_and_vestas():
    """ENR.XETRA is filed as Industrial Machinery. The override puts it with its real rivals and
    the report says so; Vestas is outside the tight band, so the cohort is found at step 2."""
    subject = _co("ENR.XETRA", "Siemens Energy AG", 136.9, sub=MACH, gics_industry="Machinery",
                  market="XETRA")
    gev = _co("GEV.US", "GE Vernova LLC", 250.4, sub=HEAVY, gics_industry="Electrical Equipment")
    vestas = _co("VWS.CO", "Vestas Wind Systems A/S", 31.5, sub=HEAVY,
                 gics_industry="Electrical Equipment", market="CO")
    others = [_co(f"HEAVY{i}.US", f"Heavy Rival {i}", 20.0 + i, sub=HEAVY,
                  gics_industry="Electrical Equipment", eodhd="Grid Equipment")
              for i in range(10)]
    group = peers("ENR.XETRA", rows=[subject, gev, vestas, *others],
                  overrides=load_label_overrides())
    assert group.available and group.step == 2
    assert "GEV.US" in _tickers(group) and "VWS.CO" in _tickers(group)
    assert any(r.startswith("label overridden: ENR.XETRA") and HEAVY in r for r in group.reasons)
    assert group.overridden[0]["ticker"] == "ENR.XETRA" and group.overridden[0]["role"] == "subject"


def test_an_override_can_bring_in_a_rival_the_eodhd_label_does_not():
    """Micron's US line is filed as Semiconductor Materials & Equipment. Without the correction
    it is not a semiconductor peer; with it, it is, and the report says the label was changed."""
    subject = _row("TSM.US", "TSMC", cap_bn=100, sub="Semiconductors", eodhd="Chip Design")
    micron = _row("MU.US", "Micron Technology Inc", cap_bn=110,
                  sub="Semiconductor Materials & Equipment", eodhd="Semiconductors")
    fillers = _fillers(12, cap_bn=100.0, eodhd="Chip Design")
    rows = [subject, micron, *fillers]
    without = peers("TSM.US", rows=rows, overrides=[])
    with_it = peers("TSM.US", rows=rows, overrides=load_label_overrides())
    assert "MU.US" not in _tickers(without) and without.step == 1      # 12 fill the floor alone
    assert "MU.US" in _tickers(with_it) and with_it.step == 1
    line = next(r for r in with_it.reasons if r.startswith("label overridden: MU.US"))
    assert "Semiconductor Materials & Equipment -> Semiconductors" in line
    assert "2026-09-25" in line


def test_every_use_of_an_override_is_reported_and_an_unused_one_is_not():
    subject = _row("SUBJ.US", "Subject", sub="Semiconductors")
    rows = [subject, *_fillers(12), _row("MU.US", "Micron", sub="Semiconductors")]
    group = peers("SUBJ.US", rows=rows, overrides=load_label_overrides())
    assert [o["ticker"] for o in group.overridden] == ["MU.US"]
    assert not any("ENR.XETRA" in r for r in group.reasons)


def test_overrides_are_not_applied_to_a_table_handed_in_directly_by_default():
    """A fabricated table must never meet a real correction by accident (MU.US is a real key)."""
    subject = _row("SUBJ.US", "Subject", sub="Semiconductors", eodhd="Chip Design")
    micron = _row("MU.US", "Micron", sub="Semiconductor Materials & Equipment",
                  eodhd="Semiconductors")
    group = peers("SUBJ.US", rows=[subject, micron, *_fillers(11, eodhd="Chip Design")])
    assert group.overridden == []


def test_applying_an_override_never_mutates_the_callers_rows():
    micron = _row("MU.US", "Micron", sub="Semiconductor Materials & Equipment")
    corrected, applied = apply_label_overrides([micron], load_label_overrides())
    assert corrected[0].gics_subindustry == "Semiconductors"
    assert micron.gics_subindustry == "Semiconductor Materials & Equipment"
    assert applied["MU.US"][0] == "Semiconductor Materials & Equipment"


def test_a_corrected_rows_industry_follows_from_the_industry_its_new_sub_industry_carries():
    peer = _row("GEV.US", sub=HEAVY, industry="Electrical Equipment")
    wrong = _row("ENR.XETRA", sub=MACH, industry="Machinery")
    corrected, _ = apply_label_overrides([peer, wrong], load_label_overrides())
    assert next(r for r in corrected if r.ticker == "ENR.XETRA").gics_industry == \
        "Electrical Equipment"


def test_the_shipped_override_file_is_complete_and_seeded_as_specified():
    seeded = {o.ticker: o for o in load_label_overrides()}
    assert seeded["ENR.XETRA"].gics_subindustry == HEAVY
    assert seeded["SU.PA"].gics_subindustry == HEAVY
    assert seeded["MU.US"].gics_subindustry == "Semiconductors"
    assert all(o.date and o.reason for o in seeded.values())


def test_an_override_without_a_reason_or_a_date_is_refused(tmp_path):
    bad = tmp_path / "o.yaml"
    bad.write_text("overrides:\n  - ticker: X.US\n    gics_subindustry: Foo\n"
                   "    date: 2026-09-25\n", encoding="utf-8")
    try:
        load_label_overrides(bad)
    except MarketIndexError as exc:
        assert "reason" in str(exc)
    else:
        raise AssertionError("an override with no reason must be refused")


def test_a_missing_override_file_is_no_overrides(tmp_path):
    assert load_label_overrides(tmp_path / "nothing.yaml") == []


# =========================================================================== #
# PEER-DEDUP-1, part 2 - lines no handle links (measured on the real table)
# =========================================================================== #
def test_two_lines_that_each_name_themselves_as_primary_are_one_company_by_name_and_size():
    """ASML.AS and ASML.US: different ISINs, each its own primary. In TSMC's cohort twice."""
    nl = _row("ASML.AS", "ASML Holding N.V.", cap_bn=653.2, isin="NL0010273215", market="AS")
    us = _row("ASML.US", "ASML Holding NV ADR", cap_bn=645.3, isin="USN070592100", market="US")
    assert len(company_groups([nl, us])) == 2                       # no shared handle...
    assert len(company_groups([nl, us], link_by_name=True)) == 1    # ...but one company
    kept, dropped = one_row_per_company([nl, us], link_by_name=True)
    assert [r.ticker for r in kept] == ["ASML.AS"] and dropped == 1


def test_share_classes_of_one_company_are_one_peer():
    a = _row("ATCO-A.ST", "Atlas Copco AB Series A", cap_bn=103.6, isin="SE0017486889")
    b = _row("ATCO-B.ST", "Atlas Copco AB Series B", cap_bn=89.9, isin="SE0017486897")
    assert len(company_groups([a, b], link_by_name=True)) == 1


def test_different_companies_that_share_a_short_name_are_not_merged():
    """APA Corp (US, $15.7bn) and APA Group (Australia, $10.2bn) are 1.54x apart; Argan Inc and
    Argan SA are 2.5x apart. The size guard is what stops a name from merging strangers."""
    apa_us = _row("APA.US", "APA Corporation", cap_bn=15.7, isin="US03743Q1085")
    apa_au = _row("APA.AU", "APA Group", cap_bn=10.2, isin="AU000000APA1")
    argan_us = _row("AGX.US", "Argan Inc", cap_bn=5.4, isin="US04010E1091")
    argan_fr = _row("ARG.PA", "Argan SA", cap_bn=2.1, isin="FR0010481960")
    assert len(company_groups([apa_us, apa_au, argan_us, argan_fr], link_by_name=True)) == 4


def test_a_line_with_no_size_is_never_linked_by_name():
    sized = _row("ACME.US", "Acme Holdings", cap_bn=10.0)
    unsized = _row("ACME.MX", "Acme Holdings", cap_bn=None)
    assert len(company_groups([sized, unsized], link_by_name=True)) == 2


def test_the_name_link_is_off_by_default_so_the_size_test_still_sees_separate_lines():
    a = _row("ASML.AS", "ASML Holding N.V.", cap_bn=653.2, isin="NL0010273215")
    b = _row("ASML.US", "ASML Holding NV ADR", cap_bn=645.3, isin="USN070592100")
    assert len(company_groups([a, b])) == 2


def test_a_cohort_never_holds_one_company_under_two_tickers_even_when_no_handle_links_them():
    """Eaton's real cohort held Illinois Tool Works as ITW.US and ILT.XETRA, Parker-Hannifin as
    PH.US and PAR.XETRA, Atlas Copco as ATCO-A and ATCO-B: 18 'peers', 15 companies."""
    subject = _row("SUBJ.US", "Subject", cap_bn=100.0)
    itw_us = _row("ITW.US", "Illinois Tool Works Inc", cap_bn=76.7, isin="US4523081093")
    itw_de = _row("ILT.XETRA", "Illinois Tool Works Inc.", cap_bn=76.5, isin="US4523081093x",
                  primary="ILT.XETRA", market="XETRA")
    group = peers("SUBJ.US", rows=[subject, itw_us, itw_de, *_fillers(12)])
    assert sum(1 for m in _tickers(group) if m.startswith(("ITW", "ILT"))) == 1
    assert group.distinct_companies == len(group.members) == 13


def test_a_line_of_the_subjects_own_company_found_only_by_name_is_not_its_peer():
    """ASML.US, asked about ASML.AS, is the same company however the provider links it."""
    nl = _row("ASML.AS", "ASML Holding N.V.", cap_bn=653.2, isin="NL0010273215", market="AS")
    us = _row("ASML.US", "ASML Holding NV ADR", cap_bn=645.3, isin="USN070592100", market="US")
    rivals = [_row(f"RIV{i:02d}.US", cap_bn=650.0 + i) for i in range(12)]
    group = peers("ASML.AS", rows=[nl, us, *rivals])
    assert group.available and "ASML.US" not in _tickers(group)
    assert any("never its own peer" in r for r in group.reasons)


def test_placeholder_names_are_not_a_company_name():
    """ASX deferred-settlement lines are all called 'Ordinary Fully Paid Deferred Settlement': four
    different companies read as one by name, and none of them by that name."""
    a = _row("AHNDA.AU", "Ordinary Fully Paid Deferred Settlement", cap_bn=0.01)
    b = _row("EVRDD.AU", "Ordinary Fully Paid Deferred Settlement", cap_bn=0.01)
    assert len(company_groups([a, b], link_by_name=True)) == 2
