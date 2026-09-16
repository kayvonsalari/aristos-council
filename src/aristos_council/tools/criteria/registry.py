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
    max_dividend_cuts_criterion,
    max_payout_fcf_criterion,
    CriterionResult,
    ScreenResult,
    accrual_ratio,
    altman_z_score,
    max_payout_criterion,
    min_growth_streak_criterion,
    min_market_cap_criterion,
    min_yield_criterion,
    peg_with_earnings_growth,
    piotroski_f_score,
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
    # ABSOLUTE valuation context (VALBAND-1): a ``tools.valuation_band.ValuationBand``
    # — today's EV/EBIT (or labelled P/E fallback) as a percentile of this name's OWN
    # 5-year monthly distribution. Deliberately typed loosely and defaulted to None:
    # it is computed on the RANK path (which fetches 5 years of prices), so a path that
    # doesn't compute it leaves it None and the criterion NOT-EVALs — the same honest
    # abstention every other criterion makes on a missing input, never a phantom fail.
    valuation_band: object | None = None


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
    # WHAT THE NUMBER MEANS (REPORT-1) — one of report_language.UNITS, or "" for a
    # bool flag. Reports format from this declaration, never by guessing at the render
    # site: 0.009547 with unit="percent" is "0.95%", and a criterion added tomorrow
    # reads correctly because it declared its unit, not because a render site
    # remembered it. ``currency`` names the money a "currency" unit is denominated in
    # (the min_market_cap floor is USD by its own contract — house rule 8 abstains on
    # a non-USD name rather than converting).
    unit: str = ""
    currency: str | None = None


@dataclass(frozen=True)
class Criterion:
    """A registered screen criterion: a named pure function plus its contract
    and self-description (display label + parameter specs)."""

    name: str
    fn: Callable[[Evidence, float], CriterionResult]
    # HUMAN RULE NAME (REPORT-1), e.g. "Dividend yield". Deliberately DIRECTION-FREE:
    # the direction lives in ``comparison`` and is rendered into the threshold phrase
    # ("at least 1.5%"), so a report never says "Minimum dividend yield at least 1.5%".
    # One source — a report never formats a rule name at the render site.
    label: str
    params: tuple[ParamSpec, ...]  # parameters a strategy sets (threshold, flags)
    # Which side of the threshold PASSES: "min" (observed >= threshold) or "max"
    # (observed <= threshold). Drives the plain-English threshold phrase and the
    # exclusion sentence's limit clause. Not new behaviour — it restates in data what
    # the criterion function has always done, so reports stop hardcoding it.
    # GLOSSARY-1 — the plain-English definition of the RULE, for the report's "What the
    # terms mean" section. Same contract as FactorDef.glossary: required, tested, and
    # never jargon inside a definition.
    glossary: str = ""
    comparison: str = "min"
    # The observation half of an exclusion sentence, e.g. "dividend yield {observed}".
    # Placeholders: ``{observed}`` (formatted by the threshold param's unit) and
    # ``{signed}`` (a direction + magnitude, "fell 14.0%"). Empty -> the report falls
    # back to "<label> {observed}", which is correct if plain.
    observation: str = ""
    # CRIT-NOCUT-1 — the RULE phrase, for a criterion whose threshold is not a limit on
    # the observed value. The generated phrase ("at most 5") assumes observed and
    # threshold share a unit; ``max_dividend_cuts`` measures a CUT SIZE against a WINDOW
    # of years, so the generated phrase would compare a percentage to a year count.
    # A ``{threshold}`` template here replaces it at every render site. Empty -> the
    # generated phrase, so every existing criterion is untouched.
    threshold_text: str = ""
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


def _max_payout_ratio_fcf(ev: Evidence, threshold: float) -> CriterionResult:
    return max_payout_fcf_criterion(ev.fundamentals, max_payout=threshold)


