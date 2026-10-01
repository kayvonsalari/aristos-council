"""FIND-COMPANY-1 — search the local market index by name or ticker.

Deterministic and offline: every test injects its own fake IndexRow set via ``rows=`` (never
the real market_index.parquet) and its own tmp ``cohorts_root`` — the real
``data/cohort_definitions.yaml`` is read for display names (it is a committed, stable file),
but no BUILT cohort directory is ever the real one, and ``data/local/cohorts/watch.yaml`` is
never touched by this module at all.
"""
from __future__ import annotations

import csv
from pathlib import Path

from aristos_council.company_search import search_companies
from aristos_council.market_index import SOURCE_EODHD_LISTING, IndexRow

SNAPSHOT = "2026-09-25"


def _row(ticker, name="", *, cap_bn=100.0, sub="Semiconductors",
        eodhd="Semiconductors", primary=None, isin=None, market="", currency="USD",
        country="") -> IndexRow:
    """A fabricated index row (mirrors tests/test_market_index_peer_pools.py's ``_row``)."""
    cap = None if cap_bn is None else cap_bn * 1e9
    code = ticker.rpartition(".")[0] or ticker
    return IndexRow(
        ticker=ticker, yahoo_ticker=code, name=name or ticker,
        exchange=market or ticker.rpartition(".")[2], market=market, country=country,
        currency=currency, sector="Technology", industry=eodhd, gics_subindustry=sub,
        market_cap=cap, market_cap_usd=cap,
        market_cap_usd_source="computed" if cap is not None else "abstained",
        primary_ticker=ticker if primary is None else primary,
        isin=f"XX{abs(hash(ticker)) % 10**10:010d}" if isin is None else isin,
        fetched_at=SNAPSHOT, source=SOURCE_EODHD_LISTING)


def _write_members(root: Path, slug: str, tickers: list[str], *, version: int = 1) -> None:
    d = root / slug / f"v{version}"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "members.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "name", "market_cap", "currency", "isin", "source", "filled"])
        for t in tickers:
            w.writerow([t, t, "1000000000", "USD", f"XX{abs(hash(t))}", "constituents", ""])


# Two REAL cohort slugs from the committed data/cohort_definitions.yaml (BACKTEST-2 pinned
# them too): a stable, non-arbitrary choice.
MINING_SLUG = "materials_diversified_mining"
SEMI_SLUG = "tech_semiconductors"


def test_a_full_name_match_finds_the_company():
    rows = [_row("SIE.XETRA", "Siemens Aktiengesellschaft", market="XETRA", country="DE")]
    res = search_companies("Siemens Aktiengesellschaft", rows=rows, cohorts_root=Path("/nope"))
    assert [m.ticker for m in res.matches] == ["SIE.XETRA"]


def test_a_partial_case_insensitive_name_match_finds_the_company():
    rows = [_row("SIE.XETRA", "Siemens Aktiengesellschaft", market="XETRA", country="DE"),
           _row("OTHER.US", "Unrelated Corp")]
    res = search_companies("SIEMENS", rows=rows, cohorts_root=Path("/nope"))
    assert [m.ticker for m in res.matches] == ["SIE.XETRA"]
    res_lower = search_companies("siemens", rows=rows, cohorts_root=Path("/nope"))
    assert [m.ticker for m in res_lower.matches] == ["SIE.XETRA"]


def test_an_accented_company_name_is_found_by_its_plain_query():
    rows = [_row("NESN.SW", "Nestlé S.A.", market="SW", country="CH")]
    res = search_companies("nestle", rows=rows, cohorts_root=Path("/nope"))
    assert [m.ticker for m in res.matches] == ["NESN.SW"]


def test_an_unaccented_query_with_an_accent_also_matches():
    rows = [_row("NESN.SW", "Nestlé S.A.", market="SW", country="CH")]
    res = search_companies("néstle", rows=rows, cohorts_root=Path("/nope"))
    assert [m.ticker for m in res.matches] == ["NESN.SW"]


