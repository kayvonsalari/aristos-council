"""PEER LADDER STEP 4 (2026-09-26) - the sector, when the industry is too thin.

The owner's ruling replaced the 2026-09-18 rule that abstained before reaching the sector. Measured
on the live index: Nestle ("Packaged Foods & Meats") had 1, 4 and 6 comparable companies at steps
1-3, Samsung Electronics 2, 5 and 5, Tencent 1, 5 and 5 - and the sector holds 45, 38 and 19.

Rules pinned here: step 4 fires ONLY after steps 1-3 all fall under the floor; it uses the same
1/10x-10x band, floor 12 and cap 40 as the others; it matches on either label system (the GICS
sector, or EODHD's own sector), each against itself; financials only ever meet financials; and the
page says it is a broad group. Every table here is fabricated.
"""
from __future__ import annotations

from aristos_council.market_index import (BROAD_SECTOR_NOTE, LADDER_STEPS, RUNG_INDUSTRY_WIDE,
                                          RUNG_SECTOR, SOURCE_EODHD_LISTING, IndexRow, peers)

SNAPSHOT = "2026-09-26"


def _row(ticker, *, cap_bn=50.0, sector="Consumer Defensive", gics_sector="Consumer Staples",
         industry="Packaged Foods", gics_industry="Food Products",
         sub="Packaged Foods & Meats", name="") -> IndexRow:
    cap = cap_bn * 1e9
    return IndexRow(
        ticker=ticker, yahoo_ticker=ticker.rpartition(".")[0], name=name or ticker,
        exchange="US", market="US", currency="USD", sector=sector, industry=industry,
        gics_sector=gics_sector, gics_industry=gics_industry, gics_subindustry=sub,
        market_cap=cap, market_cap_usd=cap, market_cap_usd_source="computed",
        primary_ticker=ticker, isin=f"XX{abs(hash(ticker)) % 10**10:010d}",
        fetched_at=SNAPSHOT, source=SOURCE_EODHD_LISTING)


def _sector_crowd(n, *, cap_bn=50.0, **kw):
    """``n`` companies sharing the subject's SECTOR and nothing finer."""
    return [_row(f"SEC{i:02d}.US", cap_bn=cap_bn + i * 0.1, industry=f"Other Industry {i}",
                 gics_industry=f"Other GICS Industry {i}", sub=f"Other Sub {i}", **kw)
            for i in range(n)]


def _subject(**kw):
    return _row("NESTLE.US", name="Nestle", **kw)


def _peers(rows, **kw):
    return peers("NESTLE.US", rows=rows, overrides=[], aliases=[], size_corrections=[],
                 exclude_markets=(), **kw)


# --------------------------------------------------------------------------- #
def test_the_ladder_has_four_steps_and_the_fourth_is_the_sector():
    assert LADDER_STEPS == 4
    assert RUNG_SECTOR == "sector, 1/10x-10x"
    assert BROAD_SECTOR_NOTE == "broad sector group - wider than a normal peer group"


def test_a_thin_industry_in_a_crowded_sector_reaches_step_four_and_says_it_is_broad():
    group = _peers([_subject(), *_sector_crowd(20)])
    assert group.available and group.step == 4 and group.rung == RUNG_SECTOR
    assert group.broad and len(group.members) == 20
    sentence = group.sentence()
    assert "found at step 4 of 4" in sentence
    assert sentence.endswith("broad sector group - wider than a normal peer group")
    assert any(r.startswith("broad sector group") for r in group.reasons)
    # the three rungs above each reported how thin they were, on the way
    assert sum(1 for r in group.reasons if "comparable companies found" in r) == 3


def test_step_four_fires_only_after_steps_one_to_three_have_all_failed():
    twelve_alike = [_row(f"ALIKE{i:02d}.US", cap_bn=50.0 + i * 0.1) for i in range(12)]
    group = _peers([_subject(), *twelve_alike, *_sector_crowd(30)])
    assert group.step == 1 and not group.broad
    # thirteen in the same GICS industry (not sub-industry) only: step 3, not 4
    cousins = [_row(f"COUS{i:02d}.US", cap_bn=50.0 + i * 0.1, sub=f"Cousin Sub {i}",
                    industry=f"Cousin {i}") for i in range(12)]
    group3 = _peers([_subject(), *cousins, *_sector_crowd(30)])
    assert group3.step == 3 and group3.rung == RUNG_INDUSTRY_WIDE and not group3.broad


def test_it_abstains_only_when_the_sector_also_falls_under_the_floor():
    group = _peers([_subject(), *_sector_crowd(11)])          # 11 < 12
    assert not group.available and group.members == []
    final = group.reasons[-1]
    assert f"the widest rung tried ({RUNG_SECTOR}) found only 11 comparable companies" in final


