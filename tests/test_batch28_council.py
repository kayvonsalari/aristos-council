"""Batch 28 C/D - council opinion fixes: the rank-check false positive, the record heading, the table-fact
rule, the caps row."""
from __future__ import annotations

import pytest

from aristos_council.narration_schema import validate_narration
from tests.test_narration_schema import _well_formed


def test_a_price_over_an_eps_is_not_a_rank_and_is_never_flagged():
    n = _well_formed()
    n.neutral_context = ["The forward P/E is 93.90 / 3.30 on consensus, 28.5 / 3.4 on trailing."]
    n.open_questions = ["Does 93.90 / 3.30 hold?"]
    codes = [i.code for i in validate_narration(n, ranker_verdict="buy")]
    assert "impossible_rank" not in codes and "unparseable_rank" not in codes


def test_a_real_impossible_rank_is_still_flagged_and_listed_once():
    n = _well_formed()
    n.neutral_context = ["It ranks 9/6 on growth."]
    n.open_questions = ["It ranks 9/6 on growth."]          # the same sentence in a second field
    issues = [i for i in validate_narration(n, ranker_verdict="buy") if i.code == "impossible_rank"]
    assert len(issues) == 1 and '"9/6"' in issues[0].detail


def test_a_genuine_rank_with_a_trailing_full_stop_still_parses():
    n = _well_formed()
    n.neutral_context = ["It ranks 3/6."]
    assert all(i.code != "impossible_rank" for i in validate_narration(n, ranker_verdict="buy"))


# --------------------------------------------------------------------------- #
# C12 COUNCIL-RECORD-HEADING-1
# --------------------------------------------------------------------------- #
def _vote(label, verdict):
    from aristos_council.company_report import LensVote
    return LensVote(strategy_id=f"{label}_v1", label=label, kind="selector", asks="", status="ranked",
                    verdict=verdict, position=3, cohort_size=10)


def test_an_all_hold_record_is_stated_as_hold_not_as_zero_buys():
    from aristos_council.company_report import LensVote, build_agreement
    votes = [_vote("A", "hold"), _vote("B", "hold"),
             LensVote(strategy_id="c_v1", label="C", kind="selector", asks="", status="excluded", reason="x")]
    ag = build_agreement(votes)
    assert ag.headline == "HOLD, 2 of 2 votes; 1 lens did not apply to this company"
    assert "BUY" not in ag.headline


def test_the_echoes_buy_warning_disappears_once_the_heading_states_the_record():
    from aristos_council.company_report import build_agreement
    from aristos_council.narration_schema import validate_narration
    ag = build_agreement([_vote("A", "hold"), _vote("B", "hold")])
    n = _well_formed()
    n.echoed_verdict = ag.headline
    codes = [i.code for i in validate_narration(n, ranker_verdict="hold", verdict_of_record=ag.headline)]
    assert "verdict_mismatch" not in codes and "headline_does_not_restate_verdict_of_record" not in codes


# --------------------------------------------------------------------------- #
# C13 TEXTCHECK-TABLEFACT-1
# --------------------------------------------------------------------------- #
FUND = ("Quality ranks Ford 4th of 14 on the table. The average analyst target of $94.74 is only 1% "
        "above the $93.90 price today. Free cash flow was mixed.")
RISK = ("The average analyst target of $94.74 is only 1% above the $93.90 price today, which leaves "
        "little room. Quality ranks Ford 4th of 14 on the table.")


def test_restating_the_same_table_facts_in_two_specialists_is_never_flagged():
    from aristos_council.narration_check import check_printed_repetition
    assert check_printed_repetition({"fundamental": FUND, "risk": RISK},
                                    lens_labels=["Quality", "Magic Formula RAW"]) == []


def test_real_convergent_phrasing_is_flagged_on_the_printed_sentence_and_can_be_marked():
    from aristos_council import ai_text_check as atc
    from aristos_council.narration_check import check_printed_repetition
    f = "Margins are being squeezed by tariffs and warranty costs across every segment. Debt is steady."
    r = "Honestly, margins are being squeezed by tariffs and warranty costs across every segment! Cash is fine."
    flags = check_printed_repetition({"fundamental": f, "risk": r})
    assert len(flags) == 2 and all("near-verbatim in 2 specialists' views (fundamental, risk)" in x for x in flags)
    # each stamp quotes a sentence that occurs in the PRINTED text, so the inline marker can find it
    prose = f"{f}\n\n{r}"
    for stamp in flags:
        assert atc.claim_of(stamp) in prose
    marked, swaps, unplaced = atc.mark_sentences(prose, flags)
    assert unplaced == [] and len(swaps) == 4
    assert atc.plain_reason(flags[0]) == "Two specialists used the same words"


def test_a_shared_run_that_contains_a_figure_or_a_lens_name_is_not_a_run():
    from aristos_council.narration_check import check_printed_repetition
    a = "Free cash flow fell to 5.3 billion dollars last year."
    b = "Free cash flow fell to 5.3 billion dollars last year."
    assert check_printed_repetition({"fundamental": a, "risk": b}) == []        # the figure splits it into two short runs
    c = "Quality ranks Ford high, Quality ranks Ford high, Quality ranks Ford high."
    assert check_printed_repetition({"fundamental": c, "risk": c}, lens_labels=["Quality"]) == []   # lens name masked


def test_the_council_annotates_from_the_printed_views_not_the_raw_theses():
    from types import SimpleNamespace
    from aristos_council.company_report import _annotate_specialist_repetition
    from aristos_council.agents.schemas import Narration, SpecialistView
    n = Narration(specialist_views=[
        SpecialistView(specialist="fundamental", stance="neutral", confidence=0.5,
                       reasoning="Margins are being squeezed by tariffs and warranty costs across every segment."),
        SpecialistView(specialist="risk", stance="bearish", confidence=0.5,
                       reasoning="Margins are being squeezed by tariffs and warranty costs across every segment.")])
    decision = SimpleNamespace(rationale="Prose.", narration=n.model_dump())
    rep = SimpleNamespace(decision=decision)
    state = SimpleNamespace(specialist_opinions=[])          # the raw theses are not consulted when a narration exists
    _annotate_specialist_repetition(rep, state, ["Quality"])
    assert decision.rationale.count("[⚠ AI text check:") == 1                      # one sentence, quoted once
    assert '"Margins are being squeezed by tariffs and warranty costs across every segment."' in decision.rationale


# --------------------------------------------------------------------------- #
# C14 COUNCIL-CAPS-1
# --------------------------------------------------------------------------- #
def test_a_lens_that_does_not_apply_is_not_shouted_in_the_council_lens_table():
    from aristos_council.agents.schemas import LensVerdictItem
    from aristos_council.narration_render import narration_html, narration_markdown
    n = _well_formed()
    n.lens_verdicts = [LensVerdictItem(lens="Growth", verdict="does not apply", excluded_reason="growth 5% < 10%"),
                       LensVerdictItem(lens="Quality", verdict="hold")]
    md = narration_markdown(n)
    assert "| Growth | Does not apply |" in md and "DOES NOT APPLY" not in md
    assert "| Quality | HOLD |" in md                                        # a verdict word keeps capitals
    html = narration_html(n)
    assert "Does not apply" in html and "DOES NOT APPLY" not in html and ">HOLD<" in html.replace(" ", "")
