"""BATCH 19A (2026-10) - peer-group fixes from the 2026-10-05 hand-test round.

    PEER-DEDUP-2     the same company standing 2-3 times in a peer group (Hyundai 005380/005385/
                     005389.KO, Draegerwerk DRW3/DRW8.XETRA, BBVA.MC+BBVA.US, INGA.AS+ING.US)
    PEER-FUND-2      BB Biotech AG (BION.SW), a closed-end investment company, ranked among Viking's
                     biotech peers
    PEER-LABEL-2     a one-label match is admitted only when the OTHER label has nothing to say
                     (absent), never when it contradicts it (NovoCure's peers held Nutex Health,
                     Aveanna, Sonida, P3, CLASSYS "Coal & Consumable Fuels")
    PEERS-RANK-RULE  a peers-table rank column with fewer than 3 ranked names reads "too few to
                     rank"; one with none is dropped

Every table here is fabricated, with the live names and shapes; nothing reaches the real adapter or
the real index.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aristos_council.market_index import (SINGLE_LABEL_STRICT_BELOW_USD, IndexRow, distinct_companies,
                                          is_fund, load_fund_exclusions, peers, fund_reason,
                                          secondary_lines)
from aristos_council.peer_table import (TOO_FEW_TO_RANK, peer_frame_records, rank_columns,
                                        rank_display)

SNAPSHOT = "2026-10-05"


def _mk(ticker, name, *, cap_bn, market="US", currency="USD", sector="Healthcare",
        industry="Medical Devices", gics_sector="Health Care", gics_sub="Health Care Equipment",
        primary=None, isin=None, gics_industry="Health Care Equipment & Supplies") -> IndexRow:
    cap = None if cap_bn is None else cap_bn * 1e9
    return IndexRow(
        ticker=ticker, yahoo_ticker=ticker.rpartition(".")[0] or ticker, name=name,
        exchange=market, market=market, currency=currency, sector=sector, industry=industry,
        gics_sector=gics_sector, gics_industry=gics_industry, gics_subindustry=gics_sub,
        market_cap=cap, market_cap_usd=cap, market_cap_usd_source="computed",
        primary_ticker=ticker if primary is None else primary,
        isin=f"XX{abs(hash(ticker)) % 10**10:010d}" if isin is None else isin,
        fetched_at=SNAPSHOT, source="eodhd")


def _crowd(n, *, cap_bn=10.0, **kw):
    return [_mk(f"RIV{i:02d}.US", f"Rival Company {i}", cap_bn=cap_bn + i * 0.1, **kw)
            for i in range(n)]


def _peers(subject, rows):
    return peers(subject.ticker, rows=[subject, *rows], overrides=[], aliases=[],
                 size_corrections=[], exclude_markets=())


def _tickers(group):
    return [m.ticker for m in group.members]


# =========================================================================== #
# PEER-DEDUP-2
# =========================================================================== #
def test_peer_dedup_hyundai_preference_series_fold_into_the_ordinary_line():
    """005385 'Pfd. Series 1' and 005389 'S3 Pref' read 'hyundai motor 1' / 'hyundai motor s3', so only
    the series literally named 'Pref' used to fold."""
    hyundai = [
        _mk("005380.KO", "Hyundai Motor Co. Ltd.", cap_bn=67.9, market="KO", currency="KRW"),
        _mk("005385.KO", "Hyundai Motor Co. Ltd. Pfd. Series 1", cap_bn=34.0, market="KO", currency="KRW"),
        _mk("005387.KO", "Hyundai Motor Co Pref", cap_bn=34.1, market="KO", currency="KRW"),
        _mk("005389.KO", "Hyundai Motor S3 Pref", cap_bn=33.5, market="KO", currency="KRW"),
    ]
    subject = _mk("SUBJ.US", "Subject Motors", cap_bn=50.0)
    group = _peers(subject, [*hyundai, *_crowd(12, cap_bn=45.0)])
    assert [t for t in _tickers(group) if t.endswith(".KO")] == ["005380.KO"]
    assert group.distinct_companies == len(group.members)


def test_peer_dedup_a_series_number_is_dropped_from_the_preference_row_only():
    ordinary = _mk("005380.KO", "Hyundai Motor Co. Ltd.", cap_bn=40.0, market="KO")
    lone = _mk("000155.KO", "Doosan Pref Shs", cap_bn=6.0, market="KO")
    other = _mk("000150.KO", "Doosan Bobcat Inc", cap_bn=3.0, market="KO")
    series = [_mk("005385.KO", "Hyundai Motor Co. Ltd. Pfd. Series 1", cap_bn=20.0, market="KO"),
              _mk("005389.KO", "Hyundai Motor S3 Pref", cap_bn=20.0, market="KO")]
    found = secondary_lines([ordinary, *series, lone, other])
    assert set(found) == {"005385.KO", "005389.KO"}        # Doosan stays: different company name


def test_peer_dedup_draegerwerk_preference_line_is_one_company_with_the_ordinary():
    """DRW3.XETRA '(pref)' $2.45bn beside DRW8.XETRA $1.85bn: 1.33x apart, beyond the name link, no
    shared ISIN or primary."""
    rows = [_mk("DRW8.XETRA", "Drägerwerk AG & Co. KGaA", cap_bn=1.85, market="XETRA", currency="EUR"),
            _mk("DRW3.XETRA", "Drägerwerk AG & Co. KGaA (pref)", cap_bn=2.45, market="XETRA",
                currency="EUR", primary="DRW3.F")]
    subject = _mk("SUBJ.US", "Subject Devices", cap_bn=2.0)
    group = _peers(subject, [*rows, *_crowd(12, cap_bn=2.0)])
    dr = [t for t in _tickers(group) if t.startswith("DRW")]
    assert dr == ["DRW8.XETRA"]
    assert group.distinct_companies == len(group.members)
    assert any("preference share line 1" in r for r in group.reasons)


def test_peer_dedup_a_plain_name_is_never_folded_on_a_size_guess():
    """No preference wording: two same-exchange lines of one name 1.33x apart stay as they were."""
    rows = [_mk("AAA1.XETRA", "Example AG", cap_bn=1.85, market="XETRA"),
            _mk("AAA3.XETRA", "Example AG", cap_bn=2.45, market="XETRA")]
    assert secondary_lines(rows, include_name_prefs=True) == {}


def _bank(ticker, name, cap_bn, **kw):
    return _mk(ticker, name, cap_bn=cap_bn, sector="Financial Services",
               industry="Banks - Diversified", gics_sector="Financials",
               gics_sub="Diversified Banks", gics_industry="Banks", **kw)


def test_peer_dedup_bbva_and_ing_adrs_fold_into_their_home_lines():
    """BBVA.US is 'Banco Bilbao Viscaya Argentaria SA ADR' (the provider's spelling, its own ISIN,
    names itself primary); ING.US is 'ING Group NV ADR' against 'ING Groep NV'. JPM's peers held
    both companies twice."""
    banks = [
        _bank("BBVA.MC", "Banco Bilbao Vizcaya Argentaria SA", 159.5, market="MC", currency="EUR",
              isin="ES0113211835"),
        _bank("BBVA.US", "Banco Bilbao Viscaya Argentaria SA ADR", 156.5, market="NYSE",
              isin="US05946K1016"),
        _bank("INGA.AS", "ING Groep NV", 103.7, market="AS", currency="EUR", isin="NL0011821202"),
        _bank("ING.US", "ING Group NV ADR", 103.7, market="NYSE", isin="US4568371037"),
    ]
    fillers = [_bank(f"BNK{i:02d}.US", f"Other Bank {i}", 120.0 + i) for i in range(12)]
    subject = _bank("JPM.US", "JPMorgan Chase & Co", 800.0)
    group = _peers(subject, [*banks, *fillers])
    members = _tickers(group)
    assert members.count("BBVA.MC") == 1 and "BBVA.US" not in members
    assert members.count("INGA.AS") == 1 and "ING.US" not in members
    assert group.distinct_companies == len(group.members)


def test_peer_dedup_an_adr_is_not_joined_to_a_different_company_of_similar_name_and_size():
    """Same-exchange, or far apart in size, or an unalike name: no link."""
    from aristos_council.market_index import company_groups
    home = _bank("AAA.MC", "Banco Alfa SA", 100.0, market="MC")
    far = _bank("AAA.US", "Banco Alfa SA ADR", 300.0, market="NYSE")           # 3x: not the same line
    unalike = _bank("ZZZ.US", "Banco Zeta Holding ADR", 100.0, market="NYSE")
    groups = company_groups([home, far, unalike], link_by_name=True)
    assert len(groups) == 3


# =========================================================================== #
# PEER-FUND-2
# =========================================================================== #
def test_peer_fund_bb_biotech_is_never_a_peer():
    bion = _mk("BION.SW", "BB Biotech AG", cap_bn=3.47, market="SW", currency="CHF",
               industry="Biotechnology", gics_sub="Biotechnology", gics_industry="Biotechnology")
    subject = _mk("VKTX.US", "Viking Therapeutics Inc", cap_bn=4.0, industry="Biotechnology",
                  gics_sub="Biotechnology", gics_industry="Biotechnology")
    group = _peers(subject, [bion, *_crowd(12, cap_bn=4.0, industry="Biotechnology",
                                           gics_sub="Biotechnology", gics_industry="Biotechnology")])
    assert "BION.SW" not in _tickers(group)
    assert any("fund, not a company" in r for r in group.reasons)
    assert is_fund(bion) and "fund_exclusions" in fund_reason(bion)


def test_peer_fund_investment_company_needs_an_asset_management_classification():
    fund = _mk("HANA.LSE", "Hansa Investment Company Ltd", cap_bn=0.87, market="LSE",
               industry="Asset Management", gics_sub="Asset Management & Custody Banks",
               sector="Financial Services", gics_sector="Financials")
    operator = _mk("OPCO.US", "Pacific Investment Company Holdings", cap_bn=0.87,
                   industry="Packaging", gics_sub="Paper Packaging")
    assert is_fund(fund) and not is_fund(operator)


def test_peer_fund_exclusion_file_is_dated_and_every_entry_has_a_reason():
    entries = load_fund_exclusions()
    assert "BION.SW" in entries
    for ticker, entry in entries.items():
        assert entry["date"] and entry["reason"], ticker


def test_peer_fund_exclusion_file_rejects_an_entry_without_a_reason(tmp_path):
    from aristos_council.market_index import MarketIndexError
    bad = tmp_path / "f.yaml"
    bad.write_text("exclusions:\n  - ticker: X.US\n    date: 2026-10-05\n", encoding="utf-8")
    with pytest.raises(MarketIndexError):
        load_fund_exclusions(bad)


# =========================================================================== #
# PEER-LABEL-2
# =========================================================================== #
def _small_subject():
    return _mk("NVCR.US", "Novocure Ltd", cap_bn=1.9)


def test_peer_label_below_5bn_a_one_label_match_with_a_contradicting_other_label_is_skipped():
    """Nutex Health: GICS says Health Care Equipment, EODHD says Medical Care Facilities."""
    nutex = _mk("NUTX.US", "Nutex Health Inc", cap_bn=1.4, industry="Medical Care Facilities")
    group = _peers(_small_subject(), [nutex, *_crowd(12, cap_bn=1.9)])
    assert "NUTX.US" not in _tickers(group)
    assert group.skipped_contradicted == ["NUTX.US"]
    assert any("1 candidate skipped: matched on one label but the other label contradicts" in r
               for r in group.reasons)


def test_peer_label_below_5bn_a_one_label_match_with_the_other_label_absent_is_kept():
    """Null is not false: no GICS at all contradicts nothing (Embla Medical, Heron Neutron)."""
    embla = _mk("EMBLA.CO", "Embla Medical hf", cap_bn=1.8, market="CO", gics_sector="",
                gics_sub="", gics_industry="")
    group = _peers(_small_subject(), [embla, *_crowd(12, cap_bn=1.9)])
    assert "EMBLA.CO" in _tickers(group)
    assert group.matched_on["EMBLA.CO"] == "EODHD"


def test_peer_label_a_sector_contradiction_is_skipped_at_any_size():
    """CLASSYS: GICS sub-industry 'Coal & Consumable Fuels' (Energy), EODHD 'Medical Devices'."""
    classys = _mk("214150.KQ", "CLASSYS Inc", cap_bn=1.5, market="KQ", gics_sector="Energy",
                  gics_sub="Coal & Consumable Fuels", gics_industry="Oil, Gas & Consumable Fuels")
    big = _mk("BIGCO.US", "Big Devices Inc", cap_bn=50.0)
    group = _peers(big, [_mk("BIG2.US", "Other Big", cap_bn=48.0, gics_sector="Energy",
                             gics_sub="Coal & Consumable Fuels",
                             gics_industry="Oil, Gas & Consumable Fuels"),
                         *_crowd(12, cap_bn=50.0)])
    assert "BIG2.US" not in _tickers(group)
    small = _peers(_small_subject(), [classys, *_crowd(12, cap_bn=1.9)])
    assert "214150.KQ" not in _tickers(small)


def test_peer_label_above_5bn_a_one_label_match_in_the_same_sector_stays_the_recall_path():
    """Siemens Energy's shape: GICS industry differs, EODHD industry agrees, same sector."""
    assert 10e9 > SINGLE_LABEL_STRICT_BELOW_USD
    subject = _mk("BIGCO.US", "Big Devices Inc", cap_bn=10.0)
    rival = _mk("RIVAL.US", "Rival Machines", cap_bn=9.0, gics_sub="Industrial Machinery")
    group = _peers(subject, [rival, *_crowd(12, cap_bn=10.0)])
    assert "RIVAL.US" in _tickers(group) and group.matched_on["RIVAL.US"] == "EODHD"


