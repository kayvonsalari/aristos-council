"""BATCH 31 (2026-11-01) - company-page fixes found on the MSFT runs of 2026-10-31 and 2026-11-01.

One test group per item. The report is built the way the saved MSFT run was: 13 peers found at ladder step 4 (the
broad sector group), Earnings Power Value BUY 3rd of 13, four HOLDs, Forensic a check, three lenses that did not
apply. The council text and the judge's answer are the saved run's own words (tests/fixtures/batch31).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aristos_council import report_sweep as sweep
from aristos_council.abs_readings import ForecastRow, fiscal_year_name
from aristos_council.agents.schemas import Narration, SpecialistView
from aristos_council.company_report import (LensVote, _plain_factor_label, build_agreement,
                                            epv_expensive_note)
from aristos_council.company_story import (answer_lines, peer_scope, story_paragraphs, story_page,
                                           thin_band_sentence, usable_figures_note)
from aristos_council.factors import NET_CASH_READING, factor_reading_text
from tests.test_company_story import _check, _report, _skip

FIX = Path(__file__).parent / "fixtures" / "batch31"
COUNCIL = json.loads((FIX / "msft_20261101_council.json").read_text(encoding="utf-8"))
JUDGE = json.loads((FIX / "msft_20261031_openai_judge.json").read_text(encoding="utf-8"))


def _vote(label, verdict, pos, of, *, kind="selector", no_data=0, dear=False):
    return LensVote(label.lower().replace(" ", "_") + "_v1", label, kind=kind, status="ranked",
                    verdict=verdict, position=pos, cohort_size=of, n_no_data=no_data,
                    buy_above_value=dear, asks=f"{label} asks a plain question.")


def _msft_votes():
    return [_vote("Value + Momentum", "hold", 4, 7, no_data=0),
            _skip("Defensive Income", "dividend yield 0.75%; the rule requires at least 1.5%"),
            _skip("Cyclical Income", "dividend yield 0.75%; the rule requires at least 1.5%"),
            _vote("Earnings Power Value", "buy", 3, 13, dear=True),
            _skip("Financials", "applies only to banks and insurers, not Technology companies"),
            _vote("Forensic", "hold", 7, 14, kind="check"),
            _vote("Growth", "hold", 5, 11, no_data=0),
            _vote("Magic Formula RAW", "hold", 4, 13),
            _vote("Quality", "hold", 6, 13)]


def _msft(*, step=4, broad=True, band="EV/EBIT 23.1 - 9th percentile (cheapest) of its own 5-year range "
                                      "(based on 38 of 60 months, the rest lack usable statements)"):
    check = _check(name="Microsoft Corporation")
    check.company_name = "Microsoft Corporation"
    check.peer_group = SimpleNamespace(members=list(range(13)), step=step, snapshot="2026-10-15",
                                       thin=False, broad=broad)
    check.band_percentile = 9.0
    check.valuation_band = band
    return _report(_msft_votes(), check)


# --------------------------------------------------------------------------- #
# A1 STORY-SECTOR-1
# --------------------------------------------------------------------------- #
def test_a1_a_step_4_group_is_said_to_be_the_broad_sector():
    asked = dict(story_paragraphs(_msft()))["What this run asked."]
    assert "ranked against 13 similar-sized companies in its broad sector" in asked
    assert "in its industry" not in asked


@pytest.mark.parametrize("step,broad,phrase", [(4, True, "in its broad sector"), (3, False, "in its industry"),
                                               (2, False, "among its closest rivals"),
                                               (1, False, "among its closest rivals")])
def test_a1_each_ladder_step_has_its_own_words(step, broad, phrase):
    assert peer_scope(SimpleNamespace(step=step, broad=broad)) == phrase


# --------------------------------------------------------------------------- #
# A2 VERDICT-FIRST-1
# --------------------------------------------------------------------------- #
def test_a2_the_first_line_names_the_verdict_of_record_then_the_split():
    first, _ = answer_lines(_msft())
    assert first == "HOLD by majority: one of five votes says BUY (Earnings Power Value), four say HOLD."


def test_a2_no_strict_majority_reads_mixed():
    rep = _report([_vote("Quality", "buy", 1, 13), _vote("Growth", "buy", 2, 11),
                   _vote("Magic Formula RAW", "hold", 5, 13), _vote("Value + Momentum", "hold", 4, 7)])
    first, _ = answer_lines(rep)
    assert first == ("MIXED: two of four votes say BUY (Quality and Growth), "
                     "two say HOLD (Magic Formula RAW and Value + Momentum).")


def test_a2_a_buy_majority_says_buy_first():
    rep = _report([_vote("Quality", "buy", 1, 13), _vote("Growth", "buy", 2, 11),
                   _vote("Magic Formula RAW", "buy", 2, 13), _vote("Value + Momentum", "hold", 4, 7)])
    first, _ = answer_lines(rep)
    assert first.startswith("BUY by majority: three of four votes say BUY (")
    assert first.endswith("one says HOLD (Value + Momentum).")


def test_a2_the_page_headline_and_the_saved_results_record_are_unchanged():
    ag = _msft().agreement
    assert ag.headline == "BUY on 1 of 5 votes; 3 lenses did not apply to this company"
    assert ag.verdict_of_record == "HOLD"


# --------------------------------------------------------------------------- #
# A3 EPV-LABEL-1
# --------------------------------------------------------------------------- #
def test_a3_the_epv_note_no_longer_reads_as_best_of_the_group():
    note = epv_expensive_note(3, 13)
    assert note == ("priced above its no-growth value, like every company in this group; "
                    "it ranks 3rd of 13 on that gap")
    assert "best of" not in note


def test_a3_the_same_text_is_in_the_vote_the_story_and_the_table():
    rep = _msft()
    epv = next(v for v in rep.votes if v.label == "Earnings Power Value")
    assert epv.expensive_note in epv.result()
    text = "\n".join(" ".join(r.cells()) for r in story_page(rep).rows)
    assert epv.expensive_note in text
    assert "best of an expensive group" not in text


# --------------------------------------------------------------------------- #
# A4 USABLE-FIGURES-1
# --------------------------------------------------------------------------- #
def test_a4_names_excluded_by_a_rule_are_not_called_missing_data():
    note = usable_figures_note(_msft())
    assert "7 of 14 passed Value + Momentum's rules" in note
    assert "had usable figures" not in note


def test_a4_names_with_no_figures_still_say_so():
    votes = [_vote("Value + Momentum", "hold", 4, 7, no_data=7), _vote("Quality", "hold", 6, 13)]
    check = _check(name="Microsoft Corporation")
    check.peer_group = SimpleNamespace(members=list(range(13)), step=4, snapshot="x", thin=False, broad=True)
    note = usable_figures_note(_report(votes, check))
    assert "7 of 14 had usable figures for Value + Momentum" in note


def test_a4_a_mixed_shortfall_gives_both_counts():
    votes = [_vote("Value + Momentum", "hold", 4, 7, no_data=3), _vote("Quality", "hold", 6, 13)]
    check = _check(name="Microsoft Corporation")
    check.peer_group = SimpleNamespace(members=list(range(13)), step=4, snapshot="x", thin=False, broad=True)
    assert "7 of 14 passed Value + Momentum's rules (11 had usable figures)" in usable_figures_note(
        _report(votes, check))


def test_a4_names_a_rule_could_not_read_count_as_missing_data_not_as_failing_it():
    from aristos_council.company_report import _n_no_data
    result = SimpleNamespace(
        unrateable=[("DEAD", "UNRATEABLE: no data - possibly delisted")], fetch_errors=[],
        excluded=[("AMD", "return on invested capital not available"),
                  ("MU", "not evaluated - accounts not in one currency"),
                  ("INTC", "no operating profit (fiscal year to Dec 2025)"),
                  ("ORCL", "return on invested capital 9.3%; the rule requires at least 12%")])
    assert _n_no_data(result) == 3                  # DEAD, AMD, MU; INTC and ORCL failed the lens's own rules
    assert _n_no_data(object()) is None             # a result that does not carry the split says nothing


# --------------------------------------------------------------------------- #
# A5 BAND-MONTHS-1
# --------------------------------------------------------------------------- #
def test_a5_a_band_on_38_of_60_months_is_doubted_in_the_story():
    rep = _msft()
    assert "rests on 38 of 60 months" in thin_band_sentence(rep)
    doubt = dict(story_paragraphs(rep))["What to doubt."]
    assert "38 of 60 months" in doubt


def test_a5_a_band_on_48_or_more_months_says_nothing():
    rep = _msft(band="EV/EBIT 23.1 - 9th percentile of its own 5-year range "
                     "(based on 48 of 60 months, the rest lack usable statements)")
    assert thin_band_sentence(rep) == ""


# --------------------------------------------------------------------------- #
# A6 FY-NAME-1
# --------------------------------------------------------------------------- #
def test_a6_a_forward_pe_names_its_fiscal_year():
    row = ForecastRow(label="this year (to Jun 2027)", now=20.02, ago_90d=19.68, analysts=40, currency="USD")
    nxt = ForecastRow(label="next year (to Jun 2028)", now=24.14)
    assert fiscal_year_name(row, "this year") == "FY to Jun 2027"
    assert fiscal_year_name(nxt, "next year") == "FY to Jun 2028"
    assert fiscal_year_name(None, "this year") == "this year"
    assert fiscal_year_name(ForecastRow(label="this year"), "this year") == "this year"


def test_a6_the_price_and_cash_reading_carries_the_year():
    from aristos_council.abs_readings import AnalystTrend, price_and_cash
    from tests.test_price_and_cash import _fundamentals, _news, _technical
    trend = AnalystTrend(mark="flat", currency="EUR", rows=(
        ForecastRow(label="this year (to Jun 2027)", now=7.25, currency="EUR"),
        ForecastRow(label="next year (to Jun 2028)", now=7.94, currency="EUR")))
    pac = price_and_cash(_technical(), _fundamentals(), trend=trend, news=_news())
    assert pac.forward_pe_this_year.label.startswith("forward P/E (FY to Jun 2027) 19.7x")
    assert pac.forward_pe_next_year.label.startswith("forward P/E (FY to Jun 2028) 18.0x")


# --------------------------------------------------------------------------- #
# A7 PIOTROSKI-CAP-1 / A8 NET-DEBT-ZERO-1
# --------------------------------------------------------------------------- #
def test_a7_a_label_that_opens_on_a_name_keeps_its_capital():
    assert _plain_factor_label("Piotroski F-Score (0-9)") == "Piotroski F-Score"
    assert _plain_factor_label("Altman Z-Score") == "Altman Z-Score"
    assert _plain_factor_label("Earnings yield (EBIT/EV)") == "earnings yield"
    assert _plain_factor_label("ROE") == "ROE"


def test_a8_a_net_cash_company_reads_net_cash_not_minus_zero():
    assert factor_reading_text("net_debt_to_ebit", -0.04, "multiple") == NET_CASH_READING == \
        "net cash (no debt to cover)"
    assert factor_reading_text("net_debt_to_ebit", 1.26, "multiple") == "1.3x"
    assert factor_reading_text("net_debt_to_ebit", 0.0, "multiple") == "0.0x"
    assert factor_reading_text("roic", -0.043, "percent") == "-4.3%"          # only the net-debt ratios


def test_a8_the_rank_is_kept_in_the_factor_line():
    from aristos_council.company_report import _check_components
    from aristos_council.rank_engine import RankedTicker
    row = RankedTicker(ticker="MSFT", factor_ranks={"net_debt_to_ebit": 9.0},
                       factor_values={"net_debt_to_ebit": -0.04}, combined_rank=18.0, universe_size=13)
    line = _check_components(SimpleNamespace(ranked=[row]), "MSFT", with_direction=True)[0]
    assert line.startswith("Net debt / operating profit: net cash (no debt to cover), rank 9 of 13")
    assert "-0.0x" not in line


# --------------------------------------------------------------------------- #
# B1 COUNCIL-VERDICT-1
# --------------------------------------------------------------------------- #
def test_b1_the_council_is_handed_the_verdict_of_record_line():
    from aristos_council.company_report import _council_agreement_row
    ag = _msft().agreement
    assert ag.record_line == "HOLD (1 BUY, 4 HOLD, 0 SELL); 3 lenses did not apply to this company"
    assert _council_agreement_row(ag)["headline"].startswith("HOLD (1 BUY, 4 HOLD, 0 SELL)")
    assert COUNCIL["record_line_expected"] == "HOLD (1 BUY, 4 HOLD, 0 SELL)"


def test_b1_a_mixed_record_says_mixed_in_the_council_line():
    ag = _report([_vote("Quality", "buy", 1, 13), _vote("Growth", "hold", 2, 11)]).agreement
    assert ag.record_line.startswith("MIXED (1 BUY, 1 HOLD, 0 SELL)")


def test_b1_the_run_hands_the_narrator_hold_not_the_lone_lens_buy(monkeypatch):
    """run_council_opinion's initial state: ranker verdict HOLD (the record), explanation = the record line."""
    from aristos_council import company_report as cr
    seen = {}

    class _App:
        def stream(self, initial, stream_mode=None):
            seen["initial"] = initial
            raise RuntimeError("stop here")

    monkeypatch.setattr("aristos_council.graph.build_council", lambda *a, **k: _App())
    monkeypatch.setattr(cr, "_council_company_facts", lambda report: {})
    rep = _msft()
    rep.check.data_integrity = SimpleNamespace(note="")
    op = cr.run_council_opinion(rep, adapter=object(), runners={"x": 1}, today=None)
    assert "stop here" in op.note
    initial = seen["initial"]
    assert initial.ranker_verdict.value == "hold"
    assert initial.ranker_explanation.startswith("HOLD (1 BUY, 4 HOLD, 0 SELL)")
    assert initial.agreement_row["headline"].startswith("HOLD (1 BUY, 4 HOLD, 0 SELL)")


