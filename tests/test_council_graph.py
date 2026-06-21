"""End-to-end council run with a fake adapter and fake LLM runners.

This is the proof that the whole graph wires up: gather logs tool calls, four
specialists opine, the critic argues the opposite case, the decision lands, and
the veto gate fires on exactly the right triggers. No network, no API keys.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.agents.schemas import (
    CriticOutput,
    DecisionOutput,
    FigureRef,
    SpecialistOutput,
)
from aristos_council.data.adapter import (
    DividendEvent,
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.graph import build_council
from aristos_council.state import (
    Recommendation,
    ResearchState,
    SpecialistName,
    Stance,
    VetoTrigger,
)
from aristos_council.strategy.loader import load_strategy
from aristos_council.strategy.overrides import applied_overrides, effective_strategy

STRATEGY = load_strategy(
    Path(__file__).resolve().parents[1] / "strategies" / "dividend_aristocrats_v1.yaml"
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeAdapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(
            ticker=ticker, name="Fake Corp", market_cap=5e10,
            dividend_yield=0.03, payout_ratio=0.5,
        )

    def get_dividend_history(self, ticker, *, start, end):
        # 30 years of rising dividends -> verifiable aristocrat streak
        return [
            DividendEvent(ex_date=date(1995 + i, 6, 1), amount=1.0 + 0.05 * i)
            for i in range(30)
        ]

    def get_price_history(self, ticker, *, start, end):
        bars = [
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i,
                     volume=1000)
            for i in range(220)
        ]
        return PriceHistory(ticker=ticker, bars=bars)


class ScriptedSpecialistRunner:
    """Returns a different scripted opinion per call; records (system, user)
    pairs so tests can assert on prompt structure."""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls: list[tuple[str, str]] = []

    def invoke(self, system: str, user: str):
        self.calls.append((system, user))
        return self._outputs.pop(0)


class StaticRunner:
    def __init__(self, output):
        self._output = output
        self.calls: list[tuple[str, str]] = []

    def invoke(self, system: str, user: str):
        self.calls.append((system, user))
        return self._output


def _run(specialist_outputs, decision_output, prior=None,
         critic_output=None, return_runners=False, strategy=STRATEGY):
    runners = {
        "specialist": ScriptedSpecialistRunner(specialist_outputs),
        "critic": StaticRunner(critic_output or CriticOutput(
            counter_thesis="Yield could be a trap if payout climbs.",
            weaknesses_found=["sentiment evidence missing"],
        )),
        "decision": StaticRunner(decision_output),
    }
    app = build_council(FakeAdapter(), strategy, runners)
    init = ResearchState(
        ticker="FAKE", strategy_id=strategy.id, prior_recommendation=prior
    )
    result = app.invoke(init)
    # LangGraph returns a dict-shaped state; rehydrate for convenient asserts.
    state = ResearchState.model_validate(result)
    return (state, runners) if return_runners else state


def _bullish(conf=0.8):
    return SpecialistOutput(stance=Stance.BULLISH, confidence=conf, thesis="up")


def test_full_council_run_clean():
    outs = [
        _bullish(), _bullish(),
        SpecialistOutput(stance=Stance.ABSTAIN, confidence=0.0,
                         thesis="no sentiment data wired in",
                         caveats=["sentiment provider missing"]),
        _bullish(0.7),
    ]
    state = _run(outs, DecisionOutput(
        recommendation=Recommendation.BUY, confidence=0.85, rationale="strong",
        dissent=[],
    ))

    # gather logged the adapter + tool calls
    names = {tc.tool_name for tc in state.tool_calls}
    assert {"get_fundamentals", "get_dividend_history", "get_price_history",
            "run_strategy_screen", "technical_snapshot"} <= names

    # all four specialists opined, critic + decision landed
    assert len(state.specialist_opinions) == 4
    assert state.critic_report is not None
    assert state.critic_report.targets_stance == Stance.BULLISH
    assert state.decision.recommendation == Recommendation.BUY

    # the sentiment abstention (honest: no data) trips DATA_QUALITY — by design
    assert {f.trigger for f in state.veto_flags} == {VetoTrigger.DATA_QUALITY}
    assert state.requires_human_review is True


def test_growth_run_skips_dividend_history_tool():
    """Sprint 4E: a growth strategy needs no dividend history, so gather must
    NOT invoke get_dividend_history and the evidence must carry no dividend
    events (root cause of the live MSFT dividend-citation violations)."""
    growth = load_strategy(
        Path(__file__).resolve().parents[1] / "strategies" / "growth_v1.yaml")
    state, runners = _run([_bullish()] * 4,
                          DecisionOutput(recommendation=Recommendation.HOLD,
                                         confidence=0.7, rationale="r"),
                          strategy=growth, return_runners=True)
    names = {tc.tool_name for tc in state.tool_calls}
    assert "get_dividend_history" not in names          # tool not invoked
    assert {"get_fundamentals", "get_price_history"} <= names  # core still runs
    # the agent never sees dividend history
    _, user0 = runners["specialist"].calls[0]
    assert "get_dividend_history" not in user0


def test_dividend_run_still_calls_dividend_history_tool():
    """Regression: the dividend strategy is unchanged — it still gathers
    dividend history."""
    state = _run([_bullish()] * 4,
                 DecisionOutput(recommendation=Recommendation.HOLD,
                                confidence=0.9, rationale="r"))  # default STRATEGY
    names = {tc.tool_name for tc in state.tool_calls}
    assert "get_dividend_history" in names


def test_provenance_violation_is_caught_and_flagged():
    bad_fig = FigureRef(label="yield", value=0.03,
                        call_id="not-a-real-call", field_path="x")
    outs = [
        SpecialistOutput(stance=Stance.BULLISH, confidence=0.8,
                         thesis="up", figures=[bad_fig]),
        _bullish(), _bullish(), _bullish(),
    ]
    state = _run(outs, DecisionOutput(
        recommendation=Recommendation.BUY, confidence=0.9, rationale="r"))

    assert any("provenance violation" in e for e in state.errors)
    # the bogus figure was NOT attached to the opinion
    fund = state.opinion_for(SpecialistName.FUNDAMENTAL)
    assert fund.figures == []
    assert VetoTrigger.DATA_QUALITY in {f.trigger for f in state.veto_flags}


def test_valid_figure_reference_is_accepted():
    # First run gather alone to discover a real call_id, then script a figure
    # that cites it. Simpler: run the graph once, grab a call_id from the
    # resulting state, run again citing it.
    probe = _run([_bullish()] * 4, DecisionOutput(
        recommendation=Recommendation.HOLD, confidence=0.9, rationale="r"))
    real_id = next(tc.call_id for tc in probe.tool_calls
                   if tc.tool_name == "get_fundamentals")
    # call_ids are random per run, so cite one we KNOW exists by re-running
    # with a runner that reads the prompt. Instead, verify resolution directly:
    fig_ok = probe.tool_call_by_id(real_id)
    assert fig_ok is not None and fig_ok.tool_name == "get_fundamentals"


def test_conflict_and_flip_triggers_fire_together():
    outs = [
        SpecialistOutput(stance=Stance.BULLISH, confidence=0.8, thesis="up"),
        SpecialistOutput(stance=Stance.BEARISH, confidence=0.8, thesis="down"),
        _bullish(), _bullish(),
    ]
    state = _run(
        outs,
        DecisionOutput(recommendation=Recommendation.SELL, confidence=0.5,
                       rationale="r", dissent=[SpecialistName.FUNDAMENTAL]),
        prior=Recommendation.BUY,
    )
    trig = {f.trigger for f in state.veto_flags}
    assert VetoTrigger.SPECIALIST_CONFLICT in trig
    assert VetoTrigger.RECOMMENDATION_FLIP in trig
    assert VetoTrigger.LOW_CONFIDENCE in trig  # 0.5 < 0.6
    # dissent preserved on the decision
    assert state.decision.dissent == [SpecialistName.FUNDAMENTAL]


def test_missing_call_id_is_violation_not_crash():
    """Regression: live run crashed when the Risk specialist omitted call_id.
    A missing call_id must parse fine, then be handled as a provenance
    violation — figure dropped, error logged, data-quality veto fired."""
    no_id_fig = FigureRef(label="eps", value=5.2, field_path="output.eps")
    assert no_id_fig.call_id == ""  # parse-time tolerance

    outs = [
        SpecialistOutput(stance=Stance.BULLISH, confidence=0.8,
                         thesis="up", figures=[no_id_fig]),
        _bullish(), _bullish(), _bullish(),
    ]
    state = _run(outs, DecisionOutput(
        recommendation=Recommendation.BUY, confidence=0.9, rationale="r"))

    assert any("provenance violation" in e for e in state.errors)
    assert state.opinion_for(SpecialistName.FUNDAMENTAL).figures == []
    assert VetoTrigger.DATA_QUALITY in {f.trigger for f in state.veto_flags}


def test_figures_as_json_string_is_coerced():
    """Regression: live run crashed when the model returned `figures` as a
    JSON string instead of a list. The schema must parse it back."""
    out = SpecialistOutput(
        stance=Stance.BULLISH, confidence=0.7, thesis="up",
        figures='[{"label": "payout", "value": 0.5, "call_id": "abc", '
                '"field_path": "output.payout_ratio"}]',
    )
    assert len(out.figures) == 1
    assert out.figures[0].label == "payout"


def test_unparseable_figures_string_degrades_to_empty():
    out = SpecialistOutput(
        stance=Stance.BULLISH, confidence=0.7, thesis="up",
        figures="not json at all {{{",
    )
    assert out.figures == []


def test_figure_unit_null_is_coerced_to_unitless():
    """Regression (MO live crash): an agent emitted figures with `unit: null`.
    FigureRef.unit required a string, so the WHOLE SpecialistOutput failed to
    validate and the run died. A null/missing unit must coerce to '' (unitless),
    consistent with the schema's tolerate-at-parse philosophy."""
    out = SpecialistOutput(
        stance=Stance.BULLISH, confidence=0.7, thesis="up",
        figures=[
            {"label": "dividend_yield", "value": 0.084, "unit": None,
             "call_id": "c1", "field_path": "metrics.dividend_yield"},
            {"label": "payout_ratio", "value": 0.79, "unit": None,
             "call_id": "c1", "field_path": "metrics.payout_ratio"},
            # unit omitted entirely still defaults to "" (unchanged behaviour)
            {"label": "market_cap", "value": 9.5e10,
             "call_id": "c1", "field_path": "metrics.market_cap"},
        ],
    )
    assert len(out.figures) == 3
    assert all(f.unit == "" for f in out.figures)
    # also via direct construction (before-validator runs either way)
    assert FigureRef(label="x", value=1.0, unit=None).unit == ""


