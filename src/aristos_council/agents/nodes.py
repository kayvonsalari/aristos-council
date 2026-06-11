"""Graph nodes for the council.

Division of labour (the core guardrail, restated):
- `gather` is 100% deterministic: it calls the data adapter, runs the screening
  and technical tools, and logs every result as a ToolCall. It is the ONLY
  node that touches data or does math.
- Specialist/Critic/Decision nodes call an LLM but may not compute anything.
  Numbers they cite must reference a ToolCall logged by `gather`; references
  are validated here and violations are recorded.

Prompt architecture: every agent gets a SYSTEM message (stable: role, hard
rules, strategy rationale — identical across runs, cache-friendly, and weighted
more heavily for rule-adherence) and a USER message (per-run: ticker, evidence).

Critic contract (added after the KO live run, where the Critic smuggled an
external share count and forbidden arithmetic into its strongest argument and
the Decision agent endorsed it): the Critic is provenance-bound exactly like
specialists. Quantitative concerns it cannot support from the evidence go in
`open_questions`, phrased as questions for a human — and the Decision agent is
explicitly instructed those are unresolved questions, not evidence.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import date, timedelta

from ..data.adapter import DataUnavailable, MarketDataAdapter
from ..data.sentiment import SentimentAdapter, SentimentDataUnavailable
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
from ..tools.sentiment_tools import sentiment_snapshot
from ..tools.technical import technical_snapshot
from .schemas import CriticOutput, DecisionOutput, FigureRef, SpecialistOutput


def _new_call_id() -> str:
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------- #
# gather — deterministic evidence collection
# --------------------------------------------------------------------------- #
def make_gather_node(adapter: MarketDataAdapter, strategy: Strategy,
                     sentiment_adapter: SentimentAdapter | None = None):
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
            except (DataUnavailable, SentimentDataUnavailable) as exc:
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
            last_close = (prices.closes[-1]
                          if prices is not None and prices.closes else None)
            screen = run_dividend_aristocrat_screen(
                fundamentals,
                dividends or [],
                min_yield=c.min_dividend_yield,
                max_payout=c.max_payout_ratio,
                min_market_cap=c.min_market_cap,
                min_growth_years=c.min_dividend_growth_years,
                last_close=last_close,
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

        # Sentiment evidence is OPTIONAL by design: without an adapter the
        # Sentiment specialist finds nothing and abstains (pre-Finnhub
        # behaviour, preserved exactly). With one, two provider calls plus a
        # deterministic aggregation land in the ledger like everything else.
        if sentiment_adapter is not None:
            news_start = today - timedelta(days=14)

            def _fetch_news_capped():
                # High-coverage tickers (NVDA: 300+ items/fortnight) made the
                # evidence block — and therefore EVERY agent prompt — grow
                # unboundedly, blowing per-minute token limits (live-run
                # regression). Keep the most recent MAX_NEWS_LOGGED items;
                # record how many were fetched so nothing is hidden.
                items = sentiment_adapter.get_company_news(
                    state.ticker, start=news_start, end=today
                )
                items = sorted(items, key=lambda n: n.published, reverse=True)
                return items

            news_full = log(
                "get_company_news",
                {"ticker": state.ticker, "start": str(news_start),
                 "end": str(today), "provider": sentiment_adapter.name,
                 "logged_items_cap": MAX_NEWS_LOGGED},
                _fetch_news_capped,
            )
            news = news_full
            if news_full is not None and len(news_full) > MAX_NEWS_LOGGED:
                # Replace the logged output with the capped list (audit note
                # in inputs above), keep the full list for the snapshot count.
                state.tool_calls[-1].output = news_full[:MAX_NEWS_LOGGED]
                state.tool_calls[-1].inputs["total_items_fetched"] = len(news_full)
            trends = log(
                "get_recommendation_trends",
                {"ticker": state.ticker, "provider": sentiment_adapter.name},
                lambda: sentiment_adapter.get_recommendation_trends(
                    state.ticker
                ),
            )
            if news is not None or trends is not None:
                snap = sentiment_snapshot(news or [], trends or [])  # full list: count stays truthful
                state.tool_calls.append(
                    ToolCall(
                        call_id=_new_call_id(),
                        tool_name="sentiment_snapshot",
                        inputs={"ticker": state.ticker,
                                "news_window_days": 14},
                        output=asdict(snap),
                    )
                )
        return state

    return gather


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
MAX_NEWS_LOGGED = 60
MAX_TOOL_OUTPUT_CHARS = 12000  # per tool call, in prompts only — ledger keeps full output


def _evidence_block(state: ResearchState) -> str:
    """Serialize the ledger for prompts, with a per-call size guard.

    The ledger itself is never truncated (it is the audit record); only the
    prompt-facing serialization is. An oversized output is replaced by a
    truncated string plus an explicit marker, so agents know they are seeing
    a partial view and can say so in caveats.
    """
    lines = []
    for tc in state.tool_calls:
        payload = {"call_id": tc.call_id, "tool": tc.tool_name,
                   "ok": tc.ok, "error": tc.error, "output": tc.output}
        line = json.dumps(payload, default=str)
        if len(line) > MAX_TOOL_OUTPUT_CHARS:
            payload["output"] = (
                json.dumps(tc.output, default=str)[:MAX_TOOL_OUTPUT_CHARS]
                + f" ...[TRUNCATED FOR PROMPT — full output in ledger "
                  f"call_id={tc.call_id}]"
            )
            line = json.dumps(payload, default=str)
        lines.append(line)
    return "\n".join(lines)


def _validated_figures(
    state: ResearchState, source: str, refs: list[FigureRef]
) -> list[Figure]:
    """Resolve every cited figure against the tool-call ledger.

    An unresolvable reference (missing or unknown call_id) is a provenance
    violation: the figure is dropped, the violation is logged to state.errors,
    and the data-quality veto will fire. Applies identically to specialists
    and the Critic — nobody on the council gets to cite untraceable numbers.
    """
    figures: list[Figure] = []
    for f in refs:
        tc = state.tool_call_by_id(f.call_id) if f.call_id else None
        if tc is None:
            state.errors.append(
                f"provenance violation: {source} cited '{f.label}'={f.value} "
                f"with unknown call_id '{f.call_id or '<missing>'}'"
            )
            continue
        figures.append(
            Figure(label=f.label, value=f.value, unit=f.unit,
                   provenance=Provenance(tool_name=tc.tool_name,
                                         call_id=f.call_id,
                                         field_path=f.field_path))
        )
    return figures


_HARD_RULES = (
    "HARD RULES — these override everything else:\n"
    "1. NO ARITHMETIC. You may not add, multiply, divide, annualise, or "
    "otherwise compute. All math was done by deterministic tools; you reason "
    "about their outputs only.\n"
    "2. EVIDENCE ONLY. You may not introduce outside knowledge (figures, "
    "share counts, index membership, reputation) as if it were evidence. The "
    "evidence block in the user message is the complete record before this "
    "council.\n"
    "3. PROVENANCE. Every number you cite goes in `figures` with the exact "
    "call_id and field_path it came from, copied verbatim from the evidence. "
    "Numbers without a valid call_id are discarded and flagged as violations.\n"
    "4. CALIBRATION. Your confidence must reflect the completeness of the "
    "evidence, not the strength of your conviction.\n"
)


# --------------------------------------------------------------------------- #
# specialists
# --------------------------------------------------------------------------- #
_SPECIALIST_BRIEFS = {
    SpecialistName.FUNDAMENTAL:
        "You assess business quality and dividend durability: yield, payout "
        "sustainability, growth streak, market cap. Lean on the screen results.",
    SpecialistName.TECHNICAL:
        "You assess price structure: trend vs SMA50/SMA200, distance from the "
        "52-week high, volatility. Lean on the technical_snapshot output.",
    SpecialistName.SENTIMENT:
        "You assess news/market sentiment. Your evidence is the "
        "sentiment_snapshot tool output (recent headline list, news volume, "
        "analyst recommendation counts and bullish ratio) plus the raw "
        "get_company_news / get_recommendation_trends calls. Interpreting the "
        "TEXT of headlines is your job; counting is not — cite counts and "
        "ratios only from the snapshot. If NO sentiment tool output exists in "
        "the evidence, you MUST return stance=abstain with a caveat saying "
        "sentiment data is unavailable. Do not improvise sentiment from price "
        "action.",
    SpecialistName.RISK:
        "You assess downside: payout stretch, volatility, data-quality flags, "
        "anything unverifiable. You are the council's professional pessimist.",
}


def _specialist_system(who: SpecialistName, strategy: Strategy) -> str:
    return (
        f"You are the {who.value.upper()} specialist on an investment research "
        f"council operating under the strategy '{strategy.name}' "
        f"(id {strategy.id}).\n\n"
        f"Your brief: {_SPECIALIST_BRIEFS[who]}\n\n"
        f"{_HARD_RULES}\n"
        "5. ABSTAIN rather than guess when the evidence is insufficient for "
        "your domain.\n\n"
        f"Strategy rationale:\n{strategy.rationale}\n"
    )


def _user_message(state: ResearchState) -> str:
    return (
        f"Ticker under review: {state.ticker}\n\n"
        f"EVIDENCE (one JSON tool call per line — the complete record):\n"
        f"{_evidence_block(state)}\n"
    )


def make_specialist_node(who: SpecialistName, strategy: Strategy, runner):
    system = _specialist_system(who, strategy)

    def specialist(state: ResearchState) -> ResearchState:
        out: SpecialistOutput = runner.invoke(system, _user_message(state))
        state.specialist_opinions.append(
            SpecialistOpinion(
                specialist=who, stance=out.stance, confidence=out.confidence,
                thesis=out.thesis,
                figures=_validated_figures(state, who.value, out.figures),
                caveats=out.caveats,
            )
        )
        return state

    return specialist


# --------------------------------------------------------------------------- #
# critic — argues the OPPOSITE of the emerging consensus, provenance-bound
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


def _critic_system(strategy: Strategy) -> str:
    return (
        "You are the CRITIC on an investment research council operating under "
        f"the strategy '{strategy.name}' (id {strategy.id}). Your job is to "
        "argue the strongest case AGAINST the emerging consensus before any "
        "verdict — attack weak reasoning, mis-weighted figures, convenient "
        "assumptions, and missing evidence. You do not vote.\n\n"
        f"{_HARD_RULES}\n"
        "5. OPEN QUESTIONS. When your concern is quantitative but the evidence "
        "cannot support it — a computation you are not allowed to perform, a "
        "figure that looks stale, data that is absent — put it in "
        "`open_questions`, phrased as a question for human resolution (e.g. "
        "'Is the dividend covered by free cash flow once the share count is "
        "known?'). You may NOT state the suspected answer as a fact, estimate "
        "the missing number, or perform the computation yourself. A sharp "
        "unresolved question is more valuable to this council than a "
        "fabricated certainty.\n"
    )


def make_critic_node(strategy: Strategy, runner):
    system = _critic_system(strategy)

    def critic(state: ResearchState) -> ResearchState:
        target = consensus_stance(state)
        opinions = "\n".join(
            f"- {o.specialist.value}: {o.stance.value} "
            f"(conf {o.confidence:.2f}) — {o.thesis}"
            for o in state.specialist_opinions
        )
        user = (
            f"{_user_message(state)}\n"
            f"The emerging consensus is {target.value.upper()}. Argue the "
            f"opposite case.\n\nSpecialist opinions:\n{opinions}\n"
        )
        out: CriticOutput = runner.invoke(system, user)
        state.critic_report = CriticReport(
            targets_stance=target,
            counter_thesis=out.counter_thesis,
            weaknesses_found=out.weaknesses_found,
            challenged_figures=out.challenged_figures,
            figures=_validated_figures(state, "critic", out.figures),
            open_questions=out.open_questions,
        )
        return state

    return critic


# --------------------------------------------------------------------------- #
# decision — buy/hold/sell with confidence and dissent
# --------------------------------------------------------------------------- #
def _decision_system(strategy: Strategy) -> str:
    return (
        "You are the DECISION agent of an investment research council "
        f"operating under the strategy '{strategy.name}' (id {strategy.id}). "
        "Weigh the specialists AND the critic's counter-case, then issue "
        "buy/hold/sell with a confidence in [0,1].\n\n"
        f"{_HARD_RULES}\n"
        "5. DISSENT. List every specialist whose stance your call overrides in "
        "`dissent` — dissent must never be silently dropped.\n"
        "6. OPEN QUESTIONS ARE NOT EVIDENCE. The critic's open_questions are "
        "unresolved questions for a human, not established facts. They may "
        "justify caution (a HOLD pending resolution, lower confidence) but you "
        "must not cite them as if they were findings, and you must not treat a "
        "suspected answer as a known one.\n"
        f"7. POLICY. partial_pass_allows_hold="
        f"{strategy.policy.partial_pass_allows_hold}.\n"
    )


def make_decision_node(strategy: Strategy, runner):
    system = _decision_system(strategy)

    def decide(state: ResearchState) -> ResearchState:
        opinions = "\n".join(
            f"- {o.specialist.value}: {o.stance.value} "
            f"(conf {o.confidence:.2f}) — {o.thesis} "
            f"| caveats: {'; '.join(o.caveats) or 'none'}"
            for o in state.specialist_opinions
        )
        if state.critic_report:
            cr = state.critic_report
            critic = (
                f"{cr.counter_thesis}\n"
                f"Weaknesses: {'; '.join(cr.weaknesses_found) or 'none'}\n"
                f"OPEN QUESTIONS (unresolved, for human review — not facts):\n"
                + ("\n".join(f"  ? {q}" for q in cr.open_questions)
                   if cr.open_questions else "  none")
            )
        else:
            critic = "no critic report"
        user = (
            f"{_user_message(state)}\n"
            f"Specialists:\n{opinions}\n\nCritic:\n{critic}\n"
        )
        out: DecisionOutput = runner.invoke(system, user)
        state.decision = Decision(
            recommendation=out.recommendation,
            confidence=out.confidence,
            rationale=out.rationale,
            dissent=out.dissent,
        )
        return state

    return decide
