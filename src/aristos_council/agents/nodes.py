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
from dataclasses import asdict, fields as dataclass_fields
from datetime import date, timedelta

from ..data.adapter import DataUnavailable, MarketDataAdapter
from ..data.sentiment import SentimentAdapter, SentimentDataUnavailable
from ..presentation import dividend_view, recommendation_view
from ..state import (
    CriticReport,
    Decision,
    FailureKind,
    Figure,
    Provenance,
    Recommendation,
    ResearchState,
    RunIssue,
    SpecialistName,
    SpecialistOpinion,
    Stance,
    ToolCall,
)
from ..strategy.loader import Strategy
from ..tools.criteria.registry import (
    Evidence,
    consumed_fundamentals_fields,
    required_evidence,
    run_screen,
)
from ..tools.sentiment_tools import sentiment_snapshot
from ..tools.technical import _TD_6M, _TD_12M, technical_snapshot, total_return
from .disposition import (
    disposition_ceiling,
    exceeds_ceiling,
    failed_gating_criteria,
    insufficient_evidence,
    not_evaluated_gating_criteria,
)
from .prompts import (
    PROMPT_VERSION,
    critic_system,
    decision_system,
    specialist_system,
)
from .prompts import HARD_RULES as _HARD_RULES
from .prompts import SPECIALIST_BRIEFS as _SPECIALIST_BRIEFS
from .schemas import CriticOutput, DecisionOutput, FigureRef, SpecialistOutput

# Back-compat aliases — the SYSTEM prompts moved to agents/prompts.py (externalized
# + versioned). These keep existing call sites and prompt-wording tests importing
# from nodes unchanged; the canonical definitions now live in prompts.py.
_specialist_system = specialist_system
_critic_system = critic_system
_decision_system = decision_system


def _new_call_id() -> str:
    return uuid.uuid4().hex[:12]


def _screen_criteria(state: ResearchState) -> list:
    """The screen's per-criterion results (name + three-valued passed) from the
    ledger, for the deterministic disposition gate. Empty if no screen ran."""
    for tc in state.tool_calls:
        if _is_screen_tool(tc.tool_name) and tc.output:
            out = tc.output
            crits = out.get("criteria") if isinstance(out, dict) else None
            return crits or []
    return []


