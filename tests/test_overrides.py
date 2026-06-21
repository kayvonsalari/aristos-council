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


# v1 now GATES the streak by default (the former v2 collapsed into it). A
# NON-gating variant — needed to test turning the gate ON — is built by overriding
# v1 with is_gating False, since there is no longer a shipped non-gating strategy.
def _v1_nongating():
    return effective_strategy(_v1(), is_gating={STREAK: False})


# --- pure function: applies the override, never mutates the base ------------- #
def test_effective_strategy_applies_overrides():
    # Start from the non-gating variant so turning the gate ON is a real change.
    eff = effective_strategy(_v1_nongating(), partial_pass_allows_hold=False,
                             is_gating={STREAK: True})
    assert eff.policy.partial_pass_allows_hold is False
    assert {c.name for c in eff.criteria if c.is_gating} == {STREAK}


def test_effective_strategy_does_not_mutate_base():
    base = _v1()                                   # v1: partial True, streak GATING
    effective_strategy(base, partial_pass_allows_hold=False,
                       is_gating={STREAK: False})
    # the source object is untouched — no mutation leak to the on-disk strategy
    assert base.policy.partial_pass_allows_hold is True
    assert {c.name for c in base.criteria if c.is_gating} == {STREAK}


def test_no_override_is_a_noop():
    base = _v1()
    assert applied_overrides(base, effective_strategy(base)) == {}


# --- applied_overrides records only REAL diffs vs the file ------------------- #
def test_applied_overrides_records_the_delta():
    base = _v1()                                   # streak GATING by default
    eff = effective_strategy(base, partial_pass_allows_hold=False,
                             is_gating={STREAK: False})          # relax the gate
    assert applied_overrides(base, eff) == {
        "partial_pass_allows_hold": False,
        f"criteria.{STREAK}.is_gating": False,
    }


def test_toggling_back_to_file_value_records_nothing():
    base = _v1()                                   # partial True, streak GATING
    same = effective_strategy(base, partial_pass_allows_hold=True,
                              is_gating={STREAK: True})          # == the file values
    assert applied_overrides(base, same) == {}


# --- the override actually DRIVES the gate (reuses the 5442d1c ceiling) ------ #
def test_override_turns_the_gate_OFF_for_v1():
    eff = effective_strategy(_v1(), is_gating={STREAK: False})  # v1 gates by default
    gating = {c.name for c in eff.criteria if c.is_gating}
    screen = [{"name": STREAK, "passed": False}]                # confirmed fail
    assert disposition_ceiling(screen, gating) is None          # gate removed


def test_override_turns_the_gate_ON_from_a_nongating_base():
    # The knob works in both directions: start non-gating, turn the gate back ON.
    eff = effective_strategy(_v1_nongating(), is_gating={STREAK: True})
    gating = {c.name for c in eff.criteria if c.is_gating}
    screen = [{"name": STREAK, "passed": False}]
    assert disposition_ceiling(screen, gating) is Recommendation.SELL


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


# --- threshold overrides (CLI override matrix; same effective_strategy path) -- #
def test_effective_strategy_applies_threshold_override():
    eff = effective_strategy(_v1(), thresholds={STREAK: 25.0})
    assert {c.name: c.threshold for c in eff.criteria}[STREAK] == 25.0


def test_threshold_override_does_not_mutate_base():
    base = _v1()
    before = {c.name: c.threshold for c in base.criteria}[STREAK]   # 20 from the file
    effective_strategy(base, thresholds={STREAK: 25.0})
    assert {c.name: c.threshold for c in base.criteria}[STREAK] == before


def test_applied_overrides_records_threshold_delta():
    base = _v1()
    eff = effective_strategy(base, thresholds={STREAK: 25.0})
    assert applied_overrides(base, eff) == {f"criteria.{STREAK}.threshold": 25.0}


def test_threshold_override_to_the_file_value_records_nothing():
    base = _v1()                                   # streak threshold is 20
    same = effective_strategy(base, thresholds={STREAK: 20.0})
    assert applied_overrides(base, same) == {}     # 20.0 == 20 -> no real diff


def test_threshold_override_unknown_criterion_is_a_noop():
    base = _v1()
    eff = effective_strategy(base, thresholds={"nonexistent_criterion": 99.0})
    assert applied_overrides(base, eff) == {}
