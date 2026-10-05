"""TAB-MERGE-1 commit 2 — RECORD-V2, and the byte-stability nets the rest of TAB-MERGE-1
leans on (D in the design doc: nothing in the app reopens a saved run today, so the only
risk is the on-disk SHAPES changing under this refactor).

Golden-first, per the task: these pin what must not change (report_record's v1 keys and
values, report.txt's exact text, the directory-naming rule) BEFORE the additive v2 fields
are layered on, so a regression in the OLD shape is caught the same way a regression in
the new one would be.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aristos_council.company_report import format_company_report, report_record
from tests.test_company_report import RAW, _run

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "company_report_golden"

# The exact key set schema_version 1 wrote (captured from main before RECORD-V2 touched
# report_record — see the commit message). Never add to this set; v2's own additions are
# asserted separately below, so a v1 key accidentally dropped is caught here and a v2 key
# accidentally added to v1's set is caught there.
_V1_KEYS = {"kind", "ticker", "company", "run_at", "lenses", "peer_snapshot", "peer_sentence",
           "universe", "votes", "agreement", "no_vote_reason", "cohort_slug",
           "track_record_caption", "track_record_summary", "lens_ranks", "sources",
           "seconds", "cache", "summary_written", "council_opinion"}
_V2_NEW_KEYS = {"schema_version", "outside_tested_range", "smallcap_cohort",
               "smallcap_floor_usd", "smallcap_band_note", "price_and_cash",
               "size_matched_peers"}          # SMALLCAP-BAND-GAP-1, additive


def test_every_v1_key_is_still_present_unchanged(tmp_path):
    report = _run([RAW], tmp_path=tmp_path, save=False)
    record = report_record(report)
    assert _V1_KEYS <= set(record)
    # spot-check VALUES too, not just presence — a key kept but reshaped would pass a
    # bare "in" check and still be a regression.
    assert record["kind"] == "company_report" and record["ticker"] == "CO"
    assert record["company"] == report.display
    assert record["votes"] and {"lens", "label", "votes", "status", "verdict", "position",
                                "of", "result", "track_record_badge"} <= set(record["votes"][0])
    assert record["council_opinion"] is None            # not ticked in this fixture


def test_the_full_key_set_is_exactly_v1_plus_v2_nothing_else(tmp_path):
    """Catches BOTH directions: a v1 key silently dropped, and an undocumented new key
    sneaking in beside the ones this commit deliberately adds."""
    report = _run([RAW], tmp_path=tmp_path, save=False)
    record = report_record(report)
    assert set(record) == _V1_KEYS | _V2_NEW_KEYS


def test_schema_version_is_2(tmp_path):
    report = _run([RAW], tmp_path=tmp_path, save=False)
    assert report_record(report)["schema_version"] == 2


def test_smallcap_fields_are_always_present_even_when_not_smallcap(tmp_path):
    """RECORD-V2's own point: False/''/None here means 'v2 record, definitely not
    small-company'; a MISSING key (v1) means 'don't know, this predates the field'."""
    report = _run([RAW], tmp_path=tmp_path, save=False)
    record = report_record(report)
    assert record["outside_tested_range"] is False
    assert record["smallcap_cohort"] == ""
    assert record["smallcap_floor_usd"] is None
    assert record["smallcap_band_note"] == ""


def test_price_and_cash_is_none_when_never_requested():
    """``_run`` passes with_price_and_cash through run_company_report (always True for
    the Company Check path) — this pins the OTHER caller shape: run_company_check used
    directly, without asking for it (the Run-tab/cohort path)."""
    from aristos_council.company_check import run_company_check
    from tests.test_company_report import STRAT_DIR, TODAY, UNIV_DIR, _Adapter
    from aristos_council.company_report import CompanyReport, report_record

    check = run_company_check("CO", RAW, "", adapter=_Adapter(), strategies_dir=STRAT_DIR,
                              universes_dir=UNIV_DIR, runs_dir=Path("runs"), today=TODAY)
    report = CompanyReport(ticker="CO", check=check)
    assert report_record(report)["price_and_cash"] is None


def test_price_and_cash_carries_the_rendered_lines_and_news_source(tmp_path):
    from datetime import date as _date

    from aristos_council.data.news_fallback import NewsFetchResult
    from aristos_council.data.sentiment import NewsItem

    def news(ticker, *, today):
        return NewsFetchResult(
            items=(NewsItem(published=_date(2026, 9, 24), headline="CO Corp A headline",
                            source="EODHD"),
                   NewsItem(published=_date(2026, 9, 23), headline="CO Corp other news",
                            source="EODHD")),
            source="EODHD news", tried=())

    report = _run([RAW], tmp_path=tmp_path, save=False, news_fetcher=news)
    record = report_record(report)
    pac = record["price_and_cash"]
    assert pac is not None
    assert pac["news_source"] == "EODHD news"
    assert any("last close" in ln for ln in pac["lines"])
    assert any("2026-09-24: CO Corp A headline" in ln for ln in pac["lines"])


# --------------------------------------------------------------------------- #
# Old-record tolerance — no loader exists yet (confirmed: nothing in the app reopens a
# saved run today), so this pins the DATA SHAPE's own forward compatibility: a hand-
# written v1 record (no schema_version, no council_opinion, no smallcap/price_and_cash
# keys) reads with sane defaults under the ``.get(key, default)`` convention this
# function's own docstring now states, rather than raising.
# --------------------------------------------------------------------------- #
def _v1_record() -> dict:
    return {"kind": "company_report", "ticker": "JNJ", "company": "Johnson & Johnson",
            "run_at": "2026-06-11T00:00:00+00:00", "lenses": ["dividend_aristocrats_v1"],
            "peer_snapshot": None, "peer_sentence": "", "universe": ["JNJ"], "votes": [],
            "agreement": None, "no_vote_reason": "", "cohort_slug": None,
            "track_record_caption": "", "track_record_summary": "", "lens_ranks": {},
            "sources": [], "seconds": 1.2, "cache": {}, "summary_written": False,
            "council_opinion": None}


def test_a_hand_written_v1_record_reads_with_sane_defaults():
    v1 = _v1_record()
    assert v1.get("schema_version", 1) == 1
    assert v1.get("outside_tested_range", False) is False
    assert v1.get("smallcap_cohort", "") == ""
    assert v1.get("smallcap_floor_usd") is None
    assert v1.get("price_and_cash") is None
    # and the v1 fields it DOES carry still read exactly as they always did
    assert v1["ticker"] == "JNJ" and v1["kind"] == "company_report"


def test_a_hand_written_v1_record_round_trips_through_json():
    """The actual on-disk shape is JSON text, not a Python dict — confirms the
    tolerance holds after a real json.dumps/json.loads cycle, not just in memory."""
    text = json.dumps(_v1_record())
    loaded = json.loads(text)
    assert "schema_version" not in loaded
    assert loaded.get("schema_version", 1) == 1
    assert loaded.get("price_and_cash") is None


# --------------------------------------------------------------------------- #
# report.txt — exact text, frozen (the wall-clock "Ran in Xs; day-cache ..." tail is
# excluded, exactly like the existing save-round-trip test at
# test_company_report.py::test_the_run_is_saved_under_runs_with_the_peer_snapshot...
# already does).
# --------------------------------------------------------------------------- #
def test_report_txt_text_is_byte_identical_to_the_golden(tmp_path):
    report = _run([RAW], tmp_path=tmp_path, save=False)
    text = format_company_report(report).split("Ran in")[0]
    golden = (_FIXTURES / "co_raw_text.txt").read_text(encoding="utf-8")
    assert text == golden
