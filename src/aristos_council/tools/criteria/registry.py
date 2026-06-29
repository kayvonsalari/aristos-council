"""Criterion registry — the generalization of the hardcoded dividend screen.

A *criterion* is a named, pure, deterministic function with a uniform signature:
it reads the run's ``Evidence`` (the inputs ``gather`` collected) plus its
``threshold``, and returns a ``CriterionResult`` (name, observed, threshold,
``passed``: true/false/null, note). Strategy YAMLs SELECT criteria by name and
parameterize their thresholds; the generic ``run_screen`` looks each up here and
assembles the result list — there is no strategy-specific logic in the runner.

This module adds NO new math. The four dividend criteria delegate to the
existing, unit-tested primitives in ``tools/screening.py``, so the assembled
screen is BYTE-IDENTICAL to the frozen reference ``run_strategy_screen`` (formerly
``run_dividend_aristocrat_screen``) —
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
    peg_with_earnings_growth,
    revenue_cagr,
    through_cycle_roic,
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
    # Provider's declared streak DATA-SHAPE method (Option A), riding along with the
    # dividends it describes. Default matches yfinance so the legacy/equivalence
    # paths are unchanged; ``gather`` populates it from
    # ``adapter.dividend_streak_method``.
    streak_method: str = "per_payment_median"
    # Trailing PRICE MOMENTUM (total return), computed from the price closes already
    # fetched. The value+momentum signal the momentum criterion reads. None when
    # history is too short. Decimals: +0.15 == +15%; -0.40 == a 40% drawdown.
    return_6m: float | None = None
    return_12m: float | None = None


@dataclass(frozen=True)
class ParamSpec:
    """Self-description of a parameter a strategy sets for a criterion.

    Enough for a UI to render the right input widget per parameter WITHOUT any
    strategy-specific code: name, type, and (for numerics) sensible bounds/step.
    Declared in 4A (and tested); the dynamic Strategy tab reads it in 4B.
    """

    name: str
    type: str                      # "float" | "int" | "bool"
    min: float | None = None       # numeric lower bound (inclusive)
    max: float | None = None       # numeric upper bound (inclusive); None = ∞
    step: float | None = None      # UI step for numerics
    default: object = None          # default value (UI pre-fill; also the
                                    # source for params not set by a strategy)


@dataclass(frozen=True)
class Criterion:
    """A registered screen criterion: a named pure function plus its contract
    and self-description (display label + parameter specs)."""

    name: str
    fn: Callable[[Evidence, float], CriterionResult]
    label: str                     # human display label, e.g. "Minimum dividend yield"
    params: tuple[ParamSpec, ...]  # parameters a strategy sets (threshold, flags)
    # Evidence kinds (fundamentals / dividends / last_close) that must be
    # available for this criterion to evaluate.
    requires: tuple[str, ...] = ()
    # Specific Fundamentals fields this criterion's reasoning relates to. Used to
    # SCOPE the agent evidence packet (Sprint 4D): only the active strategy's
    # consumed fields (plus a fixed core) are rendered, so dividend fields don't
    # leak into growth runs. Display-only — the ledger keeps the full object.
    fundamentals_fields: tuple[str, ...] = ()

    @property
    def threshold_param(self) -> ParamSpec | None:
        return next((p for p in self.params if p.name == "threshold"), None)


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
    return min_growth_streak_criterion(
        ev.dividends, min_years=int(threshold), method=ev.streak_method)


# --- Growth / quality criteria (Sprint 4B; hardened post-SK-Hynix) ------- #
# In-house revenue-CAGR window. Single source of truth: the years ParamSpec
# default below references it, and both the CAGR and PEG criteria compute over
# it (years is not yet a per-strategy YAML param — that's a 4C UI concern).
_REVENUE_CAGR_YEARS = 3
# ROIC through-cycle window — average operating income over this many years so a
# single peak year can't overstate the return (SK Hynix peak-OI ROIC).
_ROIC_WINDOW = 4

# GATING-ELIGIBILITY (decided, not yet wired): only min_revenue_cagr is eligible
# to be set is_gating — and only in its robust log-linear-trend form (a clean,
# trough-resistant denominator). max_peg_ratio and min_roic stay NON-GATING: their
# denominators are CONTESTABLE (PEG dies on negative earnings; ROIC's invested-
# capital base is arguable), and a contestable metric as a deterministic gate would
# recreate the master-key escape-hatch problem in reverse. Turning revenue_cagr's
# gate ON is a later, separate call — no is_gating flag is set here.


def _latest(series: list[float]):
    return series[0] if series else None


def _min_revenue_cagr(ev: Evidence, threshold: float) -> CriterionResult:
    revenue = ev.fundamentals.total_revenue if ev.fundamentals else []
    cagr, note = revenue_cagr(revenue, _REVENUE_CAGR_YEARS)   # robust trend CAGR
    if cagr is None:
        return CriterionResult(name="min_revenue_cagr", passed=None,
                               observed=None, threshold=threshold, note=note)
    return CriterionResult(name="min_revenue_cagr", passed=cagr >= threshold,
                           observed=cagr, threshold=threshold, note=note)


def _min_roic(ev: Evidence, threshold: float) -> CriterionResult:
    f = ev.fundamentals
    if f is None:
        return CriterionResult(name="min_roic", passed=None, observed=None,
                               threshold=threshold, note="no fundamentals")
    roic, note = through_cycle_roic(f.operating_income, f.tax_provision,
                                    f.pretax_income, f.invested_capital,
                                    window=_ROIC_WINDOW)
    if roic is None:
        return CriterionResult(name="min_roic", passed=None, observed=None,
                               threshold=threshold, note=note)
    return CriterionResult(name="min_roic", passed=roic >= threshold,
                           observed=roic, threshold=threshold, note=note)


# The momentum criterion's registry name — referenced by the matrix, which gives
# momentum a SIGNED, magnitude-scaled contribution (not the standard pass/fail margin).
PRICE_MOMENTUM_CRITERION = "min_price_momentum"


def _min_price_momentum(ev: Evidence, threshold: float) -> CriterionResult:
    """12-month trailing price momentum vs a floor (default 0.0 = 'not in a
    downtrend'). The market's forward vote: a falling knife is, by definition,
    falling. NON-GATING by default — it DRAGS the matrix score (a -40% name loses a
    lot), it doesn't hard-veto, so a value strategy may still buy a modest dip.
    NOT-EVAL on short history (honest abstain), exactly like the other criteria."""
    r = ev.return_12m
    if r is None:
        return CriterionResult(
            name=PRICE_MOMENTUM_CRITERION, passed=None, observed=None,
            threshold=threshold,
            note="12m price momentum unavailable: insufficient price history")
    return CriterionResult(
        name=PRICE_MOMENTUM_CRITERION, passed=r >= threshold, observed=r,
        threshold=threshold,
        note=f"12m price momentum {r:+.1%} vs floor {threshold:+.1%}")


def _max_peg_ratio(ev: Evidence, threshold: float) -> CriterionResult:
    f = ev.fundamentals
    # PEG denominator is OPERATING-INCOME growth (the earnings-growth proxy), with a
    # documented revenue-CAGR fallback when the OI series is too short; winsor cap
    # and the P/E / growth<=0 abstentions live inside peg_with_earnings_growth.
    peg, note, must_fail = peg_with_earnings_growth(
        f.pe_ratio if f else None,
        f.operating_income if f else [],
        f.total_revenue if f else [],
        _REVENUE_CAGR_YEARS,
    )
    if must_fail:
        # Growth was COMPUTED and is non-positive (not growing) -> a real FAIL, not
        # NOT-EVAL. Covers both earnings present-but-declining (FIX-1b) AND the
        # fallback-onto-declining-revenue path (FIX-1c, LMT's actual path). A
        # NOT-EVAL here got laundered into a HOLD by partial_pass_allows_hold.
        return CriterionResult(name="max_peg_ratio", passed=False, observed=None,
                               threshold=threshold, note=note)
    if peg is None:
        return CriterionResult(name="max_peg_ratio", passed=None, observed=None,
                               threshold=threshold, note=note)
    return CriterionResult(name="max_peg_ratio", passed=peg <= threshold,
                           observed=peg, threshold=threshold, note=note)


# Every criterion exposes a per-criterion "unverifiable blocks" bool (the
# successor to the old strategy-wide unverifiable_streak_is_blocking flag).
_UNVERIFIABLE_BLOCKS = ParamSpec("unverifiable_blocks", type="bool", default=False)

_CRITERIA: tuple[Criterion, ...] = (
    # --- Dividend criteria (Sprint 4A) ---
    Criterion(
        "min_dividend_yield", _min_dividend_yield,
        label="Minimum dividend yield",
        params=(ParamSpec("threshold", "float", min=0.0, max=1.0, step=0.005,
                          default=0.025),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("dividend_per_share", "dividend_yield"),
    ),
    Criterion(
        "max_payout_ratio", _max_payout_ratio,
        label="Maximum payout ratio",
        params=(ParamSpec("threshold", "float", min=0.0, max=None, step=0.05,
                          default=0.75),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("payout_ratio", "dividend_per_share"),
    ),
    Criterion(
        "min_market_cap", _min_market_cap,
        label="Minimum market cap (USD)",
        params=(ParamSpec("threshold", "float", min=0.0, max=None, step=1e9,
                          default=10_000_000_000),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("market_cap",),
    ),
    Criterion(
        "min_dividend_growth_streak", _min_dividend_growth_streak,
        label="Minimum dividend-growth streak (years)",
        params=(ParamSpec("threshold", "int", min=0.0, max=None, step=1.0,
                          default=25),
                _UNVERIFIABLE_BLOCKS),
        requires=("dividends",),
        fundamentals_fields=("years_dividend_growth",),
    ),
    # --- Growth / quality criteria (Sprint 4B) ---
    Criterion(
        "min_revenue_cagr", _min_revenue_cagr,
        label="Minimum revenue CAGR",
        params=(ParamSpec("years", "int", min=1, max=None, step=1.0,
                          default=_REVENUE_CAGR_YEARS),
                ParamSpec("threshold", "float", min=0.0, max=1.0, step=0.01,
                          default=0.10),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("total_revenue",),
    ),
    Criterion(
        "min_roic", _min_roic,
        label="Minimum ROIC",
        params=(ParamSpec("threshold", "float", min=0.0, max=1.0, step=0.01,
                          default=0.12),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("operating_income", "ebit", "tax_provision",
                             "pretax_income", "invested_capital"),
    ),
    Criterion(
        "max_peg_ratio", _max_peg_ratio,
        label="Maximum PEG ratio",
        # NB: PEG divides P/E by the SAME in-house revenue-CAGR window the
        # revenue criterion uses (the _REVENUE_CAGR_YEARS module constant, read
        # by BOTH _min_revenue_cagr and _max_peg_ratio — one source of truth, can
        # never diverge). So the window is NOT a PEG parameter; it is surfaced
        # ONCE, under min_revenue_cagr, not redundantly here.
        params=(ParamSpec("threshold", "float", min=0.0, max=None, step=0.1,
                          default=2.0),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("total_revenue", "pe_ratio"),
    ),
    # --- Price momentum (value+momentum) — fixes cheap-falling-knife false BUYs ---
    Criterion(
        PRICE_MOMENTUM_CRITERION, _min_price_momentum,
        label="Minimum 12m price momentum",
        # Floor is a return; the loader caps thresholds at >= 0, so 0.0 ('not in a
        # downtrend') is the natural floor. Reads ev.return_12m (computed from price
        # closes already fetched), so it needs no fundamentals/dividends evidence.
        params=(ParamSpec("threshold", "float", min=0.0, max=1.0, step=0.01,
                          default=0.0),
                _UNVERIFIABLE_BLOCKS),
        requires=(),
        fundamentals_fields=(),
    ),
)


def consumed_fundamentals_fields(selections) -> set[str]:
    """Union of the Fundamentals fields the selected criteria relate to.

    Used to scope the agent evidence packet to the active strategy (Sprint 4D).
    Unknown selections are ignored (the loader already validated names)."""
    out: set[str] = set()
    for sel in selections:
        crit = REGISTRY.get(sel.name)
        if crit is not None:
            out.update(crit.fundamentals_fields)
    return out


def required_evidence(selections) -> set[str]:
    """Union of the Evidence KINDS the selected criteria require.

    Drives strategy-scoped tool selection (Sprint 4E): gather only invokes a
    data-gathering tool when the active strategy actually needs its evidence —
    e.g. get_dividend_history runs only when some criterion requires
    'dividends'. Unknown selections are ignored (the loader validated names)."""
    out: set[str] = set()
    for sel in selections:
        crit = REGISTRY.get(sel.name)
        if crit is not None:
            out.update(crit.requires)
    return out

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
        tp = crit.threshold_param
        if tp is not None and (
            (tp.min is not None and sel.threshold < tp.min)
            or (tp.max is not None and sel.threshold > tp.max)
        ):
            lo = "-∞" if tp.min is None else tp.min
            hi = "∞" if tp.max is None else tp.max
            problems.append(
                f"{sel.name} threshold {sel.threshold} out of range "
                f"[{lo}, {hi}]"
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
    frozen ``run_strategy_screen`` reference exactly.
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
