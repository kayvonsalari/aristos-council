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
from ..presentation import (
    _ACCOUNTS_CURRENCY_FIELDS,
    dividend_view,
    format_factor_value,
    recommendation_view,
)
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
                     *, sentiment_missing_key: bool = False,
                     sentiment_error: str = ""):
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
                #
                # FINNHUB-SKIP-1 is the exception: a symbol the PLAN cannot serve was
                # never requested, so nothing FAILED. It is DATA_ABSENT - the datum
                # genuinely does not exist for us - which by design does not degrade the
                # run. Tagging it FETCH_ERROR made a subscription boundary read as an
                # outage, which is what put "HTTP 403" in the dark-channel table.
                from ..data.finnhub_adapter import non_us_reason as _non_us_reason
                planned = _non_us_reason("").split(";")[0]
                kind = (FailureKind.DATA_ABSENT if planned and planned in str(exc)
                        else FailureKind.FETCH_ERROR)
                state.run_issues.append(RunIssue(
                    source=source, reason=kind, detail=str(exc)))
                return None

        # An optional source with no API key is a MISSING_KEY tool gap, not honest
        # absence: record it so the run is flagged degraded and the banner names it
        # (the FINNHUB_API_KEY-unset case that silently dragged verdicts bearish).
        # SENT-WIRE-1 — REPORT THE TRUE CAUSE. This said "FINNHUB_API_KEY not set" for
        # every abstention, and for months that was FALSE: the key was set and the
        # universe pipeline simply never constructed an adapter. A dark channel naming
        # the wrong cause is worse than one naming none — it sends the reader to fix
        # something that was never broken. The three cases are now distinguished, and
        # "wired but the call failed" carries the provider's own error (logged per call
        # below), never a key story.
        if sentiment_adapter is None and sentiment_missing_key:
            state.run_issues.append(RunIssue(
                source="sentiment", reason=FailureKind.MISSING_KEY,
                detail="no FINNHUB_API_KEY set — Sentiment specialist abstained"))
        elif sentiment_adapter is None and sentiment_error:
            # A key WAS present and the provider still could not be built. That is a
            # fixable tool failure and carries the provider's own message. A caller that
            # simply does not configure sentiment passes neither flag and is untouched —
            # a run with no sentiment by design is not a DEGRADED run.
            state.run_issues.append(RunIssue(
                source="sentiment", reason=FailureKind.FETCH_ERROR,
                detail=f"sentiment provider could not be constructed ({sentiment_error})"
                       " — Sentiment specialist abstained"))

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
        # NARR-STATIC-1 (broadened by NARR-LEDGER-1): surface this name's fee/size/yield
        # absolutes into the evidence ledger, each with its ACTUAL provenance receipt —
        # static-served (ETF-STATIC-1) or vendor-computed alike — so the narrator can AUDIT
        # the lens's defining numbers (an ETF's yield/fee) instead of reporting them "not
        # present anywhere in the ledger". The ranker computed these upstream (vendor-
        # plausible values win; abstained/stale-withheld fields are already OMITTED from the
        # packet), so this only PLUMBS them through — no recomputation, no phantom fill.
        # Empty for a single-ticker council, so its ledger stays byte-unchanged.
        if state.static_factor_evidence:
            state.tool_calls.append(
                ToolCall(
                    call_id=_new_call_id(),
                    tool_name=_STATIC_LAYER_LEDGER_TOOL,
                    inputs={"ticker": state.ticker},
                    output={
                        "factors": state.static_factor_evidence,
                        "note": ("factor absolute values (fee/size/yield), from whichever "
                                 "source actually served them; each carries its provenance "
                                 "receipt verbatim — [static: <as_of>, <source>] or "
                                 "[computed]. Cite value + provenance to audit the rank."),
                    },
                )
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
            # SCREEN-LESS strategies (no criteria) log NO screen tool at all
            # (NARR-FRAME-1): the evidence ledger then carries no screen block, so a
            # rank-first strategy's narrator is never handed a foreign lens's criteria.
            if strategy.criteria:
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

# NARR-STATIC-1: the ledger tool_name under which the rank pipeline's static-sourced
# factor values (ETF-STATIC-1) land in the narrator's evidence, each with its
# ``static: <as_of>, <source>`` provenance receipt.
_STATIC_LAYER_LEDGER_TOOL = "static_layer"


def _is_screen_tool(name: str) -> bool:
    """True for the screen's current ledger tool_name OR any historical one — the
    back-compat shim that keeps pre-rename saved reports loadable."""
    return name == _SCREEN_LEDGER_TOOL or name in _LEGACY_SCREEN_LEDGER_TOOLS

# Fundamentals fields always shown in the agent evidence, regardless of strategy
# (identity + universally-relevant context). The active strategy's criteria add
# their own consumed fields on top (see _scoped_fundamentals). last_close is not
# a Fundamentals field — it reaches agents via the get_price_history evidence.
# VERIFY-2 ITEM 3: the annual FCF SERIES is the citable core field, NOT the headline TTM
# free_cash_flow (which can embed one-off events — NVO's -12.04B Catalent charge — while
# every year of the annual series is positive). The screens already use the annual series;
# the headline is quarantined from narration in _scoped_fundamentals below.
_CORE_FUNDAMENTALS_FIELDS = (
    "ticker", "name", "market_cap", "pe_ratio", "free_cash_flow_annual", "eps",
)


def _scoped_fundamentals(output: object, allowed: set[str]) -> dict:
    """Render a Fundamentals object/dict as a dict of only the allowed fields.

    Display-only scoping (Sprint 4D): the full object stays in the ledger for the
    audit; agents see only the active strategy's relevant fields, so dividend
    fields don't frame growth runs. Order follows the dataclass declaration.
    """
    if isinstance(output, dict):
        d = {k: v for k, v in output.items() if k in allowed}
    else:
        names = [f.name for f in dataclass_fields(type(output))]
        d = {n: getattr(output, n) for n in names if n in allowed}
    # VERIFY-2 ITEM 3: QUARANTINE the headline TTM free_cash_flow. It can embed one-off
    # events (NVO: -12.04B, Catalent-era) sitting beside a positive free_cash_flow_annual
    # series — the narrator built a value-trap argument on it. Withhold the value so
    # narration cannot cite it, and point to the annual series (the sustainability basis
    # the screens use). Display-only: the ledger keeps the full object; screens unchanged.
    # FACTS-ORDER-1 — the annual series goes out with its YEARS attached and its order
    # stated, never as a bare list. A bare list is what the narrator had to guess the
    # order of, and it guessed wrong: it read a rising series as "a sustained decline"
    # and the risk specialist built its main risk on that. A series whose periods cannot
    # be labelled is withheld rather than sent out unlabelled.
    if "free_cash_flow_annual" in d:
        from ..series_pack import pack_series, packed_ok
        packed = pack_series(output, "free_cash_flow_annual",
                             label="Free cash flow, annual")
        d.pop("free_cash_flow_annual")
        d["free_cash_flow_annual"] = packed if packed_ok(packed) else {
            "label": packed["label"], "note": packed["note"]}
    if "free_cash_flow" in d:
        d.pop("free_cash_flow")
        # ABS-READINGS-3: the REASON, corrected. This scalar is no longer the provider's
        # TTM headline - since ABS-READINGS-2 it is the newest row of the cash-flow
        # statement - so "ttm_incl_one_offs" asserted something untrue. The quarantine
        # itself stands: a single year's figure can still carry a one-off (NVO's -12.04B
        # Catalent charge sat beside a positive annual series), and the SERIES is what a
        # sustainability claim should cite.
        d["free_cash_flow_note"] = ("single-period figure - do not use for "
                                    "sustainability claims; cite free_cash_flow_annual "
                                    "instead")
    # VERIFY-2 ITEM 4: withhold implausible vendor values from narration (NVO's 23.9%
    # dividend_yield). They still surface, flagged, in Company Check's data integrity.
    from ..data.adapter import implausible_fields
    flagged = implausible_fields(output)
    for name in flagged:
        d.pop(name, None)
    if flagged:
        d["flagged_fields_note"] = ("vendor value implausible — flagged and withheld: "
                                    + ", ".join(sorted(flagged)))
    return d


# NARR-2: the money fields whose formatted display needs the instrument currency
# (so a market cap reads "USD 149.8bn", never an unlabelled float).
#
# DATA-HYGIENE-1 removed fund_size / total_assets from this set: a fund's net assets are
# reported in the FUND'S BASE currency, which is NOT the listing currency this label comes
# from (IQQH's assets are USD while it lists on XETRA in EUR), and the ranker further
# normalises the served value to EUR. Labelling it with the listing currency would fabricate
# a unit, so the amount renders unlabelled and the field's provenance receipt states the
# currency, the FX rate and the rate's date instead (see fund_currency).
_CURRENCY_DISPLAY_FIELDS = ("market_cap", "enterprise_value")


def _ledger_currency(state: ResearchState) -> str | None:
    """The instrument's currency from the gathered fundamentals, for the narrator's
    money formatting (NARR-2). None when absent — never invented."""
    for tc in state.tool_calls:
        if tc.tool_name == "get_fundamentals" and tc.ok and tc.output is not None:
            out = tc.output
            return (out.get("currency") if isinstance(out, dict)
                    else getattr(out, "currency", None))
    return None


def _accounts_currency(state: ResearchState) -> str | None:
    """The currency the STATEMENTS are kept in — ``financial_currency``, falling back to
    the quote currency when the provider reports only one.

    For a domestic listing the two are the same and nothing changes. For an ADR they are
    not, and conflating them is how Novo's DKK operating income reached a report as
    "USD 128.3bn" (2026-09-01). Never invented: absent -> None -> the amount renders with
    no currency at all, which is honest, rather than with the wrong one."""
    for tc in state.tool_calls:
        if tc.tool_name == "get_fundamentals" and tc.ok and tc.output is not None:
            out = tc.output
            get = (out.get if isinstance(out, dict) else lambda k: getattr(out, k, None))
            return get("financial_currency") or get("currency")
    return None


def _display_map(output, currency: str | None,
                 accounts_currency: str | None = None) -> dict:
    """A ``{field: formatted_string}`` display map over the numeric fields of a tool
    output (NARR-2), skipping fields without a formatting convention. Presentation
    only — the raw numbers stay in the output for provenance/audit; the narrator is
    told to quote the display strings in prose."""
    fields = (output if isinstance(output, dict)
              else {f.name: getattr(output, f.name)
                    for f in dataclass_fields(type(output))})
    out: dict = {}
    for name, value in fields.items():
        # Quote-currency fields take the instrument's currency; statement-side money
        # takes the ACCOUNTS currency. Anything else formats without one.
        if name in _CURRENCY_DISPLAY_FIELDS:
            cur = currency
        elif name in _ACCOUNTS_CURRENCY_FIELDS:
            cur = accounts_currency
        else:
            cur = None
        disp = format_factor_value(name, value, currency=cur)
        if disp is not None:
            out[name] = disp
    return out


# --------------------------------------------------------------------------- #
# SENT-ISOLATE-1 — a specialist may only see ITS OWN evidence channel
# --------------------------------------------------------------------------- #
# LIVE, 2026-08-26: with no news data at all, the Sentiment specialist returned
# "bullish 0.72" for ASML, reasoning from "Value + Momentum's rank-sum of 12 and Magic
# Formula RAW's rank-sum of 19". On NVO, MSFT and GOOGL the same specialist in the same
# run abstained correctly. That is the worst shape this failure can take: the panel reads
# as several independent views, so a sentiment specialist reporting the RANKER's verdicts
# back as sentiment manufactures corroboration — the reader sees three signals agreeing
# where there are two, one of them counted twice.
#
# A prompt instruction cannot fix it (the model already had one, and complied on three
# names out of four). The fix is structural: if the specialist cannot SEE the other
# channels, it cannot cite them.
#
# Only SENTIMENT is strictly scoped today. Fundamental, technical and risk are
# deliberately left on the full ledger for now: risk is cross-cutting BY DESIGN (it reads
# volatility against fundamentals), and narrowing the other two would change narration
# nobody has reported a problem with. Declared here rather than assumed, so widening the
# map later is a one-line change with a test to match.
SPECIALIST_CHANNELS: dict[str, tuple[str, ...]] = {
    "sentiment": ("get_company_news", "get_recommendation_trends", "sentiment_snapshot"),
}


def channel_tools(who) -> tuple[str, ...]:
    """The tool names this specialist may see, or ``()`` when it sees the whole ledger."""
    return SPECIALIST_CHANNELS.get(getattr(who, "value", who), ())


def _channel_absent_reason(state: ResearchState, who) -> str:
    """WHY this channel is dark, in the specialist's own abstention.

    Read from the run's own record rather than assumed, so the four cases stay
    distinguishable: no key, a provider that could not be built, a call that FAILED (the
    provider's own error, status code and all), and a call that succeeded but returned
    nothing — which is a real finding about the name, not a fault."""
    tools = channel_tools(who)
    failed = [tc for tc in state.tool_calls if tc.tool_name in tools and not tc.ok]
    if failed:
        return "; ".join(sorted({tc.error or "provider call failed" for tc in failed}))
    issues = [i for i in state.run_issues if i.source == getattr(who, "value", who)]
    if issues:
        return issues[0].detail
    if any(tc.tool_name in tools for tc in state.tool_calls):
        return "the provider returned nothing in the window"
    return "the provider was not available for this run"


def channel_is_empty(state: ResearchState, who) -> bool:
    """True when a strictly-scoped specialist's OWN channel produced nothing.

    ``()`` (unscoped) is never "empty" — an unscoped specialist has no single channel to
    be empty. A scoped one with no successful call in its channel MUST abstain, and does
    so without an LLM invocation, which is what makes "bullish 0.72 on an empty channel"
    structurally impossible rather than merely discouraged."""
    tools = channel_tools(who)
    if not tools:
        return False
    return not any(tc.tool_name in tools and tc.ok and tc.output is not None
                   for tc in state.tool_calls)


def _evidence_block(state: ResearchState, strategy: Strategy,
                    *, narrator: bool = False,
                    only_tools: tuple[str, ...] = ()) -> str:
    """Serialize the ledger for prompts, with a per-call size guard.

    The ledger itself is never truncated (it is the audit record); only the
    prompt-facing serialization is. An oversized output is replaced by a
    truncated string plus an explicit marker, so agents know they are seeing
    a partial view and can say so in caveats.

    Strategy-scoped (Sprint 4D): the screen shows a neutral tool label, and the
    fundamentals are scoped to the active strategy's consumed fields plus a core.

    NARR-2: in NARRATOR mode each numeric tool output carries a ``display`` map of
    human-formatted strings (percent/price/currency) so the writer quotes
    "22.0%" / "USD 149.8bn", not a raw float. Display-only: the raw values remain
    for the provenance audit; non-narrator councils are byte-identical (default off).

    SENT-ISOLATE-1: ``only_tools`` restricts the packet to ONE channel. Empty (the
    default) is the whole ledger, so every existing caller is byte-unchanged.
    """
    allowed_fundamentals = (
        set(_CORE_FUNDAMENTALS_FIELDS)
        | consumed_fundamentals_fields(strategy.criteria)
    )
    currency = _ledger_currency(state) if narrator else None
    accounts_ccy = _accounts_currency(state) if narrator else None
    lines = []
    for tc in state.tool_calls:
        if only_tools and tc.tool_name not in only_tools:
            continue
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
        # NARR-2: in narrator mode attach human-formatted display strings so the
        # writer quotes "22.0%" / "USD 149.8bn", not a raw float. The raw numbers
        # stay untouched (a fresh dict is built — the ledger is never mutated) so
        # the provenance audit still resolves against them.
        if narrator and tc.ok and isinstance(output, dict):
            if (tc.tool_name == _STATIC_LAYER_LEDGER_TOOL
                    and isinstance(output.get("factors"), list)):
                factors = []
                for e in output["factors"]:
                    fname = e.get("factor", "")
                    cur = (currency if fname in _CURRENCY_DISPLAY_FIELDS else None)
                    disp = format_factor_value(fname, e.get("value"), currency=cur)
                    factors.append({**e, "display": disp} if disp is not None
                                   else dict(e))
                output = {**output, "factors": factors}
            else:
                disp = _display_map(output, currency, accounts_ccy)
                if disp:
                    output = {**output, "display": disp}
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
    # Rank-semantics legend (ITEM 4): the narrator kept inverting ordinals (rank 2 called
    # "second-worst"). State the convention explicitly and confine ordinal claims to the
    # rank table. Only when the cohort size is known (a ranker run).
    legend = ""
    if state.ranker_cohort_size is not None:
        legend = (f"\nRank semantics: rank 1 = best on every factor; lower combined "
                  f"rank-sum = better. N = {state.ranker_cohort_size}. Ordinal claims "
                  f"(best, worst, second-, top-, bottom-) must be derived only from the "
                  f"rank table.")
    return (f"\nRANKER VERDICT (the deterministic verdict-of-record for this name): "
            f"{state.ranker_verdict.value.upper()}{expl}{legend}"
            f"{_boundary_tie_block(state)}{_agreement_block(state) + _cross_lens_block(state)}\n")


def _cross_lens_block(state: ResearchState) -> str:
    """EVERY selected lens's verdict for this name (NARR-UNION-1), plus the deterministic
    reasons behind each BUY.

    A multi-lens run narrates a name ONCE, so the writer is handed the WHOLE verdict row —
    the lenses that bought it and the lenses that did not, in the run's own column order.
    Without this the prose could present a name bought by one lens as simply "a BUY" while
    another lens excluded it outright: a one-sided case assembled from a true fact.

    The block is FACTS ONLY, and the accompanying constraint (see prompts.decision_system)
    forbids weighing the lenses against one another. Empty on a single-lens run, so that
    prompt is byte-unchanged."""
    rows = state.cross_lens_verdicts or []
    if not rows:
        return ""
    lines = [f"  - {r.get('lens', '')}: {r.get('cell', '')}" for r in rows]
    reasons = state.cross_lens_reasons or []
    why = ""
    if reasons:
        why = ("\nWHY EACH BUYING LENS RANKED IT THERE (that lens's own factor ranks and "
               "the screen rules it passed — attribute each to the lens it came from):\n"
               + "\n".join(
                   f"  - {r.get('lens', '')}: {r.get('explain', '')}"
                   + (f"\n      passed: {'; '.join(r.get('rules') or [])}"
                      if r.get("rules") else "")
                   for r in reasons))
    return ("\nEVERY SELECTED LENS'S VERDICT FOR THIS NAME (state ALL of these before any "
            "prose — the lenses that did NOT buy it are part of the record):\n"
            + "\n".join(lines) + why)



def _agreement_block(state) -> str:
    """NARR-CONTEXT-1 — what the RUN concluded about this name, stated FIRST.

    The cross-lens block gives every lens's verdict; this gives the reading the report
    puts on them: how many voting lenses bought it, which, what each check found in its
    own words, and every mark against it. Those are the two facts a reader of the
    shortlist arrives with, so a narration that does not open on them is answering a
    question nobody asked.

    The marks are the load-bearing part. A name is on the list carrying "doubted by
    Forensic" or "priced high: 99th percentile of its own 5-year range", and the prose has
    to meet that — ``narration_check`` refuses a narration that leaves a mark unaddressed.
    Only the mark TEXT is here: the band's reversion arithmetic never reaches a model,
    because an implied price is quoted back as a target however it is labelled."""
    row = getattr(state, "agreement_row", None) or {}
    if not row:
        return ""
    votes = row.get("buy_votes", 0)
    n = row.get("n_voting", 0)
    who = ", ".join(row.get("buy_lenses") or [])
    lines = [f"  - BUY votes: {votes} of {n}" + (f" ({who})" if who else "")]
    if row.get("sell_lenses"):
        lines.append(f"  - SELL votes: {len(row['sell_lenses'])} of {n} "
                     f"({', '.join(row['sell_lenses'])})")
    for check in row.get("checks") or []:
        lines.append(f"  - {check.get('lens', '')}: {check.get('reading', '')}")
    marks = row.get("marks") or []
    for mark in marks:
        lines.append(f"  - MARK: {mark}")
    tail = ""
    if marks:
        tail = ("\nADDRESS EVERY MARK ABOVE, explicitly and by name, using the figures "
                "already in this pack — say WHY the check doubts it (the accrual ratio, "
                "the distress score, the accounting checks) or what the price mark rests "
                "on. A mark left unmentioned is a narration that answers a different "
                "question from the one the reader has, and it is refused.")
    return ("\nWHAT THE RUN CONCLUDED ABOUT THIS NAME (open with this — it is why the "
            "name is on the list, and what sits beside it):\n" + "\n".join(lines) + tail)


def _boundary_tie_block(state: ResearchState) -> str:
    """The BOUNDARY TIE evidence line (VERDICT-TIE-1) — present only when this name's
    verdict split from a name it is TIED with on the alphabetical tie-break. States the
    shared score, the partner(s) and their verdict, and forbids ordering the pair: a tie is
    not a ranking, so "decisively ranked below <partner>" is false however the verdicts
    read. Empty (byte-unchanged prompt) for every other name."""
    fact = state.ranker_boundary_tie or {}
    partners = fact.get("partners") or []
    if not partners:
        return ""
    who = ", ".join(f"{p.get('ticker')} ({str(p.get('verdict', '')).upper()})"
                    for p in partners)
    return (f"\nBOUNDARY TIE (a fact of the rank table — state it, do not smooth it over): "
            f"this name's combined rank-sum {fact.get('score_display')} is TIED with "
            f"{who}, so the verdict boundary fell BETWEEN equally-scored names and the "
            f"deterministic alphabetical tie-break — not a score difference — decided which "
            f"side each received. The verdicts stand as issued. Do NOT describe this name as "
            f"ranked above, below, ahead of or behind a name it is tied with; say the scores "
            f"are equal and the verdict split on the tie-break.")


def _user_message(state: ResearchState, strategy: Strategy,
                  *, narrator: bool = False, who=None) -> str:
    # NARR-2: in narrator mode the evidence carries human-formatted `display`
    # strings; the note points the writer at them so prose never quotes raw floats.
    display_note = (
        "\nIn PROSE, quote the `display` string for any number (already formatted "
        "with units/currency, e.g. \"22.0%\", \"USD 149.8bn\") — never a raw ratio "
        "or an unlabelled large number.\n" if narrator else "")
    # SENT-ISOLATE-1: a channel-scoped specialist sees ITS OWN evidence and NOT the
    # ranker's verdicts — the rank-sums it was caught reciting as "sentiment" are simply
    # not in the packet. Unscoped specialists (and the critic/narrator) are unchanged.
    only = channel_tools(who) if who is not None else ()
    ranker = "" if only else f"{_ranker_block(state)}\n"
    scope_note = ("\nThis packet contains YOUR channel's evidence only. If it is empty, "
                  "abstain — do not substitute another channel's inputs.\n"
                  if only else "")
    return (
        f"Ticker under review: {state.ticker}\n"
        f"{ranker}"
        f"EVIDENCE (one JSON tool call per line — the complete record):\n"
        f"{_evidence_block(state, strategy, narrator=narrator, only_tools=only)}\n"
        f"{scope_note}{display_note}"
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
        # SENT-ISOLATE-1 — an EMPTY channel abstains WITHOUT an LLM call. Not a prompt
        # rule the model may or may not follow (it followed it on three names out of four
        # and returned "bullish 0.72" from rank-sums on the fourth): with no call there is
        # no stance to invent, and the behaviour is identical for every name in the run.
        if channel_is_empty(state, who):
            reason = _channel_absent_reason(state, who)
            state.specialist_opinions.append(SpecialistOpinion(
                specialist=who, stance=Stance.ABSTAIN, confidence=0.0,
                thesis=f"No {who.value} evidence was available for this name: {reason}. "
                       "This is a missing data channel, not a measured neutral — no "
                       "stance is offered and no other channel's inputs are substituted.",
                caveats=[f"{who.value} channel empty — abstained"],
                agrees_with_ranker=None))
            return state

        user_msg = _user_message(state, strategy, narrator=narrator, who=who)
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


def make_critic_node(strategy: Strategy, runner,
                     council_mode: str = "second_opinion"):
    system = _critic_system(strategy)
    narrator = council_mode == "narrator"

    def critic(state: ResearchState) -> ResearchState:
        target = consensus_stance(state)
        opinions = "\n".join(
            f"- {o.specialist.value}: {o.stance.value} "
            f"(conf {o.confidence:.2f}) — {o.thesis}"
            for o in state.specialist_opinions
        )
        user = (
            f"{_user_message(state, strategy, narrator=narrator)}\n"
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
            f"{_user_message(state, strategy, narrator=narrator)}\n"
            f"Specialists:\n{opinions}\n\nCritic:\n{critic}\n"
        )
        out: DecisionOutput = runner.invoke(system, user)

        # REPORT-4: when the narrator returned FIELDS, `rationale` becomes the flattened
        # PROSE of those fields. Everything downstream that reads prose — the whole
        # fact-checking layer, the provenance audit, saved-report rendering — keeps
        # working unchanged, and the layout is rebuilt from the fields at render time.
        # A model that filled `rationale` and left `narration` empty (second-opinion mode,
        # any older record) is untouched.
        if out.narration is not None:
            from ..narration_render import narration_prose

            prose = narration_prose(out.narration)
            if prose:
                out.rationale = prose

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
            # REPORT-4: carry the STRUCTURED narration onto the state. Without this the
            # fields reached the report only as the flattened prose in `rationale`, and
            # the live 21:47 run rendered every narration as flat paragraphs while the
            # "what the run could not see" section reported a clean bill of health beside
            # two specialists that had plainly abstained.
            narration=out.narration,
        )
        return state

    return decide