def _min_market_cap(ev: Evidence, threshold: float) -> CriterionResult:
    return min_market_cap_criterion(ev.fundamentals, min_market_cap=threshold)


def _min_dividend_growth_streak(ev: Evidence, threshold: float) -> CriterionResult:
    return min_growth_streak_criterion(
        ev.dividends, min_years=int(threshold), method=ev.streak_method)


def _max_dividend_cuts(ev: Evidence, threshold: float) -> CriterionResult:
    # CRIT-NOCUT-1: "was it ever cut?", not "did it rise?". The threshold is the NUMBER
    # OF YEARS to examine, not a count of permitted cuts — one cut fails.
    return max_dividend_cuts_criterion(ev.dividends, years=int(threshold))


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


def _min_dividend_streak(ev: Evidence, threshold: float) -> CriterionResult:
    """Consecutive years of dividend INCREASES (from Fundamentals.dividend_streak_years,
    derived by the adapter from the payment history) at or above a floor. Catches
    cut-history yield traps (T/MMM read 0). ABSTAINS on missing history — a non-payer
    is caught by min_dividend_yield, not this criterion."""
    f = ev.fundamentals
    s = f.dividend_streak_years if f else None
    if s is None:
        return CriterionResult(
            name="min_dividend_streak", passed=None, observed=None,
            threshold=threshold,
            note="dividend-growth streak unavailable (insufficient payment history)")
    return CriterionResult(
        name="min_dividend_streak", passed=s >= threshold, observed=float(s),
        threshold=threshold,
        note=f"{s}y consecutive dividend increases vs floor {int(threshold)}y")


def _min_f_score(ev: Evidence, threshold: float) -> CriterionResult:
    """Piotroski F-Score (0-9) at or above a floor — an accounting-quality screen.

    All nine checks come from ``screening.piotroski_f_score``, the SAME function the
    rankable ``piotroski_f_score`` factor calls (two registries, one arithmetic), so
    the screened and ranked values can never diverge. ABSTAINS (passed=None) when
    fewer than 5 of the 9 checks are computable — a partial tally presented as a score
    would read as a terrible company when it is actually a missing statement. The note
    carries the N/9 plus the unavailability accounting either way.

    NOT gating-eligible: the score aggregates nine checks whose availability depends on
    provider statement coverage, so a hard deterministic veto on it would fail names
    for data gaps. No ``is_gating`` flag is set or proposed.
    """
    result = piotroski_f_score(ev.fundamentals)
    if result.score is None:
        return CriterionResult(name="min_f_score", passed=None, observed=None,
                               threshold=threshold, note=result.note)
    return CriterionResult(name="min_f_score", passed=result.score >= threshold,
                           observed=float(result.score), threshold=threshold,
                           note=result.note)


def _valuation_band_percentile(ev: Evidence, threshold: float) -> CriterionResult:
    """Today's valuation as a percentile of this name's OWN 5-year band, vs a CEILING.

    The absolute counterpart to every relative value measure in the system: a cohort
    rank says "cheapest of these ten", this says "and it is still at the 92nd percentile
    of its own five-year range". Lower is better; the threshold is the highest percentile
    a strategy will accept.

    ALL arithmetic lives in ``tools.valuation_band.valuation_band`` — the SAME function
    the rankable ``valuation_band_percentile`` factor calls (two registries, one
    computation), so a screened and a ranked band can never diverge.

    ABSTAINS (passed=None, rule 3) whenever the band abstains and whenever the band was
    never computed on this path — the note carries the reason verbatim ("insufficient
    history: 1.4y", "no dated statement history …"). A recent IPO ABSTAINS; it is never
    failed for being young.

    NOT gating-eligible and selected by NO strategy in this PR: the denominator is a
    reconstructed history whose coverage varies by provider depth, which is exactly the
    contestable-denominator class (PEG/ROIC) that must never become a deterministic veto.
    """
    band = ev.valuation_band
    if band is None:
        return CriterionResult(
            name="valuation_band_percentile", passed=None, observed=None,
            threshold=threshold,
            note="valuation band not computed on this path (needs 5y price history)")
    pct = getattr(band, "percentile", None)
    note = getattr(band, "display", "") or getattr(band, "note", "")
    if pct is None:
        return CriterionResult(name="valuation_band_percentile", passed=None,
                               observed=None, threshold=threshold, note=note)
    return CriterionResult(name="valuation_band_percentile", passed=pct <= threshold,
                           observed=pct, threshold=threshold, note=note)


