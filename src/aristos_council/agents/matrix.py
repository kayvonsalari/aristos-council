"""Deterministic decision matrix — the reproducible half of the hybrid verdict.

``decision_matrix(state, strategy)`` computes a BUY/HOLD/SELL (or the gated
verdict) from DETERMINISTIC inputs with NO LLM call, so the matrix verdict never
wobbles. It runs ALONGSIDE the LLM Decision agent (it does not replace it); both
verdicts are reported and compared.

Design:
- PRIMARY, screen-anchored: each criterion's three-valued ``passed`` plus its
  observed-vs-threshold MARGIN (a continuous, deterministic distance). The
  disposition gate is respected EXACTLY — a confirmed gating fail (SELL cap) or a
  NOT-EVAL gating criterion (INSUFFICIENT_EVIDENCE) returns the gated verdict and
  skips scoring, identical to the LLM path.
- SECONDARY, low-weighted: specialist stances as numbers (bullish +1 / neutral 0 /
  bearish -1 / abstain 0) times confidence times a SMALL stance weight. These are
  the wobble source, so they only tilt a score the screen dominates.
- Score -> verdict with a dead-band: >= buy_threshold BUY, <= sell_threshold SELL,
  else HOLD; within ``borderline_margin`` of the nearest boundary -> BORDERLINE (a
  deterministic single-run "your call" signal). Every input's contribution is
  emitted so the verdict is fully auditable and the weights are tunable.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass

from ..state import (
    MatrixContribution,
    MatrixVerdict,
    Recommendation,
    ResearchState,
    Stance,
    ToolCall,
)
from ..strategy.loader import Strategy
from .disposition import disposition_ceiling, insufficient_evidence
# Single source for reading the screen criteria out of the ledger (handles the
# current + legacy screen tool names, dict-or-dataclass output).
from .nodes import _screen_criteria

_STANCE_NUM = {
    Stance.BULLISH: 1.0,
    Stance.BEARISH: -1.0,
    Stance.NEUTRAL: 0.0,
    Stance.ABSTAIN: 0.0,
}


def _get(c, key):
    return c.get(key) if isinstance(c, dict) else getattr(c, key, None)


def _criterion_points(name, passed, observed, threshold, weight) -> tuple[float, str]:
    """Signed points for one criterion: weight x clamped margin.

    Direction-aware (min_* higher-is-better, max_* lower-is-better); a NOT-EVAL
    (passed is None) contributes 0; a pass/fail with no usable observed value
    contributes +/- the full weight by its ``passed`` sign (e.g. the FIX-1c PEG
    fail, which is passed=False with observed=None).
    """
    if passed is None:
        return 0.0, f"{name}: NOT-EVAL -> 0"
    direction = -1.0 if name.startswith("max_") else 1.0
    if observed is None or threshold is None or threshold == 0:
        frac = 1.0 if passed else -1.0
        margin_txt = "no margin"
    else:
        raw = direction * (observed - threshold) / abs(threshold)
        frac = max(-1.0, min(1.0, raw))
        margin_txt = f"margin {frac:+.2f}"
    pts = weight * frac
    verb = "pass" if passed else "fail"
    return pts, f"{name}: {verb} ({margin_txt}) x w{weight:.0f} = {pts:+.1f}"


def _map_score(score: float, sc) -> tuple[Recommendation, bool]:
    if score >= sc.buy_threshold:
        verdict = Recommendation.BUY
    elif score <= sc.sell_threshold:
        verdict = Recommendation.SELL
    else:
        verdict = Recommendation.HOLD
    nearest = min(abs(score - sc.buy_threshold), abs(score - sc.sell_threshold))
    return verdict, nearest <= sc.borderline_margin


def decision_matrix(state: ResearchState, strategy: Strategy) -> MatrixVerdict:
    """Compute the deterministic matrix verdict. Pure: identical state -> identical
    score and verdict."""
    sc = strategy.scoring
    screen = _screen_criteria(state)
    gating = {c.name for c in strategy.criteria if getattr(c, "is_gating", False)}

    # The gate is deterministic and SUPERSEDES scoring — respect it exactly.
    if gating:
        ceiling = disposition_ceiling(screen, gating)
        if ceiling is not None:
            return MatrixVerdict(
                verdict=ceiling, score=None, gated=True, borderline=False,
                contributions=[MatrixContribution(
                    name="gate", points=0.0,
                    detail="disposition gate: confirmed gating fail -> SELL cap "
                           "(scoring skipped)")])
        if insufficient_evidence(screen, gating):
            return MatrixVerdict(
                verdict=Recommendation.INSUFFICIENT_EVIDENCE, score=None,
                gated=True, borderline=False,
                contributions=[MatrixContribution(
                    name="gate", points=0.0,
                    detail="disposition gate: gating criterion NOT-EVAL -> "
                           "INSUFFICIENT_EVIDENCE (scoring skipped)")])

    contributions: list[MatrixContribution] = []
    # PRIMARY: screen criteria (the anchor).
    for c in screen:
        name = _get(c, "name")
        pts, detail = _criterion_points(
            name, _get(c, "passed"), _get(c, "observed"), _get(c, "threshold"),
            sc.weight_for(name))
        contributions.append(MatrixContribution(name=name, points=pts, detail=detail))

    # SECONDARY: specialist stances, LOW-weighted (only tilt the score).
    for op in state.specialist_opinions:
        num = _STANCE_NUM.get(op.stance, 0.0)
        pts = num * float(op.confidence) * sc.stance_weight
        contributions.append(MatrixContribution(
            name=f"stance:{op.specialist.value}", points=pts,
            detail=(f"{op.specialist.value} {op.stance.value} x conf "
                    f"{op.confidence:.2f} x w{sc.stance_weight:.0f} = {pts:+.1f}")))

    score = sum(c.points for c in contributions)
    verdict, borderline = _map_score(score, sc)
    return MatrixVerdict(verdict=verdict, score=score, borderline=borderline,
                         gated=False, contributions=contributions)


def screen_only_matrix(screen, strategy: Strategy, *, ticker: str = "") -> MatrixVerdict:
    """The matrix verdict from the SCREEN ALONE — no specialist stances. Used by the
    fast screen-only ranking path (examples/rank_screen.py).

    It calls ``decision_matrix`` on a state whose ``specialist_opinions`` is EMPTY,
    so the stance contributions are 0 and the screen-only score is exactly the full
    matrix score minus the (small) stance terms. Reusing ``decision_matrix`` means
    the criterion weights / margin math / thresholds are IDENTICAL to the full
    matrix — they cannot drift. ``screen`` is a ScreenResult (or its asdict dict).
    """
    out = asdict(screen) if is_dataclass(screen) else screen
    state = ResearchState(ticker=ticker, strategy_id=strategy.id)
    state.tool_calls.append(ToolCall(
        call_id="screen", tool_name="run_strategy_screen", output=out))
    return decision_matrix(state, strategy)


def make_matrix_node(strategy: Strategy):
    """Graph node: stamp the deterministic matrix verdict onto the state, in
    parallel with the LLM decision. No LLM, no mutation of the LLM decision."""
    def matrix(state: ResearchState) -> ResearchState:
        state.matrix_decision = decision_matrix(state, strategy)
        return state

    return matrix
