"""BATCH 8, commit 3 - money, the peers table and the exports, on fabricated rows.

Measured on the live page 2026-09-26: a London cap read "76,547,235,840 GBX" (it is POUNDS, the
INDEX-GBX-SCALE-1 repair); AstraZeneca's debt read "27.4bn GBp" (the listing's currency on the
accounts' figure); the growth record printed "source: EODHD, 36 annual reports" beside "grew in 8 of
the 10 years"; the analyst block was three sentences and a cost line.
"""
from __future__ import annotations

from pathlib import Path

from aristos_council.cohorts.report import cap_cell
from aristos_council.cohorts.source import PATH_CONSTITUENTS, PATH_INDEX, Candidate
from aristos_council.company_check import format_company_check, run_company_check
from aristos_council.export.report_html import company_check_html
from aristos_council.market_index import (SOURCE_EODHD_LISTING, USD_COMPUTED,
                                          USD_COMPUTED_MAJOR_UNIT, IndexRow, PeerGroup)
from aristos_council.peer_table import (LOCAL_COLUMN, USD_COLUMN, local_cap_currency,
                                        peer_frame_records, peer_rows, peer_text_lines)
from tests.test_company_check import _MU, STRAT_DIR, UNIV_DIR, _OneName


def _row(ticker, name, *, cap, usd, currency="USD", source=USD_COMPUTED, sub="Pharmaceuticals"):
    return IndexRow(
        ticker=ticker, yahoo_ticker=ticker.rpartition(".")[0], name=name,
        exchange=ticker.rpartition(".")[2], market=ticker.rpartition(".")[2], currency=currency,
        sector="Healthcare", industry="Drug Manufacturers - General",
        gics_industry="Pharmaceuticals, Biotechnology", gics_subindustry=sub,
        market_cap=cap, market_cap_usd=usd, market_cap_usd_source=source,
        primary_ticker=ticker, isin="XX" + ticker[:6], fetched_at="2026-09-26",
        source=SOURCE_EODHD_LISTING)


def _group():
    members = [
        _row("GSK.LSE", "GSK plc", cap=76_547_235_840.0, usd=102_486_591_485.0, currency="GBX",
             source=USD_COMPUTED_MAJOR_UNIT),
        _row("ABBV.US", "AbbVie Inc", cap=466_448_285_696.0, usd=466_448_285_696.0),
        _row("NOVN.SW", "Novartis AG", cap=221_357_260_800.0, usd=269_684_772_290.0, currency="CHF"),
        _row("LLY.US", "Eli Lilly and Company", cap=1_027_672_309_760.0, usd=1_027_672_309_760.0),
    ]
    return PeerGroup(subject=members[1], members=members, rung="sub-industry, 1/4x–4x",
                     band="0.25x-4x market cap (USD)", snapshot="2026-09-25", step=1,
                     distinct_companies=4, reasons=["a reason"],
                     matched_on={m.ticker: "GICS+EODHD" for m in members})


# =========================================================================== #
# the peers table
# =========================================================================== #
def test_a_london_market_cap_is_labelled_in_pounds_not_pence():
    gsk = _group().members[0]
    assert local_cap_currency(gsk) == "GBP"
    row = next(r for r in peer_rows(_group()) if r.ticker == "GSK.LSE")
    assert row.local_text == "£76.5bn" and row.usd_text == "$102.5bn"
    assert "GBX" not in row.local_text


def test_a_pence_code_without_the_major_unit_tag_is_not_relabelled():
    """Only a row the index CALLS major-unit is pounds; anything else keeps its own code."""
    odd = _row("ODD.LSE", "Odd plc", cap=5e9, usd=6.5e9, currency="GBX", source=USD_COMPUTED)
    assert local_cap_currency(odd) == "GBX"


