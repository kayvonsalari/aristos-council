"""Decision-node micro-harness — replay ONLY the Decision node N times on one
cached post-Critic snapshot, label STABLE/BORDERLINE by the vote distribution.

The whole point: the deterministic screen + 4 specialists + Critic run ONCE; only
the Decision node repeats. The core correctness guarantee is that the upstream
runners are invoked exactly once across the entire N-replay call. Deterministic:
fake adapter + fake runners, no network/LLM.
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
    DividendEvent,
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.graph import build_upstream_council
from aristos_council.reproducibility import (
    decision_stability_banner,
    decision_stability_label,
    decision_stability_summary,
    run_decision_n,
)
from aristos_council.state import Recommendation, ResearchState, Stance
from aristos_council.strategy.loader import load_strategy

STRATEGY = load_strategy(
    Path(__file__).resolve().parents[1]
    / "strategies" / "dividend_aristocrats_v1.yaml"
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeAdapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name="Fake Corp", market_cap=5e10,
                            dividend_yield=0.03, payout_ratio=0.5,
                            dividend_per_share=2.0)

    def get_dividend_history(self, ticker, *, start, end):
        # 30 years rising -> streak passes -> NOT gated -> Decision verdict stands.
        return [DividendEvent(ex_date=date(1995 + i, 6, 1), amount=1.0 + 0.05 * i)
                for i in range(30)]

    def get_price_history(self, ticker, *, start, end):
        bars = [PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                         close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=1000)
                for i in range(220)]
        return PriceHistory(ticker=ticker, bars=bars)


class _ShortStreakAdapter(_FakeAdapter):
    # ~6 rising years -> streak is a CONFIRMED fail -> the gate caps a bullish
    # Decision to SELL -> gated outcome.
    def get_dividend_history(self, ticker, *, start, end):
        return [DividendEvent(ex_date=date(2018 + i, 6, 1), amount=1.0 + 0.1 * i)
                for i in range(6)]


class _CountingRunner:
    """Returns a fixed output and counts how many times it was invoked."""

    def __init__(self, output):
        self._output = output
        self.calls = 0

    def invoke(self, system, user):
        self.calls += 1
        return self._output


class _CyclingRunner:
    """Returns outputs in a repeating cycle; counts invocations."""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0

    def invoke(self, system, user):
        out = self._outputs[self.calls % len(self._outputs)]
        self.calls += 1
        return out


def _bullish():
    return SpecialistOutput(stance=Stance.BULLISH, confidence=0.7, thesis="up")


def _dec(rec, conf=0.62):
    return DecisionOutput(recommendation=rec, confidence=conf, rationale="r")


def _runners(decision_runner, *, adapter_specialist=None, critic=None):
    return {
        "specialist": adapter_specialist or _CountingRunner(_bullish()),
        "critic": critic or _CountingRunner(CriticOutput(counter_thesis="c")),
        "decision": decision_runner,
    }


# --------------------------------------------------------------------------- #
# Distribution + stability label
# --------------------------------------------------------------------------- #
def test_fixed_verdict_is_stable():
    runners = _runners(_CountingRunner(_dec(Recommendation.BUY, 0.8)))
    rep = run_decision_n(ticker="AAA", strategy=STRATEGY,
                         adapter=_FakeAdapter(), runners=runners, n=5)
    assert rep.stability == "stable"
    assert rep.distribution == {"buy": 5} and rep.modal_verdict == "buy"
    assert decision_stability_label(rep) == "STABLE BUY"
    assert decision_stability_banner(rep) is None


def test_split_verdict_is_borderline_leaning_modal():
    cyc = _CyclingRunner([_dec(Recommendation.BUY, 0.62),
                          _dec(Recommendation.HOLD, 0.60),
                          _dec(Recommendation.BUY, 0.64),
                          _dec(Recommendation.HOLD, 0.58),
                          _dec(Recommendation.BUY, 0.61)])
    rep = run_decision_n(ticker="GOOGL", strategy=STRATEGY,
                         adapter=_FakeAdapter(), runners=_runners(cyc), n=5)
    assert rep.stability == "BORDERLINE"
    assert rep.distribution == {"buy": 3, "hold": 2}
    assert rep.modal_verdict == "buy"
    assert decision_stability_label(rep) == "BORDERLINE (leaning BUY, 3/5)"
    banner = decision_stability_banner(rep)
    assert banner is not None and "BUY 3 / HOLD 2" in banner and "5 replays" in banner


# --------------------------------------------------------------------------- #
# The core guarantee: upstream runs ONCE, Decision runs N times
# --------------------------------------------------------------------------- #
def test_upstream_runners_invoked_exactly_once_decision_n_times():
    specialist = _CountingRunner(_bullish())
    critic = _CountingRunner(CriticOutput(counter_thesis="c"))
    decision = _CountingRunner(_dec(Recommendation.BUY, 0.8))
    runners = _runners(decision, adapter_specialist=specialist, critic=critic)

    run_decision_n(ticker="AAA", strategy=STRATEGY, adapter=_FakeAdapter(),
                   runners=runners, n=5)

    # FOUR specialists -> one pipeline pass = 4 specialist calls; critic once.
    assert specialist.calls == 4          # NOT 4 * 5 — proves no pipeline re-run
    assert critic.calls == 1              # NOT 5
    assert decision.calls == 5            # only the Decision node repeats


# --------------------------------------------------------------------------- #
# Per-replay isolation: each Decision replay gets an independent deep-copy
# --------------------------------------------------------------------------- #
def test_post_critic_snapshot_is_isolated_per_replay():
    upstream = build_upstream_council(
        _FakeAdapter(), STRATEGY,
        {"specialist": _CountingRunner(_bullish()),
         "critic": _CountingRunner(CriticOutput(counter_thesis="c"))})
    snapshot = ResearchState.model_validate(
        upstream.invoke(ResearchState(ticker="AAA", strategy_id=STRATEGY.id)))
    # Upstream stops BEFORE the Decision node.
    assert snapshot.decision is None
    assert len(snapshot.specialist_opinions) == 4 and snapshot.critic_report

    a = snapshot.model_copy(deep=True)
    b = snapshot.model_copy(deep=True)
    a.specialist_opinions.clear()                 # mutate one replay's copy
    a.critic_report = None
    assert len(b.specialist_opinions) == 4        # the other is untouched
    assert b.critic_report is not None
    assert len(snapshot.specialist_opinions) == 4  # and so is the shared snapshot


# --------------------------------------------------------------------------- #
# Gated short-circuit: deterministic outcome -> n=1, Decision NOT replayed
# --------------------------------------------------------------------------- #
def test_gated_outcome_short_circuits_to_one_replay():
    decision = _CountingRunner(_dec(Recommendation.BUY, 0.9))   # gate caps BUY->SELL
    runners = _runners(decision)
    rep = run_decision_n(ticker="LMT", strategy=STRATEGY,
                         adapter=_ShortStreakAdapter(), runners=runners, n=5)
    assert rep.stability == "deterministic" and rep.gated is True
    assert rep.n_run == 1
    assert rep.modal_verdict == "sell"           # capped by the gate
    assert decision.calls == 1                    # NOT replayed 5x
    assert decision_stability_label(rep) == "STABLE (gated) SELL"
    assert decision_stability_banner(rep) is None


# --------------------------------------------------------------------------- #
# Machine-readable summary
# --------------------------------------------------------------------------- #
def test_decision_stability_summary_shape():
    cyc = _CyclingRunner([_dec(Recommendation.BUY, 0.62),
                          _dec(Recommendation.HOLD, 0.60),
                          _dec(Recommendation.BUY, 0.64),
                          _dec(Recommendation.HOLD, 0.58),
                          _dec(Recommendation.BUY, 0.61)])
    rep = run_decision_n(ticker="GOOGL", strategy=STRATEGY,
                         adapter=_FakeAdapter(), runners=_runners(cyc), n=5)
    summary = decision_stability_summary(rep)
    assert summary["stability"] == "BORDERLINE"
    assert summary["modal_verdict"] == "buy"
    assert summary["verdict_distribution"] == {"buy": 3, "hold": 2}
    assert summary["n"] == 5 and summary["gated"] is False
