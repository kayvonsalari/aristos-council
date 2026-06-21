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

from dataclasses import dataclass

from ..data.adapter import DividendEvent, Fundamentals

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
    dividends: list[DividendEvent], *, min_years: int
) -> CriterionResult:
    streak, note = consecutive_dividend_growth_years(dividends)
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
# Growth / quality primitives (Sprint 4B) — pure math, NOT-EVAL on missing data
# --------------------------------------------------------------------------- #
def revenue_cagr(
    revenue: list[float], years: int
) -> tuple[float | None, str]:
    """Compound annual growth rate over ``years`` from a NEWEST-FIRST series.

    CAGR = (rev[0] / rev[years]) ** (1/years) - 1. Returns (None, note) when it
    cannot be computed honestly: fewer than ``years``+1 clean annual points, or
    a non-positive endpoint (a negative/zero base destroys the ratio; a
    fractional power of a negative would also go complex).
    """
    if years < 1:
        return None, "years must be >= 1"
    if len(revenue) < years + 1:
        return None, (f"insufficient revenue history: need {years + 1} annual "
                      f"points, have {len(revenue)}")
    latest, base = revenue[0], revenue[years]
    if base <= 0 or latest <= 0:
        return None, (f"revenue non-positive at an endpoint (base={base}, "
                      f"latest={latest}); CAGR undefined")
    cagr = (latest / base) ** (1.0 / years) - 1.0
    return cagr, f"{years}y revenue CAGR = (rev[0]/rev[{years}])^(1/{years}) - 1"


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


def peg_ratio(
    pe_ratio: float | None, growth_rate: float | None
) -> tuple[float | None, str]:
    """PEG = P/E / (growth_rate * 100), with ``growth_rate`` a decimal CAGR.

    Returns (None, note) when PEG is undefined: no positive P/E (negative or
    missing earnings) or a non-positive growth rate. The caller supplies the
    in-house revenue CAGR as ``growth_rate`` — never a provider forward estimate
    — so the figure stays auditable.
    """
    if pe_ratio is None or pe_ratio <= 0:
        return None, "no positive P/E (negative or missing earnings); PEG undefined"
    if growth_rate is None or growth_rate <= 0:
        return None, "growth rate <= 0 or unavailable; PEG undefined"
    peg = pe_ratio / (growth_rate * 100.0)
    return peg, "PEG = P/E / (in-house revenue CAGR x 100)"


# --------------------------------------------------------------------------- #
# Aggregate screen
# --------------------------------------------------------------------------- #
def run_dividend_aristocrat_screen(
    fundamentals: Fundamentals,
    dividends: list[DividendEvent],
    *,
    min_yield: float,
    max_payout: float,
    min_market_cap: float,
    min_growth_years: int,
    last_close: float | None = None,
) -> ScreenResult:
    """Compose the primitives into the full aristocrat screen.

    Thresholds are injected by the caller (which reads them from the versioned
    strategy YAML), so this function holds no policy of its own — only math.
    `last_close` enables deterministic yield derivation (see
    min_yield_criterion).
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