def _max_accrual_ratio(ev: Evidence, threshold: float) -> CriterionResult:
    """Sloan accrual ratio against a CEILING — an earnings-quality screen.

    All arithmetic comes from ``screening.accrual_ratio``, the SAME function the
    rankable ``accrual_ratio`` factor calls (two registries, one computation), so the
    screened and ranked values can never diverge. ABSTAINS (passed=None, rule 3) on a
    missing input or a non-positive asset base, carrying the reason verbatim.

    Gating-eligible? NOT decided here, and forensic_v1 sets no ``is_gating`` flag. The
    generic machinery would honour one — this is a statement about the EVIDENCE, not the
    plumbing: a high accrual ratio is a legitimate warning, and whether it is
    DISQUALIFYING is a call for the scoreboard to make, exactly as the 12% ROIC bar was.
    """
    r = accrual_ratio(ev.fundamentals)
    if r.value is None:
        return CriterionResult(name="max_accrual_ratio", passed=None, observed=None,
                               threshold=threshold, note=r.note)
    return CriterionResult(name="max_accrual_ratio", passed=r.value <= threshold,
                           observed=r.value, threshold=threshold, note=r.note)


def _min_altman_z(ev: Evidence, threshold: float) -> CriterionResult:
    """Altman Z-Score against a FLOOR — a distress screen. Higher is safer.

    Shares ``screening.altman_z_score`` with the rankable ``altman_z`` factor. ABSTAINS
    on a missing input, a non-positive denominator, and on the CROSS-CURRENCY case
    (rule 8): the score's fourth term divides a quote-currency market cap by
    statement-currency liabilities, so an ADR or a non-USD reporter abstains with the
    note naming the reason rather than summing two currencies. No strategy marks it
    gating (see ``_max_accrual_ratio``'s note)."""
    r = altman_z_score(ev.fundamentals)
    if r.value is None:
        return CriterionResult(name="min_altman_z", passed=None, observed=None,
                               threshold=threshold, note=r.note)
    return CriterionResult(name="min_altman_z", passed=r.value >= threshold,
                           observed=r.value, threshold=threshold, note=r.note)


