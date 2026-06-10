"""The human-veto gate. Fully deterministic — no LLM decides whether a human
gets to look. The four triggers from the project spec:

1. LOW_CONFIDENCE        decision confidence below the strategy threshold
2. SPECIALIST_CONFLICT   at least one bull AND one bear among specialists
3. DATA_QUALITY          adapter errors, unverifiable screen criteria,
                         provenance violations, or a specialist abstaining
                         for lack of data
4. RECOMMENDATION_FLIP   recommendation differs from the prior run's
"""

from __future__ import annotations

from ..state import (
    ResearchState,
    Stance,
    VetoFlag,
    VetoTrigger,
)
from ..strategy.loader import Strategy


def make_veto_node(strategy: Strategy):
    min_conf = strategy.veto.min_confidence

    def veto(state: ResearchState) -> ResearchState:
        flags: list[VetoFlag] = []

        # 1 — low confidence
        if state.decision and state.decision.confidence < min_conf:
            flags.append(VetoFlag(
                trigger=VetoTrigger.LOW_CONFIDENCE,
                detail=f"decision confidence {state.decision.confidence:.2f} "
                       f"< threshold {min_conf:.2f}",
            ))

        # 2 — specialist conflict
        stances = {o.stance for o in state.specialist_opinions}
        if Stance.BULLISH in stances and Stance.BEARISH in stances:
            flags.append(VetoFlag(
                trigger=VetoTrigger.SPECIALIST_CONFLICT,
                detail="at least one bullish and one bearish specialist",
            ))

        # 3 — data quality
        dq: list[str] = []
        dq += [e for e in state.errors]  # adapter failures + provenance violations
        for tc in state.tool_calls:
            if tc.tool_name == "run_dividend_aristocrat_screen" and tc.output:
                dq += [str(f) for f in tc.output.get("flags", [])]
        for o in state.specialist_opinions:
            if o.stance == Stance.ABSTAIN:
                dq.append(f"{o.specialist.value} abstained (insufficient data)")
        if dq:
            flags.append(VetoFlag(
                trigger=VetoTrigger.DATA_QUALITY,
                detail="; ".join(dq[:6]) + ("; ..." if len(dq) > 6 else ""),
            ))

        # 4 — recommendation flip
        if (state.prior_recommendation is not None
                and state.decision is not None
                and state.decision.recommendation != state.prior_recommendation):
            flags.append(VetoFlag(
                trigger=VetoTrigger.RECOMMENDATION_FLIP,
                detail=f"{state.prior_recommendation.value} -> "
                       f"{state.decision.recommendation.value}",
            ))

        state.veto_flags.extend(flags)
        return state

    return veto
