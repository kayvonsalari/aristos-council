"""REPEATS-NAMES-1 - three things the live sweep (2026-10-22) printed more than once or cut short.

(a) KO.US "### Lens notes": the track-record chance sentence ("Across all tested industries, 15 of
    72 cleared the bar (about 9 to 10 would by chance) ... These results are exploratory, not proof
    of a repeatable edge.") ended every lens line, five times. It is now stated ONCE under the
    heading; each lens line keeps its own result.
(b) "Analysts expect The Coca-Cola to earn", "Analysts expect Ford Motor to earn": the analyst lines
    now use the story's name rule (Coca-Cola, Ford, JPMorgan).
(c) list_oil_6: the header read "shortlist: 1 on 3 of 8, 3 on 1 of 8" over a table reading "3 of 3
    ... (5 did not apply)". The header now uses the table's form: the lenses that voted, with the
    ones that did not apply said apart.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from aristos_council.abs_readings import analyst_trend
from aristos_council.backtest import evidence_summary
from aristos_council.company_check import run_company_check
from aristos_council.company_markdown import company_report_markdown
from aristos_council.company_story import lens_notes_chance, short_company_name, table_rows
from aristos_council.data.adapter import Fundamentals
from aristos_council.data.analyst_trend import TrendData, TrendPeriod
from aristos_council.export.report_html import company_report_html
from aristos_council.pipeline import lens_agreement, lens_agreement_table
from tests.test_agreement import _Multi, _multi, _Res, _Row, _Strat
from tests.test_backtest_badges import _badge
from tests.test_company_check import STRAT_DIR, UNIV_DIR, _OneName
from tests.test_company_report import RAW, SCREENED, _run


# --------------------------------------------------------------------------- #
# (a) the chance sentence, once
# --------------------------------------------------------------------------- #
def _badged(tmp_path):
    rep = _run([RAW, SCREENED], tmp_path=tmp_path)
    rep.votes = [replace(v, badge=_badge(0.0257, 0.056, 5)) if v.ranked else v for v in rep.votes]
    assert sum(1 for v in rep.votes if v.badge is not None) >= 1
    return rep


def test_the_chance_sentence_is_said_once_under_lens_notes_in_markdown(tmp_path):
    rep = _badged(tmp_path)
    chance = evidence_summary().sentence()
    md = company_report_markdown(rep)
    notes = md.split("### Lens notes", 1)[1].split("### Sources", 1)[0]
    assert notes.count(chance) == 1
    assert notes.lstrip().startswith(chance)                     # directly under the heading
    for line in notes.splitlines():
        if line.startswith("- ") and "track record:" in line:
            assert chance not in line and "beat its group" in line


def test_each_lens_note_keeps_only_its_own_result(tmp_path):
    rows = table_rows(_badged(tmp_path))
    details = [r.badge_detail for r in rows if r.badge_detail]
    assert details and all("would by chance" not in d for d in details)
    assert lens_notes_chance(rows) == evidence_summary().sentence()


def test_no_badge_no_chance_sentence(tmp_path):
    rep = _run([RAW], tmp_path=tmp_path)
    rep.votes = [replace(v, badge=None) for v in rep.votes]
    assert lens_notes_chance(table_rows(rep)) == ""


def test_the_html_lens_notes_say_it_once_too(tmp_path):
    import html
    page = html.unescape(company_report_html(_badged(tmp_path)))
    assert page.count(evidence_summary().sentence()) == 1


# --------------------------------------------------------------------------- #
# (b) the analyst lines use the story's name
# --------------------------------------------------------------------------- #
def test_the_story_and_the_analyst_line_share_one_name_rule():
    assert short_company_name("KO", "The Coca-Cola Company") == "Coca-Cola"
    assert short_company_name("F", "Ford Motor Company") == "Ford"
    assert short_company_name("JPM", "JPMorgan Chase & Co.") == "JPMorgan"
    # the cleanups the analyst line already had stay
    assert short_company_name("2330.TW", "Taiwan Semiconductor Manufacturing Co Ltd") == \
        "Taiwan Semiconductor Manufacturing"
    assert short_company_name("X", "") == "X"


def _trend():
    return TrendData(current=TrendPeriod("2026-12-31", 3.30, 3.27, 20),
                     next_year=TrendPeriod("2027-12-31", 3.52, 3.48, 18), as_of="2026-10-22")


def test_live_lines_read_the_short_name():
    for ticker, legal, short in (("KO", "The Coca-Cola Company", "Coca-Cola"),
                                 ("F", "Ford Motor Company", "Ford"),
                                 ("JPM", "JPMorgan Chase & Co.", "JPMorgan")):
        trend = analyst_trend(_trend(), currency="USD", company=short_company_name(ticker, legal))
        first = trend.forecast_sentences()[0]
        assert first.startswith(f"Analysts expect {short} to earn $3.30 per share this year.")


def test_company_check_hands_the_analyst_line_the_story_name():
    """The call site: Ford's legal name in, "Ford" in the sentence (live: "Ford Motor")."""
    ford = Fundamentals(ticker="F", company_name="Ford Motor Company", market_cap=4.0e10,
                        sector="Consumer Cyclical", ebit=[3000.0], pe_ratio=8.0,
                        operating_income=[500.0] * 4, tax_provision=[100.0] * 4,
                        pretax_income=[480.0] * 4, invested_capital=[8000.0] * 4,
                        total_revenue=[250.0, 200, 170, 150])
    result = run_company_check("F", "magic_formula_momentum_v1", "", adapter=_OneName(ford),
                               strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR,
                               runs_dir=Path("runs"), today=date(2026, 6, 30),
                               with_analyst_trend=True,
                               analyst_fetcher=lambda ticker, *, today: _trend())
    first = result.analyst_trend.forecast_sentences()[0]
    assert first.startswith("Analysts expect Ford to earn")
    assert "Ford Motor" not in first


