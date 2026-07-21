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

from .agents.matrix import make_matrix_node
from .agents.nodes import (
    make_critic_node,
    make_decision_node,
    make_gather_node,
    make_specialist_node,
)
from .agents.veto import make_veto_node
from .audit.provenance import make_audit_node
from .data.adapter import MarketDataAdapter
from .data.sentiment import SentimentAdapter
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
    sentiment_adapter: SentimentAdapter | None = None,
    *,
    sentiment_missing_key: bool = False,
    council_mode: str = "second_opinion",   # A/B toggle (B default); see prompts.py
    run_matrix: bool = True,                 # skip when a RANKER drives the pipeline
):
    g = StateGraph(ResearchState)

    g.add_node("gather",
               make_gather_node(adapter, strategy, sentiment_adapter,
                                sentiment_missing_key=sentiment_missing_key))
    for who in SPECIALIST_ORDER:
        g.add_node(
            who.value,
            make_specialist_node(who, strategy, runners["specialist"], council_mode),
        )
    g.add_node("critic",
               make_critic_node(strategy, runners["critic"], council_mode))
    g.add_node("decision",
               make_decision_node(strategy, runners["decision"], council_mode))
    g.add_node("audit", make_audit_node())
    g.add_node("veto", make_veto_node(strategy))

    g.set_entry_point("gather")
    g.add_edge("gather", SPECIALIST_ORDER[0].value)
    for a, b in zip(SPECIALIST_ORDER[:-1], SPECIALIST_ORDER[1:]):
        g.add_edge(a.value, b.value)
    g.add_edge(SPECIALIST_ORDER[-1].value, "critic")
    g.add_edge("critic", "decision")
    if run_matrix:
        # The old deterministic matrix verdict (screen strategies). REDUNDANT when a
        # RANKER is the verdict-of-record, so the pipeline skips it for rank runs.
        g.add_node("matrix", make_matrix_node(strategy))
        g.add_edge("decision", "matrix")
        g.add_edge("matrix", "audit")
    else:
        g.add_edge("decision", "audit")
    g.add_edge("audit", "veto")
    g.add_edge("veto", END)

    return g.compile()


def build_upstream_council(
    adapter: MarketDataAdapter,
    strategy: Strategy,
    runners: dict,   # only "specialist" + "critic" are used here
    sentiment_adapter: SentimentAdapter | None = None,
    *,
    sentiment_missing_key: bool = False,
):
    """The UPSTREAM-ONLY graph: gather -> specialists -> critic -> END.

    Stops BEFORE the Decision node, so a single invoke yields the post-Critic
    ResearchState (specialist_opinions + critic_report populated) without ever
    running the Decision/audit/veto stages. This is the substrate for the
    decision-node micro-harness (reproducibility.run_decision_n): the deterministic
    screen, the four specialists, and the Critic are empirically STABLE, so they run
    ONCE; only the Decision node — where the BUY/HOLD/SELL wobble lives — is replayed
    on copies of this snapshot. Node factories are shared with build_council, so the
    upstream wiring can never drift from the full graph.
    """
    g = StateGraph(ResearchState)

    g.add_node("gather",
               make_gather_node(adapter, strategy, sentiment_adapter,
                                sentiment_missing_key=sentiment_missing_key))
    for who in SPECIALIST_ORDER:
        g.add_node(
            who.value,
            make_specialist_node(who, strategy, runners["specialist"]),
        )
    g.add_node("critic", make_critic_node(strategy, runners["critic"]))

    g.set_entry_point("gather")
    g.add_edge("gather", SPECIALIST_ORDER[0].value)
    for a, b in zip(SPECIALIST_ORDER[:-1], SPECIALIST_ORDER[1:]):
        g.add_edge(a.value, b.value)
    g.add_edge(SPECIALIST_ORDER[-1].value, "critic")
    g.add_edge("critic", END)

    return g.compile()
