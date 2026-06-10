"""Council graph assembly.

    gather -> fundamental -> technical -> sentiment -> risk
           -> critic -> decision -> veto -> END

Sequential for Phase 2 (simple to trace in LangSmith); the four specialists are
independent and can be parallelised later without touching node code.

`build_council` takes its dependencies explicitly — adapter, strategy, runners —
so tests inject fakes and production injects yfinance/EODHD + tiered LLM
runners. Nothing is constructed implicitly inside.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .agents.nodes import (
    make_critic_node,
    make_decision_node,
    make_gather_node,
    make_specialist_node,
)
from .agents.veto import make_veto_node
from .data.adapter import MarketDataAdapter
from .state import ResearchState, SpecialistName
from .strategy.loader import Strategy

SPECIALIST_ORDER = [
    SpecialistName.FUNDAMENTAL,
    SpecialistName.TECHNICAL,
    SpecialistName.SENTIMENT,
    SpecialistName.RISK,
]


def build_council(
    adapter: MarketDataAdapter,
    strategy: Strategy,
    runners: dict,   # keys: "specialist", "critic", "decision"
):
    g = StateGraph(ResearchState)

    g.add_node("gather", make_gather_node(adapter, strategy))
    for who in SPECIALIST_ORDER:
        g.add_node(
            who.value,
            make_specialist_node(who, strategy, runners["specialist"]),
        )
    g.add_node("critic", make_critic_node(strategy, runners["critic"]))
    g.add_node("decision", make_decision_node(strategy, runners["decision"]))
    g.add_node("veto", make_veto_node(strategy))

    g.set_entry_point("gather")
    g.add_edge("gather", SPECIALIST_ORDER[0].value)
    for a, b in zip(SPECIALIST_ORDER[:-1], SPECIALIST_ORDER[1:]):
        g.add_edge(a.value, b.value)
    g.add_edge(SPECIALIST_ORDER[-1].value, "critic")
    g.add_edge("critic", "decision")
    g.add_edge("decision", "veto")
    g.add_edge("veto", END)

    return g.compile()
