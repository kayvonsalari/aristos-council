"""Deterministic disposition ceiling — authoritative over the LLM Decision.

Why this is code, not a prompt: ``partial_pass_allows_hold`` was a SOFT prompt
hint and proved EVADABLE. Across four council runs (T, MSFT, ASML, ARM) the
Decision agent overrode screen failures whenever the Critic supplied an
input-quality argument, and flipping the flag to False did not reliably change
the verdict (ARM held on a double screen-failure even with the flag off, by
declaring the failures "not dispositive"). So gating is enforced HERE, as a
post-Decision ceiling the LLM cannot raise.

Disposition ordering, most to least bullish: BUY > HOLD > SELL.
"""

from __future__ import annotations

from ..state import Recommendation

# A ceiling caps how bullish the final verdict may be. Higher rank = more bullish.
_RANK = {Recommendation.BUY: 2, Recommendation.HOLD: 1, Recommendation.SELL: 0}


def _name(c) -> str:
    return c["name"] if isinstance(c, dict) else c.name


def _passed(c):
    return c["passed"] if isinstance(c, dict) else c.passed


def failed_gating_criteria(screen_criteria, gating_names) -> list[str]:
    """Names of gating criteria with a CONFIRMED fail, in screen order.

    A confirmed fail is ``passed is False`` — an IDENTITY check, deliberately:
    a NOT-EVAL (``passed is None``) is NOT a confirmed fail and must not gate.

    TODO (future build): a NOT-EVAL on a gating criterion currently yields NO
    cap. Strategy-disposition work (INSUFFICIENT_EVIDENCE) may later escalate it;
    this build intentionally does not cap on ``passed is None``.
    """
    gating = set(gating_names)
    return [
        _name(c)
        for c in screen_criteria
        if _name(c) in gating and _passed(c) is False
    ]


def disposition_ceiling(screen_criteria, gating_names) -> Recommendation | None:
    """The most-bullish disposition the screen permits.

    Returns ``SELL`` if any gating criterion is a confirmed fail (``passed is
    False``); otherwise ``None`` (the gate is silent — no cap).
    """
    if failed_gating_criteria(screen_criteria, gating_names):
        return Recommendation.SELL
    return None


def exceeds_ceiling(recommendation: Recommendation,
                    ceiling: Recommendation) -> bool:
    """True if ``recommendation`` is more bullish than ``ceiling`` (needs capping)."""
    return _RANK[recommendation] > _RANK[ceiling]