def test_a_ticker_match_finds_the_company_by_substring():
    rows = [_row("2330.TW", "Taiwan Semiconductor Manufacturing Co. Ltd.", cap_bn=2000,
                market="TW", country="TW")]
    res = search_companies("2330", rows=rows, cohorts_root=Path("/nope"))
    assert [m.ticker for m in res.matches] == ["2330.TW"]


# =========================================================================== #
# FIND-COMPANY-2 — alias names, ticker-search ranking, no duplicated country/exchange
# =========================================================================== #
def test_a_common_short_name_is_found_by_alias():
    rows = [_row("2330.TW", "Taiwan Semiconductor Manufacturing Co. Ltd.", cap_bn=2000,
                market="TW", country="TW")]
    res = search_companies("TSMC", rows=rows, cohorts_root=Path("/nope"))
    assert [m.ticker for m in res.matches] == ["2330.TW"]
    res_lower = search_companies("tsmc", rows=rows, cohorts_root=Path("/nope"))
    assert [m.ticker for m in res_lower.matches] == ["2330.TW"]


def test_a_ticker_search_ranks_the_exact_match_first_the_real_2330_collision():
    """The real-world bug: "2330" used to rank 282330.KO, 2330.HK, 012330.KO, 052330.KQ ahead
    of TSMC's own 2330.TW (alphabetically "Taiwan..." sorts last among those five names).
    Exact-ticker-ignoring-suffix now wins regardless of name, and market cap breaks the
    remaining ties."""
    rows = [
        _row("282330.KO", "BGF Retail Co Ltd", cap_bn=1.68, market="KO", country="KR"),
        _row("2330.HK", "China Uptown Group Co Ltd", cap_bn=0.0147, market="HK", country="HK"),
        _row("012330.KO", "Hyundai Mobis Co.,Ltd", cap_bn=23.86, market="KO", country="KR"),
        _row("052330.KQ", "Kortek Corporation", cap_bn=0.099, market="KQ", country="KR"),
        _row("2330.TW", "Taiwan Semiconductor Manufacturing Co. Ltd.", cap_bn=2026,
            market="TW", country="TW"),
    ]
    res = search_companies("2330", rows=rows, cohorts_root=Path("/nope"))
    assert [m.ticker for m in res.matches] == [
        "2330.TW",       # exact ticker match ignoring suffix, largest cap of the two exact hits
        "2330.HK",       # exact ticker match ignoring suffix, smaller cap
        "012330.KO",     # contains "2330", largest cap of the remaining three
        "282330.KO",
        "052330.KQ",
    ]


def test_an_exact_ticker_match_outranks_a_mere_name_match():
    rows = [_row("GE.US", "GE Aerospace", cap_bn=326.0, market="NYSE", country="US"),
           _row("GEHC.US", "GE HealthCare Technologies Inc.", cap_bn=29.0, market="NASDAQ",
                country="US")]
    res = search_companies("GE", rows=rows, cohorts_root=Path("/nope"))
    assert res.matches[0].ticker == "GE.US"


def test_no_duplicated_country_and_exchange_codes():
    taiwan = _row("2330.TW", "Taiwan Semiconductor Manufacturing Co. Ltd.", market="TW",
                 country="TW")
    hongkong = _row("OTHER.HK", "Some Hong Kong Co", market="HK", country="HK")
    germany = _row("SIE.XETRA", "Siemens Aktiengesellschaft", market="XETRA", country="DE")
    unmapped = _row("ZZZ.ZZ", "Unmapped Co", market="ZZ", country="ZZ")
    for row, expected in ((taiwan, "Taiwan"), (hongkong, "Hong Kong"),
                         (germany, "Germany — XETRA"), (unmapped, "ZZ")):
        res = search_companies(row.name, rows=[row], cohorts_root=Path("/nope"))
        assert res.matches[0].where == expected, row.ticker


