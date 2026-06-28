"""Contested-verdict flag — a ONE-RUN 'this is a close call, read the report' label.

Derived (not new analysis) from signals already on a single report: the
specialist_conflict veto, decision.dissent, majority_override. All deterministic:
fake states/reports, no LLM or network.
"""

from __future__ import annotations

import json

from aristos_council.presentation import (
    contested,
    contested_banner,
    contested_label,
)
from aristos_council.state import (
    Decision,
    Recommendation,
    ResearchState,
    SpecialistName,
    SpecialistOpinion,
    Stance,
    VetoFlag,
    VetoTrigger,
)


def _op(specialist: SpecialistName, stance: Stance) -> SpecialistOpinion:
    return SpecialistOpinion(specialist=specialist, stance=stance, confidence=0.6,
                             thesis="t")


def _state(*, recommendation=Recommendation.HOLD, confidence=0.62, dissent=(),
           vetoes=(), stances=None, gate_override=False,
           insufficient=False) -> ResearchState:
    s = ResearchState(ticker="TEST", strategy_id="growth_v1")
    if stances:
        s.specialist_opinions = [_op(sp, st) for sp, st in stances]
    s.decision = Decision(
        recommendation=recommendation, confidence=confidence, rationale="r",
        dissent=list(dissent), gate_override_applied=gate_override,
        insufficient_evidence=insufficient)
    s.veto_flags = [VetoFlag(trigger=t, detail="d") for t in vetoes]
    return s


# --------------------------------------------------------------------------- #
# Primary triggers — panel split / dissent / majority override
# --------------------------------------------------------------------------- #
def test_panel_split_is_contested():
    s = _state(vetoes=(VetoTrigger.SPECIALIST_CONFLICT,),
               stances=[(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
                        (SpecialistName.TECHNICAL, Stance.BEARISH),
                        (SpecialistName.RISK, Stance.BEARISH)])
    flag, reasons = contested(s)
    assert flag is True and "panel_split" in reasons
    banner = contested_banner(s)
    assert banner is not None and banner.startswith("CONTESTED CALL")
    assert "1 bullish / 2 bearish" in banner


def test_decision_dissent_is_contested():
    s = _state(dissent=(SpecialistName.RISK,))
    flag, reasons = contested(s)
    assert flag is True and "decision_dissent" in reasons
    assert "overrode 1 specialist" in contested_banner(s)


def test_majority_override_is_contested():
    s = _state(vetoes=(VetoTrigger.MAJORITY_OVERRIDE,))
    flag, reasons = contested(s)
    assert flag is True and "majority_override" in reasons
    assert "contradicts the specialist majority" in contested_banner(s)


# --------------------------------------------------------------------------- #
# Clean verdicts — NOT contested
# --------------------------------------------------------------------------- #
def test_clean_aligned_panel_is_not_contested():
    s = _state(recommendation=Recommendation.BUY, confidence=0.8,
               stances=[(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
                        (SpecialistName.TECHNICAL, Stance.BULLISH),
                        (SpecialistName.RISK, Stance.NEUTRAL)])
    flag, reasons = contested(s)
    assert flag is False and reasons == []
    assert contested_banner(s) is None
    assert contested_label(s) == ""


def test_mid_confidence_but_aligned_panel_is_not_contested():
    # MSFT-shaped: confidence in the band (0.59) but no panel split, no dissent ->
    # confidence ALONE must not trigger contested.
    s = _state(confidence=0.59,
               stances=[(SpecialistName.FUNDAMENTAL, Stance.NEUTRAL),
                        (SpecialistName.TECHNICAL, Stance.NEUTRAL),
                        (SpecialistName.RISK, Stance.NEUTRAL)])
    flag, reasons = contested(s)
    assert flag is False and reasons == []


# --------------------------------------------------------------------------- #
# Gated outcomes are SETTLED, not contested
# --------------------------------------------------------------------------- #
def test_gated_sell_cap_is_not_contested_even_with_panel_split():
    s = _state(recommendation=Recommendation.SELL, confidence=0.55,
               gate_override=True,
               vetoes=(VetoTrigger.SPECIALIST_CONFLICT,),
               stances=[(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
                        (SpecialistName.RISK, Stance.BEARISH)])
    flag, reasons = contested(s)
    assert flag is False and reasons == []
    assert contested_banner(s) is None


def test_insufficient_evidence_is_not_contested():
    s = _state(recommendation=Recommendation.INSUFFICIENT_EVIDENCE, confidence=0.3,
               insufficient=True, dissent=(SpecialistName.RISK,))
    flag, _ = contested(s)
    assert flag is False


# --------------------------------------------------------------------------- #
# Confidence band only ESCALATES once a primary signal fired
# --------------------------------------------------------------------------- #
def test_confidence_band_escalates_only_with_a_primary_signal():
    # panel split AND confidence in band -> primary fires + supplementary reason
    s = _state(confidence=0.60, vetoes=(VetoTrigger.SPECIALIST_CONFLICT,),
               stances=[(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
                        (SpecialistName.RISK, Stance.BEARISH)])
    flag, reasons = contested(s)
    assert flag is True
    assert "panel_split" in reasons and "contested_confidence" in reasons
    assert "contested band" in contested_banner(s)


def test_confidence_out_of_band_adds_no_supplementary_reason():
    s = _state(confidence=0.85, dissent=(SpecialistName.RISK,))
    flag, reasons = contested(s)
    assert flag is True and "contested_confidence" not in reasons


# --------------------------------------------------------------------------- #
# Surfacing — label + machine-readable RunReport fields + round-trip
# --------------------------------------------------------------------------- #
def test_contested_label_format():
    s = _state(vetoes=(VetoTrigger.SPECIALIST_CONFLICT,),
               dissent=(SpecialistName.RISK,))
    label = contested_label(s)
    assert label.startswith("[CONTESTED:") and "panel_split" in label
    assert "decision_dissent" in label


def test_report_from_state_carries_contested_and_round_trips():
    from aristos_council.persistence.reports import RunReport, report_from_state

    s = _state(vetoes=(VetoTrigger.SPECIALIST_CONFLICT,),
               dissent=(SpecialistName.RISK,),
               stances=[(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
                        (SpecialistName.RISK, Stance.BEARISH)])
    rep = report_from_state(s)
    assert rep.contested is True
    assert "panel_split" in rep.contested_reasons
    back = RunReport.model_validate(json.loads(rep.model_dump_json()))
    assert back.contested is True and "decision_dissent" in back.contested_reasons
    # the report renders the contested line iff contested
    assert contested_banner(back) is not None


def test_clean_report_has_no_contested_flag_or_banner():
    from aristos_council.persistence.reports import report_from_state

    s = _state(recommendation=Recommendation.BUY, confidence=0.8,
               stances=[(SpecialistName.FUNDAMENTAL, Stance.BULLISH),
                        (SpecialistName.TECHNICAL, Stance.BULLISH)])
    rep = report_from_state(s)
    assert rep.contested is False and rep.contested_reasons == []
    assert contested_banner(rep) is None