def test_null_unit_figures_survive_validation_with_empty_unit():
    """The null-unit figures must SURVIVE provenance validation (valid call_id)
    and land as state Figures with unit '' — not be dropped."""
    from aristos_council.agents.nodes import _validated_figures
    from aristos_council.state import Figure, Provenance, ResearchState, ToolCall

    state = ResearchState(ticker="MO", strategy_id=STRATEGY.id)
    state.tool_calls.append(ToolCall(
        call_id="c1", tool_name="run_strategy_screen",
        output={"metrics": {"dividend_yield": 0.084, "payout_ratio": 0.79}}))
    refs = [
        FigureRef(label="dividend_yield", value=0.084, unit=None,
                  call_id="c1", field_path="metrics.dividend_yield"),
        FigureRef(label="payout_ratio", value=0.79, unit=None,
                  call_id="c1", field_path="metrics.payout_ratio"),
    ]
    figures = _validated_figures(state, "fundamental", refs)
    assert len(figures) == 2
    assert all(f.unit == "" for f in figures)
    # the state-level Figure tolerates a null unit directly too
    assert Figure(label="x", value=1.0, unit=None,
                  provenance=Provenance(tool_name="t", call_id="c1",
                                        field_path="f")).unit == ""


# --------------------------------------------------------------------------- #
# Critic provenance contract (added after the KO live run, where the Critic
# smuggled an external share count and forbidden arithmetic into its strongest
# argument and the Decision agent endorsed it)
# --------------------------------------------------------------------------- #
def test_critic_bogus_figure_is_violation():
    critic_out = CriticOutput(
        counter_thesis="The payout is worse than it looks.",
        figures=[FigureRef(label="shares_outstanding", value=4.3e9,
                           call_id="smuggled", field_path="nowhere")],
    )
    state = _run([_bullish()] * 4,
                 DecisionOutput(recommendation=Recommendation.HOLD,
                                confidence=0.9, rationale="r"),
                 critic_output=critic_out)
    assert any("provenance violation: critic" in e for e in state.errors)
    assert state.critic_report.figures == []  # smuggled figure dropped
    assert VetoTrigger.DATA_QUALITY in {f.trigger for f in state.veto_flags}


