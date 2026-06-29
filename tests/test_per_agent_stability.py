"""Per-agent stability diagnostic — record EVERY agent's output across N full runs
and locate WHICH layer the verdict wobble lives in (specialists / Critic / Decision).

Deterministic: fake adapter + fake runners, no network/LLM. The specialist runner is
shared and invoked once per specialist per run in FUNDAMENTAL, TECHNICAL, SENTIMENT,
RISK order, so a flat scripted list maps cleanly to (run, specialist).
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
from aristos_council.reproducibility import (
    AgentRunRecord,
    aggregate_per_agent,
    format_per_agent_table,
    per_agent_csv_row,
    run_per_agent_n,
)
from aristos_council.state import Recommendation, Stance
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
        return [DividendEvent(ex_date=date(1995 + i, 6, 1), amount=1.0 + 0.05 * i)
                for i in range(30)]                          # rising -> not gated

    def get_price_history(self, ticker, *, start, end):
        bars = [PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                         close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=1000)
                for i in range(220)]
        return PriceHistory(ticker=ticker, bars=bars)


class _ShortStreakAdapter(_FakeAdapter):
    def get_dividend_history(self, ticker, *, start, end):
        return [DividendEvent(ex_date=date(2018 + i, 6, 1), amount=1.0 + 0.1 * i)
                for i in range(6)]                           # confirmed fail -> gated


class _ScriptedRunner:
    """Pops outputs in order; over-invoking raises (catches accidental extra calls)."""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0

    def invoke(self, system, user):
        out = self._outputs[self.calls]
        self.calls += 1
        return out


class _StaticRunner:
    def __init__(self, output):
        self._output = output
        self.calls = 0

    def invoke(self, system, user):
        self.calls += 1
        return self._output


class _CyclingRunner:
    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0

    def invoke(self, system, user):
        out = self._outputs[self.calls % len(self._outputs)]
        self.calls += 1
        return out


def _spec(stance):
    return SpecialistOutput(stance=stance, confidence=0.6, thesis="t")


def _spec_runner(per_run):
    """per_run: list of 4-tuples (F, T, S, R stances) — flattened to call order."""
    outs = [_spec(st) for run in per_run for st in run]
    return _ScriptedRunner(outs)


def _dec(rec):
    return DecisionOutput(recommendation=rec, confidence=0.62, rationale="r")


# --------------------------------------------------------------------------- #
# Wobble is DOWNSTREAM — specialists STABLE, only Decision wobbles
# --------------------------------------------------------------------------- #
def test_specialists_stable_decision_borderline():
    n = 5
    fixed = (Stance.BULLISH, Stance.BEARISH, Stance.NEUTRAL, Stance.BEARISH)
    runners = {
        "specialist": _spec_runner([fixed] * n),
        "critic": _StaticRunner(CriticOutput(counter_thesis="c")),
        "decision": _CyclingRunner([_dec(Recommendation.BUY),
                                    _dec(Recommendation.HOLD)]),
    }
    rep = run_per_agent_n(ticker="GOOGL", strategy=STRATEGY,
                          adapter=_FakeAdapter(), runners=runners, n=n)
    assert rep.n_run == 5
    by = {a.agent: a for a in rep.agents}
    assert by["fundamental"].label == "STABLE" and by["fundamental"].modal == "bullish"
    assert by["technical"].label == "STABLE" and by["technical"].modal == "bearish"
    assert by["sentiment"].label == "STABLE"
    assert by["risk"].label == "STABLE"
    assert by["decision"].label == "BORDERLINE"
    assert by["decision"].distribution == {"buy": 3, "hold": 2}
    assert "FINAL COMPRESSION" in rep.diagnosis


# --------------------------------------------------------------------------- #
# Wobble is UPSTREAM — sentiment specialist flips across runs
# --------------------------------------------------------------------------- #
def test_sentiment_specialist_wobbles_is_flagged_upstream():
    # sentiment alternates neutral/bearish across 5 runs -> 3 neutral / 2 bearish.
    per_run = [
        (Stance.BULLISH, Stance.BEARISH,
         Stance.NEUTRAL if i % 2 == 0 else Stance.BEARISH, Stance.BEARISH)
        for i in range(5)
    ]
    runners = {
        "specialist": _spec_runner(per_run),
        "critic": _StaticRunner(CriticOutput(counter_thesis="c")),
        "decision": _StaticRunner(_dec(Recommendation.HOLD)),
    }
    rep = run_per_agent_n(ticker="META", strategy=STRATEGY,
                          adapter=_FakeAdapter(), runners=runners, n=5)
    by = {a.agent: a for a in rep.agents}
    assert by["sentiment"].label == "WOBBLES"
    assert by["sentiment"].distribution == {"neutral": 3, "bearish": 2}
    assert by["fundamental"].label == "STABLE"
    assert "UPSTREAM" in rep.diagnosis and "sentiment" in rep.diagnosis


# --------------------------------------------------------------------------- #
# Capture reads from EACH run's state -> n records, full pipeline each time
# --------------------------------------------------------------------------- #
def test_per_agent_capture_is_one_record_per_full_run():
    n = 5
    spec = _spec_runner([(Stance.BULLISH,) * 4] * n)
    critic = _StaticRunner(CriticOutput(counter_thesis="c"))
    decision = _CyclingRunner([_dec(Recommendation.BUY)])
    runners = {"specialist": spec, "critic": critic, "decision": decision}
    rep = run_per_agent_n(ticker="AAA", strategy=STRATEGY,
                          adapter=_FakeAdapter(), runners=runners, n=n)
    assert rep.n_run == 5
    # FULL pipeline each run: 4 specialist calls x 5 runs, critic + decision x 5.
    assert spec.calls == 20 and critic.calls == 5 and decision.calls == 5
    by = {a.agent: a for a in rep.agents}
    assert sum(by["decision"].distribution.values()) == 5


# --------------------------------------------------------------------------- #
# Gated short-circuit
# --------------------------------------------------------------------------- #
def test_gated_outcome_short_circuits_per_agent():
    spec = _spec_runner([(Stance.BULLISH,) * 4])     # one run's worth only
    runners = {"specialist": spec,
               "critic": _StaticRunner(CriticOutput(counter_thesis="c")),
               "decision": _StaticRunner(_dec(Recommendation.BUY))}
    rep = run_per_agent_n(ticker="LMT", strategy=STRATEGY,
                          adapter=_ShortStreakAdapter(), runners=runners, n=5)
    assert rep.gated is True and rep.n_run == 1
    assert spec.calls == 4                            # exactly one pipeline pass
    assert "GATED" in rep.diagnosis


# --------------------------------------------------------------------------- #
# Aggregation correctness (pure, on fake records)
# --------------------------------------------------------------------------- #
def _rec(f, t, s, r, *, target, verdict, conf):
    return AgentRunRecord(
        specialists={"fundamental": ("bullish", f), "technical": ("bearish", t),
                     "sentiment": (s, 0.5), "risk": ("bearish", r)},
        critic_target=target, decision_verdict=verdict, decision_confidence=conf)


def test_aggregate_distribution_modal_meanstdev_and_boundaries():
    recs = [_rec(0.7, 0.6, "neutral", 0.6, target="bearish", verdict="buy", conf=0.60),
            _rec(0.8, 0.6, "neutral", 0.6, target="bearish", verdict="hold", conf=0.70),
            _rec(0.6, 0.6, "bearish", 0.6, target="bearish", verdict="buy", conf=0.80)]
    rep = aggregate_per_agent("AAA", recs, n_requested=3)
    by = {a.agent: a for a in rep.agents}
    # fundamental: always bullish -> STABLE; conf mean of [.7,.8,.6] = .7
    assert by["fundamental"].label == "STABLE"
    assert abs(by["fundamental"].confidence_mean - 0.70) < 1e-9
    assert abs(by["fundamental"].confidence_stdev - 0.081649658) < 1e-6
    # sentiment: neutral 2 / bearish 1 -> WOBBLES, modal neutral
    assert by["sentiment"].label == "WOBBLES"
    assert by["sentiment"].distribution == {"neutral": 2, "bearish": 1}
    assert by["sentiment"].modal == "neutral"
    # critic target all bearish -> STABLE, no confidence
    assert by["critic"].label == "STABLE" and by["critic"].confidence_mean is None
    # decision buy 2 / hold 1 -> BORDERLINE
    assert by["decision"].label == "BORDERLINE"
    assert by["decision"].distribution == {"buy": 2, "hold": 1}


def test_table_and_csv_row_shapes():
    recs = [_rec(0.7, 0.6, "neutral", 0.6, target="bullish", verdict="buy", conf=0.6),
            _rec(0.7, 0.6, "bearish", 0.6, target="neutral", verdict="hold", conf=0.6)]
    rep = aggregate_per_agent("META", recs, n_requested=2)
    table = format_per_agent_table(rep)
    assert "per-agent stability" in table and "sentiment" in table
    assert "targets:" in table                        # critic row prefixed
    row = per_agent_csv_row(rep)
    assert row["ticker"] == "META" and row["n_run"] == 2
    assert row["sentiment"] in ("bearish:1;neutral:1", "neutral:1;bearish:1")
    assert row["sentiment_stable"] == "WOBBLES"
    assert "critic" in row and "decision" in row and "diagnosis" in row