def _max_debt_to_market_cap(ev: Evidence, threshold: float) -> CriterionResult:
    """Balance-sheet leverage as total_debt / market_cap (a yield-trap separator:
    VZ ~1.2x fails). Chosen over debt-to-equity BECAUSE it is ROBUST TO NEGATIVE
    EQUITY: heavy-buyback names (MCD/LOW) have undefined d/e, which must NOT exclude
    them — market cap is always positive. ABSTAINS on missing total_debt / market_cap."""
    f = ev.fundamentals
    debt = f.total_debt if f else None
    cap = f.market_cap if f else None
    if debt is None or cap is None or cap <= 0:
        return CriterionResult(
            name="max_debt_to_market_cap", passed=None, observed=None,
            threshold=threshold,
            note="leverage unavailable (total_debt or market_cap missing)")
    ratio = debt / cap
    return CriterionResult(
        name="max_debt_to_market_cap", passed=ratio <= threshold, observed=ratio,
        threshold=threshold,
        note=f"total_debt/market_cap = {ratio:.2f} vs ceiling {threshold:.2f} "
             f"(market-cap based — robust to negative-equity buyback names)")


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
        label="Dividend yield",
        glossary=("The rule requires the dividend to be at least this percentage of "
                  "the share price."),
        comparison="min",
        observation="dividend yield {observed}",
        params=(ParamSpec("threshold", "float", min=0.0, max=1.0, step=0.005,
                          default=0.025, unit="percent"),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("dividend_per_share", "dividend_yield"),
    ),
    Criterion(
        "max_payout_ratio", _max_payout_ratio,
        label="Dividends vs earnings",
        glossary=("The rule caps the share of profit paid out as dividends, so the "
                  "payout has room to survive a bad year."),
        comparison="max",
        observation="dividends took {observed} of earnings",
        params=(ParamSpec("threshold", "float", min=0.0, max=None, step=0.05,
                          default=0.75, unit="percent"),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("payout_ratio", "dividend_per_share"),
    ),
    Criterion(
        "max_payout_ratio_fcf", _max_payout_ratio_fcf,
        label="Dividends vs free cash flow",
        glossary=("The rule caps the share of free cash flow paid out as dividends "
                  "— the cash test rather than the profit test."),
        comparison="max",
        observation="dividends took {observed} of free cash flow",
        params=(ParamSpec("threshold", "float", min=0.0, max=None, step=0.05,
                          default=0.80, unit="percent"),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("dividends_paid", "free_cash_flow", "operating_cash_flow",
                             "capital_expenditure", "payout_ratio",
                             "dividend_per_share"),
    ),
    Criterion(
        "min_market_cap", _min_market_cap,
        label="Company size",
        glossary=("The rule requires the company to be worth at least this much in "
                  "total, so names too small to trade sensibly are skipped."),
        comparison="min",
        observation="market value {observed}",
        params=(ParamSpec("threshold", "float", min=0.0, max=None, step=1e9,
                          default=10_000_000_000, unit="currency", currency="USD"),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("market_cap",),
    ),
    Criterion(
        "min_dividend_growth_streak", _min_dividend_growth_streak,
        label="Consecutive years of dividend increases (from payment history)",
        glossary=("The rule requires the dividend to have risen for at least this "
                  "many consecutive years."),
        comparison="min",
        observation="{observed} consecutive years of dividend increases",
        params=(ParamSpec("threshold", "int", min=0.0, max=None, step=1.0,
                          default=25, unit="count"),
                _UNVERIFIABLE_BLOCKS),
        requires=("dividends",),
        fundamentals_fields=("years_dividend_growth",),
    ),
    Criterion(
        "max_dividend_cuts", _max_dividend_cuts,
        label="Dividend cuts in the last N years",
        glossary=("The rule requires that no calendar year in the window paid a lower "
                  "total dividend than the year before. It asks whether the dividend "
                  "was ever CUT, which is a different question from whether it ROSE: a "
                  "company that held its dividend flat through a downturn passes this "
                  "rule and fails a growth-streak rule. For a cyclical payer the flat "
                  "dividend is the evidence of durability."),
        comparison="max",
        observation="the dividend was cut — the largest fall was {observed} of the "
                    "prior year's total",
        threshold_text="no year paid less than the year before, "
                       "across the last {threshold} complete years",
        params=(ParamSpec("threshold", "int", min=1.0, max=None, step=1.0,
                          default=5, unit="count"),
                _UNVERIFIABLE_BLOCKS),
        requires=("dividends",),
        fundamentals_fields=("dividend_per_share",),
    ),
    # --- Growth / quality criteria (Sprint 4B) ---
    Criterion(
        "min_revenue_cagr", _min_revenue_cagr,
        label="Revenue growth (annual average)",
        glossary=("The rule requires average yearly sales growth of at least this "
                  "much over the measured period."),
        comparison="min",
        observation="revenue grew {observed} a year",
        params=(ParamSpec("years", "int", min=1, max=None, step=1.0,
                          default=_REVENUE_CAGR_YEARS, unit="count"),
                ParamSpec("threshold", "float", min=0.0, max=1.0, step=0.01,
                          default=0.10, unit="percent"),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("total_revenue",),
    ),
    Criterion(
        "min_roic", _min_roic,
        label="Return on invested capital",
        glossary=("The rule requires the company to earn at least this much profit "
                  "on the money tied up in it — it screens out businesses that need "
                  "a lot of capital to make a little profit."),
        comparison="min",
        observation="return on invested capital {observed}",
        params=(ParamSpec("threshold", "float", min=0.0, max=1.0, step=0.01,
                          default=0.12, unit="percent"),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("operating_income", "ebit", "tax_provision",
                             "pretax_income", "invested_capital"),
    ),
    Criterion(
        "max_peg_ratio", _max_peg_ratio,
        label="Price/earnings against growth (PEG)",
        glossary=("The rule caps what you pay per unit of growth — the P/E divided "
                  "by the growth rate. Lower means growth is cheaper."),
        comparison="max",
        observation="PEG ratio {observed}",
        # NB: PEG divides P/E by the SAME in-house revenue-CAGR window the
        # revenue criterion uses (the _REVENUE_CAGR_YEARS module constant, read
        # by BOTH _min_revenue_cagr and _max_peg_ratio — one source of truth, can
        # never diverge). So the window is NOT a PEG parameter; it is surfaced
        # ONCE, under min_revenue_cagr, not redundantly here.
        params=(ParamSpec("threshold", "float", min=0.0, max=None, step=0.1,
                          default=2.0, unit="ratio"),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("total_revenue", "pe_ratio"),
    ),
    # --- Price momentum (value+momentum) — fixes cheap-falling-knife false BUYs ---
    Criterion(
        PRICE_MOMENTUM_CRITERION, _min_price_momentum,
        label="12-month price change",
        glossary=("The rule requires the share to have returned at least this much "
                  "over the trailing twelve months. The floor catches breakdowns "
                  "rather than flatness, so it may itself be negative."),
        comparison="min",
        observation="share price {signed} over 12 months",
        # Floor is a 12m RETURN and MAY be negative: the floor catches BREAKDOWNS, not
        # flatness. A defensive floor of -0.10 excludes a name down >10% (breaking
        # down) while letting a quiet defensive down 0-10% through; 0.0 = 'not in a
        # downtrend'. Reads ev.return_12m (from price closes already fetched).
        params=(ParamSpec("threshold", "float", min=-1.0, max=1.0, step=0.01,
                          default=0.0, unit="percent"),
                _UNVERIFIABLE_BLOCKS),
        requires=(),
        fundamentals_fields=(),
    ),
    # --- Defensive-risk criteria (free-data yield-trap separators) ---
    Criterion(
        "min_dividend_streak", _min_dividend_streak,
        label="Consecutive years of dividend increases",
        glossary=("The rule requires the dividend to have risen for at least this "
                  "many consecutive years."),
        comparison="min",
        observation="{observed} consecutive years of dividend increases",
        params=(ParamSpec("threshold", "int", min=0, max=None, step=1, default=10,
                          unit="count"),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("dividend_streak_years",),
    ),
    Criterion(
        "max_debt_to_market_cap", _max_debt_to_market_cap,
        label="Total debt vs market value",
        glossary=("The rule caps borrowings against what the company is worth, so "
                  "heavily indebted names are screened out."),
        comparison="max",
        observation="total debt {observed} of market value",
        params=(ParamSpec("threshold", "float", min=0.0, max=None, step=0.1,
                          default=1.0, unit="multiple"),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("total_debt",),
    ),
    # --- Accounting quality (PIOTROSKI-1) — OPTIONAL, no lens selects it yet ---
    Criterion(
        "min_f_score", _min_f_score,
        label="Accounting quality (Piotroski F-Score, 0-9)",
        glossary=("The rule requires at least this many points on a nine-point "
                  "checklist of basic financial health."),
        comparison="min",
        observation="F-Score {observed} of 9",
        params=(ParamSpec("threshold", "int", min=0, max=9, step=1, default=5,
                          unit="score"),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("total_assets_annual", "long_term_debt_annual",
                             "current_assets_annual", "current_liabilities_annual",
                             "shares_outstanding_annual", "gross_profit_annual",
                             "net_income", "operating_cash_flow_annual",
                             "total_revenue"),
    ),
    # --- Forensic criteria (FORENSIC-1) — selectable by any strategy, GATING BY NONE.
    # Both share their arithmetic with the like-named rank factor. forensic_v1 ranks on
    # the factors and sets no threshold; these exist so a later lens CAN put a floor on
    # earnings quality once the scoreboard justifies one.
    Criterion(
        "max_accrual_ratio", _max_accrual_ratio,
        label="Profit backed by cash (accrual ratio)",
        glossary=("The rule caps how much of the reported profit may be accounting "
                  "entries rather than cash the business actually collected."),
        comparison="max",
        observation="accruals {observed} of assets",
        params=(ParamSpec("threshold", "float", min=0.0, max=None, step=0.01,
                          default=0.10, unit="ratio"),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("net_income", "operating_cash_flow_annual",
                             "total_assets_annual"),
    ),
    Criterion(
        "min_altman_z", _min_altman_z,
        label="Distance from financial distress (Altman Z-Score)",
        glossary=("The rule requires a minimum score on a five-part measure of "
                  "financial strength, so companies close to trouble are screened out."),
        comparison="min",
        observation="Z-Score {observed}",
        params=(ParamSpec("threshold", "float", min=0.0, max=None, step=0.1,
                          default=1.8, unit="score"),
                _UNVERIFIABLE_BLOCKS),
        requires=("fundamentals",),
        fundamentals_fields=("current_assets_annual", "current_liabilities_annual",
                             "retained_earnings_annual", "ebit", "total_assets_annual",
                             "total_liabilities_annual", "total_revenue", "market_cap"),
    ),
    # --- Absolute valuation band (VALBAND-1) — OPTIONAL, no strategy selects it --- #
    Criterion(
        "valuation_band_percentile", _valuation_band_percentile,
        label="Valuation against its own 5-year range",
        glossary=("Where today's valuation multiple sits in the company's own five- "
                  "year history — context only, and it decides nothing."),
        comparison="max",
        observation="valuation at the {observed}th percentile of its own 5-year range",
        params=(ParamSpec("threshold", "float", min=0.0, max=100.0, step=5.0,
                          default=80.0, unit="score"),
                _UNVERIFIABLE_BLOCKS),
        # requires=() DELIBERATELY: the band is not one of the gathered evidence KINDS
        # (fundamentals / dividends / last_close) that validate_selections knows about.
        # Declaring a kind that AVAILABLE_EVIDENCE doesn't list would make every strategy
        # selecting this criterion fail to load; declaring one it DOES list would be a
        # lie on the council path, which never computes a band. It rides on
        # Evidence.valuation_band and NOT-EVALs when absent.
        requires=(),
        # The band is built from PRICE history + the DATED statement dicts, not from a
        # scalar Fundamentals field, so there is nothing to add to the evidence packet.
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


def consumed_fields_for_strategies(strategies) -> set[str]:
    """Union of the consumed Fundamentals fields across SEVERAL strategies (NARR-UNION-1).

    A multi-lens run narrates a name ONCE, so its evidence packet is the union of the
    fields consumed by the lenses that actually rated it BUY — not a dump of everything.
    This EXTENDS ``consumed_fundamentals_fields`` to more than one strategy rather than
    bypassing the scoping: a lens that did not buy the name contributes nothing, exactly
    as a criterion the strategy does not select contributes nothing today.

    A screen-LESS strategy (no criteria) contributes no fields, which is correct — it
    consumed none."""
    out: set[str] = set()
    for strategy in strategies or []:
        out.update(consumed_fundamentals_fields(
            getattr(strategy, "criteria", None) or []))
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
