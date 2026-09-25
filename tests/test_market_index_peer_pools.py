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

from aristos_council.market_index import (SOURCE_EODHD_LISTING, IndexRow, company_groups,
                                          company_key, one_row_per_company, peers)

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
