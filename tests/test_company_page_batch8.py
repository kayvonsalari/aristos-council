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


# =========================================================================== #
# commit 4 - the dagger and the Sources block
# =========================================================================== #
from aristos_council.company_check import (SEE_SOURCES, FactorCell, company_sources,  # noqa: E402
                                           factor_source_display, mixed_source_marker,
                                           provider_of, split_fx_source)
from aristos_council.market_index import IndexStore, peer_snapshot, peers  # noqa: E402
from aristos_council.peer_table import ONE_SYSTEM_NOTE, has_one_system_peers  # noqa: E402


def _two_system_group():
    group = _group()
    group.systems = ("GICS", "EODHD")
    group.matched_on = {"GSK.LSE": "GICS+EODHD", "ABBV.US": "EODHD", "NOVN.SW": "GICS+EODHD",
                        "LLY.US": "GICS"}
    return group


def test_a_peer_admitted_on_one_of_two_label_systems_gets_a_dagger_and_one_footnote():
    rows = {r.ticker: r for r in peer_rows(_two_system_group())}
    assert rows["ABBV.US"].one_system and rows["LLY.US"].one_system
    assert not rows["GSK.LSE"].one_system and not rows["NOVN.SW"].one_system
    assert rows["ABBV.US"].marked_ticker == "ABBV.US †"
    text = peer_text_lines(_two_system_group())
    assert text[-1] == ONE_SYSTEM_NOTE == "† counted as a peer on one industry classification only"
    assert sum(1 for line in text if "†" in line) == 3          # two rows and the one footnote
    assert has_one_system_peers(peer_rows(_two_system_group()))


def test_no_dagger_when_the_subject_could_only_be_matched_in_one_system():
    """Every peer is one-system by construction then; marking them all would say nothing."""
    group = _group()
    group.systems = ("EODHD",)
    group.matched_on = {m.ticker: "EODHD" for m in group.members}
    assert not has_one_system_peers(peer_rows(group))
    assert ONE_SYSTEM_NOTE not in peer_text_lines(group)


def test_the_page_records_carry_the_dagger_and_no_matched_on_column():
    records = peer_frame_records(_two_system_group())
    assert any(r["Ticker"] == "ABBV.US †" for r in records)
    assert "Matched on" not in records[0]


def test_the_per_peer_detail_stays_in_the_snapshot_and_the_run_record():
    snapshot = peer_snapshot(_two_system_group())
    assert snapshot["matched_on"]["ABBV.US"] == "EODHD"
    assert snapshot["systems"] == ["GICS", "EODHD"]


def test_a_size_correction_a_group_used_is_recorded_and_named_in_the_sources_block():
    from aristos_council.market_index import SizeCorrection
    rows = [_row("SUBJ.US", "Subject Corp", cap=100e9, usd=100e9),
            *[_row(f"RIV{i:02d}.US", f"Rival {i}", cap=(90 + i) * 1e9, usd=(90 + i) * 1e9)
              for i in range(12)]]
    correction = SizeCorrection(ticker="RIV03.US", action="set", date="2026-09-26",
                                reason="stated", market_cap_usd=95e9)
    group = peers("SUBJ.US", rows=rows, overrides=[], aliases=[], size_corrections=[correction])
    assert [c["ticker"] for c in group.size_corrected] == ["RIV03.US"]
    assert any(r.startswith("size corrected: RIV03.US") for r in group.reasons)
    result = _check()
    result.peer_group = group
    text = dict((s.topic, s.text) for s in company_sources(result))
    assert "data/size_corrections.yaml (RIV03.US)" in text["Correction files used"]


# ---------------------------------------------------------------- sources
def _check_with_analyst(which="rising"):
    from tests.test_analyst_trend import _TRENDS, _fetcher
    return run_company_check(
        "MU", "magic_formula_momentum_v1", "", adapter=_OneName(_MU), strategies_dir=STRAT_DIR,
        universes_dir=UNIV_DIR, runs_dir=Path("runs"),
        today=__import__("datetime").date(2026, 6, 30), with_analyst_trend=True,
        analyst_fetcher=_fetcher(_TRENDS[which]))