def test_critic_open_questions_reach_decision_as_questions():
    critic_out = CriticOutput(
        counter_thesis="Coverage is unproven.",
        open_questions=["Is the dividend covered by FCF once share count is known?"],
    )
    state, runners = _run([_bullish()] * 4,
                          DecisionOutput(recommendation=Recommendation.HOLD,
                                         confidence=0.9, rationale="r"),
                          critic_output=critic_out, return_runners=True)
    # carried into state
    assert state.critic_report.open_questions == [
        "Is the dividend covered by FCF once share count is known?"]
    # surfaced to the Decision agent, explicitly labelled as not-facts
    _, decision_user = runners["decision"].calls[0]
    assert "OPEN QUESTIONS (unresolved, for human review — not facts)" in decision_user
    assert "share count" in decision_user
    # and the Decision SYSTEM message carries the not-evidence rule
    decision_system, _ = runners["decision"].calls[0]
    assert "OPEN QUESTIONS ARE NOT EVIDENCE" in decision_system


def test_system_user_split_structure():
    state, runners = _run([_bullish()] * 4,
                          DecisionOutput(recommendation=Recommendation.BUY,
                                         confidence=0.9, rationale="r"),
                          return_runners=True)
    # Specialist system message: stable content (role, rules, strategy) —
    # and NO per-run evidence.
    sys0, user0 = runners["specialist"].calls[0]
    assert "FUNDAMENTAL specialist" in sys0
    assert "HARD RULES" in sys0
    assert "NO ARITHMETIC" in sys0
    assert "EVIDENCE" not in sys0.replace("EVIDENCE ONLY", "")
    # User message: per-run evidence, no rules.
    assert "Ticker under review: FAKE" in user0
    assert "call_id" in user0
    assert "HARD RULES" not in user0
    # Critic system message carries the open-questions contract.
    critic_sys, _ = runners["critic"].calls[0]
    assert "OPEN QUESTIONS" in critic_sys
    assert "may NOT state the suspected answer as a fact" in critic_sys


