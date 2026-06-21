"""The human-veto gate. Fully deterministic — no LLM decides whether a human
gets to look. The four triggers from the project spec:

1. LOW_CONFIDENCE        decision confidence below the strategy threshold
2. SPECIALIST_CONFLICT   at least one bull AND one bear among specialists
3. DATA_QUALITY          adapter errors, unverifiable screen criteria,
                         provenance violations, or a specialist abstaining
                         for lack of data
4. RECOMMENDATION_FLIP   recommendation differs from the prior run's
5. MAJORITY_OVERRIDE     decision verdict contradicts the strict stance-majority
                         of non-abstaining specialists
6. INSUFFICIENT_EVIDENCE verdict is INSUFFICIENT_EVIDENCE (a gating criterion was
                         NOT-EVAL) — always pauses for a human, unconditionally
"""

from __future__ import annotations

from ..state import (
    Recommendation,
    ResearchState,
    Stance,
    VetoFlag,
    VetoTrigger,
)
from ..strategy.loader import Strategy

# Specialist stance -> the verdict it implies, for the MAJORITY_OVERRIDE check.
_STANCE_VERDICT = {
    Stance.BULLISH: Recommendation.BUY,
    Stance.NEUTRAL: Recommendation.HOLD,
    Stance.BEARISH: Recommendation.SELL,
}


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

        # 4 — recommendation flip. An OVERRIDE run is an experiment: any verdict
        # change is an artifact of the changed setting, not market instability, so
        # it must NOT fire the flip veto (that would pollute the one signal whose
        # job is catching genuine verdict instability). Override runs are also not
        # the flip baseline (verdicts.load_latest skips them), so a later default
        # run never compares against an experiment either.
        #
        # INSUFFICIENT_EVIDENCE is OFF the buy/hold/sell ladder: it is never a flip
        # TARGET (this run) nor a flip BASELINE (the prior). verdicts.load_latest
        # already skips it as a baseline; we also guard both sides here so a
        # directly-set prior can't manufacture a spurious flip.
        if (not state.applied_overrides
                and state.prior_recommendation is not None
                and state.prior_recommendation != Recommendation.INSUFFICIENT_EVIDENCE
                and state.decision is not None
                and state.decision.recommendation != Recommendation.INSUFFICIENT_EVIDENCE
                and state.decision.recommendation != state.prior_recommendation):
            flags.append(VetoFlag(
                trigger=VetoTrigger.RECOMMENDATION_FLIP,
                detail=f"{state.prior_recommendation.value} -> "
                       f"{state.decision.recommendation.value}",
            ))

        # 5 — majority override: the Decision verdict contradicts a STRICT
        # majority (>50%) of the non-abstaining specialists' implied verdicts.
        # No confidence condition; a tie or no-majority is silent. Skipped when the
        # verdict is INSUFFICIENT_EVIDENCE — it is off the ladder, so comparing it
        # to a directional stance-majority is meaningless (and human review already
        # fires via trigger 6 below).
        if (state.decision is not None
                and state.decision.recommendation
                != Recommendation.INSUFFICIENT_EVIDENCE):
            voting = [o for o in state.specialist_opinions
                      if o.stance != Stance.ABSTAIN]
            bulls = sum(o.stance == Stance.BULLISH for o in voting)
            neutrals = sum(o.stance == Stance.NEUTRAL for o in voting)
            bears = sum(o.stance == Stance.BEARISH for o in voting)
            majority_stance = None
            for stance, count in ((Stance.BULLISH, bulls),
                                  (Stance.NEUTRAL, neutrals),
                                  (Stance.BEARISH, bears)):
                if count * 2 > len(voting):   # strict >50%, so at most one wins
                    majority_stance = stance
                    break
            if majority_stance is not None:
                majority_verdict = _STANCE_VERDICT[majority_stance]
                if state.decision.recommendation != majority_verdict:
                    flags.append(VetoFlag(
                        trigger=VetoTrigger.MAJORITY_OVERRIDE,
                        detail=f"decision {state.decision.recommendation.value} "
                               f"vs majority {majority_verdict.value} "
                               f"({bulls} bullish / {neutrals} neutral / "
                               f"{bears} bearish)",
                    ))

        # 6 — insufficient evidence: a NOT-EVAL on a GATING criterion short-
        # circuited the verdict to INSUFFICIENT_EVIDENCE (off the buy/hold/sell
        # ladder). This ALWAYS pauses for a human — unconditionally, no threshold.
        if (state.decision is not None
                and state.decision.recommendation
                == Recommendation.INSUFFICIENT_EVIDENCE):
            crit = state.decision.gating_criterion_fired or "a gating criterion"
            flags.append(VetoFlag(
                trigger=VetoTrigger.INSUFFICIENT_EVIDENCE,
                detail=f"gating criterion not evaluated ({crit}) — verdict is off "
                       f"the buy/hold/sell ladder; human review required",
            ))

        state.veto_flags.extend(flags)
        return state

    return veto
