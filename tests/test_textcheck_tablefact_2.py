"""TEXTCHECK-TABLEFACT-2 - restating a table fact is never flagged, and a genuine flag quotes the sentence as
printed by every specialist it names. Live, KO.US sweep 2026-10-22: the fundamental and risk views both cited
the Earnings Power Value -67.0% reading and were flagged twice, each flag quoting a sentence found in only
one of the two printed views:

[⚠ AI text check: "The Earnings Power Value lens's -67.0% reading is cited as confirmation that the price is
well above the no-growth operating value." appears near-verbatim in 2 specialists' views (fundamental, risk)
- convergent phrasing on the same figures, not independent domain analysis]"""
from __future__ import annotations

from types import SimpleNamespace

from aristos_council import ai_text_check as atc
from aristos_council.narration_check import check_printed_repetition

KO_LENSES = ["Value + Momentum", "Defensive Income", "Cyclical Income", "Earnings Power Value", "Financials",
             "Forensic", "Growth", "Magic Formula RAW", "Quality"]

# The two specialist views as printed in KO.US.md, verbatim.
KO_FUNDAMENTAL = (
    "The fundamental specialist identifies the 97th-percentile valuation as the central constraint, noting that "
    "the EV/EBIT of 24.6 is the dearest KO has been in five years. The Earnings Power Value lens's -67.0% "
    "reading is cited as confirmation that the price is well above the no-growth operating value. Business "
    "quality is described as solid but not exceptional: ROIC of 14.3% and earnings yield of 4.1% are mid-table "
    "across every voting lens. The dividend-payout exclusion — 120% of 4-year average FCF against an 80% rule "
    "— is treated as a material structural concern, not a marginal miss. The Forensic check's 'no concern' "
    "verdict is accepted, with the Altman Z-Score and Piotroski F-Score both reassuring, though the accrual "
    "ratio of 5.6% (rank 27 of 28) is noted as a near-worst-in-class reading. The specialist lands at "
    "neutral/HOLD, consistent with the ranker's verdict.")
KO_RISK = (
    "The risk specialist centres the bearish case on three compounding risks: valuation at the 97th percentile "
    "of KO's own 5-year EV/EBIT range; a dividend consuming 120% of 4-year average FCF against an 80% "
    "sustainability threshold; and a FCF series that has roughly halved in the two most recent years (FY2024 "
    "$4.7bn and FY2025 $5.3bn versus FY2022 $9.5bn and FY2023 $9.7bn). The Earnings Power Value lens's -67.0% "
    "reading is cited as confirmation that the market is pricing in growth the fundamentals do not currently "
    "support. The accrual ratio of 5.6% (rank 27 of 28, lower is better) is flagged as a near-worst-in-class "
    "reading that may signal softer earnings quality. The 10-year revenue CAGR of 0.8% is treated as the more "
    "reliable structural growth rate for a mature consumer staples business. Net debt of USD 27.9bn taking 5.3 "
    "years of FCF to repay is noted as a long tail for a mature business at current FCF levels. Confidence is "
    "0.68.")


def test_the_ko_earnings_power_value_restatement_is_not_flagged():
    printed = {"fundamental": KO_FUNDAMENTAL, "risk": KO_RISK}
    assert check_printed_repetition(printed, lens_labels=KO_LENSES) == []
    assert check_printed_repetition(printed) == []          # the shared -67.0% figure alone is enough


def test_a_shared_sentence_without_a_common_table_fact_is_still_flagged_and_quotes_both_printed_sentences():
    f = KO_FUNDAMENTAL + " Pricing power is being tested by private-label rivals in every major market."
    r = KO_RISK + " Honestly, pricing power is being tested by private-label rivals in every major market!"
    flags = check_printed_repetition({"fundamental": f, "risk": r}, lens_labels=KO_LENSES)
    assert len(flags) == 2
    fund_sentence = "Pricing power is being tested by private-label rivals in every major market."
    risk_sentence = "Honestly, pricing power is being tested by private-label rivals in every major market!"
    by_claim = {atc.claim_of(x): x for x in flags}
    assert set(by_claim) == {fund_sentence, risk_sentence}
    # each stamp names both specialists and quotes the OTHER one's sentence exactly as printed
    assert f'risk prints "{risk_sentence}"' in by_claim[fund_sentence]
    assert f'fundamental prints "{fund_sentence}"' in by_claim[risk_sentence]
    for stamp in flags:
        assert "near-verbatim in 2 specialists' views (fundamental, risk)" in stamp
    # every quote occurs in the printed text, so the inline markers all land
    prose = f"{f}\n\n{r}"
    _, swaps, unplaced = atc.mark_sentences(prose, flags)
    assert unplaced == [] and len(swaps) == 4
    assert atc.plain_reason(flags[0]) == "Two specialists used the same words"


def test_the_council_quotes_the_rendered_view_not_the_raw_field():
    """The check reads what the page prints (a stray markdown heading neutralised by the renderer), so a quote
    matches the page, never the pre-render field."""
    from aristos_council.agents.schemas import Narration, SpecialistView
    from aristos_council.company_report import _annotate_specialist_repetition
    from aristos_council.narration_render import narration_markdown
    raw = "#### Pricing power is being tested by private-label rivals in every major market."
    n = Narration(specialist_views=[
        SpecialistView(specialist="fundamental", stance="neutral", confidence=0.5, reasoning=raw),
        SpecialistView(specialist="risk", stance="bearish", confidence=0.5, reasoning=raw)])
    decision = SimpleNamespace(rationale="Prose.", narration=n.model_dump())
    _annotate_specialist_repetition(SimpleNamespace(decision=decision), SimpleNamespace(specialist_opinions=[]),
                                    KO_LENSES)
    _, stamps = atc.split(decision.rationale)
    assert len(stamps) == 1
    claim = atc.claim_of(stamps[0])
    assert claim == "Pricing power is being tested by private-label rivals in every major market."
    assert "#" not in claim and claim in narration_markdown(n)


def test_the_ko_council_annotation_adds_no_stamp():
    from aristos_council.agents.schemas import Narration, SpecialistView
    from aristos_council.company_report import _annotate_specialist_repetition
    n = Narration(specialist_views=[
        SpecialistView(specialist="fundamental", stance="neutral", confidence=0.58, reasoning=KO_FUNDAMENTAL),
        SpecialistView(specialist="risk", stance="bearish", confidence=0.68, reasoning=KO_RISK)])
    decision = SimpleNamespace(rationale="Prose.", narration=n.model_dump())
    _annotate_specialist_repetition(SimpleNamespace(decision=decision), SimpleNamespace(specialist_opinions=[]),
                                    KO_LENSES)
    assert decision.rationale == "Prose."


def test_a_shared_year_alone_is_not_a_table_fact():
    """A bare whole number (a year, a count) does not exempt two copied sentences; a reading does."""
    from aristos_council.narration_check import check_printed_repetition
    copied = "Free cash flow has fallen for two years as capital spending rose sharply in 2025 and beyond."
    flags = check_printed_repetition({"fundamental": copied, "risk": copied})
    assert flags, "two identical sentences sharing only a year must still be flagged"
    with_reading = "The -67.0% reading is cited as confirmation that the price is above its no-growth value."
    assert check_printed_repetition({"fundamental": with_reading, "risk": with_reading}) == []
