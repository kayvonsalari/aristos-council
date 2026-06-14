"""Criterion registry — the generalization of the hardcoded dividend screen.

A *criterion* is a named, pure, deterministic function with a uniform signature:
it reads the run's ``Evidence`` (the inputs ``gather`` collected) plus its
``threshold``, and returns a ``CriterionResult`` (name, observed, threshold,
``passed``: true/false/null, note). Strategy YAMLs SELECT criteria by name and
parameterize their thresholds; the generic ``run_screen`` looks each up here and
assembles the result list — there is no strategy-specific logic in the runner.

This module adds NO new math. The four dividend criteria delegate to the
existing, unit-tested primitives in ``tools/screening.py``, so the assembled
screen is BYTE-IDENTICAL to the original ``run_dividend_aristocrat_screen`` —
pinned by the equivalence test (tests/test_criteria_registry.py). Behavior
preserved exactly: three-valued ``passed``, derived-yield, the streak
floor/lower-bound, and the no-current-dividend determinations.

Each criterion also declares the ``Evidence`` it requires and the valid range of
its threshold, so a strategy can be validated UP FRONT (``validate_selections``)
— fail fast on an unknown criterion, an out-of-range threshold, or evidence the
run can't supply.

Adding a criterion (4B and beyond): write a pure ``fn(Evidence, threshold) ->
CriterionResult`` (do the math here or in tools/screening.py — never in an
agent), then add one ``Criterion(...)`` entry to ``_CRITERIA`` declaring its
name, required evidence, and threshold bounds. Strategies can then select it by
name; no runner changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ...data.adapter import DividendEvent, Fundamentals
from ..screening import (
    CriterionResult,
    ScreenResult,
    max_payout_criterion,
    min_growth_streak_criterion,
    min_market_cap_criterion,
    min_yield_criterion,
)


# --------------------------------------------------------------------------- #
# Evidence + criterion types
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Evidence:
    """The deterministic inputs a criterion may read (assembled by ``gather``).

    A criterion reads only what it needs; ``requires`` (below) names the fields
    that must be present for it to be evaluable at all.
    """

    fundamentals: Fundamentals | None = None
    dividends: list[DividendEvent] = field(default_factory=list)
    last_close: float | None = None


@dataclass(frozen=True)
class Criterion:
    """A registered screen criterion: a named pure function plus its contract."""

    name: str
    fn: Callable[[Evidence, float], CriterionResult]
    # Evidence fields that must be available for this criterion to evaluate.
    requires: tuple[str, ...] = ()
    # Inclusive threshold bounds (None = unbounded above).
    threshold_min: float = 0.0
    threshold_max: float | None = None


@dataclass(frozen=True)
class CriterionSelection:
    """A strategy's selection of a registered criterion and its threshold.

    Mirrors the loader's pydantic ``CriterionSpec`` (which is also accepted by
    ``run_screen`` — anything with ``.name`` and ``.threshold`` works)."""

    name: str
    threshold: float


# --------------------------------------------------------------------------- #
# The four dividend criteria — thin adapters over tools/screening primitives.
# Behavior is delegated unchanged; only the call shape is uniform here.
# --------------------------------------------------------------------------- #
def _min_dividend_yield(ev: Evidence, threshold: float) -> CriterionResult:
    return min_yield_criterion(ev.fundamentals, min_yield=threshold,
                               last_close=ev.last_close)


def _max_payout_ratio(ev: Evidence, threshold: float) -> CriterionResult:
    return max_payout_criterion(ev.fundamentals, max_payout=threshold)


def _min_market_cap(ev: Evidence, threshold: float) -> CriterionResult:
    return min_market_cap_criterion(ev.fundamentals, min_market_cap=threshold)


def _min_dividend_growth_streak(ev: Evidence, threshold: float) -> CriterionResult:
    return min_growth_streak_criterion(ev.dividends, min_years=int(threshold))


_CRITERIA: tuple[Criterion, ...] = (
    Criterion("min_dividend_yield", _min_dividend_yield,
              requires=("fundamentals",), threshold_min=0.0, threshold_max=1.0),
    Criterion("max_payout_ratio", _max_payout_ratio,
              requires=("fundamentals",), threshold_min=0.0),
    Criterion("min_market_cap", _min_market_cap,
              requires=("fundamentals",), threshold_min=0.0),
    Criterion("min_dividend_growth_streak", _min_dividend_growth_streak,
              requires=("dividends",), threshold_min=0.0),
)

REGISTRY: dict[str, Criterion] = {c.name: c for c in _CRITERIA}

# Evidence kinds the gather pipeline supplies to every screen (values may be
# None/empty, but the KIND is available — the runtime data-quality handling lives
# in the criteria themselves). Used by validate_selections for fail-fast.
AVAILABLE_EVIDENCE: tuple[str, ...] = ("fundamentals", "dividends", "last_close")


# --------------------------------------------------------------------------- #
# Validation (fail fast, up front)
# --------------------------------------------------------------------------- #
def validate_selections(
    selections, available: tuple[str, ...] = AVAILABLE_EVIDENCE
) -> list[str]:
    """Return a list of problems with a strategy's criterion selections.

    Empty list == valid. Flags unknown criterion names, thresholds outside the
    criterion's declared bounds, and required evidence the run can't supply.
    """
    problems: list[str] = []
    avail = set(available)
    for sel in selections:
        crit = REGISTRY.get(sel.name)
        if crit is None:
            problems.append(f"unknown criterion '{sel.name}'")
            continue
        if sel.threshold < crit.threshold_min or (
            crit.threshold_max is not None and sel.threshold > crit.threshold_max
        ):
            hi = "∞" if crit.threshold_max is None else crit.threshold_max
            problems.append(
                f"{sel.name} threshold {sel.threshold} out of range "
                f"[{crit.threshold_min}, {hi}]"
            )
        missing = [r for r in crit.requires if r not in avail]
        if missing:
            problems.append(
                f"{sel.name} requires evidence not available: "
                f"{', '.join(missing)}"
            )
    return problems


# --------------------------------------------------------------------------- #
# Generic screen runner
# --------------------------------------------------------------------------- #
def run_screen(selections, evidence: Evidence, *, ticker: str) -> ScreenResult:
    """Run each selected criterion against the evidence and assemble the result.

    ``selections`` is any iterable of objects with ``.name`` and ``.threshold``
    (the loader's ``CriterionSpec`` or ``CriterionSelection``). The assembly —
    result order and the ``unverifiable:<name>:<note>`` flags — mirrors the
    original ``run_dividend_aristocrat_screen`` exactly.
    """
    results: list[CriterionResult] = []
    for sel in selections:
        crit = REGISTRY.get(sel.name)
        if crit is None:
            raise KeyError(f"unknown criterion '{sel.name}'")
        results.append(crit.fn(evidence, sel.threshold))

    flags = [f"unverifiable:{c.name}:{c.note}"
             for c in results if c.passed is None]
    return ScreenResult(ticker=ticker, criteria=results, flags=flags)
