"""BATCH 19B - reader-text fixes B2-B10 (words only: no vote, rank or number moves).

Built over the recorded sweep fixtures (``tests/fixtures/report_sweep``), so nothing here reaches a
provider.
"""
from __future__ import annotations

import json

import pytest

from aristos_council.abs_readings import plain_company_name
from aristos_council.company_report import (OUTSIDE_RANGE_ROW_TAG, format_company_report,
                                            run_company_report)
from aristos_council.export.report_html import company_report_html
from aristos_council.market_index import IndexRow, PeerGroup
from aristos_council.peer_table import clean_company_name, peer_text_lines
from aristos_council.persistence.replay import FrozenAdapter
from aristos_council.report_language import (forced_bottom_note, forced_bottom_note_multi,
                                             score_gloss_with_note)
from aristos_council.shadow_rank import WouldRank, _missing_reason
from tests.test_report_sweep import FIX, META, TODAY, _Store, _no_news


@pytest.fixture(scope="module")
def small_company(tmp_path_factory):
    """NVCR (under $5bn, include-small on): every lens row carries the outside-range tag."""
    rows = json.loads((FIX / "index_rows.json").read_text(encoding="utf-8"))
    return run_company_report(
        "NVCR.US", META["stock_lenses"], adapter=FrozenAdapter(FIX / "frozen"),
        store=_Store([IndexRow(**r) for r in rows["NVCR.US"]]), today=TODAY, include_small=True,
        save=False, news_fetcher=_no_news, runs_dir=tmp_path_factory.mktemp("b19b_runs"))


# ----------------------------------------------------------------------------- B2
def test_would_rank_unavailable_reads_in_plain_words_not_ids():
    text = WouldRank(False, reason=_missing_reason(
        ["roic", "revenue_growth"], {"roic": "abstained", "revenue_growth": "abstained"})).text
    assert text == ("would-rank not available: return on invested capital and revenue growth "
                    "could not be computed")
    assert "roic" not in text and "abstained" not in text and "_" not in text


def test_a_specific_provider_reason_is_kept_in_brackets():
    reason = _missing_reason(["gross_profitability"],
                             {"gross_profitability": "abstained: gross profit unavailable"})
    assert "(gross profit unavailable)" in reason and reason.endswith("could not be computed")


# ----------------------------------------------------------------------------- B3
def test_sector_scope_reason_reads_not_for_this_sector(small_company):
    text = format_company_report(small_company)
    assert "not for this sector (Healthcare)" in text
    assert "outside this strategy's scope" not in text


# ----------------------------------------------------------------------------- B4
def test_the_small_company_sentence_is_in_the_header_once_and_rows_carry_a_short_tag(
        small_company):
    text = format_company_report(small_company)
    assert text.count("no track record applies") == 1             # the header, once
    rows = [ln for ln in text.splitlines()
            if ln.startswith("  ") and "votes" in ln or "marks (does not vote)" in ln]
    tagged = [ln for ln in rows if ln.rstrip().endswith(OUTSIDE_RANGE_ROW_TAG)]
    assert OUTSIDE_RANGE_ROW_TAG == "(outside tested range)"
    assert len(tagged) >= 5
    html = company_report_html(small_company)
    assert html.count("no track record applies") == 1


# ----------------------------------------------------------------------------- B5 / B6
def _group():
    subject = IndexRow(ticker="NVCR.US", name="NovoCure Ltd", exchange="US", country="US",
                       currency="USD", market_cap=1.9e9, market_cap_usd=1.9e9,
                       sector="Healthcare", industry="Medical Devices")
    members = [IndexRow(ticker=f"P{i}.US", name=f"Peer {i} Inc", exchange="US", country="US",
                        currency="USD", market_cap=2e9, market_cap_usd=2e9, sector="Healthcare",
                        industry="Medical Devices") for i in range(4)]
    return PeerGroup(subject=subject, members=members, rung="sub-industry, 1/4x–4x",
                     band="0.25x-4x market cap (USD)", snapshot="2026-09-25", step=1,
                     distinct_companies=4,
                     reasons=["matched on: GICS only 0, EODHD label only 2, both 2",
                              "414 candidates skipped: market excluded by setting (SA)",
                              "716 cross-listings collapsed into their home listing"])


def test_the_peers_header_states_the_size_band_once():
    line = _group().reader_sentence()
    assert line == ("4 peers: sub-industry, within 1/4x–4x of its market cap (USD) "
                    "(step 1 of 4)")
    assert line.count("market cap") == 1 and "0.25x" not in line


def test_the_header_keeps_the_broad_sector_warning():
    group = _group()
    group.rung = "sector, 1/10x-10x"
    group.step = 4
    assert "broad sector group" in group.reader_sentence()


def test_diagnostics_are_method_lines_not_header_or_footer_reader_text():
    group = _group()
    method = "\n".join(group.method_lines())
    for diagnostic in ("matched on:", "market excluded by setting", "cross-listings collapsed",
                       "index snapshot 2026-09-25", "4 distinct companies"):
        assert diagnostic in method
        assert diagnostic not in group.reader_sentence()