def test_a_secondary_line_never_appears_as_its_own_match():
    """TSM.US is TSMC's own ADR line, naming 2330.TW as its home (PrimaryTicker) — the SAME
    real shape tests/test_market_index_peer_pools.py pins. clean_pool collapses it into the
    ONE row for the company, so a search for "Taiwan Semiconductor" returns 2330.TW once,
    never TSM.US as a second, separate match."""
    rows = [_row("2330.TW", "Taiwan Semiconductor Manufacturing Co. Ltd.", cap_bn=2000,
                isin="TW0002330008", market="TW", currency="TWD", country="TW"),
           _row("TSM.US", "Taiwan Semiconductor Manufacturing Co", cap_bn=2250,
                primary="2330.TW", isin="US8740391003", market="US", country="US")]
    res = search_companies("Taiwan Semiconductor", rows=rows, cohorts_root=Path("/nope"))
    assert [m.ticker for m in res.matches] == ["2330.TW"]
    assert res.matches[0].is_home


def test_market_cap_renders_in_the_short_money_format():
    rows = [_row("SIE.XETRA", "Siemens Aktiengesellschaft", cap_bn=236.4, market="XETRA",
                country="DE")]
    res = search_companies("siemens", rows=rows, cohorts_root=Path("/nope"))
    assert res.matches[0].market_cap_display == "$236.4bn"


def test_a_company_in_two_cohorts_shows_both(tmp_path):
    _write_members(tmp_path, MINING_SLUG, ["DUAL.XX"])
    _write_members(tmp_path, SEMI_SLUG, ["DUAL.XX"])
    rows = [_row("DUAL.XX", "Dual Cohort Co", market="XX", country="ZZ")]
    res = search_companies("Dual Cohort", rows=rows, cohorts_root=tmp_path)
    assert res.cohorts_known is True
    cohorts = set(res.matches[0].cohorts)
    assert cohorts == {"Materials - Diversified Mining", "Tech - Semiconductors"}


def test_a_company_in_no_built_cohort_shows_none(tmp_path):
    _write_members(tmp_path, MINING_SLUG, ["SOMEONE_ELSE.XX"])
    rows = [_row("LONE.XX", "Lone Company", market="XX", country="ZZ")]
    res = search_companies("Lone Company", rows=rows, cohorts_root=tmp_path)
    assert res.cohorts_known is True                  # cohorts ARE built, just not this one
    assert res.matches[0].cohorts == ()


def test_no_built_cohorts_at_all_is_reported_so_the_column_can_be_dropped(tmp_path):
    empty = tmp_path / "nothing_built_here"
    rows = [_row("SIE.XETRA", "Siemens Aktiengesellschaft", market="XETRA", country="DE")]
    res = search_companies("siemens", rows=rows, cohorts_root=empty)
    assert res.cohorts_known is False
    assert res.matches[0].cohorts == ()


def test_an_empty_or_whitespace_query_returns_no_matches():
    rows = [_row("SIE.XETRA", "Siemens Aktiengesellschaft", market="XETRA", country="DE")]
    assert search_companies("", rows=rows, cohorts_root=Path("/nope")).matches == ()
    assert search_companies("   ", rows=rows, cohorts_root=Path("/nope")).matches == ()


def test_matches_are_capped_at_the_limit():
    rows = [_row(f"CO{i}.US", f"Example Company {i}") for i in range(30)]
    res = search_companies("Example Company", rows=rows, cohorts_root=Path("/nope"), limit=15)
    assert len(res.matches) == 15


def test_the_where_property_names_country_and_exchange():
    """``country`` holds the bare code the index actually stores ("DE"), not a full name —
    ``where`` looks it up (FIND-COMPANY-2 item 3)."""
    rows = [_row("SIE.XETRA", "Siemens Aktiengesellschaft", market="XETRA", country="DE")]
    res = search_companies("siemens", rows=rows, cohorts_root=Path("/nope"))
    assert res.matches[0].where == "Germany — XETRA"
