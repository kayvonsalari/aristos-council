"""COMPANY-STORY-1 - the first screen of a company page is written by code from templates.

One fixture report per template branch, built WITHOUT an adapter (the story reads only figures the
run already holds), so each branch's sentences are pinned exactly: no votes; votes with a BUY; votes
without a BUY; a bank; under $5bn; an untested industry; the model's summary withheld; the model's
summary written (it replaces the story, never sits beside it). The renderers all read one
``StoryPage``, so a last group of tests pins that the text, the HTML and the Markdown agree and keep
the design's order.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aristos_council import report_sweep as sweep
from aristos_council.abs_readings import (DebtAndCash, GrowthLeg, GrowthRecord, Reading)
from aristos_council.backtest import Badge
from aristos_council.company_markdown import company_report_markdown
from aristos_council.company_report import (NO_COHORT_TRACK_RECORD_LINE, CompanyReport,
                                            CouncilOpinion, LensVote, build_agreement,
                                            format_company_report)
from aristos_council.company_story import (NOT_A_PREDICTION, SUMMARY_WITHHELD_PREFIX, answer_lines,
                                           narration_check_line, short_name, story_page,
                                           story_paragraphs)
from aristos_council.export.report_html import company_report_html
from aristos_council.reader import ReaderResult, reader_paragraphs
from tests.test_company_report import RAW, SCREENED, _run, _Writer, _fields

NO_PROFIT = "no operating profit (fiscal year to Dec 2025)"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _check(*, name="Acme Corporation", bank=False, band=None, accounts_basis=None,
           group=True, ratings=True):
    net = (Reading(not_meaningful="debt and cash: not meaningful for banks and insurers")
           if bank else Reading(value=-1.0e9, label="owes $1.0bn net of cash"))
    years = Reading(value=3.0, label="would take 3.0 years of free cash flow to repay its debt")
    dc = DebtAndCash(net_debt=net, years_to_repay=years)
    leg = lambda text: GrowthLeg(cagr={5: Reading(value=0.08, label=text, span=5)})   # noqa: E731
    growth = GrowthRecord(revenue=leg("revenue compounded +8.0% a year over 5 years"),
                          eps=leg("earnings per share compounded +6.0% a year over 5 years"))
    rv = SimpleNamespace(
        available=ratings, summary_line=lambda: "20 analysts: 5 strong buy, 3 buy, 12 hold, 0 sell, "
                                                "0 strong sell",
        target_sentence="Average price target $16.03, 32% above today's price")
    peer = SimpleNamespace(members=list(range(12)), step=1, snapshot="2026-09-25", thin=False,
                           broad=False) if group else None
    return SimpleNamespace(
        company_name=name, display=f"{name} (ACM)", ticker="ACM", unrateable=False,
        peer_group=peer, debt_and_cash=dc, growth_record=growth,
        accounts={"basis": accounts_basis or "annual accounts for the fiscal year to Dec 2025 "
                                              "(the filing date is not in the data)"},
        providers={"as_of": "2026-10-05", "price_as_of": "2026-10-02"},
        analyst_trend=SimpleNamespace(ratings=rv, as_of="2026-10-05"),
        band_percentile=band,
        valuation_band="not evaluated — valuation measure undefined in 61 of 61 months"
        if band is None else f"P/E 15.5 — {band}th percentile of its own 5-year range",
        data_integrity=SimpleNamespace(note=""), pointer="")


def _rank(label, verdict, pos, of=14, badge=None, kind="selector", factor_note=""):
    return LensVote(label.lower().replace(" ", "_") + "_v1", label, kind=kind, status="ranked",
                    verdict=verdict, position=pos, cohort_size=of, badge=badge,
                    asks=f"{label} asks a plain question.", factor_note=factor_note)


def _skip(label, reason=NO_PROFIT):
    return LensVote(label.lower().replace(" ", "_") + "_v1", label, status="excluded", reason=reason,
                    asks=f"{label} asks a plain question.")


def _story_text(rep) -> str:
    """Everything sections 1-3 print, as one string (no renderer needed)."""
    page = story_page(rep)
    parts = [*page.answer, *(f"{lead} {text}" for lead, text in page.paragraphs), page.note,
             *page.tag, page.no_vote, page.caption]
    parts += [" ".join(r.cells()) for r in page.rows]
    return "\n".join(p for p in parts if p)


def _report(votes, check=None, **kw) -> CompanyReport:
    check = check or _check()
    rep = CompanyReport(ticker="ACM", check=check, votes=list(votes), **kw)
    if votes and any(v.status != "no_group" for v in votes):
        rep.agreement = build_agreement(votes, band_percentile=check.band_percentile)
    return rep


_PROVEN = Badge(label="proven here", verdict="proven", mean_excess=0.04, luck_pct=0.03,
                years_positive=8, years_measured=10, rounds_held=100)
_PROMISING = Badge(label="promising here", verdict="promising", mean_excess=0.03, luck_pct=0.1,
                   years_positive=7, years_measured=10, rounds_held=100)


# --------------------------------------------------------------------------- #
# branch 1 - no votes
# --------------------------------------------------------------------------- #
def test_story_no_votes_names_the_one_reason_that_excluded_every_lens():
    rep = _report([_skip("Quality"), _skip("Magic Formula RAW"), _skip("Growth")])
    first, second = answer_lines(rep)
    assert first == "No lens voted on Acme."
    assert second == f"None of the three lenses could apply: {NO_PROFIT}."
    page = story_page(rep)
    assert [r.outcome for r in page.rows] == ["does not apply"] * 3
    assert all(r.reason == NO_PROFIT for r in page.rows)


def test_story_the_most_common_reason_leads_and_carries_its_figure():
    votes = [_skip("Quality"), _skip("Growth", "revenue grew 5.7% a year; the rule requires at least 10%"),
             _skip("Magic Formula RAW"), _skip("Cyclical Income")]
    first, second = answer_lines(_report(votes))
    assert first == "No lens voted on Acme."
    assert second == ("Four lenses did not apply; the most common reason (three of them): "
                      f"{NO_PROFIT}.")


def test_story_a_reason_with_different_figures_is_one_reason():
    votes = [_skip("A", "return on invested capital 9.3%; the rule requires at least 12%"),
             _skip("B", "return on invested capital 4.1%; the rule requires at least 12%"),
             _skip("C", "dividend yield 0%; the rule requires at least 1.5%")]
    _first, second = answer_lines(_report(votes))
    assert "the most common reason (two of them): return on invested capital 9.3%" in second


# --------------------------------------------------------------------------- #
# branches 2 and 3 - votes with a BUY, votes without one
# --------------------------------------------------------------------------- #
def test_story_votes_with_a_buy_state_the_split_and_name_the_lenses():
    rep = _report([_rank("Quality", "buy", 3), _rank("Growth", "hold", 8), _rank("Value", "sell", 14),
                   _skip("Magic Formula RAW")])
    first, second = answer_lines(rep)
    assert first == "One of three votes says BUY (Quality); one says HOLD (Growth); one says SELL (Value)."
    assert second.startswith("One lens did not apply: " + NO_PROFIT)        # B22-U10
    assert "all for one reason" not in second


def test_story_votes_without_a_buy_say_so_by_leading_with_what_voted():
    rep = _report([_rank("Quality", "hold", 5), _rank("Growth", "hold", 6), _rank("Value", "sell", 14)])
    first, _second = answer_lines(rep)
    assert first == "Two of three votes say HOLD (Quality and Growth); the other one says SELL (Value)."
    assert "BUY" not in first


def test_story_one_vote_names_the_lens_and_its_place():
    rep = _report([_rank("Quality", "buy", 3), _skip("Growth")])
    assert answer_lines(rep)[0] == "BUY, on the one lens that voted (Quality, 3rd of 14)."


def test_story_the_check_lens_marks_and_the_badge_count_is_in_the_second_line():
    forensic = _rank("Forensic", "buy", 9, kind="check")
    rep = _report([_rank("Quality", "buy", 3, badge=_PROMISING), forensic])
    rep.track_record_caption = "Track record from the Autos cohort, 10 years to Sep 2026"
    second = answer_lines(rep)[1]
    assert "Forensic reads clean." in second


# --------------------------------------------------------------------------- #
# branch 4 - a bank
# --------------------------------------------------------------------------- #
def test_story_a_bank_says_which_lens_is_built_for_it_and_that_the_rest_are_not():
    rep = _report([_rank("Financials", "hold", 19, of=27, badge=Badge(
        label="untested here", verdict=None, mean_excess=None, luck_pct=None, years_positive=None,
        years_measured=None, rounds_held=None, note="no backtested cohort covers it")),
        _skip("Quality", "sector excluded (Financial Services)"),
        _skip("Growth", "sector excluded (Financial Services)")],
        check=_check(name="Big Bank Corp", bank=True))
    rep.track_record_caption = NO_COHORT_TRACK_RECORD_LINE
    first, second = answer_lines(rep)
    assert first == "HOLD, on one lens built for banks (Financials, 19th of 27)."
    assert second == ("The other two lenses are not for banks. No track record exists for this "
                      "industry yet.")
    survived = dict(story_paragraphs(rep))["Other facts."]
    assert survived.startswith("Debt and cash do not describe a bank or insurer")
    assert story_page(rep).caption == ""          # the Badge column and the answer already say it


# --------------------------------------------------------------------------- #
# branch 5 - under $5bn
# --------------------------------------------------------------------------- #
def _small():
    rep = _report([_skip("Quality", "market cap below the $5.0bn minimum"),
                   _skip("Growth", "market cap below the $5.0bn minimum")],
                  check=_check(name="Tiny Therapeutics Inc"), outside_tested_range=True)
    rep.smallcap_cohort, rep.smallcap_floor_usd = "Biotechnology", 1.0e9
    return rep


def test_story_under_5bn_says_so_in_the_answer_and_states_the_tag_once_above_the_table():
    rep = _small()
    assert answer_lines(rep)[0] == "No lens voted on Tiny Therapeutics."
    assert "It is also outside the tested range (under $5bn): no track record applies." in \
        answer_lines(rep)[1]
    page = story_page(rep)
    assert page.tag[0] == rep.tested_range_line
    text = _story_text(rep)
    assert text.count(rep.tested_range_line) == 1
    assert "(outside tested range)" not in text          # no per-row repeat
    assert dict(story_paragraphs(rep))["What to doubt."].count("under $5bn") == 1


def test_story_under_5bn_cannot_tell_paragraph_repeats_the_untested_range():
    cannot = dict(story_paragraphs(_small()))["What this cannot tell you."]
    assert cannot.startswith(NOT_A_PREDICTION) and "outside the tested range" in cannot


# --------------------------------------------------------------------------- #
# branch 6 - an industry no backtest covers
# --------------------------------------------------------------------------- #
def test_story_an_untested_industry_says_so_without_the_word_cohort():
    rep = _report([_rank("Quality", "hold", 5), _rank("Growth", "hold", 7)])
    rep.track_record_caption = NO_COHORT_TRACK_RECORD_LINE
    assert answer_lines(rep)[1].endswith("No track record exists for this industry yet.")
    assert "cohort" not in " ".join(answer_lines(rep)).lower()
    assert story_page(rep).caption == ""


def test_story_a_tested_industry_shows_the_caption_without_the_word_cohort():
    rep = _report([_rank("Quality", "hold", 5)])
    rep.track_record_caption = "Track record from the Auto Manufacturers cohort, 10 years to Sep 2026"
    assert story_page(rep).caption == ("Track record from tests on Auto Manufacturers companies, "
                                       "10 years to Sep 2026")


# --------------------------------------------------------------------------- #
# branches 7 and 8 - the model's summary: withheld, and written
# --------------------------------------------------------------------------- #
def test_story_model_summary_withheld_keeps_the_story_and_says_why_in_one_line(tmp_path):
    probe = _run([RAW], tmp_path=tmp_path, save=False)
    writer = _Writer(_fields(probe, extra=" Its profit grew 777 percent."))   # a number not on the page
    rep = _run([RAW], tmp_path=tmp_path, with_summary=True, reader_runner=writer, save=False)
    page = story_page(rep)
    assert not page.model_summary and not rep.summary.available
    assert page.note == f"{SUMMARY_WITHHELD_PREFIX}: {rep.summary.note.rstrip('. ')}."
    assert "number not in the facts: 777" in page.note
    assert [lead for lead, _t in page.paragraphs][0] == "What this run asked."
    text = format_company_report(rep)
    assert text.count(SUMMARY_WITHHELD_PREFIX) == 1 and NOT_A_PREDICTION in text
    assert SUMMARY_WITHHELD_PREFIX in company_report_html(rep)
    assert SUMMARY_WITHHELD_PREFIX in company_report_markdown(rep)


def test_story_model_summary_written_replaces_the_story_never_two(tmp_path):
    probe = _run([RAW], tmp_path=tmp_path, save=False)
    rep = _run([RAW], tmp_path=tmp_path, with_summary=True, reader_runner=_Writer(_fields(probe)),
               save=False)
    assert rep.summary.available
    page = story_page(rep)
    assert page.model_summary and not page.note
    assert list(page.paragraphs) == reader_paragraphs(rep.summary.summary, company=True)
    for doc in (format_company_report(rep), company_report_html(rep), company_report_markdown(rep)):
        assert "One test may not apply." in doc            # the model's own "doubt" paragraph
        assert NOT_A_PREDICTION not in doc                 # the code-written story is not beside it


def test_story_no_summary_ticked_shows_the_code_story_and_no_note():
    rep = _report([_rank("Quality", "hold", 5)])
    page = story_page(rep)
    assert rep.summary is None and not page.model_summary and page.note == ""


# --------------------------------------------------------------------------- #
# no peer group; every figure carries its date
# --------------------------------------------------------------------------- #
def test_story_no_peer_group_says_no_lens_could_rank_and_why():
    rep = _report([LensVote("quality_v1", "Quality", status="no_group", reason="no peer group")],
                  check=_check(group=False), no_vote_reason="no peer group could be formed")
    rep.agreement = None
    first, _second = answer_lines(rep)
    assert first == "No lens could rank Acme: no peer group could be formed."
    asked = dict(story_paragraphs(rep))["What this run asked."]
    assert asked.endswith("so no lens ran.")


def test_story_every_figure_carries_its_as_of_date():
    rep = _report([_rank("Quality", "hold", 5)], check=_check(band=94))
    paras = dict(story_paragraphs(rep))
    # B22-B5: the peer-search detail (step, market-index date) moved under "How this peer group was
    # built" in the workings; the story just says who it was ranked against.
    assert "market index of" not in paras["What this run asked."]
    survived = paras["Other facts."]
    assert "Debt and cash (fiscal year to Dec 2025)" in survived
    assert "Growth record (fiscal year to Dec 2025)" in survived
    assert "Analysts (2026-10-05)" in survived
    assert "prices to 2026-10-02" in paras["What happened."]


def test_story_accounts_without_a_dated_series_say_latest_annual_accounts():
    rep = _report([_rank("Quality", "hold", 5)],
                  check=_check(accounts_basis="the latest annual accounts (their period end date is "
                                              "not in the data)"))
    assert "Debt and cash (latest annual accounts)" in dict(story_paragraphs(rep))["Other facts."]


def test_story_net_cash_is_never_described_as_zero_years_to_repay():
    check = _check()
    check.debt_and_cash = DebtAndCash(
        net_debt=Reading(value=5e8, label="holds $500.0m more cash than debt"),
        years_to_repay=Reading(value=0.0, label="has no net debt to repay"))
    survived = dict(story_paragraphs(_report([_rank("Quality", "hold", 5)], check=check)))["Other facts."]
    assert "holds $500.0m more cash than debt." in survived and "0.0 years" not in survived


def test_story_short_name_cuts_at_the_comma_or_legal_suffix_never_at_an_ampersand():
    mk = lambda n: short_name(SimpleNamespace(check=SimpleNamespace(company_name=n), ticker="X"))  # noqa: E731
    assert mk("Ford Motor Company") == "Ford Motor"
    assert mk("Advanced Micro Devices, Inc.") == "Advanced Micro Devices"
    assert mk("JPMorgan Chase & Co.") == "JPMorgan Chase"
    assert mk("") == "X"


# --------------------------------------------------------------------------- #
# the sweep's words, on every branch
# --------------------------------------------------------------------------- #
def _every_branch():
    forensic = _rank("Forensic", "buy", 9, kind="check")
    bank = _report([_rank("Financials", "hold", 19, of=27), _skip("Quality", "sector excluded (Financial Services)")],
                   check=_check(name="Big Bank Corp", bank=True))
    bank.track_record_caption = NO_COHORT_TRACK_RECORD_LINE
    tested = _report([_rank("Quality", "buy", 3, badge=_PROVEN), _rank("Growth", "hold", 8), forensic])
    tested.track_record_caption = "Track record from the Auto Manufacturers cohort, 10 years to Sep 2026"
    withheld = _report([_rank("Quality", "hold", 5)])
    withheld.summary = ReaderResult(note="no API key")
    return {"none": _report([_skip("Quality"), _skip("Growth")]), "bank": bank, "small": _small(),
            "tested": tested, "withheld": withheld,
            "nogroup": _report([LensVote("q", "Quality", status="no_group", reason="none")],
                               check=_check(group=False), no_vote_reason="no peer group")}


@pytest.mark.parametrize("name", ["none", "bank", "small", "tested", "withheld", "nogroup"])
def test_story_sections_one_to_three_have_no_id_column_name_cohort_or_strategy(name):
    """Every template branch, by the sweep's own rule (the sweep applies it to all three real
    exports of real companies in ``test_report_sweep``)."""
    assert sweep.story_findings(_story_text(_every_branch()[name]), name) == []


def test_story_a_lens_id_in_a_reason_is_caught_by_the_same_rule():
    rep = _report([_skip("Quality", "ranked by magic_formula_raw_v1 and a cohort")])
    assert sweep.story_findings(_story_text(rep), "x")


# --------------------------------------------------------------------------- #
# one page plan: the text, the HTML and the Markdown agree, in the design's order
# --------------------------------------------------------------------------- #
def _real(tmp_path, **attrs):
    rep = _run([RAW, SCREENED], tmp_path=tmp_path, save=False)
    for key, value in attrs.items():
        setattr(rep, key, value)
    return rep


def test_story_the_three_renderers_carry_the_same_answer_table_and_order(tmp_path):
    rep = _real(tmp_path)
    page = story_page(rep)
    text, html, md = format_company_report(rep), company_report_html(rep), company_report_markdown(rep)
    for line in page.answer:
        assert line in text and line in md and line.replace("&", "&amp;") in html
    for row in page.rows:
        assert row.outcome in text and row.outcome in md and row.outcome in html
    # the table, then the fold, in every rendering
    assert text.index("Vote or mark") < text.index("SHOW THE WORKINGS")
    assert md.index("| Lens | Vote or mark | Badge | Reason |") < md.index("## Show the workings")
    assert html.index("<h2>Lens by lens</h2>") < html.index('id="workings"')
    # "what it asks" is a tooltip in the HTML and a footnote in the Markdown
    first = next(r for r in page.rows if r.asks)
    assert f'title="{first.asks}"' in html.replace("&#x27;", "'")
    assert f"[^1]: {first.lens} asks: {first.asks}" in md and f"{first.lens}[^1]" in md
    # the workings are folded in the HTML and sub-headings at the end of the Markdown
    assert '<details class="gate workings"' in html and "<summary>Show the workings</summary>" in html
    assert "### Valuation band" in md and md.index("### Sources") > md.index("### Peers")


def test_story_council_opinion_sits_under_the_table_above_the_fold_only_when_ticked(tmp_path):
    rep = _real(tmp_path)
    assert "COUNCIL OPINION" not in format_company_report(rep)
    assert "Council opinion" not in company_report_html(rep)
    assert "Council opinion" not in company_report_markdown(rep)
    rep.council_opinion = CouncilOpinion(available=True, narrative="It ranked well.")
    text, html, md = format_company_report(rep), company_report_html(rep), company_report_markdown(rep)
    assert text.index("Vote or mark") < text.index("COUNCIL OPINION") < text.index("SHOW THE WORKINGS")
    assert html.index("<h2>Lens by lens</h2>") < html.index("<h2>Council opinion</h2>") < html.index('id="workings"')
    assert md.index("## Lens by lens") < md.index("## Council opinion") < md.index("## Show the workings")


def test_story_the_narration_check_is_one_folded_line_and_only_with_a_council_opinion(tmp_path):
    rep = _real(tmp_path)
    assert narration_check_line(rep) == ""
    rep.council_opinion = CouncilOpinion(available=True, narrative="Fine. [⚠ narration check: \"x\" is wrong]")
    assert narration_check_line(rep) == ("Narration check: 1 statement in the council opinion was "
                                         "flagged; each is marked where it appears.")
    assert narration_check_line(rep) in format_company_report(rep).split("SHOW THE WORKINGS")[1]


def test_story_the_text_keeps_every_section_below_the_fold(tmp_path):
    text = format_company_report(_real(tmp_path))
    below = text.split("SHOW THE WORKINGS")[1]
    for heading in ("VALUATION BAND", "PRICE AND CASH", "ABSOLUTE READINGS", "WHAT ANALYSTS SAY",
                    "PEERS", "LENS NOTES", "SOURCES"):
        assert heading in below, heading


def test_story_the_first_screen_fits_in_25_lines_for_a_nine_lens_page(tmp_path):
    rep = _real(tmp_path)
    rep.votes = [_skip(f"Lens {n}") for n in range(8)] + [_rank("Forensic", "buy", 9, kind="check")]
    rep.agreement = build_agreement(rep.votes)
    assert sweep.first_screen_lines(format_company_report(rep)) <= 25


def test_story_a_company_too_small_for_every_lens_is_called_outside_the_tested_range():
    """Viking, without the small-company band ticked: the flag is off but the market cap is $3.4bn."""
    check = _check(name="Viking Therapeutics, Inc.")
    check.peer_group.subject = SimpleNamespace(market_cap_usd=3.4e9)
    rep = _report([_skip("Quality", "market cap below the $5.0bn minimum"),
                   _skip("Growth", "market cap below the $5.0bn minimum")], check=check)
    assert not rep.outside_tested_range
    first, second = answer_lines(rep)
    assert first == "No lens voted on Viking Therapeutics."
    assert second.endswith("It is also outside the tested range (under $5bn): no track record applies.")
    assert story_page(rep).tag == ()                      # the tag is only for the small-company band
    assert "under $5bn" in dict(story_paragraphs(rep))["What to doubt."]
    check.peer_group.subject = SimpleNamespace(market_cap_usd=52.7e9)
    assert "under $5bn" not in " ".join(answer_lines(_report([_skip("Quality")], check=check)))


# --------------------------------------------------------------------------- #
# Batch 20: STORY-OTHER-REASON and FLOOR-WORDS-1
# --------------------------------------------------------------------------- #
def test_story_names_every_reason_with_no_unnamed_remainder():
    """Viking (2026-10-06): four reasons, and the fourth read "1 other reason also kept lenses out"."""
    rep = _report([_skip("Quality"), _skip("Defensive Income", "dividend yield 0%; the rule requires "
                                                                "at least 1.5%"),
                   _skip("Financials", "not for this sector (Healthcare)"),
                   _skip("Growth", "PEG ratio not available; the rule allows at most 2.00")])
    happened = dict(story_paragraphs(rep))["What happened."]
    assert "other reason" not in happened
    assert "Growth did not apply: PEG ratio not available" in happened


def test_the_two_size_floors_read_as_one_sentence():
    rep = _small()
    rep.size_matched_peers = True
    rep.smallcap_cohort, rep.smallcap_floor_usd = "", None
    rep.smallcap_band_note = "its industry's tested range starts at $10bn, above this company"
    rep.check.peer_group = SimpleNamespace(members=list(range(12)), step=1, snapshot="", thin=False,
                                           broad=False, subject=SimpleNamespace(market_cap_usd=3.4e9))
    tag = story_page(rep).tag
    assert tag[1] == ("Lenses are tested on companies worth $5bn or more; in this industry the "
                      "backtest covered companies from $10bn up. Tiny Therapeutics ($3.4bn) is "
                      "outside both.")
    assert not any("starts at" in line for line in tag)


# --------------------------------------------------------------------------- #
# Batch 20: SHORT-NAME-2
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ticker,legal,short", [
    ("F", "Ford Motor Company", "Ford"),
    ("JPM", "JPMorgan Chase & Co.", "JPMorgan"),
    ("GOOGL", "Alphabet Inc.", "Alphabet"),
    ("GOOG", "Alphabet Inc.", "Alphabet"),
])
def test_the_dated_short_name_file_names_the_common_name(ticker, legal, short):
    rep = _report([], check=_check(name=legal))
    rep.ticker = ticker
    assert short_name(rep) == short


def test_an_unlisted_company_falls_back_to_its_cleaned_legal_name(tmp_path):
    from aristos_council.company_story import load_short_names
    rep = _report([], check=_check(name="Ford Motor Company"))        # ticker ACM: not in the file
    assert short_name(rep) == "Ford Motor"
    assert load_short_names(tmp_path / "missing.yaml") == {}            # no file: every name falls back


def test_ford_answer_uses_the_short_name():
    rep = _report([_skip("Quality")], check=_check(name="Ford Motor Company"))
    rep.ticker = "F"
    assert answer_lines(rep)[0] == "No lens voted on Ford."


def test_b22_u10_one_lens_says_its_reason_and_several_keep_the_old_shapes():
    one = _report([_rank("Quality", "hold", 5), _skip("Growth")])
    assert answer_lines(one)[1].startswith("One lens did not apply: " + NO_PROFIT)
    none_voted = _report([_skip("Growth")])
    assert answer_lines(none_voted)[1].startswith("The one lens did not apply: " + NO_PROFIT)
    same = _report([_rank("Quality", "hold", 5), _skip("A"), _skip("B")])
    assert answer_lines(same)[1].startswith("Two lenses did not apply, all for one reason: " + NO_PROFIT)
    mixed = _report([_skip("A"), _skip("B"), _skip("C", "dividend yield 0%; the rule requires at least 1.5%")])
    assert "the most common reason (two of them)" in answer_lines(mixed)[1]


# --------------------------------------------------------------------------- #
# Batch 22 B3 - group sizes explained once
# --------------------------------------------------------------------------- #
def _byd_like():
    chk = _check(name="BYD Company Limited")
    chk.peer_group = SimpleNamespace(members=list(range(19)), step=2, snapshot="2026-09-25", thin=False,
                                     broad=False)
    return _report([_rank("Earnings Power Value", "hold", 10, of=14), _rank("Quality", "hold", 4, of=14),
                    _rank("Forensic", "buy", 16, of=20, kind="check"),
                    _skip("Growth", "return on invested capital 9.3%; the rule requires at least 12%")],
                   check=chk)


def test_b22_b3_the_usable_figures_note_says_n_of_total_once_per_group():
    from aristos_council.company_story import usable_figures_note
    rep = _byd_like()
    assert usable_figures_note(rep) == ("14 of 20 had usable figures for Earnings Power Value and Quality; "
                                        "20 of 20 for Forensic.")
    assert story_page(rep).group_note == usable_figures_note(rep)


def test_b22_b3_no_note_when_every_lens_ranked_the_whole_group():
    from aristos_council.company_story import usable_figures_note
    chk = _check()
    chk.peer_group = SimpleNamespace(members=list(range(13)), step=1, snapshot="", thin=False, broad=False)
    assert usable_figures_note(_report([_rank("Quality", "hold", 4, of=14)], check=chk)) == ""


def test_b22_b3_the_story_and_every_export_use_the_same_words(tmp_path):
    from dataclasses import replace
    rep = _run([RAW, SCREENED], tmp_path=tmp_path)
    total = len(rep.check.peer_group.members) + 1
    rep.votes = [replace(v, cohort_size=total - 3) if v.ranked and i == 0 else v
                 for i, v in enumerate(rep.votes)]
    from aristos_council.company_story import usable_figures_note
    note = usable_figures_note(rep)
    assert note.startswith(f"{total - 3} of {total} had usable figures for ")
    happened = dict(story_paragraphs(rep))["What happened."]
    assert note[0].upper() + note[1:] in happened
    text, html, md = format_company_report(rep), company_report_html(rep), company_report_markdown(rep)
    assert note in text and note in md and note in html


def test_b22_b4_the_company_story_calls_its_facts_paragraph_other_facts(tmp_path):
    """"What survived." held debt, growth and analysts - facts, not a shortlist's survivors."""
    from aristos_council.company_story import LEADS
    assert LEADS == ("What this run asked.", "What happened.", "Other facts.", "What to doubt.",
                     "What this cannot tell you.")
    probe = _run([RAW], tmp_path=tmp_path, save=False)
    rep = _run([RAW], tmp_path=tmp_path, with_summary=True, reader_runner=_Writer(_fields(probe)),
               save=False)
    leads = [lead for lead, _t in story_page(rep).paragraphs]
    assert leads[2] == "Other facts." and "What survived." not in leads
    for doc in (format_company_report(rep), company_report_html(rep), company_report_markdown(rep)):
        assert "What survived" not in doc and "Other facts." in doc
    # the LIST summary keeps its own heading: there, names do survive a shortlist
    assert reader_paragraphs(rep.summary.summary)[2][0] == "What survived."


def test_b22_b5_the_story_has_no_peer_search_jargon_but_the_workings_keep_it(tmp_path):
    rep = _byd_like()
    asked = dict(story_paragraphs(rep))["What this run asked."]
    assert asked == ("BYD was ranked against 19 similar-sized companies in its industry, under "
                     "four lenses (Earnings Power Value, Quality, Forensic and Growth).")
    for bad in ("step", "of 4", "peer search", "market index"):
        assert bad not in asked
    real = _run([RAW, SCREENED], tmp_path=tmp_path)
    assert "step" not in dict(story_paragraphs(real))["What this run asked."]
    text = format_company_report(real)
    assert text.index("SHOW THE WORKINGS") < text.index("How this peer group was built")
    assert "index snapshot" in text[text.index("How this peer group was built"):]
    one = _report([_rank("Quality", "hold", 5)])
    one.check.peer_group = SimpleNamespace(members=[1], step=1, snapshot="", thin=True, broad=False)
    assert "against 1 similar-sized company in its industry" in dict(story_paragraphs(one))["What this run asked."]
