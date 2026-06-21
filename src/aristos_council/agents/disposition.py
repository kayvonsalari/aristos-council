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
    A NOT-EVAL on a gating criterion is handled separately by
    ``insufficient_evidence`` (short-circuit to INSUFFICIENT_EVIDENCE).
    """
    gating = set(gating_names)
    return [
        _name(c)
        for c in screen_criteria
        if _name(c) in gating and _passed(c) is False
    ]


def not_evaluated_gating_criteria(screen_criteria, gating_names) -> list[str]:
    """Names of gating criteria that are NOT-EVAL (``passed is None``), in order.

    The NOT-EVAL counterpart to ``failed_gating_criteria`` — an IDENTITY check on
    None, so a confirmed fail (``passed is False``) is excluded.
    """
    gating = set(gating_names)
    return [
        _name(c)
        for c in screen_criteria
        if _name(c) in gating and _passed(c) is None
    ]


def insufficient_evidence(screen_criteria, gating_names) -> bool:
    """True if any GATING criterion is NOT-EVAL (passed is None) — identity check."""
    gating = set(gating_names)
    return any(_name(c) in gating and _passed(c) is None for c in screen_criteria)


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
    """True if ``recommendation`` is more bullish than ``ceiling`` (needs capping).

    Defensive: INSUFFICIENT_EVIDENCE is OFF the buy/hold/sell ladder and absent
    from ``_RANK``. It must never reach here (the decision node short-circuits
    before the ceiling), so a ranking attempt is a programming error, raised
    loudly rather than silently mis-ordered.
    """
    for rec in (recommendation, ceiling):
        if rec not in _RANK:
            raise ValueError(
                f"{rec!r} is off the buy/hold/sell ladder and cannot be ranked "
                "against a disposition ceiling (INSUFFICIENT_EVIDENCE "
                "short-circuits before the ceiling logic runs)."
            )
    return _RANK[recommendation] > _RANK[ceiling]
