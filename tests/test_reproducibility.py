"""Reproducibility harness — aggregation, stability flag, gated short-circuit.

All pure: FAKE RunOutcomes / a fake run_one. No LLM, no network. The harness's job
is to MEASURE verdict (in)stability honestly, never to hide a split.
"""

from __future__ import annotations

import pytest

from aristos_council.reproducibility import (
    RunOutcome,
    aggregate_outcomes,
    cost_guard_line,
    estimate_cost,
    format_stability,
    outcome_from_state,
    run_council_n,
    stability_csv_row,
)


def _o(verdict: str, conf: float, *, vetoes=(), gated=False) -> RunOutcome:
    return RunOutcome(verdict=verdict, confidence=conf, vetoes=tuple(vetoes),
                      gated=gated)


class _Sequence:
    """A run_one() that returns a scripted list of outcomes and counts calls."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self._outcomes[self.calls - 1]


# --------------------------------------------------------------------------- #
# Stability flag
# --------------------------------------------------------------------------- #
def test_five_identical_runs_are_stable():
    seq = _Sequence([_o("buy", 0.7) for _ in range(5)])
    rep = run_council_n(seq, ticker="AAA", n=5)
    assert seq.calls == 5
    assert rep.stability == "stable"
    assert rep.distribution == {"buy": 5}
    assert rep.modal_verdict == "buy"
    assert rep.n_run == 5


def test_split_runs_are_borderline():
    seq = _Sequence([_o("buy", 0.62), _o("hold", 0.60), _o("buy", 0.64),
                     _o("hold", 0.58), _o("buy", 0.61)])
    rep = run_council_n(seq, ticker="GOOGL", n=5)
    assert seq.calls == 5
    assert rep.stability == "BORDERLINE"
    assert rep.distribution == {"buy": 3, "hold": 2}
    assert rep.modal_verdict == "buy"            # 3 > 2
    assert "BORDERLINE" in format_stability(rep)


def test_gated_first_run_short_circuits_to_one_run():
    # A gated first result (SELL cap / INSUFFICIENT_EVIDENCE) is deterministic —
    # the harness must NOT spend the remaining n-1 runs re-confirming it.
    seq = _Sequence([_o("sell", 0.9, gated=True)]
                    + [_o("sell", 0.9, gated=True) for _ in range(4)])
    rep = run_council_n(seq, ticker="LMT", n=5)
    assert seq.calls == 1                         # short-circuited
    assert rep.stability == "deterministic"
    assert rep.n_run == 1 and rep.gated is True
    assert rep.modal_verdict == "sell"
    assert "deterministic" in format_stability(rep)
    assert "4 extra runs skipped" in format_stability(rep)


def test_insufficient_evidence_first_run_also_short_circuits():
    seq = _Sequence([_o("insufficient_evidence", 0.3, gated=True)]
                    + [_o("insufficient_evidence", 0.3, gated=True) for _ in range(4)])
    rep = run_council_n(seq, ticker="000660.KS", n=5)
    assert seq.calls == 1 and rep.stability == "deterministic"


# --------------------------------------------------------------------------- #
# Distribution / confidence stats / modal tie-break
# --------------------------------------------------------------------------- #
def test_confidence_mean_stdev_and_range():
    outs = [_o("buy", 0.60), _o("buy", 0.70), _o("buy", 0.80)]
    rep = aggregate_outcomes("AAA", outs, n_requested=3)
    assert abs(rep.confidence_mean - 0.70) < 1e-9
    assert rep.confidence_min == 0.60 and rep.confidence_max == 0.80
    # population stdev of [0.6,0.7,0.8] = sqrt(((.1)^2+0+(.1)^2)/3) ~ 0.08165
    assert abs(rep.confidence_stdev - 0.081649658) < 1e-6


def test_modal_tie_break_is_deterministic():
    # 2 buy / 2 hold -> tie; broken by verdict name ("buy" < "hold") for stability.
    outs = [_o("buy", 0.6), _o("hold", 0.6), _o("buy", 0.6), _o("hold", 0.6)]
    rep = aggregate_outcomes("AAA", outs, n_requested=4)
    assert rep.stability == "BORDERLINE"
    assert rep.modal_verdict == "buy"
    assert rep.distribution == {"buy": 2, "hold": 2}


def test_veto_union_collected_across_runs():
    outs = [_o("hold", 0.6, vetoes=("low_confidence",)),
            _o("hold", 0.6, vetoes=("specialist_conflict",)),
            _o("hold", 0.6)]
    rep = aggregate_outcomes("AAA", outs, n_requested=3)
    assert rep.veto_union == ("low_confidence", "specialist_conflict")


def test_aggregate_rejects_empty():
    with pytest.raises(ValueError):
        aggregate_outcomes("AAA", [], n_requested=5)


def test_run_council_n_rejects_zero_n():
    with pytest.raises(ValueError):
        run_council_n(_Sequence([_o("buy", 0.7)]), ticker="AAA", n=0)


# --------------------------------------------------------------------------- #
# Cost guard + CSV row
# --------------------------------------------------------------------------- #
def test_cost_estimate_and_guard_line():
    assert abs(estimate_cost(5) - 0.95) < 1e-9
    line = cost_guard_line(5)
    assert "5 runs" in line and "$0.95" in line


def test_stability_csv_row_preserves_split():
    seq = _Sequence([_o("buy", 0.62), _o("hold", 0.60), _o("buy", 0.64),
                     _o("hold", 0.58), _o("buy", 0.61)])
    rep = run_council_n(seq, ticker="GOOGL", n=5)
    row = stability_csv_row(rep)
    assert row["ticker"] == "GOOGL"
    assert row["stability"] == "BORDERLINE"
    assert row["modal_verdict"] == "buy"
    assert row["distribution"] == "buy:3;hold:2"   # split preserved, not collapsed
    assert row["n_run"] == 5


# --------------------------------------------------------------------------- #
# outcome_from_state — extraction incl. the gated detection
# --------------------------------------------------------------------------- #
def test_outcome_from_state_detects_gate_override_as_gated():
    from aristos_council.state import Decision, Recommendation, ResearchState

    state = ResearchState(ticker="LMT", strategy_id="growth_v1")
    state.decision = Decision(
        recommendation=Recommendation.SELL, confidence=0.8, rationale="capped",
        gate_override_applied=True, original_recommendation=Recommendation.HOLD)
    out = outcome_from_state(state)
    assert out.verdict == "sell" and out.gated is True


def test_outcome_from_state_clean_buy_is_not_gated():
    from aristos_council.state import Decision, Recommendation, ResearchState

    state = ResearchState(ticker="GOOGL", strategy_id="growth_v1")
    state.decision = Decision(recommendation=Recommendation.BUY, confidence=0.62,
                              rationale="clean")
    out = outcome_from_state(state)
    assert out.verdict == "buy" and out.gated is False
