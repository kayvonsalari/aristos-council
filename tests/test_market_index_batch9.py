"""BATCH 9 item 1 - CCZ.US: a bond-like security read as a $61bn company (2026-09-26).

CCZ is "Comcast Holdings Corp. 2.0% Exchangeable Subordinated Debentures due 2029" (Comcast's
ZONES). The index gave it a market cap, filed it under REIT - Residential, and it stood in Tencent's
step-4 sector group beside CMCSA.US. It is excluded by a dated correction, and `status` lists the same
PATTERN (report only) so the next one is visible. Every table here is fabricated except the shipped
correction FILE, which is read as data.
"""
from __future__ import annotations

from aristos_council.market_index import (SOURCE_EODHD_LISTING, IndexRow, IndexStore,
                                          contradicting_name_pairs, load_size_corrections, peers,
                                          status)


def _row(ticker, name, *, industry, isin, market="US", cap_bn=60.0, sector="Communication Services",
         gics_sector="Communication Services"):
    cap = cap_bn * 1e9
    return IndexRow(
        ticker=ticker, yahoo_ticker=ticker.rpartition(".")[0], name=name, exchange=market,
        market=market, currency="USD", sector=sector, industry=industry, gics_sector=gics_sector,
        gics_industry="Media", gics_subindustry="Cable & Satellite", market_cap=cap,
        market_cap_usd=cap, market_cap_usd_source="computed", primary_ticker=ticker, isin=isin,
        fetched_at="2026-09-26", source=SOURCE_EODHD_LISTING)


def test_ccz_is_excluded_by_a_dated_correction_that_says_what_it_is():
    ccz = {c.ticker: c for c in load_size_corrections()}["CCZ.US"]
    assert ccz.action == "exclude" and ccz.date == "2026-09-26" and ccz.market_cap_usd is None
    assert "not a share" in ccz.reason and "Exchangeable Subordinated Debentures" in ccz.reason
    assert "cmcsa.com" in ccz.reason                             # a public source


def _sector_group(with_ccz):
    subject = _row("TCEHY.US", "Tencent Like Ltd", industry="Internet Content & Information",
                   isin="US0000000001", cap_bn=520.0)
    crowd = [_row(f"CO{i:02d}.US", f"Company {i}", industry=f"Industry {i}",
                  isin=f"US00000001{i:02d}", cap_bn=100.0 + i) for i in range(14)]
    rows = [subject, *crowd, _row("CMCSA.US", "Comcast Corp", industry="Telecom Services",
                                  isin="US20030N1019", cap_bn=80.0)]
    if with_ccz:
        rows.append(_row("CCZ.US", "Comcast Holdings Corp", industry="REIT - Residential",
                         isin="US2003005079", cap_bn=61.1, sector="Communication Services"))
    return rows


def test_ccz_no_longer_stands_in_a_sector_group_beside_comcast():
    rows = _sector_group(with_ccz=True)
    with_fix = peers("TCEHY.US", rows=rows, overrides=[], aliases=[], exclude_markets=(),
                     size_corrections=[c for c in load_size_corrections()
                                       if c.ticker == "CCZ.US"])
    without = peers("TCEHY.US", rows=rows, overrides=[], aliases=[], exclude_markets=(),
                    size_corrections=[])
    assert "CCZ.US" in [m.ticker for m in without.members]        # the defect
    assert "CCZ.US" not in [m.ticker for m in with_fix.members]   # fixed
    assert "CMCSA.US" in [m.ticker for m in with_fix.members]     # the real Comcast stays


def test_the_pattern_finder_pairs_a_bond_like_line_with_its_company():
    pairs = contradicting_name_pairs(_sector_group(with_ccz=True))
    assert [(a.ticker, b.ticker) for a, b in pairs] == [("CCZ.US", "CMCSA.US")]


def test_same_industry_other_market_or_same_isin_is_not_a_pair():
    same_industry = [_row("AAA.US", "Alpha Corp", industry="Banks", isin="US1"),
                     _row("AAB.US", "Alpha Corp Pref", industry="Banks", isin="US2")]
    other_market = [_row("BBB.US", "Beta Corp", industry="Banks", isin="US3"),
                    _row("BBB.LSE", "Beta Corp", industry="Insurance", isin="GB4", market="LSE")]
    same_isin = [_row("CCC.US", "Gamma Corp", industry="Banks", isin="US5"),
                 _row("CCC.NASDAQ", "Gamma Corp", industry="Insurance", isin="US5")]
    no_industry = [_row("DDD.US", "Delta Corp", industry="", isin="US6"),
                   _row("DDE.US", "Delta Corp", industry="Banks", isin="US7")]
    assert contradicting_name_pairs(same_industry + other_market + same_isin + no_industry) == []


def test_status_lists_the_pairs_and_excludes_nothing(tmp_path):
    rows = _sector_group(with_ccz=True)
    store = IndexStore(tmp_path)
    store.save(rows)
    out = status(store, aliases=[])
    assert out.contradicting_pairs == [("CCZ.US", "REIT - Residential", "CMCSA.US",
                                        "Telecom Services")]
    text = " ".join(out.lines())
    assert "report only" in text and "CCZ.US (REIT - Residential) vs CMCSA.US" in text
    assert out.rows == len(rows)                                   # the table is untouched