def _narration(**kw) -> Narration:
    base = dict(echoed_verdict="HOLD (1 BUY, 4 HOLD, 0 SELL)", lens_verdicts=[], lens_attribution=[],
                neutral_context=["x"], disagreement_note="No split.", open_questions=["q"],
                specialist_views=[SpecialistView(specialist="fundamental", stance="bullish", confidence=0.7,
                                                 reasoning="Bullish.")])
    base.update(kw)
    return Narration(**base)


def _codes(n, **kw):
    from aristos_council.narration_schema import validate_narration
    return [i.code for i in validate_narration(n, **kw)]


def test_b1_calling_a_lens_buy_the_verdict_is_flagged_when_the_record_is_hold():
    text = COUNCIL["specialists"]["fundamental"]
    assert "The BUY verdict rests on" in text                       # the saved run's own sentence
    n = _narration(specialist_views=[SpecialistView(specialist="fundamental", stance="bullish",
                                                    confidence=0.7, reasoning=text)])
    codes = _codes(n, ranker_verdict="hold", verdict_of_record="HOLD (1 BUY, 4 HOLD, 0 SELL)")
    assert "verdict_word_is_not_the_record" in codes


def test_b1_a_lenses_own_verdict_and_the_record_word_are_not_flagged():
    n = _narration(disagreement_note="Earnings Power Value's BUY verdict of 3rd of 13 stands alone. "
                                     "The HOLD verdict of record rests on four lenses.")
    assert "verdict_word_is_not_the_record" not in _codes(n, ranker_verdict="hold")


