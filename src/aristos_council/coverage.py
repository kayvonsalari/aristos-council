"""Deterministic evidence-coverage score (hardening ITEM 3).

Closes the last LLM side-door. The low-confidence escalation veto used to consume the
NARRATOR's self-assigned confidence — an LLM number moving a mechanical outcome (the
escalation decision), the exact failure class the council was demoted for. It now
consumes THIS score: a pure function of what the run ACTUALLY SAW.

Five components, each in [0, 1], combined by fixed weights (sum = 1.0):

  criteria      0.30   screen criteria EVALUATED / total (a NOT-EVAL is not evidence)
  factors       0.20   1 - fraction of ranker factors IMPUTED (absent values)
  provenance    0.25   figures VERIFIED / audited (mismatch/unresolvable discount)
  fundamentals  0.15   core fundamentals fields present / expected
  price         0.10   price history sufficient for the technical snapshot (0/1)

A component whose data is ABSENT (not part of this state) defaults to 1.0 — it never
invents a penalty from context that was never gathered (a standalone/legacy run has no
ranker factors; a unit-test state has no tool calls). A component whose fetch was
ATTEMPTED and FAILED scores 0.0 (a real fundamentals/price failure IS a coverage gap).

No LLM anywhere. Pure and unit-tested.
"""

from __future__ import annotations

from typing import Optional

WEIGHTS = {"criteria": 0.30, "factors": 0.20, "provenance": 0.25,
           "fundamentals": 0.15, "price": 0.10}

# Core fundamentals always surfaced by get_fundamentals (Sprint 4D fixed core).
_CORE_FUNDAMENTALS = ("market_cap", "pe_ratio", "eps", "free_cash_flow")


def _clamp(v: float) -> float:
    return 0.0 if v < 0 else (1.0 if v > 1 else float(v))


def evidence_coverage_score(*, criteria: float = 1.0, factors: float = 1.0,
                            provenance: float = 1.0, fundamentals: float = 1.0,
                            price: float = 1.0) -> float:
    """The weighted coverage score in [0, 1] from the five components."""
    comps = {"criteria": criteria, "factors": factors, "provenance": provenance,
             "fundamentals": fundamentals, "price": price}
    return round(sum(WEIGHTS[k] * _clamp(v) for k, v in comps.items()), 4)


def _criteria_component(screen: Optional[dict]) -> float:
    crits = (screen or {}).get("criteria") or []
    if not crits:
        return 1.0                        # no screen -> not measured
    evaluated = sum(1 for c in crits if c.get("passed") is not None)
    return evaluated / len(crits)


def _provenance_component(audit: Optional[dict]) -> float:
    n = (audit or {}).get("figures_audited", 0) or 0
    if not n:
        return 1.0                        # nothing audited -> not measured
    return ((audit or {}).get("verified", 0) or 0) / n


def _field(obj, name):
    """Read a field from a dict OR a dataclass/model — the get_fundamentals tool output
    is a ``Fundamentals`` object at veto time, a dict when reconstructed from a report."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _fundamentals_component(found: bool, ok: bool, output) -> float:
    if not found:
        return 1.0                        # not fetched in this state -> not measured
    if not ok or output is None:
        return 0.0                        # fetch ATTEMPTED and failed -> a real gap
    present = sum(1 for f in _CORE_FUNDAMENTALS if _field(output, f) is not None)
    return present / len(_CORE_FUNDAMENTALS)


def coverage_components_from_state(state) -> dict[str, float]:
    """Extract the five components from a completed ResearchState (deterministic)."""
    from .agents.nodes import _is_screen_tool          # lazy: avoid an import cycle

    screen: Optional[dict] = None
    fund_found = fund_ok = False
    fund_out: Optional[dict] = None
    price_found = price_ok = False
    for tc in state.tool_calls:
        if _is_screen_tool(tc.tool_name) and tc.output:
            screen = tc.output
        elif tc.tool_name == "get_fundamentals":
            fund_found, fund_ok, fund_out = True, tc.ok, tc.output
        elif tc.tool_name == "technical_snapshot":
            price_found, price_ok = True, (tc.ok and bool(tc.output))

    imputed = getattr(state, "ranker_imputed_fraction", None) or 0.0
    return {
        "criteria": _criteria_component(screen),
        "factors": 1.0 - _clamp(imputed),
        "provenance": _provenance_component(getattr(state, "provenance_audit", None)),
        "fundamentals": _fundamentals_component(fund_found, fund_ok, fund_out),
        "price": 1.0 if (not price_found or price_ok) else 0.0,
    }


def coverage_from_state(state) -> dict:
    """{'score': float, 'components': {...}} for a completed ResearchState."""
    comps = coverage_components_from_state(state)
    return {"score": evidence_coverage_score(**comps), "components": comps}