def test_a_provider_is_named_once_in_the_footer_and_nowhere_above_it():
    import dataclasses
    result = _check_with_analyst()
    group = _two_system_group()
    group.reasons = []                                       # correction notes name providers by nature
    result.peer_group = group
    result.growth_record = dataclasses.replace(result.growth_record,
                                               source_tag="source: EODHD, 36 annual reports")
    text = format_company_check(result)
    above, _, footer = text.partition("\nSOURCES:")
    assert footer, "the Sources block is missing"
    assert "EODHD" not in above, [l for l in above.splitlines() if "EODHD" in l]
    for topic in ("Analyst ratings and forecasts: EODHD, as of", "Growth record: EODHD, 36 annual",
                  "Fundamentals and accounts:", "Prices:", "Market index:"):
        assert topic in footer, topic
    html = company_check_html(result)
    body, _, html_footer = html.partition("<h2>Sources</h2>")
    assert html_footer and "Analyst ratings and forecasts:" in html_footer
    assert "36 annual reports" not in body


def test_a_value_from_a_different_source_keeps_a_short_marker_that_points_to_the_footer():
    result = _check()
    result.providers = {"fundamentals": "yfinance"}
    assert mixed_source_marker(result, "source: EODHD, 36 annual reports") == " (see Sources)"
    assert mixed_source_marker(result, "EODHD Earnings::Trend") == " (see Sources)"
    assert "see Sources" in SEE_SOURCES
    result.providers = {"fundamentals": "EODHD"}
    assert mixed_source_marker(result, "source: EODHD, 36 annual reports") == ""     # same source
    result.providers = {}
    assert mixed_source_marker(result, "source: EODHD, 36 annual reports") == ""     # never assumed
    assert provider_of("source: yfinance, 4 annual reports") == "yfinance"


def test_the_marker_appears_in_both_exports_when_the_sources_differ():
    result = _check_with_analyst()
    result.providers = {**result.providers, "fundamentals": "yfinance"}
    assert ("What analysts say (a mark: it does not vote and changes no verdict) "
            "(see Sources)") in format_company_check(result)
    assert "What analysts say (see Sources)" in company_check_html(result)


def test_a_static_fund_receipt_keeps_a_marker_not_the_provider_beside_the_figure():
    assert factor_source_display("static: 2026-07-21, EODHD") == "static — see Sources"
    assert factor_source_display("computed") == "computed"
    assert factor_source_display("ev, USD→GBp @ 0.757 (2026-09-26)") == \
        "ev, USD→GBp @ 0.757 (2026-09-26)"                    # a calculation fact stays
    result = _check()
    result.factors = [FactorCell("expense_ratio", "Expense ratio", 0.07,
                                 "static: 2026-07-21, EODHD", "context")]
    assert "static — see Sources" in format_company_check(result).split("\nSOURCES:")[0]
    assert ("Fund figures marked static", "2026-07-21, EODHD") in [
        (s.topic, s.text) for s in company_sources(result)]


def test_the_band_keeps_its_conversion_but_its_rate_source_moves_to_the_footer():
    text = ("EV/EBIT 20.0 — 4th percentile (cheapest) of its own 5-year range (based on 43 of 61 "
            "months, the rest lack usable statements; USD accounts converted to GBp, monthly FX, "
            "source yfinance USDGBp=X)")
    band, fx = split_fx_source(text)
    assert band.endswith("USD accounts converted to GBp, monthly FX)")
    assert "yfinance" not in band and fx == "yfinance USDGBp=X"
    inverted = "x (a; USD accounts converted to GBp, monthly FX, source yfinance GBpUSD=X inverted)"
    assert split_fx_source(inverted)[1] == "yfinance GBpUSD=X inverted"
    assert split_fx_source("EV/EBIT 20.0 — 4th percentile") == ("EV/EBIT 20.0 — 4th percentile", "")
    result = _check()
    result.fx_source = fx
    assert ("Currency rates in the valuation band", "yfinance USDGBp=X, monthly") in [
        (s.topic, s.text) for s in company_sources(result)]


def test_the_footer_links_the_classification_page_when_there_is_a_peer_group():
    result = _check()
    result.peer_group = _group()
    lines = dict((s.topic, s.text) for s in company_sources(result))
    assert lines["How peers are chosen"] == "docs/CLASSIFICATION.md"
    assert lines["Market index"].startswith("local table of listed companies built from EODHD")
    result.peer_group = None
    assert "How peers are chosen" not in dict((s.topic, s.text) for s in company_sources(result))