def test_the_text_report_puts_the_diagnostics_under_their_own_sub_heading_at_the_end(
        small_company):
    text = format_company_report(small_company)
    peers = text[text.index("PEERS ("):]
    head, _sep, tail = peers.partition("How this peer group was built:")
    assert _sep, "the sub-heading is missing"
    assert "matched on:" not in head and "candidates skipped" not in head
    assert "matched on:" in tail or "comparable companies found" in tail
    assert "† counted as a peer on one industry classification only" in head   # legend stays


def test_the_html_report_folds_the_diagnostics_into_a_collapsed_details_block(small_company):
    html = company_report_html(small_company)
    block = html[html.index("How this peer group was built"):]
    assert html.rfind("<details", 0, html.index("How this peer group was built")) != -1
    assert "<details class=\"gate\" open" not in html
    assert "matched on:" in block[:4000] or "comparable companies found" in block[:4000]
    before = html[:html.index("How this peer group was built")]
    assert "matched on: GICS" not in before


# ----------------------------------------------------------------------------- B7
@pytest.mark.parametrize("raw,clean", [
    ("Indivior PLC Ordinary Shares", "Indivior PLC"),
    ("MBX Biosciences, Inc. Common", "MBX Biosciences, Inc."),
    ("Alamar Biosciences, Inc. Com", "Alamar Biosciences, Inc."),
    ("CALIWAY BIOPHARMACEUTICALS CO., LTD.", "Caliway Biopharmaceuticals Co., Ltd."),
    ("NVIDIA Corporation", "NVIDIA Corporation"),            # mixed case is left alone
    ("Alphabet Inc. Class A", "Alphabet Inc. Class A"),      # a class that tells lines apart stays
    ("Commonwealth Bank of Australia", "Commonwealth Bank of Australia"),
    ("", ""),
])
def test_company_names_lose_their_share_class_and_all_caps(raw, clean):
    assert clean_company_name(raw) == clean


def test_the_name_column_is_wider_and_never_cuts_mid_word():
    group = _group()
    group.members[0] = IndexRow(
        ticker="3696.HK", name="InSilico Medicine Cayman Topco Limited Holdings", exchange="HK",
        country="HK", currency="HKD", market_cap=3e10, market_cap_usd=4.3e9, sector="Healthcare",
        industry="Biotechnology")
    lines = peer_text_lines(group)
    row = next(ln for ln in lines if ln.startswith("3696.HK"))
    assert "InSilico Medicine Cayman Top " not in row
    assert "InSilico Medicine Cayman…" in row or "InSilico Medicine Cayman Topco" in row


# ----------------------------------------------------------------------------- B8
@pytest.mark.parametrize("raw,short", [
    ("JPMorgan Chase & Co.", "JPMorgan Chase & Co."),     # a name never ends on "&"
    ("Procter & Gamble Co", "Procter & Gamble"),
    ("Micron Technology, Inc.", "Micron Technology"),      # cut at the first comma
    ("AstraZeneca PLC", "AstraZeneca"),
    ("Johnson & Johnson", "Johnson & Johnson"),
])
def test_the_short_name_never_ends_on_a_connector(raw, short):
    assert plain_company_name(raw) == short
    assert not plain_company_name(raw).endswith("&")


# ----------------------------------------------------------------------------- B10
def test_a_small_ranked_list_says_the_bottom_slot_is_forced():
    note = forced_bottom_note(6)
    assert note == ("With 6 names the bottom slot is forced; read it as “lowest of these”, "
                    "not as a warning.")
    assert forced_bottom_note(9) and not forced_bottom_note(10) and not forced_bottom_note(2)
    assert note in score_gloss_with_note(3, 6)
    assert score_gloss_with_note(3, 12).endswith("worst is 36.")
    assert "as few as 4" in forced_bottom_note_multi([4, 12, 2])
    assert forced_bottom_note_multi([12, 30]) == ""


def test_the_list_reports_carry_the_small_list_line_and_the_one_line_badge_form():
    """Built from the sweep's six-name list: the sentence is there, the five-sentence badge
    paragraph is not (it lives in the glossary)."""
    import tempfile
    from pathlib import Path

    from aristos_council.export.report_html import multi_strategy_report_html
    from aristos_council.pipeline import (format_multi_strategy_grid, run_multi_strategy_pipeline)
    from aristos_council.report_sweep import badge_paragraph_findings, visible_text
    from tests.test_report_sweep import RUN_START

    with tempfile.TemporaryDirectory() as tmp:
        multi = run_multi_strategy_pipeline(
            META["car_list"], META["list_lenses"], adapter=FrozenAdapter(FIX / "frozen"),
            today=TODAY, use_cache=False, freeze_dir=Path(tmp))
    page = visible_text(multi_strategy_report_html(multi, run_start=RUN_START))
    assert "bottom slot is forced" in page
    assert "bottom slot is forced" in format_multi_strategy_grid(multi)
    assert not badge_paragraph_findings(page, "multi html")