# --------------------------------------------------------------------------- #
# B2 STRUCT-CHECK-FORENSIC-1
# --------------------------------------------------------------------------- #
FORENSIC_SENTENCE = ("The Forensic check returned 'no concern.' The specialist acknowledges that four voting "
                     "lenses issued HOLD verdicts and that free cash flow, after peaking in FY2024, has "
                     "partially retreated through FY2026 - but notes that FY2026 free cash flow remains above "
                     "FY2023.")


def test_b2_the_saved_sentence_is_in_the_saved_text():
    assert FORENSIC_SENTENCE.split(" - ")[0] in COUNCIL["specialists"]["fundamental"]


def test_b2_hold_about_the_voting_lenses_is_not_a_verdict_given_to_forensic():
    n = _narration(specialist_views=[SpecialistView(specialist="fundamental", stance="bullish", confidence=0.7,
                                                    reasoning=FORENSIC_SENTENCE)])
    assert "check_lens_anchors_a_verdict" not in _codes(n, ranker_verdict="hold",
                                                        check_lens_labels=["Forensic"])


def test_b2_the_real_saved_text_is_not_flagged_either():
    n = _narration(specialist_views=[SpecialistView(specialist="fundamental", stance="bullish", confidence=0.7,
                                                    reasoning=COUNCIL["specialists"]["fundamental"])])
    assert "check_lens_anchors_a_verdict" not in _codes(n, ranker_verdict="hold",
                                                        check_lens_labels=["Forensic"])