# =========================================================================== #
# PEERS-RANK-RULE
# =========================================================================== #
def _report(lens_sizes):
    """A report whose lens ``label`` ranked ``n`` names, the rest excluded."""
    votes, ranks = [], {}
    for sid, (label, n) in enumerate(lens_sizes.items()):
        votes.append(SimpleNamespace(strategy_id=f"s{sid}", label=label, votes=True))
        ranks[f"s{sid}"] = {
            "ranked": [{"ticker": f"T{i}", "position": i + 1, "verdict": "hold"} for i in range(n)],
            "excluded": [{"ticker": "OUT"}], "unrateable": [], "fetch_errors": []}
    return SimpleNamespace(votes=votes, lens_ranks=ranks)


def test_peers_rank_columns_under_three_names_read_too_few_to_rank():
    columns = rank_columns(_report({"Value + Momentum": 2, "Quality": 5}))
    by_label = {c.header: c for c in columns}
    assert "Value + Momentum rank" in by_label                      # no "(of 2)"
    assert "Quality rank (of 5)" in by_label
    few = by_label["Value + Momentum rank"]
    assert set(few.values.values()) == {TOO_FEW_TO_RANK}            # every cell, excluded included
    assert by_label["Quality rank (of 5)"].values["T0"] == 1


def test_peers_rank_columns_with_nobody_ranked_are_dropped():
    columns = rank_columns(_report({"Defensive Income": 0, "Quality": 4}))
    assert [c.header for c in columns] == ["Quality rank (of 4)"]


def test_peers_rank_exactly_three_is_a_ranking():
    columns = rank_columns(_report({"Quality": 3}))
    assert columns[0].header == "Quality rank (of 3)"


def test_peers_too_few_to_rank_displays_as_words_and_sorts_after_real_ranks():
    assert rank_display(TOO_FEW_TO_RANK) == TOO_FEW_TO_RANK
    from aristos_council.peer_table import _rank_number
    assert _rank_number(TOO_FEW_TO_RANK) > _rank_number(40)


def test_peers_a_check_column_nobody_carries_is_dropped_too():
    """EMPTY-COLUMN-2 (JPM, 2026-10-06): Forensic is "does not apply" for every bank, so its column is
    dropped like an empty voting column; a check column that does carry a mark stays."""
    report = _report({"Financials": 4, "Forensic": 0})
    report.votes[1].votes = False
    assert [c.header for c in rank_columns(report)] == ["Financials rank (of 4)"]
    report = _report({"Financials": 4, "Forensic": 3})
    report.votes[1].votes = False
    assert [c.header for c in rank_columns(report)][1] == "Forensic mark (check - does not vote)"
