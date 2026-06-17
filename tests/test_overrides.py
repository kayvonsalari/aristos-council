"""Ephemeral per-run override tests.

An override produces an in-memory effective Strategy from the immutable base, the
base is never mutated, the delta is recorded on the report/verdict, and the
override actually drives the deterministic gate (reusing disposition_ceiling).
The flip-suppression and no-baseline behaviour are pinned in test_persistence.py.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.agents.disposition import disposition_ceiling
from aristos_council.persistence.reports import report_from_state
from aristos_council.persistence.verdicts import record_from_state
from aristos_council.state import Recommendation, ResearchState
from aristos_council.strategy.loader import load_strategy
from aristos_council.strategy.overrides import applied_overrides, effective_strategy

_STRAT = Path(__file__).resolve().parents[1] / "strategies"
STREAK = "min_dividend_growth_streak"


def _v1():
    return load_strategy(_STRAT / "dividend_aristocrats_v1.yaml")


def _v2():
    return load_strategy(_STRAT / "dividend_aristocrats_v2.yaml")


# --- pure function: applies the override, never mutates the base ------------- #
def test_effective_strategy_applies_overrides():
    eff = effective_strategy(_v1(), partial_pass_allows_hold=False,
                             is_gating={STREAK: True})
    assert eff.policy.partial_pass_allows_hold is False
    assert {c.name for c in eff.criteria if c.is_gating} == {STREAK}


def test_effective_strategy_does_not_mutate_base():
    base = _v1()                                   # v1: partial True, no gating
    effective_strategy(base, partial_pass_allows_hold=False,
                       is_gating={STREAK: True})
    # the source object is untouched — no mutation leak to the on-disk strategy
    assert base.policy.partial_pass_allows_hold is True
    assert {c.name for c in base.criteria if c.is_gating} == set()


def test_no_override_is_a_noop():
    base = _v1()
    assert applied_overrides(base, effective_strategy(base)) == {}


# --- applied_overrides records only REAL diffs vs the file ------------------- #
def test_applied_overrides_records_the_delta():
    base = _v1()
    eff = effective_strategy(base, partial_pass_allows_hold=False,
                             is_gating={STREAK: True})
    assert applied_overrides(base, eff) == {
        "partial_pass_allows_hold": False,
        f"criteria.{STREAK}.is_gating": True,
    }


def test_toggling_back_to_file_value_records_nothing():
    base = _v1()                                   # partial True, streak not gating
    same = effective_strategy(base, partial_pass_allows_hold=True,
                              is_gating={STREAK: False})
    assert applied_overrides(base, same) == {}


# --- the override actually DRIVES the gate (reuses the 5442d1c ceiling) ------ #
def test_override_turns_the_gate_ON_for_v1():
    eff = effective_strategy(_v1(), is_gating={STREAK: True})   # v1 has no gating
    gating = {c.name for c in eff.criteria if c.is_gating}
    screen = [{"name": STREAK, "passed": False}]                # confirmed fail
    assert disposition_ceiling(screen, gating) is Recommendation.SELL


def test_override_turns_the_gate_OFF_for_v2():
    eff = effective_strategy(_v2(), is_gating={STREAK: False})  # v2 gates by default
    gating = {c.name for c in eff.criteria if c.is_gating}
    screen = [{"name": STREAK, "passed": False}]
    assert disposition_ceiling(screen, gating) is None          # gate removed


# --- the delta is recorded on BOTH sinks (reproducibility) ------------------ #
def test_report_and_verdict_record_the_applied_overrides():
    delta = {"partial_pass_allows_hold": False,
             f"criteria.{STREAK}.is_gating": True}
    s = ResearchState(ticker="X", strategy_id="dividend_aristocrats_v1",
                      applied_overrides=delta)
    assert report_from_state(s).applied_overrides == delta
    assert record_from_state(s).applied_overrides == delta


def test_default_run_records_empty_overrides():
    s = ResearchState(ticker="X", strategy_id="dividend_aristocrats_v1")
    assert report_from_state(s).applied_overrides == {}
    assert record_from_state(s).applied_overrides == {}
