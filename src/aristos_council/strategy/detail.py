"""Generic strategy-detail model — the data behind the dynamic Strategy tab (Sprint 4C).

Everything the Strategy tab shows is DERIVED from the selected strategy's YAML: nothing
strategy-specific is hardcoded. A new strategy dropped into ``strategies/`` renders fully
with zero UI-code changes, because the UI only walks the sections this builder produces.

The sections mirror the tab, in order: header · description · screen criteria (with
gating) · gates (sector + rationale, market cap, payout) · rank factors + verdict cut ·
policy flags (meanings from the shared glossary, not per-strategy prose) · provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Policy-flag meanings — ONE shared glossary, never per-strategy prose (Sprint 4C).
POLICY_GLOSSARY: dict[str, str] = {
    "partial_pass_allows_hold":
        "A name passing some but not all criteria may warrant a HOLD (council "
        "discretion), not an automatic SELL.",
    "prefilter_screen":
        "The lens screen runs as a hard prefilter — any confirmed criterion fail "
        "excludes the name before ranking.",
    "council_mode":
        "How the LLM council is used: narrator (explains the ranker verdict) or "
        "second_opinion (an independent comparison verdict).",
    "council_runs_on":
        "Which ranked names the LLM council runs on (buy_quintile / top_k / all).",
    "unverifiable_streak_is_blocking":
        "An unverifiable dividend streak blocks (pauses for human review).",
}


@dataclass
class CriterionRow:
    name: str
    threshold: object
    gating: bool                        # is_gating (a confirmed fail caps disposition)


@dataclass
class GateRow:
    name: str
    value: str
    rationale: str = ""


@dataclass
class FactorRow:
    name: str
    direction: str                      # "high" | "low"


@dataclass
class PolicyRow:
    name: str
    value: str
    meaning: str


@dataclass
class StrategyDetail:
    # 1 — header
    display_name: str
    id: str
    version: object
    created: str
    kind: str
    # 2 — description (verbatim)
    description: str
    # 3 — screen criteria
    criteria: list[CriterionRow]
    screen_source: str                  # "own criteria" or "lens: <id>"
    # 4 — gates
    gates: list[GateRow]
    # 5 — rank factors + verdict cut
    factors: list[FactorRow]
    cut_rule: str
    # 6 — policy flags
    policy: list[PolicyRow]
    # 7 — provenance
    path: str


def _load_by_kind(path: Path, kind: str):
    from .loader import load_strategy
    from .rank_loader import load_rank_strategy
    return load_rank_strategy(path) if kind == "rank" else load_strategy(path)


def _criteria_rows(specs) -> list[CriterionRow]:
    return [CriterionRow(name=getattr(c, "name", ""),
                         threshold=getattr(c, "threshold", None),
                         gating=bool(getattr(c, "is_gating", False)))
            for c in specs]


def _cut_rule(s) -> str:
    cut = getattr(s, "cut", "")
    if cut == "top_k":
        return f"top_k (BUY the top {getattr(s, 'k', '?')} by combined rank)"
    if cut == "top_percentile":
        return f"top_percentile (BUY the top {getattr(s, 'percentile', '?')} fraction)"
    if cut == "quintile":
        return "quintile (top 20% BUY · middle 60% HOLD · bottom 20% SELL)"
    return cut or "—"


def _gate_rows(s) -> list[GateRow]:
    gates: list[GateRow] = []
    sectors = getattr(s, "exclude_sectors", None) or []
    if sectors:
        gates.append(GateRow("sector", "excludes " + ", ".join(sectors),
                             rationale=getattr(s, "sector_exclusion_rationale", "") or ""))
    # Sector INCLUSION scope (FIN-1) — mirror of the exclusion row. Data-layer only; the
    # tab walks the gates list, so this renders with zero UI-code changes.
    include = getattr(s, "include_sectors", None) or []
    if include:
        gates.append(GateRow("sector_scope", "admits only " + ", ".join(include),
                             rationale=getattr(s, "sector_inclusion_rationale", "") or ""))
    cap = getattr(s, "min_market_cap", None)
    if cap is not None:
        gates.append(GateRow("min_market_cap", f"≥ {cap:,.0f}"))
    payout = getattr(s, "max_payout_ratio", None)
    if payout is not None:
        gates.append(GateRow("max_payout_ratio", f"≤ {payout:.0%}"))
    return gates


def _policy_rows(s) -> list[PolicyRow]:
    """Every glossary flag the strategy actually carries, with its value + plain meaning.
    Sourced from the shared glossary — never per-strategy prose."""
    rows: list[PolicyRow] = []
    policy = getattr(s, "policy", None)
    for name, meaning in POLICY_GLOSSARY.items():
        val = None
        if policy is not None and hasattr(policy, name):
            val = getattr(policy, name)
        elif hasattr(s, name):
            val = getattr(s, name)
        if val is not None and not (isinstance(val, str) and val == ""):
            rows.append(PolicyRow(name=name, value=str(val), meaning=meaning))
    return rows


def strategy_detail(strategy_id: str, strategies_dir: str | Path) -> StrategyDetail:
    """Build the full detail model for one strategy id — resolving the screen criteria
    (a rank strategy's lens, or a council strategy's own criteria) and the gates/factors/
    policy entirely from YAML. Raises KeyError if the id isn't discovered."""
    from .discovery import discover_strategies

    strategies_dir = Path(strategies_dir)
    infos = {i.id: i for i in discover_strategies(strategies_dir)}
    if strategy_id not in infos:
        raise KeyError(f"unknown strategy id '{strategy_id}'")
    info = infos[strategy_id]
    s = _load_by_kind(info.path, info.kind)

    # Screen criteria: a rank strategy's lens screen, else the strategy's own criteria.
    lens_id = getattr(s, "council_screen_strategy", None)
    if info.kind == "rank" and lens_id and lens_id in infos:
        lens = _load_by_kind(infos[lens_id].path, "council")
        criteria = _criteria_rows(getattr(lens, "criteria", []))
        screen_source = f"lens: {lens_id}"
    else:
        criteria = _criteria_rows(getattr(s, "criteria", []))
        screen_source = "own criteria"

    from ..factors import FACTOR_REGISTRY
    factors = [FactorRow(name=f.name,
                         direction=(getattr(f, "direction", None)
                                    or FACTOR_REGISTRY[f.name].direction))
               for f in getattr(s, "factors", [])]

    return StrategyDetail(
        display_name=(getattr(s, "display_name", "") or getattr(s, "name", "") or s.id),
        id=s.id, version=getattr(s, "version", ""),
        created=getattr(s, "created", "") or "", kind=info.kind,
        description=(getattr(s, "description", "") or "").strip(),
        criteria=criteria, screen_source=screen_source,
        gates=_gate_rows(s), factors=factors,
        cut_rule=(_cut_rule(s) if info.kind == "rank" else "—"),
        policy=_policy_rows(s), path=str(info.path))


PROVENANCE_NOTE = ("configs are versioned; strategies are never mutated, "
                   "they are superseded.")