def test_step_four_uses_the_wide_size_band():
    inside = _sector_crowd(12, cap_bn=50.0)                   # 1x: inside 1/10x-10x
    far_too_big = [_row(f"HUGE{i:02d}.US", cap_bn=900.0, industry=f"X{i}", gics_industry=f"Y{i}",
                        sub=f"Z{i}") for i in range(10)]     # 18x: outside
    tiny = [_row(f"TINY{i:02d}.US", cap_bn=1.0, industry=f"T{i}", gics_industry=f"U{i}",
                 sub=f"V{i}") for i in range(10)]            # 0.02x: outside
    group = _peers([_subject(), *inside, *far_too_big, *tiny])
    assert group.step == 4
    assert all(m.ticker.startswith("SEC") for m in group.members)


def test_the_cap_of_forty_keeps_the_nearest_in_size():
    group = _peers([_subject(), *_sector_crowd(45)])
    assert group.step == 4 and len(group.members) == 40
    assert "45 matched at this rung; trimmed to 40 nearest in size" in group.reasons


def test_either_label_system_can_admit_a_peer_each_against_itself():
    """GICS says 'Consumer Staples', EODHD says 'Consumer Defensive': a peer filed under only one of
    them still qualifies, and each system is compared only with itself."""
    gics_only = _sector_crowd(6, sector="Something Else Entirely")           # GICS sector matches
    eodhd_only = [_row(f"EOD{i:02d}.US", sector="Consumer Defensive", gics_sector="Utilities",
                       industry=f"E{i}", gics_industry=f"F{i}", sub=f"G{i}") for i in range(6)]
    group = _peers([_subject(), *gics_only, *eodhd_only])
    assert group.step == 4 and group.systems == ("GICS", "EODHD")
    assert {group.matched_on[m.ticker] for m in group.members
            if m.ticker.startswith("SEC")} == {"GICS"}
    assert {group.matched_on[m.ticker] for m in group.members
            if m.ticker.startswith("EOD")} == {"EODHD"}


def test_a_gics_sector_is_not_matched_against_an_eodhd_sector_by_wording():
    """'Technology' is EODHD's sector wording and not a GICS sector name; a row carrying it only in
    the OTHER system does not match across systems."""
    subject = _row("NESTLE.US", sector="", gics_sector="Technology")
    lookalikes = [_row(f"LOOK{i:02d}.US", sector="Technology", gics_sector="",
                       industry=f"L{i}", gics_industry=f"M{i}", sub=f"N{i}") for i in range(14)]
    group = _peers([subject, *lookalikes])
    assert not group.available


def test_financials_never_reach_a_non_financial_sector_group():
    banks = [_row(f"BANK{i:02d}.US", cap_bn=50.0, sector="Consumer Defensive",
                  gics_sector="Consumer Staples", industry="Banks - Regional",
                  gics_industry="Banks", sub=f"Regional Banks {i}") for i in range(20)]
    group = _peers([_subject(), *banks, *_sector_crowd(5)])
    assert not group.available                     # 5 real peers; the 20 banks are not counted
    ok = _peers([_subject(), *banks, *_sector_crowd(12)])
    assert ok.step == 4 and not any(m.ticker.startswith("BANK") for m in ok.members)


def test_a_financial_subject_meets_only_financials_at_step_four():
    bank = _row("NESTLE.US", sector="Financial Services", gics_sector="Financials",
                industry="Banks - Regional", gics_industry="Banks", sub="Regional Banks")
    other_banks = [_row(f"BK{i:02d}.US", sector="Financial Services", gics_sector="Financials",
                        industry=f"Insurance - Kind {i}", gics_industry=f"Insurance Kind {i}",
                        sub=f"Insurance Sub {i}") for i in range(14)]
    # matched on the GICS sector wording, but they are not financials (an operating steel maker)
    industrials = [_row(f"IND{i:02d}.US", sector="Industrials", gics_sector="Financials",
                        industry="Steel", gics_industry="Metals", sub=f"Steel {i}")
                   for i in range(14)]
    group = _peers([bank, *other_banks, *industrials])
    assert group.step == 4
    assert all(m.ticker.startswith("BK") for m in group.members)


def test_the_snapshot_records_the_step_and_the_systems():
    from aristos_council.market_index import peer_snapshot
    snap = peer_snapshot(_peers([_subject(), *_sector_crowd(15)]))
    assert snap["step"] == 4 and snap["rung"] == RUNG_SECTOR
    assert snap["systems"] == ["GICS", "EODHD"]
