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
