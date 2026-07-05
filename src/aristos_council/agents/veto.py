"""The human-veto gate. Fully deterministic — no LLM decides whether a human
gets to look. The four triggers from the project spec:

1. LOW_CONFIDENCE        deterministic EVIDENCE COVERAGE below the threshold
                         (coverage.py — NOT the narrator's self-assigned confidence)
2. SPECIALIST_CONFLICT   at least one bull AND one bear among specialists
3. DATA_QUALITY          SEVERITY-AWARE — fires only on a MATERIAL data gap
                         (adapter/provenance error, or 2+ unevaluable screen
                         criteria); MINOR noise (one abstention, one non-gating
                         NOT-EVAL, an optional sentiment 403) is recorded, not
                         escalated
4. RECOMMENDATION_FLIP   recommendation differs from the prior run's
5. MAJORITY_OVERRIDE     decision verdict contradicts the strict stance-majority
                         of non-abstaining specialists
6. INSUFFICIENT_EVIDENCE verdict is INSUFFICIENT_EVIDENCE (a gating criterion was
                         NOT-EVAL) — always pauses for a human, unconditionally
7. GATE_OVERRIDE_MATERIAL the deterministic gate capped the verdict AND the
                         LLM/gate disagreement was LARGE (confident BUY -> SELL)
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
from .disposition import _RANK  # buy/hold/sell rung order — read-only, for magnitude
from .nodes import _is_screen_tool

# Specialist stance -> the verdict it implies, for the MAJORITY_OVERRIDE check.
_STANCE_VERDICT = {
    Stance.BULLISH: Recommendation.BUY,
    Stance.NEUTRAL: Recommendation.HOLD,
    Stance.BEARISH: Recommendation.SELL,
}

# Optional, by-design data sources: a failure here is MINOR (never material alone)
# — sentiment is informational and the council degrades to abstention without it.
_SENTIMENT_TOOLS = ("get_company_news", "get_recommendation_trends",
                    "sentiment_snapshot")

# A gate cap escalates only when the LLM was confident BEYOND the veto threshold by
# this margin (so >= 0.70 when min_confidence is 0.60) — see trigger 7.
_GATE_CONF_MARGIN = 0.10


def make_veto_node(strategy: Strategy):
    min_conf = strategy.veto.min_confidence

    def veto(state: ResearchState) -> ResearchState:
        flags: list[VetoFlag] = []

        # 1 — low EVIDENCE COVERAGE (hardening ITEM 3). The escalation no longer
        # consumes the narrator's self-assigned confidence (an LLM number moving a
        # mechanical outcome); it consumes a DETERMINISTIC coverage score of what the
        # run actually saw. The narrator's prose conviction cannot alter this.
        from ..coverage import coverage_from_state
        cov = coverage_from_state(state)
        state.evidence_coverage = cov["score"]
        if cov["score"] < min_conf:
            flags.append(VetoFlag(
                trigger=VetoTrigger.LOW_CONFIDENCE,
                detail=f"evidence coverage {cov['score']:.2f} < threshold "
                       f"{min_conf:.2f} (deterministic — not the narrator's number)",
            ))

        # 2 — specialist conflict
        stances = {o.stance for o in state.specialist_opinions}
        if Stance.BULLISH in stances and Stance.BEARISH in stances:
            flags.append(VetoFlag(
                trigger=VetoTrigger.SPECIALIST_CONFLICT,
                detail="at least one bullish and one bearish specialist",
            ))

        # 3 — data quality, SEVERITY-AWARE. Firing on ANY data noise (which nearly
        # every run carries) trained reviewers to ignore the flag, so we split items
        # into MATERIAL (escalates) vs MINOR (recorded, never escalates alone) and
        # fire ONLY when >=1 material item is present. When it DOES fire, the detail
        # lists ALL items (material + minor) for the full picture. Minor-only runs
        # produce no veto, but the items are NOT lost — they remain in state.errors,
        # the screen's `flags`, and the specialist abstentions the report renders.
        material: list[str] = []
        minor: list[str] = []

        # (a) errors: a real market-data fetch failure or a provenance violation is
        #     MATERIAL; an OPTIONAL sentiment-source failure (403 / missing news) is
        #     MINOR (classified by the tool-name prefix the gather logger stamps).
        for e in state.errors:
            tool_part = e.split(":", 1)[0].strip()
            (minor if tool_part in _SENTIMENT_TOOLS else material).append(e)

        # (b) screen NOT-EVAL criteria (`unverifiable:<name>:<note>`). TWO OR MORE
        #     means the screen is mostly blind -> MATERIAL; a lone one is a single
        #     non-gating gap -> MINOR. (A GATING NOT-EVAL already short-circuited to
        #     INSUFFICIENT_EVIDENCE via trigger 6.)
        screen_flags: list[str] = []
        for tc in state.tool_calls:
            if _is_screen_tool(tc.tool_name) and tc.output:
                screen_flags += [str(f) for f in tc.output.get("flags", [])]
        (material if len(screen_flags) >= 2 else minor).extend(screen_flags)

        # (c) a specialist abstention is an honest "I lack data" — never material.
        for o in state.specialist_opinions:
            if o.stance == Stance.ABSTAIN:
                minor.append(f"{o.specialist.value} abstained (insufficient data)")

        if material:
            items = material + minor
            flags.append(VetoFlag(
                trigger=VetoTrigger.DATA_QUALITY,
                detail="; ".join(items[:6]) + ("; ..." if len(items) > 6 else ""),
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

        # 7 — gate-cap magnitude escalation. The deterministic gate is authoritative
        # and MOST caps need no human (a routine HOLD->SELL on a confirmed screen
        # fail). We escalate ONLY the SURPRISING cap — the LLM was confidently
        # bullish and the gate hard-stopped it — because that specific large gap is
        # where either the LLM's reasoning or the underlying data deserves a second
        # look. INSUFFICIENT_EVIDENCE caps are trigger 6's job and excluded here.
        d = state.decision
        if (d is not None and d.gate_override_applied and not d.insufficient_evidence
                and d.original_recommendation is not None):
            original, final = d.original_recommendation, d.recommendation
            large = (
                original == Recommendation.BUY            # confident BUY slammed to the cap
                or (d.confidence >= min_conf + _GATE_CONF_MARGIN  # HIGH confidence ...
                    and _RANK[original] - _RANK[final] > 1)       # ... and >1 rung drop
            )
            if large:
                flags.append(VetoFlag(
                    trigger=VetoTrigger.GATE_OVERRIDE_MATERIAL,
                    detail=f"deterministic gate capped a {d.confidence:.2f}-"
                           f"confidence {original.value} to {final.value} on "
                           f"{d.gating_criterion_fired} — large LLM/gate "
                           f"disagreement, human review advised",
                ))

        state.veto_flags.extend(flags)
        return state

    return veto
