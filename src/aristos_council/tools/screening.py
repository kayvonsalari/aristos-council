"""Deterministic screening tools.

Hard rule of the council: ALL math happens here, in pure deterministic
functions — never in an LLM. Each tool:

- takes normalized DTOs (or plain numbers) as input,
- returns a small typed result the caller logs as a ToolCall,
- makes ZERO network calls (the adapter already fetched the data),
- handles missing inputs explicitly (None in -> documented behaviour, never a
  silent zero that corrupts a downstream decision).

The dividend-aristocrat screen is assembled from these primitives so each piece
is independently testable and each number a specialist later cites can be traced
to exactly one of these outputs.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from ..data.adapter import DividendEvent, Fundamentals

# Growth-hardening constants (SK Hynix cyclical-trough failure).
# CAGR: warn when the log-linear trend and the two-point endpoint diverge by more
# than this (a cyclical base year is smoothing/inflating the endpoint estimate).
_CAGR_DISPERSION_WARN = 0.10
# PEG: winsorize the growth input here — above this a "CAGR" is almost certainly
# cyclical noise, not sustainable growth, and would make PEG look spuriously cheap.
_PEG_GROWTH_CAP = 0.40

# Absolute-money screen thresholds (min_market_cap) are USD-denominated. A non-
# USD listing makes that comparison meaningless (SK Hynix's 1.69e15 KRW market
# cap would "pass" a 1e10 USD floor for the wrong reason). We ABSTAIN — return
# NOT-EVAL with a note — rather than apply FX, consistent with how insufficient
# history is handled. Ratio criteria (yield, payout, CAGR, ROIC, PEG) are
# currency-INVARIANT and never consult this.
USD = "USD"


def _non_usd_currency(fundamentals: Fundamentals) -> str | None:
    """Return the listing currency iff it's a KNOWN non-USD currency, else None.

    None means 'evaluate normally': either the listing IS USD, or the provider
    reported no currency at all. A missing currency must NOT manufacture a
    foreign-listing abstention — that would N/E every USD record predating the
    field. Only absolute-money-vs-USD criteria call this.
    """
    cur = (fundamentals.currency or "").strip().upper()
    if not cur or cur == USD:
        return None
    return cur


# --------------------------------------------------------------------------- #
# Primitive results
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CriterionResult:
    """Outcome of a single screen criterion.

    `passed` is None when the criterion could not be evaluated (missing data) —
    distinct from False (evaluated and failed). The screen aggregator treats
    None as a data-quality flag, not a pass and not a fail.
    """

    name: str
    passed: bool | None
    observed: float | None
    threshold: float | None
    note: str = ""
    # The measurement BASIS a multi-basis criterion used, disclosed in the report
    # (max_payout_ratio_fcf: "fcf" | "eps" for the marked fallback | "" otherwise).
    basis: str = ""


@dataclass(frozen=True)
class ScreenResult:
    ticker: str
    criteria: list[CriterionResult]
    flags: list[str]

    @property
    def evaluated(self) -> list[CriterionResult]:
        return [c for c in self.criteria if c.passed is not None]

    @property
    def unverifiable(self) -> list[CriterionResult]:
        return [c for c in self.criteria if c.passed is None]

    @property
    def passes_all_evaluated(self) -> bool:
        ev = self.evaluated
        return bool(ev) and all(c.passed for c in ev)


# --------------------------------------------------------------------------- #
# Primitive tools (each independently unit-tested)
# --------------------------------------------------------------------------- #
def _has_current_dividend(fundamentals: Fundamentals) -> bool:
    """True iff the company pays a positive current dividend (DPS > 0).

    A ZERO DPS is a determination (suspended/never-pays). A NULL DPS is a DATA
    GAP, NOT a determination (hard rule 3) — callers must distinguish the two
    BEFORE consulting this predicate (which returns False for both). History is
    judged separately by the growth-streak criterion.
    """
    dps = fundamentals.dividend_per_share
    return dps is not None and dps > 0


def min_yield_criterion(
    fundamentals: Fundamentals, *, min_yield: float,
    last_close: float | None = None,
) -> CriterionResult:
    """Dividend yield at or above the strategy floor (decimals: 0.025 == 2.5%).

    NULL vs ZERO dividend_per_share are DIFFERENT outcomes (hard rule 3):
    - null (None) is a DATA GAP -> NOT EVALUATED. A missing figure must never
      become a phantom FAIL. (Live bug: yfinance's summaryDetail block can come
      back empty for genuine payers — PG/JNJ/MO/T/MMM — so dividendRate arrives
      None; the adapter now falls back to trailingAnnualDividendRate, but if
      EVEN THAT is absent we abstain rather than fabricate a zero.)
    - zero (<= 0) is a GENUINE non-payer -> FAIL, observed 0.0. A real 0 (e.g.
      a suspended dividend, INTC: trailingAnnualDividendRate == 0) categorically
      cannot meet a minimum yield.

    UNITS LESSON (found live by the council's own Critic, NVDA run): provider
    yield fields have ambiguous units — yfinance has shipped both 0.0254 and
    2.54 for the same 2.54% yield over its versions, so the raw field is
    untrustworthy by construction. We therefore DERIVE the yield from two
    unambiguous inputs: annual dividend_per_share / last_close. The provider
    field is never compared against the threshold; if a paying company's price
    is missing, the criterion is UNVERIFIABLE rather than silently trusted.
    """
    dps = fundamentals.dividend_per_share
    if dps is None:
        return CriterionResult(
            name="min_dividend_yield",
            passed=None,
            observed=None,
            threshold=min_yield,
            note="NOT EVALUATED: dividend figure unavailable "
                 "(dividend_per_share is null) — a data gap, not a non-payer",
        )
    if dps <= 0:
        return CriterionResult(
            name="min_dividend_yield",
            passed=False,
            observed=0.0,
            threshold=min_yield,
            note="no current dividend (dividend_per_share is zero): "
                 "a non-payer cannot meet the minimum yield",
        )
    if last_close is not None and last_close > 0:
        # Currency-INVARIANT: dps and last_close share the listing currency, so
        # the ratio is dimensionless — a KRW payer's yield is as valid as a USD
        # one. No currency guard here (unlike min_market_cap's USD threshold).
        derived = dps / last_close
        return CriterionResult(
            name="min_dividend_yield",
            passed=derived >= min_yield,
            observed=derived,
            threshold=min_yield,
            note="derived deterministically as dividend_per_share / last_close"
                 " (provider yield field ignored: ambiguous units)",
        )
    return CriterionResult(
        name="min_dividend_yield",
        passed=None,
        observed=None,
        threshold=min_yield,
        note="yield underivable: dividend_per_share present but last_close "
             "missing; provider yield field not used (ambiguous units)",
    )


def max_payout_criterion(
    fundamentals: Fundamentals, *, max_payout: float
) -> CriterionResult:
    """Payout ratio at or below the sustainability ceiling.

    NO CURRENT DIVIDEND -> NOT EVALUATED (Tier 0 stress basket): there is no
    payout to sustain, and a provider-reported 0.0 payout would otherwise PASS
    the ceiling and read as 'sustainable', which is misleading for a non-payer.
    So this is passed=None, not a (false) PASS.

    A payout ratio above the ceiling means dividends may be funded beyond
    earnings — the opposite of aristocrat-grade durability. Negative payout
    (negative earnings) is treated as a FAIL, not unverifiable, because it is a
    meaningful signal, with an explanatory note.

    NULL vs ZERO dividend_per_share both yield NOT EVALUATED here (there is no
    sustainable-payout judgement to make either way), but with DISTINCT notes
    (hard rule 3): null is a data gap; zero is a genuine non-payer.
    """
    if fundamentals.dividend_per_share is None:
        return CriterionResult(
            name="max_payout_ratio",
            passed=None,
            observed=None,
            threshold=max_payout,
            note="not evaluated: dividend figure unavailable "
                 "(dividend_per_share is null) — a data gap",
        )
    if not _has_current_dividend(fundamentals):
        return CriterionResult(
            name="max_payout_ratio",
            passed=None,
            observed=None,
            threshold=max_payout,
            note="not evaluated: no current dividend, so there is no payout "
                 "to sustain (a reported 0.0 payout would PASS misleadingly)",
        )
    p = fundamentals.payout_ratio
    if p is None:
        return CriterionResult(
            name="max_payout_ratio",
            passed=None,
            observed=None,
            threshold=max_payout,
            note="payout_ratio unavailable from provider",
        )
    if p < 0:
        return CriterionResult(
            name="max_payout_ratio",
            passed=False,
            observed=p,
            threshold=max_payout,
            note="negative payout ratio implies negative earnings",
        )
    return CriterionResult(
        name="max_payout_ratio",
        passed=p <= max_payout,
        observed=p,
        threshold=max_payout,
    )


# Through-cycle window for the FCF payout denominator — matches ROIC's 4-year smoothing.
_FCF_WINDOW = 4
# CRIT-NOCUT-1: FLAT is not a CUT. The same tolerance ``dividend_streak`` applies to the
# same calendar-year totals, so the two readings of one history cannot disagree about
# whether a year was a reduction.
_FLAT_TOL = 0.005
# FACTOR-NDOI-1: the same 4-year window for the operating-income denominator, for the same
# reason ROIC smooths — a single peak year makes a cyclical's balance sheet look light
# (Valero: 15,751m of operating income in FY2022 against 4,312m in FY2025).
_OI_WINDOW = 4


def fcf_annual_series(f: Fundamentals) -> list[float]:
    """Newest-first annual free cash flow: the provider's ``free_cash_flow_annual`` row
    if present, else per-year ``operating_cash_flow_annual + capital_expenditure_annual``
    (yfinance CapEx is a negative outflow). Empty when neither is available."""
    if f.free_cash_flow_annual:
        return list(f.free_cash_flow_annual)
    ocf, capex = f.operating_cash_flow_annual, f.capital_expenditure_annual
    if ocf and capex:
        return [ocf[i] + capex[i] for i in range(min(len(ocf), len(capex)))]
    return []


def through_cycle_fcf(f: Fundamentals, *, window: int = _FCF_WINDOW):
    """(mean FCF over the last up-to-``window`` fiscal years, n_years). A single crushed
    or inflated year is dampened — single-year FCF carries one-off cash events (KO's
    fairlife earnout) exactly as GAAP earnings carried non-cash ones."""
    series = fcf_annual_series(f)[:window]
    if not series:
        return None, 0
    return sum(series) / len(series), len(series)


def payout_coverage_fcf(f: Fundamentals, *, window: int = _FCF_WINDOW):
    """``(coverage, note)`` — current ``dividends_paid`` over the THROUGH-CYCLE MEAN free
    cash flow, or ``(None, reason)`` when that basis is not available.

    FACTOR-COVER-1. THE one place this ratio is computed. ``max_payout_ratio_fcf`` (the
    screen floor) and ``payout_coverage_fcf`` (the rank factor) both call it, so the
    number a report shows in the rules table and the number it shows in the ranked table
    are the same number, not two implementations that agree today.

    Returns None — never a guess and never a zero — when the mean free cash flow is
    non-positive (investment-driven, the utilities lesson) or the dividend figure is
    missing. A NON-PAYER is coverage 0.0, which is honest: it pays out none of its cash.
    """
    if f is None:
        return None, "no fundamentals"
    if f.dividend_per_share is None:
        return None, "dividend figure unavailable (dividend_per_share is null)"
    if not _has_current_dividend(f):
        return 0.0, "no current dividend — nothing is paid out"
    mean_fcf, n_years = through_cycle_fcf(f, window=window)
    if n_years < 2 or mean_fcf is None:
        return None, f"fewer than 2 years of free-cash-flow history ({n_years})"
    if f.dividends_paid is None:
        return None, "dividends paid unavailable"
    if mean_fcf <= 0:
        return None, (f"{n_years}y-mean free cash flow ≤ 0 (investment-driven, not "
                      "dividend distress)")
    return (f.dividends_paid / mean_fcf,
            f"current dividends_paid / {n_years}y-mean free cash flow")


def net_debt_to_operating_income(f: Fundamentals, *, window: int = _OI_WINDOW):
    """``(ratio, note)`` — net debt over the THROUGH-CYCLE MEAN operating income, or
    ``(None, reason)``.

    FACTOR-NDOI-1. The window matches ``through_cycle_roic``'s and exists for the same
    reason: measured against a single peak year a cyclical looks almost debt-free. Valero
    earned 15,751m in FY2022 and 4,312m in FY2025 — the same balance sheet reads three and
    a half times heavier on the second number than the first, and neither is the truth.

    NET CASH (cash exceeds debt) returns a NEGATIVE ratio and therefore ranks best under
    the factor's LOW direction — correct, and not a special case.

    Abstains when the mean operating income is non-positive (the ratio would invert: more
    debt would read as better) or either balance-sheet figure is missing. Generic on
    purpose — the planned quality_v1 lens wants this same measure, so it is built once
    here rather than inside an income lens.
    """
    if f is None:
        return None, "no fundamentals"
    if f.total_debt is None or f.total_cash is None:
        missing = "total debt" if f.total_debt is None else "cash"
        return None, f"{missing} unavailable"
    series = (f.operating_income or [])[:window]
    if not series:
        return None, "no operating-income history"
    mean_oi = sum(series) / len(series)
    if mean_oi <= 0:
        return None, (f"{len(series)}y-mean operating income ≤ 0 — the ratio would "
                      "invert, making more debt read as better")
    return ((f.total_debt - f.total_cash) / mean_oi,
            f"net debt / {len(series)}y-mean operating income")


def max_payout_fcf_criterion(
    fundamentals: Fundamentals, *, max_payout: float
) -> CriterionResult:
    """Dividend coverage measured against CASH, not GAAP earnings (dividends are paid
    from cash): CURRENT-year ``dividends_paid`` / the THROUGH-CYCLE MEAN free cash flow
    (last up-to-4 fiscal years) at or below the ceiling. The numerator stays current
    (today's dividend safety); only the denominator smooths — single-year FCF carries
    one-off cash events (KO's fairlife earnout, JNJ settlements) as GAAP earnings carried
    non-cash charges (ABBV 3.26), so the through-cycle mean (matching ROIC's window)
    dampens both.

    Semantics — each a lesson already paid for:
    - dividend_per_share NULL -> NOT EVALUATED (data gap, hard rule 3).
    - NO current dividend (dps == 0) -> PASS trivially (non-payers are yield's job).
    - >= 2 years of FCF with a MEAN <= 0 -> NOT EVALUATED (abstain, NEVER fail — the
      utilities lesson, now through-cycle).
    - >= 2 years of FCF (mean > 0) with dividends_paid -> the FCF basis, ``basis='fcf'``.
    - < 2 years of FCF, or the numerator missing, but a GAAP payout is computable -> the
      EPS basis as a MARKED fallback, ``basis='eps'``.
    """
    name = "max_payout_ratio_fcf"
    if fundamentals.dividend_per_share is None:
        return CriterionResult(name=name, passed=None, observed=None,
                               threshold=max_payout, basis="abstained",
                               note="not evaluated: dividend figure unavailable "
                                    "(dividend_per_share is null) — a data gap")
    if not _has_current_dividend(fundamentals):
        return CriterionResult(name=name, passed=True, observed=0.0,
                               threshold=max_payout,
                               note="no current dividend — trivially covered "
                                    "(exclusion of non-payers is min_dividend_yield's job)")
    mean_fcf, n_years = through_cycle_fcf(fundamentals)
    if n_years >= 2 and mean_fcf is not None and fundamentals.dividends_paid is not None:
        if mean_fcf <= 0:
            return CriterionResult(name=name, passed=None, observed=None,
                                   threshold=max_payout, basis="abstained",
                                   note=f"not evaluated: {n_years}y-mean free cash flow "
                                        "≤ 0 (investment-driven, not dividend distress) "
                                        "— the utilities lesson")
        # FACTOR-COVER-1: the arithmetic lives in payout_coverage_fcf, which the
        # like-named RANK FACTOR also calls — so the screen's number and the rank's
        # number are one number. The surrounding branches (the abstentions above, the
        # marked EPS fallback below) are this CRITERION's contract and stay here.
        payout, _ = payout_coverage_fcf(fundamentals)
        return CriterionResult(name=name, passed=payout <= max_payout, observed=payout,
                               threshold=max_payout, basis="fcf",
                               note=f"FCF basis: current dividends_paid / {n_years}y-mean "
                                    "free cash flow")
    # < 2 years of FCF, or the numerator missing -> EPS payout as a MARKED fallback.
    p = fundamentals.payout_ratio
    if p is not None:
        return CriterionResult(name=name, passed=p <= max_payout, observed=p,
                               threshold=max_payout, basis="eps",
                               note="EPS-fallback basis (< 2 years of free-cash-flow "
                                    "history) — a MARKED GAAP proxy")
    return CriterionResult(name=name, passed=None, observed=None, threshold=max_payout,
                           basis="abstained",
                           note="not evaluated: neither a through-cycle free-cash-flow "
                                "nor a GAAP payout basis is available")


def min_market_cap_criterion(
    fundamentals: Fundamentals, *, min_market_cap: float
) -> CriterionResult:
    # Currency safety FIRST: the threshold is USD; a non-USD market cap can't be
    # compared against it without FX, so abstain honestly (no silent pass/fail).
    cur = _non_usd_currency(fundamentals)
    if cur is not None:
        return CriterionResult(
            name="min_market_cap",
            passed=None,
            observed=None,
            threshold=min_market_cap,
            note=f"not evaluated: market cap is in {cur}, not USD, and the "
                 "threshold is USD-denominated; no FX conversion is applied "
                 "(honest abstention, not a silent pass/fail)",
        )
    mc = fundamentals.market_cap
    if mc is None:
        return CriterionResult(
            name="min_market_cap",
            passed=None,
            observed=None,
            threshold=min_market_cap,
            note="market_cap unavailable from provider",
        )
    return CriterionResult(
        name="min_market_cap",
        passed=mc >= min_market_cap,
        observed=mc,
        threshold=min_market_cap,
    )


def consecutive_dividend_growth_years(
    dividends: list[DividendEvent],
) -> tuple[int | None, str]:
    """Best-effort count of consecutive years of dividend increases.

    Returns (years, note). Years is None when there isn't enough clean annual
    data to judge.

    Method: compare the PER-PAYMENT dividend RATE year over year — the MEDIAN of
    each calendar year's payments — walking from the most recent COMPLETE year
    backwards, counting strictly-increasing years. We do NOT sum per calendar
    year: the count of ex-dates landing in a calendar year is not constant
    (payment timing drifts), so a year with an extra ex-date inflates the SUM and
    makes the next normal year read as a false CUT. (Live false-fail: PG's 2002
    had 5 ex-dates -> 2003's normal 4-payment year looked like a decrease and
    broke a genuine 68-year streak at 22; the median per-payment rate is immune
    to ex-date count, recovering PG to ~38 while T's 2022 cut and INTC's
    suspension — real per-payment drops — still break correctly.)

    Honesty caveat (returned in `note`): on yfinance the dividend history is
    often too short to verify the canonical 25-year aristocrat streak. This
    function does not lie about that — it reports what the series supports and
    flags the limitation. EODHD's longer history is the real depth fix; this fix
    is about the COUNTING method, not the data source.
    """
    if not dividends:
        return None, "no dividend events available"

    # Per-payment RATE per year = median of that year's payments (robust to the
    # ex-date COUNT and to one-off special dividends), NOT the calendar-year sum.
    from statistics import median

    payments_by_year: dict[int, list[float]] = {}
    for ev in dividends:
        payments_by_year.setdefault(ev.ex_date.year, []).append(ev.amount)
    rate_by_year = {y: median(p) for y, p in payments_by_year.items()}

    years_sorted = sorted(rate_by_year)
    if len(years_sorted) < 2:
        return None, "insufficient annual dividend history (<2 years)"

    # Drop the latest year (possibly partial / pre-raise) and walk back, counting
    # strictly-increasing per-payment rates.
    complete_years = years_sorted[:-1]
    streak = 0
    for i in range(len(complete_years) - 1, 0, -1):
        if rate_by_year[complete_years[i]] > rate_by_year[complete_years[i - 1]]:
            streak += 1
        else:
            break

    # Live-run lesson: 'treat as a floor' was ambiguous enough that two
    # agents read it as 'could be shorter'. State the direction explicitly.
    note = (
        f"estimated from {len(years_sorted)} years of provider dividend data "
        "by per-payment rate (median), immune to ex-date timing; this is a "
        "floor / LOWER BOUND — the true streak is AT LEAST this many years "
        "(provider history simply ends here); it is NOT a verified aristocrat "
        "count and the true streak may be LONGER, never shorter"
    )
    return streak, note


def min_growth_streak_criterion(
    dividends: list[DividendEvent], *, min_years: int,
    method: str = "per_payment_median",
) -> CriterionResult:
    """The streak criterion, computed by the PROVIDER-DECLARED method (Option A).

    ``method`` names the data-shape-matched streak function (``streak_by_method``):
    ``per_payment_median`` for yfinance's ex-date noise, ``calendar_year_sum`` for
    EODHD's adjusted annual totals. The chosen method is RECORDED IN THE NOTE so
    the audit trail shows the provider-matched method as a sourced choice. The
    default keeps yfinance behaviour — and the frozen ``run_strategy_screen``
    equivalence — unchanged.
    """
    streak, note = streak_by_method(method, dividends, min_years=min_years)
    note = f"{note}; streak computed by {method} ({_STREAK_SHAPE[method]})"
    if streak is None:
        return CriterionResult(
            name="min_dividend_growth_streak",
            passed=None,
            observed=None,
            threshold=float(min_years),
            note=note,
        )
    return CriterionResult(
        name="min_dividend_growth_streak",
        passed=streak >= min_years,
        observed=float(streak),
        threshold=float(min_years),
        note=note,
    )


# --------------------------------------------------------------------------- #
# EODHD-grade streak (calendar-year SUM of ADJUSTED values)
# --------------------------------------------------------------------------- #
# A SECOND streak method, on purpose. ``consecutive_dividend_growth_years`` above
# uses the per-payment MEDIAN, tuned for yfinance's ex-date timing noise (PG's
# 2002 had a stray 5th ex-date that a calendar-year SUM misreads as a cut). This
# one sums per calendar year — the opposite trade-off — because EODHD ships clean
# ADJUSTED values where the real hazard is CADENCE CHANGE (annual -> Interim+Final),
# which a per-payment comparison misreads as a cut. The two methods are matched to
# the two providers' data shapes and must NOT be collapsed; this is additive and
# the per-payment method (and its regression guards) are untouched.
def dividend_growth_streak_by_calendar_year(
    dividends: list[DividendEvent], *, min_years: int,
) -> tuple[int | None, str]:
    """Consecutive calendar years whose TOTAL dividend strictly exceeds the prior
    year's total — the EODHD method. Returns (streak, note); streak is None
    (NOT-EVAL) when history is too short to verify a ``min_years`` streak.

    Traps the Nestlé data exposed, handled here:
    1. Caller passes ADJUSTED values (EODHD ``value``), so split jumps (Nestlé
       2002: 0.64 adjusted vs 6.40 raw) never create a phantom break.
    2. GROUP BY CALENDAR YEAR and SUM, so a cadence change (annual -> Interim+Final
       from ~2025) compares like-for-like annual totals, not individual payments.
    3. STRICT INCREASE counts as a streak year (a "growth" streak; a hold-flat or
       a cut stops it).
    4. The most-recent year is EXCLUDED as possibly INCOMPLETE (only Interim paid
       so far), so a mid-year run does not read the partial year as a cut.

    NOT-EVAL vs FAIL (honesty): if the history is too short to even reach a
    ``min_years`` streak (a name listed <``min_years`` years ago), the result is
    None — NOT a fail — so a gating criterion routes it to INSUFFICIENT_EVIDENCE.
    A cut WITHIN a long-enough window is a genuine fail (a short streak, returned).
    """
    if not dividends:
        return None, "no dividend events available"

    totals: dict[int, float] = {}
    for ev in dividends:
        totals[ev.ex_date.year] = totals.get(ev.ex_date.year, 0.0) + ev.amount

    years_sorted = sorted(totals)
    # Drop the latest calendar year — it may be incomplete (only Interim paid).
    complete_years = years_sorted[:-1]
    if len(complete_years) < 2:
        return None, "insufficient complete-year dividend history (<2 years)"

    # The longest streak this much COMPLETE history could possibly demonstrate.
    max_observable = len(complete_years) - 1
    if max_observable < min_years:
        return None, (
            f"insufficient history to verify a {min_years}-year streak: "
            f"{len(complete_years)} complete years (latest excluded as possibly "
            f"incomplete) support a streak of at most {max_observable}"
        )

    streak = 0
    for i in range(len(complete_years) - 1, 0, -1):
        if totals[complete_years[i]] > totals[complete_years[i - 1]]:
            streak += 1
        else:
            break

    note = (
        f"streak {streak}: consecutive calendar years with a strictly higher TOTAL "
        f"dividend (adjusted value, summed per year; latest/incomplete year "
        f"excluded) across {len(complete_years)} complete years"
    )
    return streak, note


# CRIT-NOCUT-2 — how far BOTH measures must fall before a year counts as a cut. 10% is
# the value docs/diagnosis_dividend_history_2026-09-16.md defends: the largest FALSE reading
# it found was 8.3% (TotalEnergies on the median, from a 5->4 payment year), and the
# SMALLEST real cut on record was Equinor's 2020 at 24.8% in NOK. It sits roughly a factor
# of two clear of both. NOT 40% — that figure was asserted in the original brief and the
# diagnosis disproved it: a 40% bound would wave through Equinor's 2020 cut and Eni's
# (34.5% in EUR).
CUT_TOLERANCE = 0.10


def _year_stats(payments_by_year: dict) -> dict:
    """``{year: (total, median, n)}`` from ``{year: [amount, ...]}``."""
    return {y: (sum(a), statistics.median(a), len(a))
            for y, a in payments_by_year.items() if a}


def _cuts_from_year_stats(stats: dict, *, years: int,
                          tolerance: float = CUT_TOLERANCE) -> tuple:
    """The rule: a year is a CUT only when the year's TOTAL **and** its TYPICAL PAYMENT
    both fell by more than ``tolerance`` against the prior year.

    CRIT-NOCUT-2. The first version compared year totals alone and failed 67 of 91 names on
    the oil cohort, not one of which had cut its dividend. The reason is that each ordinary
    accounting artefact moves exactly ONE of the two measures, while a real cut moves BOTH:

      * a year with one fewer payment drops the TOTAL and leaves the typical payment alone
        (Canadian Natural, 5 payments then 4: total -22.1%, median payment +15.6%);
      * a special dividend does the same (EOG: the regular dividend rose 10% while the
        total fell 34%);
      * a CADENCE change does the opposite — it halves the typical payment while the total
        rises (Eni, 2 payments a year then 4: total +14.4%, median -44.1%), which is why
        the median alone is not a safe measure either;
      * a real cut moves both together (BP, Shell, SLB, Suncor and Equinor in 2020).

    So neither measure is trustworthy alone and the AND of the two is. ``observed`` is the
    size of the largest qualifying fall in the TOTAL, as a fraction.

    SINGLE-PAYMENT YEARS. A median of one number is not a typical payment — it IS that
    payment — so a year with one payment following a year with several is compared on the
    TOTAL alone. Judging an annual payer's single payment as though it were a "typical" one
    would make every switch to annual payment read as a cut.

    ROBUST TO A MISSING PAYMENT, and deliberately so: when a provider's record drops the
    last payments of a year (the US ADR lines for CNQ and Eni both stop mid-2025), the
    total falls but the typical payment does not, so this rule passes the name. The YIELD
    criterion reading the same short record is NOT protected that way — it is a separate
    queued item and is not addressed here.
    """
    complete_years = sorted(stats)[:-1]
    needed = years + 1
    if len(complete_years) < needed:
        return None, (f"only {len(complete_years)} complete years of dividend history; "
                      f"the rule needs {needed} (the latest year is excluded as "
                      f"possibly incomplete)")

    window = complete_years[-needed:]
    worst = 0.0
    worst_year = None
    worst_median_fall = 0.0
    for prev, year in zip(window, window[1:]):
        (t_prev, m_prev, n_prev), (t_now, m_now, n_now) = stats[prev], stats[year]
        if t_prev <= 0:
            continue
        total_fall = (t_prev - t_now) / t_prev
        if total_fall <= tolerance:
            continue                      # the total held; nothing else to check
        if n_now == 1 and n_prev > 1:
            median_fall = total_fall      # single payment: the total IS the measure
        elif m_prev <= 0:
            continue
        else:
            median_fall = (m_prev - m_now) / m_prev
        if median_fall <= tolerance:
            continue                      # the typical payment held -> not a cut
        if total_fall > worst:
            worst, worst_year, worst_median_fall = total_fall, year, median_fall

    if worst_year is not None:
        return worst, (f"the dividend was cut in {worst_year}: the year's total fell "
                       f"{worst:.0%} and the typical payment fell {worst_median_fall:.0%} "
                       f"against {worst_year - 1}")
    return 0.0, (f"no year cut the dividend across the last {years} complete years — a cut "
                 f"needs the year's total AND its typical payment both to fall more than "
                 f"{tolerance:.0%} (adjusted values; the latest year is excluded as "
                 "possibly incomplete)")


def _cuts_from_year_totals(totals: dict, *, years: int,
                           tolerance: float = CUT_TOLERANCE) -> tuple:
    """TOTALS ONLY — the degraded path, for a provider record that carries year totals but
    not the payments behind them.

    It cannot apply the typical-payment half of the rule, so it says so in its note rather
    than reporting a confident answer from half the evidence. Reached only for a
    Fundamentals record written before ``dividend_year_stats`` existed (the day cache ages
    out within a day); every live path has the payments."""
    stats = {y: (t, t, 1) for y, t in totals.items()}
    worst, note = _cuts_from_year_stats(stats, years=years, tolerance=tolerance)
    if worst:
        note += " (typical-payment check unavailable: this record carries year totals only)"
    return worst, note


def dividend_cuts_by_calendar_year(
    dividends: list[DividendEvent], *, years: int, tolerance: float = CUT_TOLERANCE,
) -> tuple[float | None, str]:
    """The LARGEST dividend cut in the last ``years`` complete calendar years, as a
    fraction (0.50 = the total halved), or 0.0 when no year paid less than the one
    before. ``None`` (NOT-EVAL) when the history cannot answer the question.

    CRIT-NOCUT-1. A different question from the growth streak, deliberately computed
    beside it on the SAME event source, the SAME calendar-year totals, the SAME adjusted
    per-share values and the SAME "drop the latest, possibly-incomplete year" rule — so
    the two can never disagree about what a year paid.

    Why not the streak with a low threshold: a streak of 3 asserts the dividend ROSE three
    times. A company that holds its dividend flat through a downturn has a streak of 0 and
    has cut nothing, and for a cyclical payer that flat dividend is the evidence of
    durability. So flat must PASS here and FAIL a streak test. Chevron (a 9-year streak),
    Kinder Morgan and Hess Midstream (8) are not cutters; BP, which halved in 2020, is.

    KNOWN FALSE POSITIVE, not detected in v1: a SPECIAL dividend inflates one year's
    total, so the following ordinary year reads as a cut. The measure is the year total by
    design (it is what makes a cadence change safe), and separating specials from ordinary
    payments needs a payment-type the free data does not carry reliably. A name flagged
    this way is wrong about the cause, never about the arithmetic — the note names the
    year, so it can be checked by eye. Documented in CALCULATIONS.md.
    """
    if not dividends:
        return None, "no dividend history"
    per_year: dict = {}
    for ev in dividends:
        per_year.setdefault(ev.ex_date.year, []).append(ev.amount)
    return _cuts_from_year_stats(_year_stats(per_year), years=years,
                                 tolerance=tolerance)


def max_dividend_cuts_criterion(
    dividends: list[DividendEvent], *, years: int, fundamentals=None,
    tolerance: float = CUT_TOLERANCE,
) -> CriterionResult:
    """"No dividend cut in the last ``years`` complete calendar years" as a criterion.

    PASS with ``observed=0.0`` when no year cut the dividend; FAIL with ``observed`` = the
    size of the largest qualifying fall in the year TOTAL. A year is a cut only when its
    total AND its typical payment both fell more than ``CUT_TOLERANCE`` (CRIT-NOCUT-2 — see
    ``_cuts_from_year_stats``); NOT-EVAL (``passed=None``) when there is
    no dividend history at all, or too little of it. A non-payer is failed by the YIELD
    criterion, never by this one — absence of a dividend is not a cut.

    TWO SOURCES, ONE ARITHMETIC. The council path hands over dividend EVENTS; the RANK
    prefilter does not fetch them (``factors.screen_evaluate`` builds its Evidence with
    ``dividends=[]``), which is why ``min_dividend_streak`` reads an adapter-derived
    scalar rather than the history. This criterion needs the cut's SIZE and a
    strategy-settable window, so a scalar cannot serve it — it falls back to the
    per-calendar-year totals the adapter now carries on Fundamentals, which are the same
    totals it already summed to derive that scalar. Same numbers, same rule, no extra
    fetch, and the lens works on both paths."""
    if dividends:
        worst, note = dividend_cuts_by_calendar_year(dividends, years=years,
                                                     tolerance=tolerance)
    else:
        # CRIT-NOCUT-2 prefers the richer carried field: the two-measure rule needs the
        # typical payment as well as the total. `dividend_year_totals` remains as the
        # degraded path for a Fundamentals record written before the stats existed (the
        # day cache ages out within a day), and it SAYS so in its note rather than
        # reporting a confident answer from half the evidence.
        stats = getattr(fundamentals, "dividend_year_stats", None) if fundamentals else None
        carried = getattr(fundamentals, "dividend_year_totals", None) if fundamentals             else None
        if stats:
            worst, note = _cuts_from_year_stats(
                {int(r[0]): (float(r[1]), float(r[2]), int(r[3])) for r in stats},
                years=years, tolerance=tolerance)
        elif carried:
            worst, note = _cuts_from_year_totals(
                {int(y): float(t) for y, t in carried}, years=years,
                tolerance=tolerance)
        else:
            worst, note = None, "no dividend history"
    return CriterionResult(
        name="max_dividend_cuts",
        passed=None if worst is None else worst == 0.0,
        observed=worst, threshold=float(years), note=note)


def min_growth_streak_criterion_by_year(
    dividends: list[DividendEvent], *, min_years: int
) -> CriterionResult:
    """``min_dividend_growth_streak`` evaluated with the calendar-year method.

    Same CriterionResult shape and three-valued ``passed`` as
    ``min_growth_streak_criterion`` (None == NOT-EVAL, never a phantom fail), so it
    is a drop-in for the EODHD provider when the live wiring lands."""
    streak, note = dividend_growth_streak_by_calendar_year(
        dividends, min_years=min_years)
    if streak is None:
        return CriterionResult(
            name="min_dividend_growth_streak", passed=None, observed=None,
            threshold=float(min_years), note=note,
        )
    return CriterionResult(
        name="min_dividend_growth_streak", passed=streak >= min_years,
        observed=float(streak), threshold=float(min_years), note=note,
    )


# --------------------------------------------------------------------------- #
# Provider-declared streak dispatch (Option A)
# --------------------------------------------------------------------------- #
# The adapter DECLARES its data shape (MarketDataAdapter.dividend_streak_method);
# screening OWNS the math and maps the declared name to the matching function
# here. The two methods are NOT collapsed — they handle opposite hazards
# (per-payment median vs calendar-year sum) and each is correct only for its
# provider's shape. Unknown name -> raise (fail loud), never a silent wrong method.
_STREAK_METHODS = {
    "per_payment_median": consecutive_dividend_growth_years,
    "calendar_year_sum": dividend_growth_streak_by_calendar_year,
}
_STREAK_SHAPE = {
    "per_payment_median": "yfinance shape",
    "calendar_year_sum": "EODHD shape",
}


def dividend_streak(
    annual_by_year: dict[int, float], as_of_year: int, *, flat_tol: float = 0.005,
) -> tuple[int | None, int | None]:
    """Consecutive years of dividend INCREASES ending at the latest COMPLETE year, and
    the most recent CUT year — from per-CALENDAR-YEAR dividend totals.

    Returns ``(streak_years, last_reduction_year)``. The CURRENT (partial) year
    ``as_of_year`` is EXCLUDED. Two signals kept SEPARATE (the T/MMM lesson — those
    cut, then went FLAT; a naive ``cur > prev`` mislabels flat years as cuts):
    - streak_years: walking back from the latest complete year, count consecutive
      YoY increases; a year within +/-``flat_tol`` of the prior is FLAT and ENDS the
      growth streak but is NOT a cut.
    - last_reduction_year: the most recent year whose total fell more than ``flat_tol``
      below the prior (an actual cut), scanning ALL history — independent of where the
      growth streak broke.
    None (both) on fewer than 3 complete years of history (honest abstain). The series
    is used as given (yfinance dividends are already split-adjusted)."""
    years = sorted(y for y in annual_by_year if y < as_of_year)
    if len(years) < 3:
        return None, None

    last_cut: int | None = None
    for i in range(1, len(years)):
        prev, cur = annual_by_year[years[i - 1]], annual_by_year[years[i]]
        if prev > 0 and (cur - prev) / prev < -flat_tol:
            last_cut = years[i]                       # keep -> ends at the most recent

    streak = 0
    for i in range(len(years) - 1, 0, -1):
        prev, cur = annual_by_year[years[i - 1]], annual_by_year[years[i]]
        if prev > 0 and (cur - prev) / prev > flat_tol:
            streak += 1
        else:
            break                                     # flat or cut ends the streak
    return streak, last_cut


def streak_by_method(
    method: str, dividends: list[DividendEvent], *, min_years: int
) -> tuple[int | None, str]:
    """Dispatch to the provider-declared streak function. Unknown -> ValueError.

    ``per_payment_median``'s NOT-EVAL is purely data-driven (<2 years) so it
    ignores ``min_years``; ``calendar_year_sum`` needs it (NOT-EVAL when the
    history can't reach the floor). The dict maps straight to the two functions;
    the only branch is which one takes ``min_years``.
    """
    fn = _STREAK_METHODS.get(method)
    if fn is None:
        raise ValueError(
            f"unknown dividend_streak_method {method!r}; "
            f"known: {sorted(_STREAK_METHODS)}"
        )
    if method == "calendar_year_sum":
        return fn(dividends, min_years=min_years)
    return fn(dividends)


# --------------------------------------------------------------------------- #
# Growth / quality primitives (Sprint 4B) — pure math, NOT-EVAL on missing data
# --------------------------------------------------------------------------- #
def _series_cagr(
    series: list[float], years: int, *, label: str
) -> tuple[float | None, str]:
    """Base-year-robust CAGR of a NEWEST-FIRST numeric series over ``years``.

    The OBSERVED value is a LOG-LINEAR TREND CAGR, not the two-point endpoint
    ratio: fit a least-squares line to (t, ln(series_t)) across all ``years``+1
    points (t = 0..years, oldest..newest) and return ``exp(slope) - 1`` — the
    continuous growth rate the whole series implies, so a single cyclical-trough
    BASE year can't anchor the estimate the way ``(s[0]/s[years])^(1/years)-1``
    does (the SK Hynix failure). The note records the OLD endpoint CAGR and, when
    the two DIVERGE, a WARNING. ``label`` ("revenue", "operating-income") frames
    the note. Returns (None, note) on the honest-abstention cases: fewer than
    ``years``+1 clean points, or ANY non-positive point (a non-positive value
    destroys the log / the ratio). SHARED by ``revenue_cagr`` and
    ``operating_income_cagr`` so both use identical, tested logic.
    """
    if years < 1:
        return None, "years must be >= 1"
    if len(series) < years + 1:
        return None, (f"insufficient {label} history: need {years + 1} annual "
                      f"points, have {len(series)}")
    points = series[:years + 1]                  # newest-first window
    if any(p <= 0 for p in points):
        return None, (f"{label} non-positive in the {years + 1}-point CAGR window "
                      f"(base={series[years]}, latest={series[0]}); CAGR undefined")

    # Log-linear least-squares slope over t = 0..years (oldest..newest).
    ys = [math.log(p) for p in reversed(points)]
    n = years + 1
    ts = range(n)
    mean_t = sum(ts) / n
    mean_y = sum(ys) / n
    cov = sum((t - mean_t) * (y - mean_y) for t, y in zip(ts, ys))
    var = sum((t - mean_t) ** 2 for t in ts)
    trend_cagr = math.exp(cov / var) - 1.0
    endpoint_cagr = (series[0] / series[years]) ** (1.0 / years) - 1.0

    note = (f"{years}y {label} CAGR (log-linear trend over {n} points) = "
            f"{trend_cagr:.4f}; two-point endpoint CAGR = {endpoint_cagr:.4f}")
    if abs(trend_cagr - endpoint_cagr) > _CAGR_DISPERSION_WARN:
        note += (f"; WARNING: endpoint vs trend CAGR diverge by "
                 f"{abs(trend_cagr - endpoint_cagr):.4f} — base year may be cyclical")
    return trend_cagr, note


def revenue_cagr(
    revenue: list[float], years: int
) -> tuple[float | None, str]:
    """Base-year-robust revenue CAGR (log-linear trend; see ``_series_cagr``).

    The GATING-ELIGIBLE growth metric — a clean, trough-resistant denominator.
    Output is unchanged from before the ``_series_cagr`` extraction.
    """
    return _series_cagr(revenue, years, label="revenue")


def operating_income_cagr(
    operating_income: list[float], years: int
) -> tuple[float | None, str]:
    """Operating-income growth (log-linear trend; see ``_series_cagr``) — the
    available EARNINGS-growth proxy (there is no EPS series). This is the correct
    PEG denominator (a standard PEG uses earnings growth, not revenue). NOT-EVAL
    (None) on a too-short series or a non-positive operating-income point (earnings
    growth undefined)."""
    return _series_cagr(operating_income, years, label="operating-income")


def nopat_roic(
    operating_income: float | None,
    tax_provision: float | None,
    pretax_income: float | None,
    invested_capital: float | None,
) -> tuple[float | None, str]:
    """Return on invested capital from the PROVIDED invested_capital line.

    NOPAT = operating_income * (1 - effective_tax_rate); ROIC = NOPAT /
    invested_capital. We use the provider's invested_capital line directly and
    do NOT reconstruct it from debt+equity — negative-equity names (e.g. MO)
    break that reconstruction while their provided invested_capital is sane.

    A negative NOPAT yields a negative ROIC — a real determination (the
    criterion will FAIL it), not an error. Returns (None, note) only when ROIC
    is genuinely undefined: missing/zero invested_capital, or missing operating
    income. Effective tax rate is tax_provision/pretax_income clamped to [0,1];
    if tax data is unusable it falls back to 0 (NOPAT = operating income).
    """
    if invested_capital is None or invested_capital == 0:
        return None, "invested_capital missing or zero; ROIC undefined"
    if operating_income is None:
        return None, "operating_income missing; NOPAT undefined"
    eff_tax = 0.0
    tax_note = "no usable tax data; effective tax rate assumed 0"
    if (tax_provision is not None and pretax_income is not None
            and pretax_income > 0):
        eff_tax = min(max(tax_provision / pretax_income, 0.0), 1.0)
        tax_note = f"effective tax rate = tax_provision/pretax = {eff_tax:.3f}"
    nopat = operating_income * (1.0 - eff_tax)
    roic = nopat / invested_capital
    return roic, (
        "ROIC = NOPAT / invested_capital (provided line); "
        f"NOPAT = operating_income * (1 - eff_tax); {tax_note}"
    )


def through_cycle_roic(
    operating_income: list[float],
    tax_provision: list[float],
    pretax_income: list[float],
    invested_capital: list[float],
    *,
    window: int,
) -> tuple[float | None, str]:
    """ROIC normalized to a THROUGH-CYCLE operating income, not a single peak.

    A peak-year operating income overstates the return a cyclical business earns
    over the cycle (SK Hynix: 28% ROIC on a peak OI that was NEGATIVE a period
    earlier). So the NOPAT numerator uses the MEAN operating income over the last
    ``window`` years, with the tax rate also taken through-cycle (mean tax / mean
    pretax over the same window). The DENOMINATOR stays the LATEST invested capital
    — the current capital base is the right base for "what do I earn on it now."
    Delegates the NOPAT/ROIC arithmetic to ``nopat_roic`` on the aggregates, then
    frames the note as through-cycle. With only one OI point it falls back to that
    point and flags single-period (may be peak/trough). Stays NON-GATING — the
    capital base is arguable, so it must not be a deterministic gate.
    """
    ic_latest = invested_capital[0] if invested_capital else None
    oi_points = operating_income[:window]
    if not oi_points:
        # let nopat_roic produce the canonical "operating_income missing" note
        return nopat_roic(None, None, None, ic_latest)

    n = len(oi_points)
    oi_mean = sum(oi_points) / n
    tax_points = tax_provision[:window]
    pretax_points = pretax_income[:window]
    tax_mean = sum(tax_points) / len(tax_points) if tax_points else None
    pretax_mean = sum(pretax_points) / len(pretax_points) if pretax_points else None

    # VERIFY-2 ITEM 2: a loss-mixed pretax history can make the through-cycle effective
    # tax rate degenerate — NOPAT collapses to a "-0" artifact (ENR.DE:
    # pretax [2213, 1822, -3387, -603], mean ~11.25, rate blows up). ABSTAIN then,
    # consistent with the PEG must-fail/abstain precedent; abstention never excludes.
    #
    # sign-mixed ALONE is NOT the trigger — a loss year inside a net-positive window is
    # the through-cycle design (000660.KS / SK Hynix: dampen a peak OI using a negative
    # prior year — that MUST keep computing). Abstain ONLY on a degenerate rate
    # (tax_mean/pretax_mean ∉ [0, 0.6]) or a non-positive pretax sum.
    # Abstain fixture: ENR.DE.  Must-compute fixture: 000660.KS.
    if pretax_points:
        degenerate = pretax_mean is not None and pretax_mean <= 0
        if not degenerate and pretax_mean and pretax_mean > 0 and tax_mean is not None:
            raw_rate = tax_mean / pretax_mean
            degenerate = not (0.0 <= raw_rate <= 0.6)
        if degenerate:
            return None, ("loss-mixed history — effective tax rate not computable "
                          "(degenerate through-cycle tax rate ∉[0,0.6] or non-positive "
                          "pretax sum)")

    roic, base_note = nopat_roic(oi_mean, tax_mean, pretax_mean, ic_latest)
    if roic is None:
        return None, base_note
    cycle_note = (f"ROIC on through-cycle ({n}y mean) operating income" if n > 1
                  else "single-period ROIC (no cycle history) — may be peak/trough")
    return roic, f"{cycle_note}; {base_note}"


def peg_ratio(
    pe_ratio: float | None, growth_rate: float | None,
    *, source: str = "in-house revenue CAGR",
) -> tuple[float | None, str, bool]:
    """PEG = P/E / (growth_rate * 100), with ``growth_rate`` a decimal growth rate.

    Returns ``(value, note, must_fail)``. ``must_fail`` is the FIX-1c signal: True
    means the growth-adjusted-value criterion must FAIL (passed=False), not abstain.

    PEG v2 WINSORIZES the growth input: an extreme trough-inflated growth rate makes
    PEG artificially tiny (looks cheap), so the growth term is capped at
    ``_PEG_GROWTH_CAP`` (0.40) before forming PEG — above that, a "growth rate" is
    almost certainly cyclical noise, not sustainable growth, so the cap makes PEG
    CONSERVATIVE rather than spuriously cheap, and the note records the clamp. Only
    the GROWTH term is winsorized; the P/E is never invented.

    The two "PEG undefined" reasons are DIFFERENT and must not be conflated (the
    FIX-1c root cause — they used to share one abstain branch):
    - no positive P/E, OR growth_rate is None (the series was too short/absent to
      compute a rate): a genuine DATA GAP -> abstain (NOT-EVAL), ``must_fail=False``.
    - growth_rate is a COMPUTED non-positive number (<= 0): the company is NOT
      GROWING. That is evaluable and bad for a growth strategy -> FAIL
      (``must_fail=True``), never a laundered NOT-EVAL. This is what closes LMT:
      a flat/declining growth rate (whether operating-income or the revenue
      fallback) now FAILS instead of silently abstaining and softening the verdict.

    The caller supplies an in-house ROBUST (trend) growth rate as ``growth_rate`` —
    never a provider forward estimate. ``source`` labels which series it came from.
    """
    if pe_ratio is None or pe_ratio <= 0:
        return (None,
                "no positive P/E (negative or missing earnings); PEG undefined",
                False)
    if growth_rate is None:
        # Growth could not be COMPUTED (data gap) — abstain, do not fail.
        return None, "growth rate unavailable (insufficient data); PEG undefined", False
    if growth_rate <= 0:
        # Growth WAS computed and is non-positive: not growing -> FAIL (FIX-1c).
        return (None,
                f"growth rate {growth_rate:.4f} <= 0 (not growing) — fails "
                f"growth-adjusted value; a growth name must show growth",
                True)
    note = f"PEG = P/E / ({source} x 100)"
    g = growth_rate
    if g > _PEG_GROWTH_CAP:
        note += (f"; PEG growth input winsorized from {growth_rate:.4f} to "
                 f"{_PEG_GROWTH_CAP:.2f} (extreme growth, likely cyclical) — "
                 f"PEG is conservative")
        g = _PEG_GROWTH_CAP
    peg = pe_ratio / (g * 100.0)
    return peg, note, False


def peg_with_earnings_growth(
    pe_ratio: float | None,
    operating_income: list[float],
    revenue: list[float],
    years: int,
) -> tuple[float | None, str, bool]:
    """PEG using OPERATING-INCOME growth as the denominator — the available
    EARNINGS-growth proxy (there is no per-share EPS series in the gathered data).

    Returns ``(value, note, earnings_fail)``. The third element is the FIX-1b
    signal: ``True`` means the criterion must FAIL (passed=False), NOT abstain —
    earnings were evaluable and NOT GROWING. Only the present-yet-non-growing case
    sets it; every other return path leaves it False.

    A standard PEG divides P/E by EARNINGS growth, not revenue growth. The Critic
    flagged revenue-PEG as the wrong denominator on essentially every growth name;
    this switches to operating-income growth. For a margin-EXPANDING compounder,
    operating income grows FASTER than revenue, so the earnings PEG is LOWER (and
    more correct); for a margin-COMPRESSING name it is HIGHER (the revenue PEG was
    flattering it). The winsor cap and the P/E / growth<=0 abstentions are unchanged
    (they live in ``peg_ratio``).

    Three outcomes, kept strictly distinct:
    - operating income PRESENT but NOT GROWING (a non-positive value -> CAGR None,
      or a flat/declining series -> CAGR <= 0): evaluable AND bad -> FAIL here, with
      a clear earnings note. NEVER a revenue fallback that would launder a real
      earnings problem behind a flattering revenue number (FIX-1b).
    - operating-income series MISSING / too short: a DATA GAP -> FALL BACK to revenue
      CAGR (documented prior behaviour). The fallback then COMPUTES a revenue growth
      rate and hands it to ``peg_ratio``, which itself FAILS a computed non-positive
      rate and abstains only on a true data gap (FIX-1c). So a fallback onto
      DECLINING revenue now FAILS (the actual LMT live path: short OI series ->
      fallback -> negative revenue CAGR -> previously a laundered NOT-EVAL).
    - no positive P/E, or growth uncomputable: abstain (handled in ``peg_ratio``).
    """
    oi = operating_income or []
    if len(oi) >= years + 1:
        growth, _ = operating_income_cagr(oi, years)
        if growth is None or growth <= 0:
            # Earnings PRESENT but not growing -> FAIL (do not abstain, do not fall
            # back to revenue). `growth is None` == a non-positive value in the
            # window; `growth <= 0` == flat/declining positive series.
            detail = ("operating income non-positive over the window"
                      if growth is None else
                      f"operating-income growth {growth:.4f} <= 0 (flat/declining)")
            note = (f"earnings not growing ({detail}) — fails growth-adjusted "
                    f"value; a GARP name must show earnings growth")
            return None, note, True
        source = "operating-income growth"
        rev, _ = revenue_cagr(revenue, years)
        if rev is not None and abs(growth - rev) > _CAGR_DISPERSION_WARN:
            # Surface the gap so a reviewer sees WHY the earnings PEG differs.
            source += (f", revenue CAGR {rev:.4f} vs earnings growth {growth:.4f}")
    else:
        growth, _ = revenue_cagr(revenue, years)
        source = "revenue CAGR — fallback, operating-income series unavailable"
    # peg_ratio returns must_fail=True for a COMPUTED non-positive growth (the
    # fallback-onto-declining-revenue case), which we propagate unchanged.
    peg, note, must_fail = peg_ratio(pe_ratio, growth, source=source)
    return peg, note, must_fail


# --------------------------------------------------------------------------- #
# Piotroski F-Score (PIOTROSKI-1) — ONE implementation, TWO consumers
# --------------------------------------------------------------------------- #
# The nine checks live HERE, in the deterministic-math module, because the score is
# consumed by BOTH the rankable factor (factors._piotroski_f_score) and the screen
# criterion (criteria.registry._min_f_score). Two registries, one arithmetic — the
# same discipline that keeps _REVENUE_CAGR_YEARS readable by both revenue CAGR and
# PEG, so the factor value and the criterion's observed value can never diverge.
#
# Standard Piotroski (2000): nine binary checks over the TWO most recent annual
# periods, index [0] = current year, [1] = prior year. One point each, 0-9.
#
# DOCUMENTED CONVENTIONS (decided; see CALCULATIONS.md — do not re-litigate):
#  * ZERO LONG-TERM DEBT is STRICT: a firm with LTD 0 in both years has a ratio that
#    did NOT decrease, so check 5 scores NO point. Comparability with published
#    F-Scores beats economic charity.
#  * ROA and asset turnover use SAME-YEAR (ending) total assets, not Piotroski's
#    beginning-of-year / average-assets denominator. One convention applied
#    identically to both years, so the YoY comparison stays apples-to-apples. A
#    deliberate simplification.
#  * A check whose inputs are MISSING, or whose denominator is zero or negative,
#    scores NO point AND is counted UNAVAILABLE. It is NEVER counted as a failed
#    check — the null≠false discipline (project rule 3) applied to the checks.
_F_SCORE_CHECKS = 9
# Below this many computable checks the score is not a score. A partial tally
# (3/9 because six lines are missing) reads as a terrible company when it is
# actually an absent statement, so the whole thing ABSTAINS instead.
_F_SCORE_MIN_COMPUTABLE = 5

# Check names in scoring order — profitability, leverage & liquidity, efficiency.
_F_SCORE_CHECK_NAMES: tuple[str, ...] = (
    "roa_positive",
    "ocf_positive",
    "roa_improved",
    "ocf_exceeds_net_income",
    "ltd_ratio_decreased",
    "current_ratio_improved",
    "no_new_share_issuance",
    "gross_margin_improved",
    "asset_turnover_improved",
)


@dataclass(frozen=True)
class FScoreResult:
    """The outcome of the nine checks for one name.

    ``score`` is None when fewer than ``_F_SCORE_MIN_COMPUTABLE`` checks were
    computable — an ABSTENTION, not a zero. ``points`` is always the raw tally of
    earned points (kept for the note even when abstaining); ``computed`` is C, the
    number of checks that could be evaluated, and ``unavailable`` is M = 9 − C.
    ``checks`` maps each check name to True (point) / False (no point) / None
    (unavailable), in scoring order.
    """

    score: int | None
    points: int
    computed: int
    unavailable: int
    note: str
    checks: tuple[tuple[str, bool | None], ...] = ()


def _newest_first_at(series, index: int) -> float | None:
    """Value at NEWEST-FIRST ``index`` of an annual series, or None when the series
    is absent/too short or the cell is not a number. Annual series are newest-first
    BY CONTRACT (the adapter guarantees it); this helper does not sort, so a
    reversed series is honestly read as reversed rather than silently repaired."""
    if not series or index >= len(series):
        return None
    v = series[index]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _f_score_ratio(numerator: float | None, denominator: float | None) -> float | None:
    """``numerator / denominator``, or None when either input is missing or the
    denominator is zero or NEGATIVE. A non-positive denominator makes the ratio
    meaningless (a negative-asset ROA flips sign), so the check that needs it is
    UNAVAILABLE — never a failed check."""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


# The nine input series the checks read: (pair key, positional Fundamentals attr).
# The pair key is ALSO the key in the PIOTROSKI-2 ``aligned_annual`` /
# ``aligned_period_ends`` dicts on Fundamentals.
_F_SCORE_SERIES: tuple[tuple[str, str], ...] = (
    ("net_income", "net_income"),
    ("total_assets", "total_assets_annual"),
    ("operating_cash_flow", "operating_cash_flow_annual"),
    ("long_term_debt", "long_term_debt_annual"),
    ("current_assets", "current_assets_annual"),
    ("current_liabilities", "current_liabilities_annual"),
    ("shares_outstanding", "shares_outstanding_annual"),
    ("gross_profit", "gross_profit_annual"),
    ("total_revenue", "total_revenue"),
)


def _f_score_pairs(f) -> dict[str, tuple[float | None, float | None]]:
    """(current-year, prior-year) value per input series — PERIOD-MATCHED when the
    adapter supplied period-labelled series (PIOTROSKI-2), positional otherwise.

    Period-matched path: each series becomes a {period-end date: value} map (None
    holes skipped — a hole is an absence, never a value); the reference years are
    the two most recent period-end dates across ALL supplied series. A series with
    no value at a reference date contributes None for that year, so the dependent
    check counts UNAVAILABLE — never a silent wrong-year comparison. Statements
    whose period axes genuinely differ therefore evaluate only on the overlap.

    Positional fallback (aligned dicts absent/empty — EODHD, fakes, pre-existing
    fixtures): index [0]/[1] of the NaN-dropped lists, the exact PIOTROSKI-1
    behavior, preserved byte-identical as the regression pin.
    """
    aligned = getattr(f, "aligned_annual", None) or {}
    periods = getattr(f, "aligned_period_ends", None) or {}
    usable = {k for k in aligned
              if aligned.get(k) and periods.get(k)
              and len(aligned[k]) == len(periods[k])}
    if usable:
        by_date: dict[str, dict[str, float]] = {}
        for key in usable:
            vals: dict[str, float] = {}
            for d, v in zip(periods[key], aligned[key]):
                if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue                      # a hole stays a hole
                vals[str(d)] = float(v)
            by_date[key] = vals
        dates = sorted({d for m in by_date.values() for d in m}, reverse=True)
        y0 = dates[0] if dates else None
        y1 = dates[1] if len(dates) > 1 else None
        return {
            key: (
                by_date.get(key, {}).get(y0) if y0 is not None else None,
                by_date.get(key, {}).get(y1) if y1 is not None else None,
            )
            for key, _attr in _F_SCORE_SERIES
        }
    out: dict[str, tuple[float | None, float | None]] = {}
    for key, attr in _F_SCORE_SERIES:
        series = getattr(f, attr, None) or []
        out[key] = (_newest_first_at(series, 0), _newest_first_at(series, 1))
    return out


def piotroski_f_score(fundamentals: Fundamentals | None) -> FScoreResult:
    """The Piotroski F-Score (0-9) from the annual statement series, or an abstention.

    Pure and deterministic: same ``Fundamentals`` in -> same score, unavailability
    count and note out. Inputs are (current-year, prior-year) pairs per statement
    line, PERIOD-MATCHED across statements when the adapter supplied the
    PIOTROSKI-2 period-labelled series, positional ([0]/[1]) otherwise — see
    ``_f_score_pairs``. No scalars, no TTM, no network; conventions block above.
    """
    f = fundamentals
    if f is None:
        pairs: dict[str, tuple[float | None, float | None]] = {
            key: (None, None) for key, _attr in _F_SCORE_SERIES}
    else:
        pairs = _f_score_pairs(f)          # period-matched when possible (PIOTROSKI-2)
    ni_0, ni_1 = pairs["net_income"]
    ta_0, ta_1 = pairs["total_assets"]
    ocf_0, _ocf_1 = pairs["operating_cash_flow"]
    ltd_0, ltd_1 = pairs["long_term_debt"]
    ca_0, ca_1 = pairs["current_assets"]
    cl_0, cl_1 = pairs["current_liabilities"]
    sh_0, sh_1 = pairs["shares_outstanding"]
    gp_0, gp_1 = pairs["gross_profit"]
    rev_0, rev_1 = pairs["total_revenue"]

    # 1-4 profitability / cash quality.
    roa_0 = _f_score_ratio(ni_0, ta_0)
    roa_1 = _f_score_ratio(ni_1, ta_1)
    roa_positive = None if roa_0 is None else roa_0 > 0
    ocf_positive = None if ocf_0 is None else ocf_0 > 0
    roa_improved = None if (roa_0 is None or roa_1 is None) else roa_0 > roa_1
    # The ACCRUAL / cash-quality check: earnings not backed by cash.
    ocf_exceeds_ni = None if (ocf_0 is None or ni_0 is None) else ocf_0 > ni_0

    # 5-7 leverage & liquidity.
    ltd_ratio_0 = _f_score_ratio(ltd_0, ta_0)
    ltd_ratio_1 = _f_score_ratio(ltd_1, ta_1)
    # STRICT zero-LTD: 0/assets is a COMPUTED 0.0 both years -> 0.0 < 0.0 is False ->
    # no point, and the check counts as COMPUTED (not unavailable).
    ltd_decreased = (None if (ltd_ratio_0 is None or ltd_ratio_1 is None)
                     else ltd_ratio_0 < ltd_ratio_1)
    cr_0 = _f_score_ratio(ca_0, cl_0)
    cr_1 = _f_score_ratio(ca_1, cl_1)
    cr_improved = None if (cr_0 is None or cr_1 is None) else cr_0 > cr_1
    no_new_shares = None if (sh_0 is None or sh_1 is None) else sh_0 <= sh_1

    # 8-9 efficiency.
    gm_0 = _f_score_ratio(gp_0, rev_0)
    gm_1 = _f_score_ratio(gp_1, rev_1)
    gm_improved = None if (gm_0 is None or gm_1 is None) else gm_0 > gm_1
    at_0 = _f_score_ratio(rev_0, ta_0)
    at_1 = _f_score_ratio(rev_1, ta_1)
    at_improved = None if (at_0 is None or at_1 is None) else at_0 > at_1

    outcomes: tuple[bool | None, ...] = (
        roa_positive, ocf_positive, roa_improved, ocf_exceeds_ni,
        ltd_decreased, cr_improved, no_new_shares, gm_improved, at_improved,
    )
    checks = tuple(zip(_F_SCORE_CHECK_NAMES, outcomes))
    points = sum(1 for o in outcomes if o is True)
    computed = sum(1 for o in outcomes if o is not None)
    unavailable = _F_SCORE_CHECKS - computed
    missing = ", ".join(n for n, o in checks if o is None)

    if computed < _F_SCORE_MIN_COMPUTABLE:
        note = (f"F-Score not computable: only {computed} of {_F_SCORE_CHECKS} checks "
                f"available (minimum {_F_SCORE_MIN_COMPUTABLE}) — abstained")
        if missing:
            note += f" (unavailable: {missing})"
        return FScoreResult(score=None, points=points, computed=computed,
                            unavailable=unavailable, note=note, checks=checks)

    note = (f"F-Score {points}/{_F_SCORE_CHECKS} — computed from {computed} checks, "
            f"{unavailable} unavailable")
    if missing:
        note += f" (unavailable: {missing})"
    return FScoreResult(score=points, points=points, computed=computed,
                        unavailable=unavailable, note=note, checks=checks)


# --------------------------------------------------------------------------- #
# Forensic measures (FORENSIC-1) — are the reported profits real, and could the
# balance sheet break?
# --------------------------------------------------------------------------- #
# Statement-series key -> the POSITIONAL Fundamentals attribute carrying the same
# line. The key is ALSO the key in the period-labelled ``aligned_annual`` /
# ``aligned_period_ends`` dicts (PIOTROSKI-2), so one name addresses both paths.
_SERIES_ATTR: dict[str, str] = {
    "net_income": "net_income",
    "total_assets": "total_assets_annual",
    "operating_cash_flow": "operating_cash_flow_annual",
    "current_assets": "current_assets_annual",
    "current_liabilities": "current_liabilities_annual",
    "gross_profit": "gross_profit_annual",
    "total_revenue": "total_revenue",
    "ebit": "ebit",
    "operating_income": "operating_income",
    "retained_earnings": "retained_earnings_annual",
    "total_liabilities": "total_liabilities_annual",
}


def period_matched(f, keys, *, periods: int = 1) -> list[dict[str, float | None]]:
    """``periods`` newest-first ``{key: value}`` dicts, PERIOD-MATCHED when the adapter
    supplied period-labelled series (PIOTROSKI-2), positional otherwise.

    The same discipline as ``_f_score_pairs``, generalized to any subset of statement
    lines and any depth: a value is read only at a REFERENCE period-end date, so a line
    absent at that date contributes None — the caller then ABSTAINS rather than quietly
    mixing two fiscal years inside one ratio. Holes stay holes (a None cell is an
    absence, never a value).

    One deliberate difference from ``_f_score_pairs``: the reference dates are drawn
    from the REQUESTED keys only, not from every recorded series. A measure reading
    three lines must not abstain because some unrelated statement line happens to carry
    a newer period-end. ``_f_score_pairs`` therefore keeps its own copy of this walk —
    its reference dates ARE drawn from every recorded series and its output is pinned by
    an equivalence test, so it must not change shape here.

    Positional fallback (aligned dicts absent/empty — EODHD, fakes, hand-built
    fixtures): index ``i`` of the NaN-dropped list per series, the pre-existing
    convention.
    """
    wanted = [k for k in keys if k in _SERIES_ATTR]
    aligned = getattr(f, "aligned_annual", None) or {}
    ends = getattr(f, "aligned_period_ends", None) or {}
    usable = {k for k in wanted
              if aligned.get(k) and ends.get(k)
              and len(aligned[k]) == len(ends[k])}
    if usable:
        by_date: dict[str, dict[str, float]] = {}
        for key in usable:
            vals: dict[str, float] = {}
            for d, v in zip(ends[key], aligned[key]):
                if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue                      # a hole stays a hole
                vals[str(d)] = float(v)
            by_date[key] = vals
        dates = sorted({d for m in by_date.values() for d in m}, reverse=True)
        out: list[dict[str, float | None]] = []
        for i in range(periods):
            ref = dates[i] if i < len(dates) else None
            out.append({k: (by_date.get(k, {}).get(ref) if ref is not None else None)
                        for k in wanted})
        return out
    return [{k: _newest_first_at(getattr(f, _SERIES_ATTR[k], None) or [], i)
             for k in wanted} for i in range(periods)]


@dataclass(frozen=True)
class ForensicResult:
    """One forensic measure's outcome.

    ``value`` is None for NOT-EVAL (rule 3 — never a zero, never a phantom fail),
    ``note`` is the human sentence the screen criterion reports, and ``detail`` is the
    short tag the rank factor's ``source_fn`` appends ("single-year assets",
    "cross-currency: market cap vs statements") so the report discloses which path was
    taken per name.
    """

    value: float | None = None
    note: str = ""
    detail: str = ""


def _cross_currency(fundamentals) -> bool:
    """True iff the listing currency and the STATEMENTS currency are both KNOWN and
    DIFFER — the case where a market-cap term and a balance-sheet term cannot be summed.

    Unknown on either side returns False (evaluate normally), the same discipline as
    ``_non_usd_currency``: absent provider data must never manufacture an abstention.
    This is the rule-8 guard for a measure that MIXES the two currencies inside one
    formula, where ``_non_usd_currency`` guards a USD-denominated threshold.
    """
    price = (getattr(fundamentals, "currency", None) or "").strip().upper()
    acct = (getattr(fundamentals, "financial_currency", None) or "").strip().upper()
    return bool(price and acct and price != acct)


CROSS_CURRENCY_NOTE = "cross-currency: market cap vs statements"

_ACCRUAL_KEYS = ("net_income", "operating_cash_flow", "total_assets")


def accrual_ratio(fundamentals) -> ForensicResult:
    """Sloan accrual ratio: (net income − operating cash flow) / average total assets.

    The share of last year's reported profit that arrived as an accounting entry rather
    than as cash. LOW is better — a large positive ratio is profit the business has not
    collected, the classic earnings-quality warning.

    Inputs are PERIOD-MATCHED at the newest reference period (``period_matched``), with
    the positional fallback. The denominator averages the current and PRIOR year's total
    assets; when the prior year is absent it falls back to the CURRENT year alone and
    says so ("single-year assets") rather than silently changing the base.

    NOT-EVAL (never a zero, never a phantom fail) on a missing numerator input or on
    total assets <= 0 — a non-positive asset base makes the ratio meaningless.

    CURRENCY: numerator and denominator are both statement-currency figures, so the
    ratio is currency-INVARIANT and needs no rule-8 abstention.
    """
    if fundamentals is None:
        return ForensicResult(note="accrual ratio unavailable: no fundamentals",
                              detail="no fundamentals")
    now, prior = period_matched(fundamentals, _ACCRUAL_KEYS, periods=2)
    ni, ocf, ta = now["net_income"], now["operating_cash_flow"], now["total_assets"]
    absent = [label for label, v in (("net income", ni),
                                     ("operating cash flow", ocf)) if v is None]
    if absent:
        reason = f"{' and '.join(absent)} missing"
        return ForensicResult(note=f"accrual ratio unavailable: {reason}", detail=reason)
    if ta is None or ta <= 0:
        reason = "total assets missing or non-positive"
        return ForensicResult(note=f"accrual ratio unavailable: {reason}", detail=reason)
    ta_prior = prior["total_assets"]
    if ta_prior is not None and ta_prior > 0:
        denominator, basis, detail = (ta + ta_prior) / 2.0, "average total assets", ""
    else:
        denominator, basis, detail = ta, "total assets", "single-year assets"
    ratio = (ni - ocf) / denominator
    note = (f"accrual ratio = (net income − operating cash flow) / {basis} "
            f"= {ratio:.4f}")
    if detail:
        note += f" ({detail}: prior-year assets unavailable)"
    return ForensicResult(value=ratio, note=note, detail=detail)


# Altman (1968) Z-Score, the public-manufacturer coefficients. Kept as named constants
# rather than inline magic numbers so the formula reads as the published one.
_ALTMAN_KEYS = ("current_assets", "current_liabilities", "retained_earnings", "ebit",
                "total_assets", "total_liabilities", "total_revenue")
_ALTMAN_WC, _ALTMAN_RE, _ALTMAN_EBIT = 1.2, 1.4, 3.3
_ALTMAN_EQUITY, _ALTMAN_SALES = 0.6, 1.0


def altman_z_score(fundamentals) -> ForensicResult:
    """Altman Z-Score — 1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MktCap/TL + 1.0·Sales/TA.

    A distress measure: HIGHER is safer (the published reading is above 2.99 safe, below
    1.81 distressed). This lens RANKS it, so those bands are context for a reader, never
    a gate.

    Working capital is current assets minus current liabilities. Every statement line is
    PERIOD-MATCHED at the newest reference period (``period_matched``, positional
    fallback); the market value of equity is the CURRENT ``market_cap`` scalar.

    NOT-EVAL on any missing input, on total assets <= 0 or total liabilities <= 0 (both
    are denominators), and — house rule 8 — when the listing currency and the statements
    currency are BOTH known and DIFFER: the fourth term divides a market cap quoted in
    one currency by liabilities reported in another, so the five terms could not be
    summed without inventing an exchange rate. We ABSTAIN instead; no FX conversion
    happens here (an FX-aware variant belongs with the valuation-band FX work). A
    MISSING currency on either side evaluates normally — absent data must not
    manufacture an abstention.
    """
    if fundamentals is None:
        return ForensicResult(note="Altman Z-Score unavailable: no fundamentals",
                              detail="no fundamentals")
    if _cross_currency(fundamentals):
        return ForensicResult(note=f"Altman Z-Score unavailable: {CROSS_CURRENCY_NOTE}",
                              detail=CROSS_CURRENCY_NOTE)
    (now,) = period_matched(fundamentals, _ALTMAN_KEYS, periods=1)
    market_cap = getattr(fundamentals, "market_cap", None)
    inputs = {
        "current assets": now["current_assets"],
        "current liabilities": now["current_liabilities"],
        "retained earnings": now["retained_earnings"],
        "EBIT": now["ebit"],
        "total assets": now["total_assets"],
        "total liabilities": now["total_liabilities"],
        "revenue": now["total_revenue"],
        "market cap": market_cap if isinstance(market_cap, (int, float))
        and not isinstance(market_cap, bool) else None,
    }
    absent = [label for label, v in inputs.items() if v is None]
    if absent:
        reason = f"{', '.join(absent)} missing"
        return ForensicResult(note=f"Altman Z-Score unavailable: {reason}",
                              detail=reason)
    ta, tl = inputs["total assets"], inputs["total liabilities"]
    if ta <= 0 or tl <= 0:
        reason = "total assets or total liabilities non-positive"
        return ForensicResult(note=f"Altman Z-Score unavailable: {reason}",
                              detail=reason)
    working_capital = inputs["current assets"] - inputs["current liabilities"]
    z = (_ALTMAN_WC * working_capital / ta
         + _ALTMAN_RE * inputs["retained earnings"] / ta
         + _ALTMAN_EBIT * inputs["EBIT"] / ta
         + _ALTMAN_EQUITY * inputs["market cap"] / tl
         + _ALTMAN_SALES * inputs["revenue"] / ta)
    return ForensicResult(
        value=z,
        note=(f"Altman Z-Score = {z:.2f} (1.2·working capital + 1.4·retained earnings "
              f"+ 3.3·EBIT + 1.0·revenue, each over total assets, plus 0.6·market cap "
              f"over total liabilities)"))


# --------------------------------------------------------------------------- #
# Aggregate screen
# --------------------------------------------------------------------------- #
def run_strategy_screen(
    fundamentals: Fundamentals,
    dividends: list[DividendEvent],
    *,
    min_yield: float,
    max_payout: float,
    min_market_cap: float,
    min_growth_years: int,
    last_close: float | None = None,
) -> ScreenResult:
    """Compose the primitives into the full (dividend-aristocrat) screen.

    Retained as the FROZEN equivalence reference that ``run_screen`` (the generic
    registry-driven runner) is pinned byte-identical against — renamed from
    ``run_dividend_aristocrat_screen`` with the neutral ledger-tool rename; the
    math and output are unchanged. Thresholds are injected by the caller (which
    reads them from the versioned strategy YAML), so this function holds no policy
    of its own — only math. `last_close` enables deterministic yield derivation
    (see min_yield_criterion).
    """
    criteria = [
        min_yield_criterion(fundamentals, min_yield=min_yield,
                            last_close=last_close),
        max_payout_criterion(fundamentals, max_payout=max_payout),
        min_market_cap_criterion(fundamentals, min_market_cap=min_market_cap),
        min_growth_streak_criterion(dividends, min_years=min_growth_years),
    ]

    flags: list[str] = []
    for c in criteria:
        if c.passed is None:
            flags.append(f"unverifiable:{c.name}:{c.note}")

    return ScreenResult(ticker=fundamentals.ticker, criteria=criteria, flags=flags)