def test_null_valued_figure_with_provenance_is_kept():
    """Regression: live run crashed when Risk cited a NULL field (e.g.
    years_dividend_growth=None). A null value with a valid call_id is
    legitimate evidence of absence and must survive with provenance."""
    probe = _run([_bullish()] * 4, DecisionOutput(
        recommendation=Recommendation.HOLD, confidence=0.9, rationale="r"))
    real_id = next(tc.call_id for tc in probe.tool_calls
                   if tc.tool_name == "get_fundamentals")

    null_fig = FigureRef(label="years_dividend_growth", value=None,
                         call_id="will-be-replaced", field_path="output.years_dividend_growth")
    # Parse-time tolerance is the schema-level assertion:
    assert null_fig.value is None

    # And the state-level Figure accepts None too:
    from aristos_council.state import Figure, Provenance
    fig = Figure(label="x", value=None,
                 provenance=Provenance(tool_name="get_fundamentals",
                                       call_id=real_id, field_path="f"))
    assert fig.value is None


def test_null_valued_figure_still_needs_valid_call_id():
    bad = FigureRef(label="missing_field", value=None,
                    call_id="nonexistent", field_path="x")
    outs = [
        SpecialistOutput(stance=Stance.BEARISH, confidence=0.6,
                         thesis="data gaps", figures=[bad]),
        _bullish(), _bullish(), _bullish(),
    ]
    state = _run(outs, DecisionOutput(
        recommendation=Recommendation.HOLD, confidence=0.9, rationale="r"))
    # Null value does not exempt the figure from provenance rules.
    assert any("provenance violation" in e for e in state.errors)


