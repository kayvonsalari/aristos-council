"""Deterministic decision matrix — the reproducible half of the hybrid verdict.

Pure function: given a state (screen in the ledger + specialist stances) and a
strategy's scoring config, it returns a verdict with NO LLM. Tests assert
determinism, the BUY/SELL/gated/borderline mapping, that the breakdown sums to the
score, and screen-dominance (a single stance flip can't cross a threshold).
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.agents.matrix import decision_matrix
from aristos_council.state import (
    Recommendation,
    ResearchState,
    SpecialistName,
    SpecialistOpinion,
    Stance,
    ToolCall,
)
from aristos_council.strategy.loader import load_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
GROWTH = load_strategy(STRAT_DIR / "growth_v1.yaml")
DIVIDEND = load_strategy(STRAT_DIR / "dividend_aristocrats_v1.yaml")


def _crit(name, passed, observed, threshold):
    return {"name": name, "passed": passed, "observed": observed,
            "threshold": threshold, "note": ""}


def _state(criteria, stances=(), strategy_id="growth_v1") -> ResearchState:
    s = ResearchState(ticker="X", strategy_id=strategy_id)
    s.tool_calls.append(ToolCall(
        call_id="c1", tool_name="run_strategy_screen",
        output={"criteria": criteria}))
    for who, stance, conf in stances:
        s.specialist_opinions.append(SpecialistOpinion(
            specialist=who, stance=stance, confidence=conf, thesis="t"))
    return s


_CLEAN = [
    _crit("min_revenue_cagr", True, 0.20, 0.10),   # +25 (full margin)
    _crit("max_peg_ratio", True, 1.0, 2.0),        # +12.5 (half margin, max_ dir)
    _crit("min_roic", True, 0.24, 0.12),           # +20 (full margin)
    _crit("min_market_cap", True, 1e11, 5e9),      # +10 (clamped full margin)
]


# --------------------------------------------------------------------------- #
# Determinism + clean BUY / 2-fail SELL
# --------------------------------------------------------------------------- #
def test_matrix_is_deterministic():
    s = _state(_CLEAN, [(SpecialistName.TECHNICAL, Stance.BEARISH, 0.7)])
    a = decision_matrix(s, GROWTH)
    b = decision_matrix(s, GROWTH)
    assert a == b                        # identical state -> identical verdict+score


def test_clean_all_pass_growth_is_buy():
    m = decision_matrix(_state(_CLEAN), GROWTH)
    assert m.gated is False
    assert m.score > GROWTH.scoring.buy_threshold
    assert m.verdict == Recommendation.BUY
    # 25 + 12.5 + 20 + 10
    assert abs(m.score - 67.5) < 1e-9


def test_lmt_shaped_two_fails_is_sell():
    # revenue CAGR fail (margin-scaled) + PEG fail (FIX-1c: passed False, no observed)
    lmt = [
        _crit("min_revenue_cagr", False, 0.0446, 0.10),  # -13.85
        _crit("max_peg_ratio", False, None, 2.0),        # -25 (no margin -> full)
        _crit("min_roic", True, 0.15, 0.12),             # +5
        _crit("min_market_cap", True, 1e11, 5e9),        # +10
    ]
    m = decision_matrix(_state(lmt), GROWTH)
    assert m.verdict == Recommendation.SELL
    assert m.score <= GROWTH.scoring.sell_threshold


# --------------------------------------------------------------------------- #
# Gate supersedes scoring
# --------------------------------------------------------------------------- #
def test_gate_confirmed_fail_returns_sell_and_skips_scoring():
    # dividend strategy gates min_dividend_growth_streak; a confirmed fail -> SELL.
    screen = [
        _crit("min_dividend_yield", True, 0.03, 0.025),
        _crit("min_dividend_growth_streak", False, 4, 25),   # gating confirmed fail
    ]
    m = decision_matrix(_state(screen, strategy_id="dividend_aristocrats_v1"),
                        DIVIDEND)
    assert m.gated is True and m.score is None
    assert m.verdict == Recommendation.SELL


def test_gate_not_eval_returns_insufficient_evidence():
    screen = [_crit("min_dividend_growth_streak", None, None, 25)]  # NOT-EVAL gating
    m = decision_matrix(_state(screen, strategy_id="dividend_aristocrats_v1"),
                        DIVIDEND)
    assert m.gated is True
    assert m.verdict == Recommendation.INSUFFICIENT_EVIDENCE


# --------------------------------------------------------------------------- #
# Dead-band borderline + breakdown sums to score
# --------------------------------------------------------------------------- #
def test_score_in_dead_band_sets_borderline():
    # +10 (market cap) + 6 (roic, 0.3 margin x 20) = 16 -> HOLD, within 6 of BUY_TH 20
    screen = [
        _crit("min_market_cap", True, 1e11, 5e9),   # +10
        _crit("min_roic", True, 0.156, 0.12),       # +6
    ]
    m = decision_matrix(_state(screen), GROWTH)
    assert m.verdict == Recommendation.HOLD
    assert m.borderline is True
    assert abs(m.score - 16.0) < 1e-9


def test_contributions_sum_to_score():
    m = decision_matrix(
        _state(_CLEAN, [(SpecialistName.TECHNICAL, Stance.BEARISH, 0.7),
                        (SpecialistName.FUNDAMENTAL, Stance.BULLISH, 0.9)]),
        GROWTH)
    assert abs(sum(c.points for c in m.contributions) - m.score) < 1e-9
    # both screen criteria AND stance rows are present in the breakdown
    names = {c.name for c in m.contributions}
    assert "min_revenue_cagr" in names and "stance:technical" in names


# --------------------------------------------------------------------------- #
# Screen-dominance — one stance flip cannot cross a threshold on a clear name
# --------------------------------------------------------------------------- #
def test_one_specialist_flip_does_not_cross_threshold():
    bull = [(w, Stance.BULLISH, 0.8) for w in
            (SpecialistName.FUNDAMENTAL, SpecialistName.TECHNICAL,
             SpecialistName.SENTIMENT, SpecialistName.RISK)]
    base = decision_matrix(_state(_CLEAN, bull), GROWTH)
    flipped_stances = [(SpecialistName.TECHNICAL, Stance.BEARISH, 0.8)
                       if w == SpecialistName.TECHNICAL else (w, st, c)
                       for (w, st, c) in bull]
    flipped = decision_matrix(_state(_CLEAN, flipped_stances), GROWTH)
    # the flip moves the score by at most 2 x conf x stance_weight = 4.8, and the
    # verdict stays BUY (the screen dominates).
    assert base.verdict == Recommendation.BUY and flipped.verdict == Recommendation.BUY
    assert abs(base.score - flipped.score) <= 2 * 0.8 * GROWTH.scoring.stance_weight + 1e-9


# --------------------------------------------------------------------------- #
# Hybrid contract end-to-end: the matrix node runs IN the graph alongside the LLM
# decision, and the report carries BOTH verdicts + the agreement field.
# --------------------------------------------------------------------------- #
def test_hybrid_runs_in_graph_and_report_carries_both():
    from datetime import date

    from aristos_council.agents.schemas import (
        CriticOutput, DecisionOutput, SpecialistOutput,
    )
    from aristos_council.data.adapter import (
        DividendEvent, Fundamentals, MarketDataAdapter, PriceBar, PriceHistory,
    )
    from aristos_council.graph import build_council
    from aristos_council.persistence.reports import report_from_state

    class _Adapter(MarketDataAdapter):
        name = "fake"

        def get_fundamentals(self, ticker):
            return Fundamentals(ticker=ticker, name="F", market_cap=5e10,
                                dividend_yield=0.03, payout_ratio=0.5,
                                dividend_per_share=2.0)

        def get_dividend_history(self, ticker, *, start, end):
            return [DividendEvent(ex_date=date(1995 + i, 6, 1), amount=1.0 + 0.05 * i)
                    for i in range(30)]

        def get_price_history(self, ticker, *, start, end):
            return PriceHistory(ticker=ticker, bars=[
                PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                         close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=10)
                for i in range(220)])

    class _R:
        def __init__(self, out):
            self._out = out

        def invoke(self, system, user):
            return self._out

    runners = {
        "specialist": _R(SpecialistOutput(stance=Stance.BULLISH, confidence=0.8,
                                          thesis="up")),
        "critic": _R(CriticOutput(counter_thesis="c")),
        "decision": _R(DecisionOutput(recommendation=Recommendation.BUY,
                                      confidence=0.8, rationale="r")),
    }
    app = build_council(_Adapter(), DIVIDEND, runners)
    state = ResearchState.model_validate(app.invoke(
        ResearchState(ticker="FAKE", strategy_id=DIVIDEND.id)))

    assert state.decision is not None and state.matrix_decision is not None
    rep = report_from_state(state)
    assert rep.matrix_decision is not None
    assert rep.agreement in ("AGREE", "DISAGREE")
    # agreement is computed from the two verdicts
    expected = ("AGREE" if rep.matrix_decision.verdict == rep.decision.recommendation
                else "DISAGREE")
    assert rep.agreement == expected