@pytest.mark.parametrize("sentence", [
    "Forensic's clean read keeps this closer to a HOLD.",
    "Forensic returned no concern, so HOLD.",
    "Forensic issued a HOLD on the accruals and the other lenses disagreed.",
])
def test_b2_a_verdict_word_attached_to_forensic_in_its_own_clause_is_still_flagged(sentence):
    n = _narration(disagreement_note=sentence)
    assert "check_lens_anchors_a_verdict" in _codes(n, ranker_verdict="hold", check_lens_labels=["Forensic"])


# --------------------------------------------------------------------------- #
# B3 FOUR-FIVE-1
# --------------------------------------------------------------------------- #
def test_b3_the_saved_note_says_four_voting_lenses_but_five_voted():
    note = COUNCIL["disagreement_note"]
    assert note.startswith("The four voting lenses split")
    n = _narration(disagreement_note=note)
    codes = _codes(n, ranker_verdict="hold", verdict_of_record="HOLD (1 BUY, 4 HOLD, 0 SELL)")
    assert "lens_count_is_not_the_data" in codes


def test_b3_the_right_count_is_not_flagged_and_no_count_is_never_flagged():
    ok = _narration(disagreement_note="The five voting lenses split: one BUY, four HOLD.")
    assert "lens_count_is_not_the_data" not in _codes(ok, verdict_of_record="HOLD (1 BUY, 4 HOLD, 0 SELL)")
    none = _narration(disagreement_note="Earnings Power Value says BUY; the other lenses say HOLD.")
    assert "lens_count_is_not_the_data" not in _codes(none, verdict_of_record="HOLD (1 BUY, 4 HOLD, 0 SELL)")
    # without the record's counts the check is skipped, never guessed
    assert "lens_count_is_not_the_data" not in _codes(_narration(
        disagreement_note="The four voting lenses split."), verdict_of_record="BUY on 1 of 5 votes")


