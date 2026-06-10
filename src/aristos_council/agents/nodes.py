"""Graph nodes for the council.

Division of labour (the core guardrail, restated):
- `gather` is 100% deterministic: it calls the data adapter, runs the screening
  and technical tools, and logs every result as a ToolCall. It is the ONLY
  node that touches data or does math.
- Specialist/Critic/Decision nodes call an LLM but may not compute anything.
  Numbers they cite must reference a ToolCall logged by `gather`; references
  are validated here and violations are recorded.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import date, timedelta

from ..data.adapter import DataUnavailable, MarketDataAdapter
from ..state import (
    CriticReport,
    Decision,
    Figure,
    Provenance,
    ResearchState,
    SpecialistName,
    SpecialistOpinion,
    Stance,
    ToolCall,
)
from ..strategy.loader import Strategy
from ..tools.screening import run_dividend_aristocrat_screen
from ..tools.technical import technical_snapshot
from .schemas import CriticOutput, DecisionOutput, SpecialistOutput


def _new_call_id() -> str:
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------- #
# gather — deterministic evidence collection
# --------------------------------------------------------------------------- #
def make_gather_node(adapter: MarketDataAdapter, strategy: Strategy):
    def gather(state: ResearchState) -> ResearchState:
        today = date.today()
        lookback_start = today - timedelta(days=400)   # enough for SMA200
        div_start = today - timedelta(days=365 * 40)   # as deep as provider allows

        def log(tool_name: str, inputs: dict, fn):
            call_id = _new_call_id()
            try:
                output = fn()
                state.tool_calls.append(
                    ToolCall(call_id=call_id, tool_name=tool_name,
                             inputs=inputs, output=output, ok=True)
                )
                return output
            except DataUnavailable as exc:
                state.tool_calls.append(
                    ToolCall(call_id=call_id, tool_name=tool_name,
                             inputs=inputs, output=None, ok=False,
                             error=str(exc))
                )
                state.errors.append(f"{tool_name}: {exc}")
                return None

        fundamentals = log(
            "get_fundamentals", {"ticker": state.ticker},
            lambda: adapter.get_fundamentals(state.ticker),
        )
        dividends = log(
            "get_dividend_history",
            {"ticker": state.ticker, "start": str(div_start), "end": str(today)},
            lambda: adapter.get_dividend_history(
                state.ticker, start=div_start, end=today
            ),
        )
        prices = log(
            "get_price_history",
            {"ticker": state.ticker, "start": str(lookback_start), "end": str(today)},
            lambda: adapter.get_price_history(
                state.ticker, start=lookback_start, end=today
            ),
        )

        if fundamentals is not None:
            c = strategy.criteria
            screen = run_dividend_aristocrat_screen(
                fundamentals,
                dividends or [],
                min_yield=c.min_dividend_yield,
                max_payout=c.max_payout_ratio,
                min_market_cap=c.min_market_cap,
                min_growth_years=c.min_dividend_growth_years,
            )
            state.tool_calls.append(
                ToolCall(
                    call_id=_new_call_id(),
                    tool_name="run_dividend_aristocrat_screen",
                    inputs={"ticker": state.ticker,
                            "strategy_id": strategy.id},
                    output=asdict(screen),
                )
            )

        if prices is not None and prices.closes:
            snap = technical_snapshot(prices.closes)
            state.tool_calls.append(
                ToolCall(
                    call_id=_new_call_id(),
                    tool_name="technical_snapshot",
                    inputs={"ticker": state.ticker,
                            "n_closes": len(prices.closes)},
                    output=asdict(snap),
                )
            )
        return state

    return gather


# --------------------------------------------------------------------------- #
# evidence serialization for prompts
# --------------------------------------------------------------------------- #
def _evidence_block(state: ResearchState) -> str:
    lines = []
    for tc in state.tool_calls:
        lines.append(
            json.dumps(
                {"call_id": tc.call_id, "tool": tc.tool_name,
                 "ok": tc.ok, "error": tc.error, "output": tc.output},
                default=str,
            )
        )
    return "\n".join(lines)


_SPECIALIST_BRIEFS = {
    SpecialistName.FUNDAMENTAL:
        "You assess business quality and dividend durability: yield, payout "
        "sustainability, growth streak, market cap. Lean on the screen results.",
    SpecialistName.TECHNICAL:
        "You assess price structure: trend vs SMA50/SMA200, distance from the "
        "52-week high, volatility. Lean on the technical_snapshot output.",
    SpecialistName.SENTIMENT:
        "You assess news/market sentiment. If NO sentiment tool output exists "
        "in the evidence, you MUST return stance=abstain with a caveat saying "
        "sentiment data is not yet wired in. Do not improvise sentiment.",
    SpecialistName.RISK:
        "You assess downside: payout stretch, volatility, data-quality flags, "
        "anything unverifiable. You are the council's professional pessimist.",
}


def _specialist_prompt(state: ResearchState, who: SpecialistName,
                       strategy: Strategy) -> str:
    return (
        f"You are the {who.value.upper()} specialist on an investment research "
        f"council analysing {state.ticker} under the strategy "
        f"'{strategy.name}' (id {strategy.id}).\n\n"
        f"Strategy rationale:\n{strategy.rationale}\n\n"
        f"Your brief: {_SPECIALIST_BRIEFS[who]}\n\n"
        "HARD RULES:\n"
        "- Do not perform arithmetic. Only cite numbers that appear verbatim in "
        "the evidence below.\n"
        "- Every number you cite goes in `figures`, with the exact call_id and "
        "field_path it came from.\n"
        "- If the evidence is insufficient for your domain, abstain.\n\n"
        f"EVIDENCE (one JSON tool call per line):\n{_evidence_block(state)}\n"
    )


def make_specialist_node(who: SpecialistName, strategy: Strategy, runner):
    def specialist(state: ResearchState) -> ResearchState:
        out: SpecialistOutput = runner.invoke(
            _specialist_prompt(state, who, strategy)
        )
        figures: list[Figure] = []
        for f in out.figures:
            if state.tool_call_by_id(f.call_id) is None:
                # Untraceable number: provenance violation, recorded loudly.
                state.errors.append(
                    f"provenance violation: {who.value} cited '{f.label}'="
                    f"{f.value} with unknown call_id '{f.call_id}'"
                )
                continue
            figures.append(
                Figure(label=f.label, value=f.value, unit=f.unit,
                       provenance=Provenance(
                           tool_name=state.tool_call_by_id(f.call_id).tool_name,
                           call_id=f.call_id, field_path=f.field_path))
            )
        state.specialist_opinions.append(
            SpecialistOpinion(
                specialist=who, stance=out.stance, confidence=out.confidence,
                thesis=out.thesis, figures=figures, caveats=out.caveats,
            )
        )
        return state

    return specialist


# --------------------------------------------------------------------------- #
# critic — argues the OPPOSITE of the emerging consensus
# --------------------------------------------------------------------------- #
def consensus_stance(state: ResearchState) -> Stance:
    votes = [o.stance for o in state.specialist_opinions
             if o.stance != Stance.ABSTAIN]
    if not votes:
        return Stance.NEUTRAL
    bulls = votes.count(Stance.BULLISH)
    bears = votes.count(Stance.BEARISH)
    if bulls > bears:
        return Stance.BULLISH
    if bears > bulls:
        return Stance.BEARISH
    return Stance.NEUTRAL


def make_critic_node(strategy: Strategy, runner):
    def critic(state: ResearchState) -> ResearchState:
        target = consensus_stance(state)
        opinions = "\n".join(
            f"- {o.specialist.value}: {o.stance.value} "
            f"(conf {o.confidence:.2f}) — {o.thesis}"
            for o in state.specialist_opinions
        )
        prompt = (
            f"You are the CRITIC on an investment research council analysing "
            f"{state.ticker}. The emerging consensus is {target.value.upper()}. "
            "Your job is to argue the strongest OPPOSITE case before any "
            "verdict. Attack weak reasoning, mis-weighted figures, and missing "
            "evidence. You do not vote.\n\n"
            f"Specialist opinions:\n{opinions}\n\n"
            f"EVIDENCE:\n{_evidence_block(state)}\n"
        )
        out: CriticOutput = runner.invoke(prompt)
        state.critic_report = CriticReport(
            targets_stance=target,
            counter_thesis=out.counter_thesis,
            weaknesses_found=out.weaknesses_found,
            challenged_figures=out.challenged_figures,
        )
        return state

    return critic


# --------------------------------------------------------------------------- #
# decision — buy/hold/sell with confidence and dissent
# --------------------------------------------------------------------------- #
def make_decision_node(strategy: Strategy, runner):
    def decide(state: ResearchState) -> ResearchState:
        opinions = "\n".join(
            f"- {o.specialist.value}: {o.stance.value} "
            f"(conf {o.confidence:.2f}) — {o.thesis} "
            f"| caveats: {'; '.join(o.caveats) or 'none'}"
            for o in state.specialist_opinions
        )
        critic = (
            f"{state.critic_report.counter_thesis}\n"
            f"Weaknesses: {'; '.join(state.critic_report.weaknesses_found)}"
            if state.critic_report else "no critic report"
        )
        prompt = (
            f"You are the DECISION agent of an investment research council "
            f"analysing {state.ticker} under strategy '{strategy.name}'. Weigh "
            "the specialists AND the critic's counter-case, then issue "
            "buy/hold/sell with a confidence in [0,1]. List every specialist "
            "whose stance your call overrides in `dissent` — dissent must "
            "never be silently dropped. Policy notes: "
            f"partial_pass_allows_hold={strategy.policy.partial_pass_allows_hold}.\n\n"
            f"Specialists:\n{opinions}\n\nCritic:\n{critic}\n"
        )
        out: DecisionOutput = runner.invoke(prompt)
        state.decision = Decision(
            recommendation=out.recommendation,
            confidence=out.confidence,
            rationale=out.rationale,
            dissent=out.dissent,
        )
        return state

    return decide