def test_peers_are_sorted_by_usd_cap_descending_and_a_trillion_reads_with_two_decimals():
    rows = peer_rows(_group())
    assert [r.ticker for r in rows] == ["LLY.US", "ABBV.US", "NOVN.SW", "GSK.LSE"]
    assert rows[0].usd_text == "$1.03tn"
    assert rows[1].usd_text == "$466.4bn"
    assert next(r for r in rows if r.ticker == "NOVN.SW").local_text == "CHF 221.4bn"


def test_the_pages_table_holds_numbers_so_it_sorts_by_size_and_has_no_matched_on_column():
    records = peer_frame_records(_group())
    assert list(records[0]) == ["Ticker", "Name", "Exchange", USD_COLUMN, LOCAL_COLUMN, "Currency",
                                "Sub-industry"]
    assert "Matched on" not in records[0]
    assert all(isinstance(r[USD_COLUMN], float) for r in records)
    assert records[0][USD_COLUMN] > records[-1][USD_COLUMN]                   # largest first
    assert next(r for r in records if r["Ticker"] == "GSK.LSE")["Currency"] == "GBP"


def test_the_text_table_uses_the_same_formatter():
    text = "\n".join(peer_text_lines(_group()))
    assert "£76.5bn" in text and "$1.03tn" in text and "CHF 221.4bn" in text
    assert "GBX" not in text and "76,547,235,840" not in text


# =========================================================================== #
# the cohort report.md table
# =========================================================================== #
def test_the_cohort_report_prints_caps_through_the_same_formatter():
    lindt = Candidate(ticker="LISN.SW", market_cap=20_160_000_000.0, currency="CHF",
                      market_cap_usd=24_560_000_000.0, source=PATH_INDEX)
    assert cap_cell(lindt) == "$24.6bn (CHF 20.2bn)"
    gsk = Candidate(ticker="GSK.LSE", market_cap=76_547_235_840.0, currency="GBX",
                    market_cap_usd=102_486_591_485.0, source=PATH_INDEX)
    assert cap_cell(gsk) == "$102.5bn (£76.5bn)"
    us = Candidate(ticker="ABBV.US", market_cap=466_448_285_696.0, currency="USD",
                   market_cap_usd=466_448_285_696.0, source=PATH_INDEX)
    assert cap_cell(us) == "$466.4bn"
    assert cap_cell(Candidate(ticker="X.US", source=PATH_INDEX)) == "—"


def test_a_constituents_path_candidate_keeps_its_own_currency_code():
    """No index evidence that a GBX cap is pounds on the legacy path, so it is not relabelled."""
    legacy = Candidate(ticker="X.LSE", market_cap=5e9, currency="GBX", source=PATH_CONSTITUENTS)
    assert cap_cell(legacy) == "GBX 5.0bn"


# =========================================================================== #
# the exports carry the peers and the readings, in the same money
# =========================================================================== #
def _check():
    return run_company_check("MU", "magic_formula_momentum_v1", "", adapter=_OneName(_MU),
                             strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR,
                             runs_dir=Path("runs"), today=__import__("datetime").date(2026, 6, 30))


def test_the_text_export_prints_the_peers_and_the_readings():
    result = _check()
    result.peer_group = _group()
    text = format_company_check(result)
    assert "PEERS (who this company would be measured against):" in text
    assert "£76.5bn" in text and "$1.03tn" in text
    assert "ABSOLUTE READINGS" in text and "Debt and cash" in text and "Growth record" in text
    assert "Matched on" not in text


def test_the_html_export_prints_the_peers_and_the_readings():
    result = _check()
    result.peer_group = _group()
    html = company_check_html(result)
    assert "<h2>Peers</h2>" in html and "£76.5bn" in html and "$1.03tn" in html
    assert "<h2>Absolute readings</h2>" in html and "Debt and cash" in html
    assert "Matched on" not in html and "76,547,235,840" not in html


def test_a_missing_index_is_a_stated_reason_in_both_exports_not_a_missing_section():
    result = _check()
    result.peer_error = "FileNotFoundError: no index"
    assert "the market index is not available" in format_company_check(result)
    assert "The market index is not available" in company_check_html(result)