# --------------------------------------------------------------------------- #
# gather — deterministic evidence collection
# --------------------------------------------------------------------------- #
def make_gather_node(adapter: MarketDataAdapter, strategy: Strategy,
                     sentiment_adapter: SentimentAdapter | None = None,
                     *, sentiment_missing_key: bool = False):
    def gather(state: ResearchState) -> ResearchState:
        today = date.today()
        lookback_start = today - timedelta(days=400)   # enough for SMA200
        div_start = today - timedelta(days=365 * 40)   # as deep as provider allows

        def log(tool_name: str, inputs: dict, fn, *, source: str):
            # `source` is the human-facing label for the run-health channel
            # ('fundamentals', 'sentiment', ...); `tool_name` stays the ledger id.
            call_id = _new_call_id()
            try:
                output = fn()
                state.tool_calls.append(
                    ToolCall(call_id=call_id, tool_name=tool_name,
                             inputs=inputs, output=output, ok=True)
                )
                # A call that SUCCEEDED but returned no usable rows is an
                # EMPTY_RESPONSE — a fixable tool failure, distinct from a fetch
                # that raised. (Objects without a length, e.g. Fundamentals, are
                # never "empty" by this rule.)
                if hasattr(output, "__len__") and len(output) == 0:
                    state.run_issues.append(RunIssue(
                        source=source, reason=FailureKind.EMPTY_RESPONSE,
                        detail=f"{tool_name} returned no rows"))
                return output
            except (DataUnavailable, SentimentDataUnavailable) as exc:
                state.tool_calls.append(
                    ToolCall(call_id=call_id, tool_name=tool_name,
                             inputs=inputs, output=None, ok=False,
                             error=str(exc))
                )
                state.errors.append(f"{tool_name}: {exc}")
                # The adapter RAISED — an actual fetch/API failure, not honest
                # absence. Tag it FETCH_ERROR so the run is marked degraded.
                state.run_issues.append(RunIssue(
                    source=source, reason=FailureKind.FETCH_ERROR,
                    detail=str(exc)))
                return None

        # An optional source with no API key is a MISSING_KEY tool gap, not honest
        # absence: record it so the run is flagged degraded and the banner names it
        # (the FINNHUB_API_KEY-unset case that silently dragged verdicts bearish).
        if sentiment_adapter is None and sentiment_missing_key:
            state.run_issues.append(RunIssue(
                source="sentiment", reason=FailureKind.MISSING_KEY,
                detail="FINNHUB_API_KEY not set — Sentiment specialist abstained"))

        # `provider` tags each market-data call with the adapter that actually
        # produced it. For a single-source adapter that's its own name; for the
        # HybridAdapter it's the real per-kind source (eodhd dividends, yfinance
        # fundamentals/prices) — so mixed provenance stays visible in the ledger.
        fundamentals = log(
            "get_fundamentals",
            {"ticker": state.ticker,
             "provider": adapter.provider_for("fundamentals")},
            lambda: adapter.get_fundamentals(state.ticker),
            source="fundamentals",
        )
        # Strategy-scoped tool selection (Sprint 4E): only fetch dividend history
        # when the active strategy actually needs it. A growth run never sees
        # dividend events, so agents can't weave dividend narratives / cite
        # dividend figures on a non-dividend stock (live leak, MSFT growth run).
        dividends = None
        if "dividends" in required_evidence(strategy.criteria):
            dividends = log(
                "get_dividend_history",
                {"ticker": state.ticker, "start": str(div_start),
                 "end": str(today),
                 "provider": adapter.provider_for("dividends")},
                lambda: adapter.get_dividend_history(
                    state.ticker, start=div_start, end=today
                ),
                source="dividends",
            )
        prices = log(
            "get_price_history",
            {"ticker": state.ticker, "start": str(lookback_start),
             "end": str(today), "provider": adapter.provider_for("prices")},
            lambda: adapter.get_price_history(
                state.ticker, start=lookback_start, end=today
            ),
            source="prices",
        )

        if fundamentals is not None:
            closes = (prices.closes
                      if prices is not None and prices.closes else [])
            last_close = closes[-1] if closes else None
            # Trailing price momentum from the closes ALREADY fetched (no new call),
            # using the SAME lookbacks the technical snapshot reports.
            return_6m = total_return(closes, _TD_6M)
            return_12m = total_return(closes, _TD_12M)
            # Generic, registry-driven screen: the strategy's selected criteria
            # run against the gathered evidence. Logged under the historical
            # tool_name so the ledger/audit/reports are unchanged.
            screen = run_screen(
                strategy.criteria,
                Evidence(fundamentals=fundamentals,
                         dividends=dividends or [],
                         last_close=last_close,
                         # Provider declares its streak data shape (Option A); the
                         # tag rides with the dividends so the criterion picks the
                         # matching method via screening.streak_by_method.
                         streak_method=adapter.dividend_streak_method,
                         return_6m=return_6m, return_12m=return_12m),
                ticker=state.ticker,
            )
            state.tool_calls.append(
                ToolCall(
                    call_id=_new_call_id(),
                    tool_name=_SCREEN_LEDGER_TOOL,
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
                source="sentiment",
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
                source="sentiment",
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

# The screen's STORED ledger tool_name. Renamed to a STRATEGY-NEUTRAL name so it no
# longer stamps "dividend" on growth-run provenance lines (and to cover dividend +
# growth + future strategies). The agent-facing label is the even shorter
# `run_screen` (display ≠ identity, the 4D fix). Old saved reports carry the LEGACY
# name; consumers match via `_is_screen_tool`, so those reports still load — no
# migration, no number change (the screen OUTPUT is byte-identical).
_SCREEN_LEDGER_TOOL = "run_strategy_screen"
_LEGACY_SCREEN_LEDGER_TOOLS = {"run_dividend_aristocrat_screen"}
_SCREEN_DISPLAY_TOOL = "run_screen"


def _is_screen_tool(name: str) -> bool:
    """True for the screen's current ledger tool_name OR any historical one — the
    back-compat shim that keeps pre-rename saved reports loadable."""
    return name == _SCREEN_LEDGER_TOOL or name in _LEGACY_SCREEN_LEDGER_TOOLS

# Fundamentals fields always shown in the agent evidence, regardless of strategy
# (identity + universally-relevant context). The active strategy's criteria add
# their own consumed fields on top (see _scoped_fundamentals). last_close is not
# a Fundamentals field — it reaches agents via the get_price_history evidence.
_CORE_FUNDAMENTALS_FIELDS = (
    "ticker", "name", "market_cap", "pe_ratio", "free_cash_flow", "eps",
)


def _scoped_fundamentals(output: object, allowed: set[str]) -> dict:
    """Render a Fundamentals object/dict as a dict of only the allowed fields.

    Display-only scoping (Sprint 4D): the full object stays in the ledger for the
    audit; agents see only the active strategy's relevant fields, so dividend
    fields don't frame growth runs. Order follows the dataclass declaration.
    """
    if isinstance(output, dict):
        return {k: v for k, v in output.items() if k in allowed}
    names = [f.name for f in dataclass_fields(type(output))]
    return {n: getattr(output, n) for n in names if n in allowed}


def _evidence_block(state: ResearchState, strategy: Strategy) -> str:
    """Serialize the ledger for prompts, with a per-call size guard.

    The ledger itself is never truncated (it is the audit record); only the
    prompt-facing serialization is. An oversized output is replaced by a
    truncated string plus an explicit marker, so agents know they are seeing
    a partial view and can say so in caveats.

    Strategy-scoped (Sprint 4D): the screen shows a neutral tool label, and the
    fundamentals are scoped to the active strategy's consumed fields plus a core.
    """
    allowed_fundamentals = (
        set(_CORE_FUNDAMENTALS_FIELDS)
        | consumed_fundamentals_fields(strategy.criteria)
    )
    lines = []
    for tc in state.tool_calls:
        output = tc.output
        # Fundamentals: render only the fields the active strategy cares about.
        if tc.tool_name == "get_fundamentals" and tc.ok and output is not None:
            output = _scoped_fundamentals(output, allowed_fundamentals)
        # Raw price bars never go into prompts: a ~270-bar series exceeds the
        # size guard and front-truncation showed agents only the OLDEST slice
        # (live-run regression, T: agents saw May–Aug 2025 bars, concluded the
        # current snapshot price was "inconsistent"). The technical_snapshot is
        # the curated view; the prompt gets a compact, current summary instead.
        if tc.tool_name == "get_price_history" and tc.ok and output is not None:
            bars = getattr(output, "bars", None) or []
            output = {
                "n_bars": len(bars),
                "first_day": str(bars[0].day) if bars else None,
                "last_day": str(bars[-1].day) if bars else None,
                "last_adj_close": bars[-1].adj_close if bars else None,
                "note": "raw bars omitted from prompt (full series in ledger); "
                        "use technical_snapshot for derived price metrics",
            }
        # Dividend history as NAMED handles instead of a bare list to index — the
        # audit resolves these same paths via prompt-view aliases (no drift).
        if tc.tool_name == "get_dividend_history" and tc.ok and output is not None:
            output = dividend_view(output)
            output["note"] = ("raw event list omitted from prompt (full series in "
                              "ledger); cite latest.amount / earliest.amount / "
                              "by_year.<year> / n_events — do NOT index a raw list")
        # Recommendation trends: expose latest_period (with the per-category counts
        # AND total) so the aggregate is a citable field, not something summed.
        if tc.tool_name == "get_recommendation_trends" and tc.ok and output is not None:
            output = recommendation_view(output)
            output["note"] = ("cite latest_period.total / latest_period.<category> "
                              "(strong_buy/buy/hold/sell/strong_sell); the bullish "
                              "ratio lives in sentiment_snapshot, not here")
        # Agent-facing tool label is strategy-neutral; the stored tc.tool_name
        # (used by the audit) is untouched. _is_screen_tool so re-rendered OLD
        # reports (legacy ledger name) also get the neutral display label.
        display_tool = (_SCREEN_DISPLAY_TOOL
                        if _is_screen_tool(tc.tool_name) else tc.tool_name)
        payload = {"call_id": tc.call_id, "tool": display_tool,
                   "ok": tc.ok, "error": tc.error, "output": output}
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


# --------------------------------------------------------------------------- #
# specialists
# --------------------------------------------------------------------------- #
def _ranker_block(state: ResearchState) -> str:
    """The RANKER's verdict-of-record, surfaced to the agents so they can analyse and
    challenge it. Empty for a standalone council run (no ranker)."""
    if state.ranker_verdict is None:
        return ""
    expl = f" — {state.ranker_explanation}" if state.ranker_explanation else ""
    return (f"\nRANKER VERDICT (the deterministic verdict-of-record for this name): "
            f"{state.ranker_verdict.value.upper()}{expl}\n")


def _user_message(state: ResearchState, strategy: Strategy) -> str:
    return (
        f"Ticker under review: {state.ticker}\n"
        f"{_ranker_block(state)}\n"
        f"EVIDENCE (one JSON tool call per line — the complete record):\n"
        f"{_evidence_block(state, strategy)}\n"
    )


def make_specialist_node(who: SpecialistName, strategy: Strategy, runner,
                         council_mode: str = "second_opinion"):
    system = specialist_system(who, strategy, council_mode)
    narrator = council_mode == "narrator"

    def specialist(state: ResearchState) -> ResearchState:
        # RESILIENCE: a single malformed structured output must NEVER kill the whole
        # run (all prior councils' spend wasted). Retry ONCE, then degrade THIS
        # specialist to ABSTAIN with a typed run issue — abstention exists for exactly
        # this. Scoped to the LLM parse; opinion construction stays outside.
        user_msg = _user_message(state, strategy)
        try:
            out: SpecialistOutput = runner.invoke(system, user_msg)
        except Exception:                                   # e.g. pydantic ValidationError
            try:
                out = runner.invoke(system, user_msg)       # one retry
            except Exception as exc:
                state.run_issues.append(RunIssue(
                    source=who.value, reason=FailureKind.FETCH_ERROR,
                    detail=f"{who.value} specialist output invalid after one "
                           f"retry ({exc}); abstained"))
                state.specialist_opinions.append(SpecialistOpinion(
                    specialist=who, stance=Stance.ABSTAIN, confidence=0.0,
                    thesis="Specialist output could not be parsed (invalid "
                           "structured output after one retry); abstaining.",
                    caveats=["malformed model output — abstained (run continues)"],
                    agrees_with_ranker=None))
                return state
        # ABSTENTION RULE: a data-less specialist (ABSTAIN — e.g. Sentiment with no
        # Finnhub data, already tagged degraded) must NOT silently "agree" and inflate
        # apparent consensus. Force agrees_with_ranker to None on abstention, whatever
        # the model returned; the agreement summary then counts only non-abstainers.
        # NARRATOR mode has no second verdict -> the field is not emitted at all.
        agrees = (None if narrator or out.stance == Stance.ABSTAIN
                  else out.agrees_with_ranker)
        state.specialist_opinions.append(
            SpecialistOpinion(
                specialist=who, stance=out.stance, confidence=out.confidence,
                thesis=out.thesis,
                figures=_validated_figures(state, who.value, out.figures),
                caveats=out.caveats,
                agrees_with_ranker=agrees,
                dissent_note=("" if narrator or out.stance == Stance.ABSTAIN
                              else out.dissent_note),
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
            f"{_user_message(state, strategy)}\n"
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
def make_decision_node(strategy: Strategy, runner,
                       council_mode: str = "second_opinion"):
    # A/B toggle (flag, never a rewrite): "second_opinion" (B, default) — the agent
    # issues its OWN verdict, compared to the ranker; "narrator" (A) — it only
    # explains the ranker's verdict and emits no independent call.
    system = decision_system(strategy, council_mode)
    narrator = council_mode == "narrator"

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
            f"{_user_message(state, strategy)}\n"
            f"Specialists:\n{opinions}\n\nCritic:\n{critic}\n"
        )
        out: DecisionOutput = runner.invoke(system, user)

        # NARRATOR (Option A): the council does NOT issue an independent verdict — it
        # echoes the RANKER's verdict-of-record. SECOND_OPINION (Option B, default):
        # the agent's OWN verdict stands as the independent check.
        base_rec = out.recommendation
        if narrator and state.ranker_verdict is not None:
            base_rec = state.ranker_verdict

        # DETERMINISTIC disposition ceiling — authoritative over the LLM. A
        # confirmed fail of a gating criterion caps the verdict at SELL no matter
        # what the agent decided (partial_pass_allows_hold is only a soft hint and
        # proved evadable; this is the enforcement). Default-off: a strategy with
        # no is_gating criteria behaves exactly as before.
        final_rec = base_rec
        gate_applied = False
        fired_name = None
        insufficient = False
        gating = {c.name for c in strategy.criteria if getattr(c, "is_gating", False)}
        if gating:
            screen = _screen_criteria(state)
            ceiling = disposition_ceiling(screen, gating)
            if ceiling is not None:
                # A CONFIRMED gating fail exists -> SELL territory. This takes
                # PRECEDENCE over a co-occurring NOT-EVAL: a real SELL beats
                # "can't tell". Only cap when the verdict is more bullish.
                if exceeds_ceiling(base_rec, ceiling):
                    final_rec = ceiling
                    gate_applied = True
                    failed = failed_gating_criteria(screen, gating)
                    fired_name = failed[0] if failed else None
            elif insufficient_evidence(screen, gating):
                # No confirmed fail, but a GATING criterion is NOT-EVAL (passed is
                # None): the screen could not decide, so the verdict is short-
                # circuited OFF the buy/hold/sell ladder to INSUFFICIENT_EVIDENCE.
                # The veto gate then forces human review unconditionally.
                final_rec = Recommendation.INSUFFICIENT_EVIDENCE
                gate_applied = True
                insufficient = True
                not_eval = not_evaluated_gating_criteria(screen, gating)
                fired_name = not_eval[0] if not_eval else None

        state.decision = Decision(
            recommendation=final_rec,
            confidence=out.confidence,
            rationale=out.rationale,
            dissent=out.dissent,
            original_recommendation=base_rec,
            gate_override_applied=gate_applied,
            gating_criterion_fired=fired_name,
            insufficient_evidence=insufficient,
            narration_only=narrator,
        )
        return state

    return decide