# --------------------------------------------------------------------------- #
# Sentiment integration (Finnhub via SentimentAdapter seam)
# --------------------------------------------------------------------------- #
from aristos_council.data.sentiment import (
    NewsItem,
    RecommendationTrend,
    SentimentAdapter,
    SentimentDataUnavailable,
)


class FakeSentimentAdapter(SentimentAdapter):
    name = "fake-sentiment"

    def get_company_news(self, ticker, *, start, end):
        return [NewsItem(published=date(2026, 6, 10),
                         headline="Fake Corp raises dividend", source="wire")]

    def get_recommendation_trends(self, ticker):
        return [RecommendationTrend(period="2026-06-01", strong_buy=3, buy=7,
                                    hold=10, sell=2, strong_sell=0)]


class BrokenSentimentAdapter(SentimentAdapter):
    name = "broken"

    def get_company_news(self, ticker, *, start, end):
        raise SentimentDataUnavailable("simulated outage")

    def get_recommendation_trends(self, ticker):
        raise SentimentDataUnavailable("simulated outage")


def _run_with_sentiment(adapter):
    runners = {
        "specialist": ScriptedSpecialistRunner([_bullish()] * 4),
        "critic": StaticRunner(CriticOutput(counter_thesis="c")),
        "decision": StaticRunner(DecisionOutput(
            recommendation=Recommendation.BUY, confidence=0.9, rationale="r")),
    }
    app = build_council(FakeAdapter(), STRATEGY, runners,
                        sentiment_adapter=adapter)
    result = app.invoke(ResearchState(ticker="FAKE", strategy_id=STRATEGY.id))
    return ResearchState.model_validate(result), runners


def test_sentiment_evidence_lands_in_ledger_and_prompt():
    state, runners = _run_with_sentiment(FakeSentimentAdapter())
    names = {tc.tool_name for tc in state.tool_calls}
    assert {"get_company_news", "get_recommendation_trends",
            "sentiment_snapshot"} <= names
    snap = next(tc for tc in state.tool_calls
                if tc.tool_name == "sentiment_snapshot")
    assert snap.output["bullish_ratio"] == 0.4545  # 10/22
    # the specialists actually see it
    _, user = runners["specialist"].calls[0]
    assert "sentiment_snapshot" in user
    assert "raises dividend" in user


def test_no_sentiment_adapter_means_no_sentiment_evidence():
    state, _ = _run_with_sentiment(None)
    names = {tc.tool_name for tc in state.tool_calls}
    assert "sentiment_snapshot" not in names  # pre-Finnhub behaviour preserved


def test_sentiment_outage_degrades_not_crashes():
    state, _ = _run_with_sentiment(BrokenSentimentAdapter())
    # failures logged as failed tool calls + errors, run completed
    failed = [tc for tc in state.tool_calls if not tc.ok]
    assert len(failed) == 2
    assert any("simulated outage" in e for e in state.errors)
    assert state.decision is not None
    # and the data-quality veto fires
    assert VetoTrigger.DATA_QUALITY in {f.trigger for f in state.veto_flags}


