"""Integrated pipeline (Aristos v2) — ranker is verdict-of-record, council is the
independent second opinion. Deterministic: fake adapter + fake runners, no network.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.agents.schemas import (
    CriticOutput,
    DecisionOutput,
    SpecialistOutput,
)
from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.pipeline import agreement_table, run_pipeline
from aristos_council.state import Recommendation, Stance
from aristos_council.strategy.loader import load_strategy
from aristos_council.strategy.rank_loader import load_rank_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
MAGIC = load_rank_strategy(STRAT_DIR / "magic_formula_v1.yaml")     # fundamentals-only
GROWTH = load_strategy(STRAT_DIR / "growth_v1.yaml")               # council substrate

# A clear ranking from fundamentals alone: A best earnings-yield + ROIC, then B, C.
_FUND = {
    "A": dict(market_cap=2e10, sector="Technology", ebit=[3000.0], pe_ratio=10.0,
              operating_income=[3000.0, 2800, 2600, 2400], tax_provision=[600.0, 560, 520, 480],
              pretax_income=[2900.0, 2700, 2500, 2300], invested_capital=[5000.0] * 4,
              total_revenue=[200.0, 170, 150, 120]),
    "B": dict(market_cap=2e10, sector="Technology", ebit=[1500.0], pe_ratio=20.0,
              operating_income=[1500.0, 1450, 1400, 1350], tax_provision=[300.0, 290, 280, 270],
              pretax_income=[1450.0, 1400, 1350, 1300], invested_capital=[5000.0] * 4,
              total_revenue=[150.0, 140, 130, 120]),
    "C": dict(market_cap=2e10, sector="Technology", ebit=[500.0], pe_ratio=40.0,
              operating_income=[500.0, 490, 480, 470], tax_provision=[100.0, 98, 96, 94],
              pretax_income=[480.0, 470, 460, 450], invested_capital=[5000.0] * 4,
              total_revenue=[125.0, 120, 115, 110]),
}


class _Adapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, **_FUND[ticker])

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


class _SpecialistRunner:
    """Role-aware (reads the system prompt): RISK dissents, SENTIMENT abstains, the
    rest agree. Counts invocations."""

    def __init__(self):
        self.calls = 0

    def invoke(self, system, user):
        self.calls += 1
        if "RISK specialist" in system:
            return SpecialistOutput(stance=Stance.BEARISH, confidence=0.7,
                                    thesis="forward risk", agrees_with_ranker=False,
                                    dissent_note="patent-cliff headline not yet in price")
        if "SENTIMENT specialist" in system:
            # abstains, but (wrongly) returns agrees True -> node must force it to None
            return SpecialistOutput(stance=Stance.ABSTAIN, confidence=0.0,
                                    thesis="no sentiment data", agrees_with_ranker=True,
                                    dissent_note="should be nulled")
        return SpecialistOutput(stance=Stance.BULLISH, confidence=0.8, thesis="up",
                                agrees_with_ranker=True)


class _CountingDecisionRunner:
    def __init__(self, out):
        self._out = out
        self.calls = 0

    def invoke(self, system, user):
        self.calls += 1
        self.last_system = system
        return self._out


def _runners(decision_out):
    return {"specialist": _SpecialistRunner(),
            "critic": _CountingDecisionRunner(CriticOutput(counter_thesis="c")),
            "decision": _CountingDecisionRunner(decision_out)}


def _run(decision_out, *, council_mode=None, council_runs_on=None):
    runners = _runners(decision_out)
    result = run_pipeline(
        universe=["A", "B", "C"], rank_strategy=MAGIC, screen_strategy=GROWTH,
        adapter=_Adapter(), runners=runners, today=date(2026, 6, 30),
        council_mode=council_mode, council_runs_on=council_runs_on)
    return result, runners


# --------------------------------------------------------------------------- #
# Stage-1 verdict-of-record + shortlist gating (only shortlisted enter council)
# --------------------------------------------------------------------------- #
def test_only_shortlisted_names_enter_the_council():
    result, runners = _run(DecisionOutput(recommendation=Recommendation.BUY,
                                          confidence=0.8, rationale="r"))
    # 3 names, quintile -> top fifth = 1 BUY (the best-ranked, A).
    assert result.shortlist == ["A"]
    assert result.ranked[0].ticker == "A" and result.ranked[0].verdict == "buy"
    # the council ran on the shortlist ONLY: decision called once, NOT 3x.
    assert runners["decision"].calls == 1
    assert runners["specialist"].calls == 4        # 4 specialists, ONE name


def test_council_runs_on_all_overrides_the_shortlist():
    result, runners = _run(DecisionOutput(recommendation=Recommendation.HOLD,
                                          confidence=0.6, rationale="r"),
                           council_runs_on="all")
    assert set(result.shortlist) == {"A", "B", "C"}
    assert runners["decision"].calls == 3


# --------------------------------------------------------------------------- #
# Option B — independent second opinion, agreement, dissent surfaced
# --------------------------------------------------------------------------- #
def test_option_b_disagreement_surfaces_dissent():
    # ranker BUYs A on factors; council SELLs (a forward risk it can't see) -> DISAGREE
    result, _ = _run(DecisionOutput(recommendation=Recommendation.SELL,
                                    confidence=0.7, rationale="forward risk"))
    a = result.council[0]
    assert a.ranker_verdict == "buy" and a.council_verdict == "sell"
    assert a.agreement == "DISAGREE"
    assert any("patent-cliff" in d for d in a.dissent_notes)
    assert "DISAGREE" in agreement_table(result)


def test_option_b_agreement_when_council_concurs():
    result, _ = _run(DecisionOutput(recommendation=Recommendation.BUY,
                                    confidence=0.8, rationale="agree"))
    assert result.council[0].agreement == "AGREE"


# --------------------------------------------------------------------------- #
# A/B toggle — same node, flag-driven
# --------------------------------------------------------------------------- #
def test_narrator_mode_emits_no_independent_verdict():
    # In narrator mode the council echoes the ranker (BUY) and sets no second opinion.
    result, _ = _run(DecisionOutput(recommendation=Recommendation.SELL,  # would-be call
                                    confidence=0.7, rationale="narrate"),
                     council_mode="narrator")
    a = result.council[0]
    assert a.council_verdict is None            # no independent verdict in narrator
    assert a.agreement is None
    assert a.report.council_mode == "narrator"
    assert a.report.decision.narration_only is True
    assert a.report.decision.recommendation == Recommendation.BUY   # echoes ranker


def test_second_opinion_is_the_default_mode():
    result, _ = _run(DecisionOutput(recommendation=Recommendation.HOLD,
                                    confidence=0.6, rationale="r"))
    assert result.council_mode == "second_opinion"
    assert result.council[0].report.decision.narration_only is False


# --------------------------------------------------------------------------- #
# Abstention rule — a data-less specialist never inflates consensus
# --------------------------------------------------------------------------- #
def test_abstaining_specialist_agreement_is_forced_null():
    result, _ = _run(DecisionOutput(recommendation=Recommendation.BUY,
                                    confidence=0.8, rationale="r"))
    rep = result.council[0].report
    sentiment = next(o for o in rep.specialist_opinions
                     if o.specialist.value == "sentiment")
    assert sentiment.stance == Stance.ABSTAIN
    assert sentiment.agrees_with_ranker is None      # forced None despite model True
    # supports/challenges count NON-abstained only; sentiment is in 'abstained'
    sup = rep.specialist_support
    assert sup["abstained"] == 1
    assert sup["challenges"] == 1                    # the RISK specialist
    # the abstainer is NOT counted as a 'support'
    assert sup["supports"] == 2                       # fundamental + technical


# --------------------------------------------------------------------------- #
# matrix-skip — the ranker supersedes the matrix in the pipeline
# --------------------------------------------------------------------------- #
def test_matrix_node_skipped_in_pipeline_but_runs_standalone():
    from aristos_council.graph import build_council
    from aristos_council.state import ResearchState

    runners = _runners(DecisionOutput(recommendation=Recommendation.BUY,
                                      confidence=0.8, rationale="r"))
    # pipeline path: run_matrix=False -> no matrix verdict on the state
    app = build_council(_Adapter(), GROWTH, runners, run_matrix=False)
    st = ResearchState.model_validate(app.invoke(
        ResearchState(ticker="A", strategy_id=GROWTH.id)))
    assert st.matrix_decision is None
    # standalone screen run (back-compat): matrix node runs
    app2 = build_council(_Adapter(), GROWTH, runners, run_matrix=True)
    st2 = ResearchState.model_validate(app2.invoke(
        ResearchState(ticker="A", strategy_id=GROWTH.id)))
    assert st2.matrix_decision is not None