# --------------------------------------------------------------------------- #
# (c) the list header and the shortlist table count the same way
# --------------------------------------------------------------------------- #
def _oil_shape():
    """Three voting lenses rank EQ; the other two lenses' rules exclude every name (the live run
    had five such lenses out of eight)."""
    tail = [_Row("P", "hold", 4), _Row("Q", "hold", 5)]
    rows = {"a_v1": [_Row("EQ", "buy", 1), _Row("CN", "buy", 2), _Row("TT", "hold", 3), *tail],
            "b_v1": [_Row("EQ", "buy", 1), _Row("CN", "hold", 2), _Row("TT", "buy", 3), *tail],
            "c_v1": [_Row("EQ", "buy", 1), _Row("CN", "hold", 2), _Row("TT", "hold", 3), *tail],
            "d_v1": [], "e_v1": []}
    results = {sid: _Res(list(r), _Strat("selector")) for sid, r in rows.items()}
    return lens_agreement(_Multi(list(results), {sid: sid.upper() for sid in results}, results))


def test_header_and_table_use_the_same_denominator():
    ag = _oil_shape()
    assert ag.n_voting == 5
    _cols, rows = lens_agreement_table(ag)
    cells = {r["Name"]: r["BUY votes"] for r in rows}
    assert any(c.startswith("3 of 3") and "(2 did not apply)" in c for c in cells.values())
    assert ag.summary_clause == (" — shortlist: 1 on 3 of 3 (2 did not apply), "
                                 "2 on 1 of 3 (2 did not apply)")
    assert "of 5" not in ag.summary_clause


def test_a_lens_that_voted_on_every_name_keeps_the_all_lenses_wording():
    tail = [_Row("P", "hold", 3), _Row("Q", "hold", 4), _Row("R", "hold", 5)]
    ag = lens_agreement(_multi(
        a_v1=[_Row("ALL", "buy", 1), _Row("SOME", "buy", 2), *tail],
        b_v1=[_Row("ALL", "buy", 1), _Row("SOME", "hold", 2), *tail]))
    assert ag.summary_clause == " — shortlist: 1 name BUY on all 2 voting lenses, 1 on 1 of 2"