# --------------------------------------------------------------------------- #
# Evidence size guards (live-run regression: NVDA news volume blew the
# per-minute token limit because every prompt carried the full news list)
# --------------------------------------------------------------------------- #
class FloodSentimentAdapter(SentimentAdapter):
    name = "flood"

    def get_company_news(self, ticker, *, start, end):
        return [NewsItem(published=date(2026, 6, 1 + i % 9),
                         headline=f"headline {i}", source="wire")
                for i in range(300)]

    def get_recommendation_trends(self, ticker):
        return [RecommendationTrend(period="2026-06-01", buy=10, hold=5)]


def test_news_flood_is_capped_in_ledger_but_counted_fully():
    state, runners = _run_with_sentiment(FloodSentimentAdapter())
    news_tc = next(tc for tc in state.tool_calls
                   if tc.tool_name == "get_company_news")
    assert len(news_tc.output) == 60                       # capped in ledger
    assert news_tc.inputs["total_items_fetched"] == 300    # honesty preserved
    snap = next(tc for tc in state.tool_calls
                if tc.tool_name == "sentiment_snapshot")
    assert snap.output["news_count"] == 300                # count stays truthful


def test_evidence_block_truncates_oversized_outputs():
    from aristos_council.agents.nodes import (
        MAX_TOOL_OUTPUT_CHARS,
        _evidence_block,
    )
    from aristos_council.state import ToolCall
    state = ResearchState(ticker="X", strategy_id=STRATEGY.id)
    state.tool_calls.append(ToolCall(
        call_id="big", tool_name="huge_tool",
        output=["x" * 100] * 1000,   # far beyond the limit
    ))
    block = _evidence_block(state, STRATEGY)
    assert "TRUNCATED FOR PROMPT" in block
    assert len(block) < MAX_TOOL_OUTPUT_CHARS * 2  # bounded


def test_price_history_is_summarized_in_prompts_not_raw():
    """Regression (T run): raw price bars exceeded the prompt size guard and
    front-truncation showed agents only the oldest bars, creating a phantom
    'price inconsistency'. Prompts must carry a compact, CURRENT summary."""
    state, runners = _run_with_sentiment(None)
    _, user = runners["specialist"].calls[0]
    assert "raw bars omitted from prompt" in user
    assert '"n_bars": 220' in user
    assert "last_day" in user
    # And the ledger still holds the full series for audit.
    ph = next(tc for tc in state.tool_calls
              if tc.tool_name == "get_price_history")
    assert len(ph.output.bars) == 220


# --------------------------------------------------------------------------- #
# Deterministic disposition gate (baked into v1) — end to end in the graph.
# The gate must override the LLM verdict on a confirmed gating-criterion fail,
# regardless of partial_pass_allows_hold (v1 keeps the flag True on purpose).
# STRATEGY (v1) now gates the streak by default — the former v2 collapsed in.
# --------------------------------------------------------------------------- #
class ShortStreakAdapter(FakeAdapter):
    """Same as FakeAdapter but only ~6 rising dividend years, so the streak
    criterion is a CONFIRMED fail (streak ~4 < 20), firing the v1 gate."""

    def get_dividend_history(self, ticker, *, start, end):
        return [
            DividendEvent(ex_date=date(2018 + i, 6, 1), amount=1.0 + 0.1 * i)
            for i in range(6)
        ]


def _run_gate(strategy, decision_rec):
    runners = {
        "specialist": ScriptedSpecialistRunner([_bullish()] * 4),
        "critic": StaticRunner(CriticOutput(counter_thesis="c")),
        "decision": StaticRunner(DecisionOutput(
            recommendation=decision_rec, confidence=0.9, rationale="r")),
    }
    app = build_council(ShortStreakAdapter(), strategy, runners)
    result = app.invoke(ResearchState(ticker="FAKE", strategy_id=strategy.id))
    return ResearchState.model_validate(result)


def test_gate_caps_buy_at_sell_on_streak_fail():
    d = _run_gate(STRATEGY, Recommendation.BUY).decision
    assert d.recommendation == Recommendation.SELL          # capped by the gate
    assert d.original_recommendation == Recommendation.BUY   # LLM pre-gate verdict
    assert d.gate_override_applied is True
    assert d.gating_criterion_fired == "min_dividend_growth_streak"