# --------------------------------------------------------------------------- #
# B4 TEXTCHECK-TEMPLATE-1
# --------------------------------------------------------------------------- #
LABELS = ["Value + Momentum", "Earnings Power Value", "Growth", "Magic Formula RAW", "Quality", "Forensic"]


def test_b4_the_fixed_opening_sentence_is_not_a_repeat():
    from aristos_council.narration_check import check_printed_repetition
    s = COUNCIL["specialists"]
    printed = {"technical": s["technical"].split(". ")[0] + ".",
               "sentiment": s["sentiment"].split(". ")[0] + "."}
    assert printed["technical"] == "The technical specialist is neutral at moderate confidence."
    assert check_printed_repetition(printed, lens_labels=LABELS) == []


def test_b4_two_specialists_restating_the_fcf_series_are_not_flagged():
    from aristos_council.narration_check import check_printed_repetition
    s = COUNCIL["specialists"]
    printed = {"fundamental": s["fundamental"], "risk": s["risk"]}
    flags = check_printed_repetition(printed, lens_labels=LABELS)
    assert not any("free cash flow" in f.lower() and "peaking" in f.lower() for f in flags), flags


def test_b4_real_convergent_phrasing_is_still_flagged():
    from aristos_council.narration_check import check_printed_repetition
    same = "The balance sheet shows that the whole position remains broadly comfortable going forward."
    flags = check_printed_repetition({"fundamental": same, "risk": same}, lens_labels=LABELS)
    assert flags


