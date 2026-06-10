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
def min_yield_criterion(
    fundamentals: Fundamentals, *, min_yield: float
) -> CriterionResult:
    """Dividend yield at or above the strategy floor.

    `min_yield` and the observed yield are both decimals (0.025 == 2.5%).
    """
    y = fundamentals.dividend_yield
    if y is None:
        return CriterionResult(
            name="min_dividend_yield",
            passed=None,
            observed=None,
            threshold=min_yield,
            note="dividend_yield unavailable from provider",
        )
    return CriterionResult(
        name="min_dividend_yield",
        passed=y >= min_yield,
        observed=y,
        threshold=min_yield,
    )


def max_payout_criterion(
    fundamentals: Fundamentals, *, max_payout: float
) -> CriterionResult:
    """Payout ratio at or below the sustainability ceiling.

    A payout ratio above the ceiling means dividends may be funded beyond
    earnings — the opposite of aristocrat-grade durability. Negative payout
    (negative earnings) is treated as a FAIL, not unverifiable, because it is a
    meaningful signal, with an explanatory note.
    """
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

    Method: sum dividend amounts per calendar year, then walk from the most
    recent COMPLETE year backwards counting strictly-increasing annual totals.

    Honesty caveat (returned in `note`): on yfinance the dividend history is
    often too short or irregular to verify the canonical 25-year aristocrat
    streak. This function does not lie about that — it reports what the series
    supports and flags the limitation. EODHD's longer history is the real fix.
    """
    if not dividends:
        return None, "no dividend events available"

    by_year: dict[int, float] = {}
    for ev in dividends:
        by_year[ev.ex_date.year] = by_year.get(ev.ex_date.year, 0.0) + ev.amount

    years_sorted = sorted(by_year)
    # Drop the latest year if it looks partial relative to history depth — we
    # can't know it's complete, so excluding it avoids a false "cut" signal.
    if len(years_sorted) < 2:
        return None, "insufficient annual dividend history (<2 years)"

    # Walk most-recent-complete backwards.
    complete_years = years_sorted[:-1] if len(years_sorted) >= 2 else years_sorted
    streak = 0
    for i in range(len(complete_years) - 1, 0, -1):
        if by_year[complete_years[i]] > by_year[complete_years[i - 1]]:
            streak += 1
        else:
            break

    note = (
        f"estimated from {len(years_sorted)} years of provider dividend data; "
        "treat as a floor, not a verified aristocrat streak"
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
) -> ScreenResult:
    """Compose the primitives into the full aristocrat screen.

    Thresholds are injected by the caller (which reads them from the versioned
    strategy YAML), so this function holds no policy of its own — only math.
    """
    criteria = [
        min_yield_criterion(fundamentals, min_yield=min_yield),
        max_payout_criterion(fundamentals, max_payout=max_payout),
        min_market_cap_criterion(fundamentals, min_market_cap=min_market_cap),
        min_growth_streak_criterion(dividends, min_years=min_growth_years),
    ]

    flags: list[str] = []
    for c in criteria:
        if c.passed is None:
            flags.append(f"unverifiable:{c.name}:{c.note}")

    return ScreenResult(ticker=fundamentals.ticker, criteria=criteria, flags=flags)