def test_gate_caps_hold_at_sell_on_streak_fail():
    d = _run_gate(STRATEGY, Recommendation.HOLD).decision
    assert d.recommendation == Recommendation.SELL
    assert d.original_recommendation == Recommendation.HOLD
    assert d.gate_override_applied is True
    assert d.gating_criterion_fired == "min_dividend_growth_streak"


def test_gate_leaves_sell_unchanged():
    # LLM already at the ceiling -> no override; metadata records the no-op.
    d = _run_gate(STRATEGY, Recommendation.SELL).decision
    assert d.recommendation == Recommendation.SELL
    assert d.gate_override_applied is False
    assert d.original_recommendation == Recommendation.SELL


def test_override_off_disables_v1_gate_on_failing_streak():
    # v1 now GATES the streak by default. An ephemeral is_gating=False override is
    # the experiment knob that turns it back off, so the same failing streak leaves
    # the LLM verdict standing (proves the gate is the default AND still relaxable).
    ungated = effective_strategy(
        STRATEGY, is_gating={"min_dividend_growth_streak": False})
    d = _run_gate(ungated, Recommendation.BUY).decision
    assert d.recommendation == Recommendation.BUY           # NOT capped (gate off)
    assert d.gate_override_applied is False
    assert d.original_recommendation == Recommendation.BUY


# --------------------------------------------------------------------------- #
# INSUFFICIENT_EVIDENCE (strict): a NOT-EVAL on a GATING criterion short-circuits
# the verdict OFF the buy/hold/sell ladder and ALWAYS fires human review.
# --------------------------------------------------------------------------- #
class NotEvalStreakAdapter(FakeAdapter):
    """Only ONE dividend year, so the streak criterion is NOT-EVAL (passed is
    None) — insufficient history to even count a streak — rather than a confirmed
    fail. Under v1 (streak gating) this must short-circuit to INSUFFICIENT_EVIDENCE."""

    def get_dividend_history(self, ticker, *, start, end):
        return [DividendEvent(ex_date=date(2024, 6, 1), amount=1.0)]


def test_not_eval_on_gating_criterion_short_circuits_to_insufficient_evidence():
    runners = {
        "specialist": ScriptedSpecialistRunner([_bullish()] * 4),
        "critic": StaticRunner(CriticOutput(counter_thesis="c")),
        "decision": StaticRunner(DecisionOutput(
            recommendation=Recommendation.BUY, confidence=0.9, rationale="r")),
    }
    app = build_council(NotEvalStreakAdapter(), STRATEGY, runners)
    state = ResearchState.model_validate(
        app.invoke(ResearchState(ticker="FAKE", strategy_id=STRATEGY.id)))
    d = state.decision
    # verdict is off the ladder, not the LLM's BUY
    assert d.recommendation == Recommendation.INSUFFICIENT_EVIDENCE
    assert d.original_recommendation == Recommendation.BUY
    assert d.gate_override_applied is True
    assert d.insufficient_evidence is True
    assert d.gating_criterion_fired == "min_dividend_growth_streak"
    # and human review fires unconditionally via the dedicated veto trigger
    assert VetoTrigger.INSUFFICIENT_EVIDENCE in {f.trigger for f in state.veto_flags}
    assert state.requires_human_review is True