# --------------------------------------------------------------------------- #
# B5 SPECIALIST-VOICE-1 / B7 NEWS-QUARTER-1  (the prompts)
# --------------------------------------------------------------------------- #
def test_b5_the_narrator_prompt_asks_for_stance_first_first_person_active_voice():
    from aristos_council.agents.prompts import STRUCTURED_NARRATION
    low = STRUCTURED_NARRATION.lower()
    for need in ("stance first", "first person", "active voice", "one figure per sentence",
                 "never in the third person"):
        assert need in low, need
    assert "the fundamental specialist is bullish" in low           # named as the thing NOT to write


def test_b7_the_prompts_forbid_restating_a_quarter_label_from_a_headline():
    from aristos_council.agents.prompts import SPECIALIST_BRIEFS, STRUCTURED_NARRATION
    from aristos_council.state import SpecialistName
    assert "the latest quarter" in STRUCTURED_NARRATION
    assert "the latest quarter" in SPECIALIST_BRIEFS[SpecialistName.SENTIMENT]


def test_b1_the_prompt_says_the_record_is_the_only_verdict():
    from aristos_council.agents.prompts import STRUCTURED_NARRATION
    assert "never called 'the verdict'" in STRUCTURED_NARRATION


# --------------------------------------------------------------------------- #
# B6 COHORT-WORD-1
# --------------------------------------------------------------------------- #
def test_b6_the_fixed_note_and_the_prompt_say_peer_group_never_cohort():
    from aristos_council.agents.prompts import STRUCTURED_NARRATION
    from aristos_council.narration_render import COHORT_SIZE_NOTE
    assert "cohort" not in COHORT_SIZE_NOTE.lower() and "group sizes differ per lens" in COHORT_SIZE_NOTE
    assert "WORDS: never write 'cohort'" in STRUCTURED_NARRATION
    assert "group sizes differ per lens" in STRUCTURED_NARRATION and "cohort sizes" not in STRUCTURED_NARRATION


def test_b6_the_rendered_note_has_no_cohort_and_the_narrators_words_are_left_alone():
    """Code-written text never says cohort; the narrator's own text is annotated, never rewritten, so a stray
    'cohort' from the model is the sweep's to find (below), not the renderer's to change."""
    from aristos_council.narration_render import narration_markdown
    n = _narration(lens_attribution=[], neutral_context=["x"], disagreement_note="A 7-name cohort is small.")
    n.lens_attribution = []
    out = narration_markdown(n)
    assert "A 7-name cohort is small." in out


def test_b6_the_sweep_rejects_the_word_in_the_council_section():
    """The council opinion sits above the fold, so the existing banned-word rule reads it; pinned here on the
    saved text so it cannot be moved below the fold without this failing."""
    text = ("THE ANSWER\nx\nCOUNCIL OPINION (narration only)\n  Factor  Rank  Note\n"
            "  12-month price momentum  7 of 7  -2.8%, last in the cohort\nSHOW THE WORKINGS\nmore\n")
    assert any("cohort" in f.detail for f in sweep.story_findings(text, "msft"))


# --------------------------------------------------------------------------- #
# C1 JUDGE-FIGURE-CHECK-1
# --------------------------------------------------------------------------- #
PACK = ("COMPANY\n- Microsoft Corporation (MSFT)\nFINANCIAL SERIES\n- Revenue 331.8bn; net income 133.7bn; free cash "
        "flow 67.0bn; capital expenditure 115.9bn\n- Total debt 56.8bn; cash and short-term investments 64.5bn; "
        "operating lease liabilities 76.7bn\n- EV/EBIT 23.1, 9th percentile\n- Analysts' average target 631.09, 21% "
        "above the price")