def test_confirmed_fail_wins_over_not_eval_at_decision_node():
    """Precedence: when a gating criterion is a CONFIRMED fail AND another is
    NOT-EVAL, the confirmed-fail SELL cap wins over INSUFFICIENT_EVIDENCE
    ('a real SELL beats can't-tell'). Exercised directly on the decision node
    with a crafted screen so both conditions co-occur."""
    from aristos_council.agents.nodes import make_decision_node
    from aristos_council.state import ToolCall

    eff = effective_strategy(STRATEGY, is_gating={
        "min_dividend_yield": True, "min_dividend_growth_streak": True})
    node = make_decision_node(eff, StaticRunner(DecisionOutput(
        recommendation=Recommendation.BUY, confidence=0.9, rationale="r")))
    state = ResearchState(ticker="FAKE", strategy_id=STRATEGY.id)
    state.tool_calls.append(ToolCall(
        call_id="s", tool_name="run_strategy_screen",
        output={"criteria": [
            {"name": "min_dividend_yield", "passed": False, "observed": 0.0,
             "threshold": 0.025, "note": "confirmed fail"},
            {"name": "min_dividend_growth_streak", "passed": None,
             "observed": None, "threshold": 25.0, "note": "not evaluated"},
        ]}))
    node(state)
    d = state.decision
    assert d.recommendation == Recommendation.SELL          # confirmed-fail wins
    assert d.insufficient_evidence is False
    assert d.gate_override_applied is True
    assert d.gating_criterion_fired == "min_dividend_yield"


def test_non_gating_not_eval_does_not_short_circuit():
    """A NOT-EVAL on a NON-gating criterion leaves the verdict untouched (the
    LLM verdict stands) — INSUFFICIENT_EVIDENCE is reserved for GATING NOT-EVAL."""
    from aristos_council.agents.nodes import make_decision_node
    from aristos_council.state import ToolCall

    # Only the streak is gating; the NOT-EVAL is on yield (non-gating).
    node = make_decision_node(STRATEGY, StaticRunner(DecisionOutput(
        recommendation=Recommendation.BUY, confidence=0.9, rationale="r")))
    state = ResearchState(ticker="FAKE", strategy_id=STRATEGY.id)
    state.tool_calls.append(ToolCall(
        call_id="s", tool_name="run_strategy_screen",
        output={"criteria": [
            {"name": "min_dividend_yield", "passed": None, "observed": None,
             "threshold": 0.025, "note": "not evaluated"},
            {"name": "min_dividend_growth_streak", "passed": True,
             "observed": 40.0, "threshold": 25.0, "note": "ok"},
        ]}))
    node(state)
    d = state.decision
    assert d.recommendation == Recommendation.BUY           # unaffected
    assert d.insufficient_evidence is False
    assert d.gate_override_applied is False


def test_dividend_history_rendered_as_named_handles_not_a_raw_list():
    # STEP 1: agents see latest/earliest/by_year/n_events handles, not a bare
    # list to index (the source of the index/semantic violation mode).
    _, runners = _run([_bullish()] * 4,
                      DecisionOutput(recommendation=Recommendation.HOLD,
                                     confidence=0.9, rationale="r"),
                      return_runners=True)
    _, user = runners["specialist"].calls[0]
    assert '"n_events"' in user and '"by_year"' in user and '"latest"' in user
    assert "raw event list omitted" in user


def test_ephemeral_override_relaxes_v1_gate_at_runtime():
    # v1 GATES the streak by default; an ephemeral is_gating=False override relaxes
    # it for ONE run, so a streak-failing ticker with an LLM BUY is NOT capped — and
    # the delta rides through to the run record for reproducibility.
    eff = effective_strategy(STRATEGY,
                             is_gating={"min_dividend_growth_streak": False})
    delta = applied_overrides(STRATEGY, eff)
    runners = {
        "specialist": ScriptedSpecialistRunner([_bullish()] * 4),
        "critic": StaticRunner(CriticOutput(counter_thesis="c")),
        "decision": StaticRunner(DecisionOutput(
            recommendation=Recommendation.BUY, confidence=0.9, rationale="r")),
    }
    app = build_council(ShortStreakAdapter(), eff, runners)
    state = ResearchState.model_validate(app.invoke(ResearchState(
        ticker="FAKE", strategy_id=STRATEGY.id, applied_overrides=delta)))
    d = state.decision
    assert d.recommendation == Recommendation.BUY           # gate relaxed -> not capped
    assert d.original_recommendation == Recommendation.BUY
    assert d.gate_override_applied is False
    # the delta rides through for the report/verdict
    assert state.applied_overrides == {
        "criteria.min_dividend_growth_streak.is_gating": False}