def _call(**kw):
    from aristos_council.judges.panel import JudgeCall
    return JudgeCall(call_no=1, status="ok", verdict="HOLD", confidence="medium", **kw)


def test_c1_a_figure_the_pack_does_not_hold_is_marked_inline_and_counted():
    from aristos_council.judges.figures import FIGURE_MARK, check_call
    call = _call(reason="EV/EBIT is 23.1, at the 9th percentile. [pack] Analysts' average price target is "
                        "$629.69 implying about 21% upside. [pack] Revenue was $331.8bn. [pack]")
    from aristos_council.judges.figures import count_marks
    assert check_call(call, PACK) == 1 and count_marks(call) == 1
    assert f"$629.69 implying about 21% upside. {FIGURE_MARK} [pack]" in call.reason
    assert call.reason.count(FIGURE_MARK) == 1                         # the supported sentences stay clean
    assert "Revenue was $331.8bn. [pack]" in call.reason


def test_c1_the_saved_judge_answer_is_checked_and_a_tagged_own_knowledge_claim_is_left_alone():
    from aristos_council.judges.figures import FIGURE_MARK, check_call
    call = _call(reason=JUDGE["reason"] + " Microsoft has 228,000 employees. [own knowledge]")
    check_call(call, PACK)
    assert "$629.69" in call.reason and FIGURE_MARK in call.reason       # 629.69 is not in this pack
    assert "228,000 employees. [own knowledge]" in call.reason
    assert f"228,000 employees. {FIGURE_MARK}" not in call.reason


def test_c1_known_limit_a_right_number_on_the_wrong_line_is_not_caught():
    """The 2026-10-31 sentence ("cash and short-term investments were $76.7bn") quotes a number that IS in the
    pack - as the lease figure. Presence-only cannot see that, and this pins it so nobody believes it can."""
    from aristos_council.judges.figures import check_call
    call = _call(reason="Cash and short-term investments were $76.7bn at fiscal year end. [pack]")
    assert check_call(call, PACK) == 0


def test_c1_the_panel_header_counts_the_marked_sentences_and_no_vote_moves():
    from aristos_council.judges import render
    from aristos_council.judges.figures import check_judges
    from aristos_council.judges.panel import (STABLE, JudgeResult, PanelResult, RecordSnapshot)
    bad = _call(reason="The target is $629.69. [pack] Another figure is 12.34%. [pack]")
    good = _call(reason="Revenue was $331.8bn. [pack]")
    judges = [JudgeResult(family="openai", label="OpenAI", model="m", status=STABLE, calls=[bad]),
              JudgeResult(family="google", label="Gemini", model="m", status=STABLE, calls=[good])]
    assert check_judges(judges, PACK) == 2
    result = PanelResult(ticker="MSFT", available=True, judges=judges, agreement="agrees",
                         record=RecordSnapshot(verdict="HOLD", split="BUY 1 / HOLD 4 / SELL 0",
                                               headline="h", n_voted=5))
    assert result.figures_off == 2
    lines = render.summary_lines(result)
    assert lines[-1] == ("2 sentences quoted a figure that is not in the facts the judges were given; "
                         "each is marked where it stands.")
    assert [j.verdict for j in judges] == ["HOLD", "HOLD"]              # judges have no vote; nothing changed
    clean = PanelResult(ticker="MSFT", available=True, judges=[judges[1]], agreement="agrees",
                        record=result.record)
    assert not any("figure that is not in the facts" in ln for ln in render.summary_lines(clean))


def test_c1_marking_is_idempotent():
    from aristos_council.judges.figures import check_call
    call = _call(reason="The target is $629.69. [pack]")
    check_call(call, PACK)
    once = call.reason
    check_call(call, PACK)
    assert call.reason == once
