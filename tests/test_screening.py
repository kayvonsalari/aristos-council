"""Unit tests for deterministic screening tools.

These are the most important tests in Phase 1: they pin the behaviour of the
ONLY place math is allowed to happen. The themes are deliberate:
- pass / fail / unverifiable are three distinct outcomes,
- missing data never silently becomes a passing zero,
- the dividend-growth streak is honest about what short history can prove.
"""

from __future__ import annotations

from datetime import date

import pytest

from aristos_council.data.adapter import DividendEvent, Fundamentals
from aristos_council.tools.screening import (
    consecutive_dividend_growth_years,
    max_payout_criterion,
    min_growth_streak_criterion,
    min_market_cap_criterion,
    min_yield_criterion,
    run_dividend_aristocrat_screen,
)


def _fund(**kw) -> Fundamentals:
    base = dict(ticker="TEST")
    base.update(kw)
    return Fundamentals(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# min_yield — derived from DPS / price; provider field ignored (units bug
# found live by the Critic on the NVDA run: percent-form 0.5 passed a
# decimal threshold of 0.025)
# --------------------------------------------------------------------------- #
def test_yield_derived_pass():
    r = min_yield_criterion(_fund(dividend_per_share=4.0),
                            min_yield=0.025, last_close=100.0)
    assert r.passed is True
    assert abs(r.observed - 0.04) < 1e-12
    assert "derived" in r.note


def test_yield_derived_fail_nvda_case():
    # NVDA-like: $1.00 DPS on a $200 stock = 0.5% — must FAIL a 2.5% floor.
    # The old provider-field comparison wrongly passed this.
    r = min_yield_criterion(_fund(dividend_per_share=1.0,
                                  dividend_yield=0.5),  # ambiguous field present
                            min_yield=0.025, last_close=200.0)
    assert r.passed is False
    assert abs(r.observed - 0.005) < 1e-12


def test_yield_provider_field_alone_is_unverifiable():
    # Provider yield present but no price: we refuse to trust it.
    r = min_yield_criterion(_fund(dividend_yield=2.54), min_yield=0.025)
    assert r.passed is None
    assert "ambiguous units" in r.note


def test_yield_boundary_is_inclusive():
    r = min_yield_criterion(_fund(dividend_per_share=2.5),
                            min_yield=0.025, last_close=100.0)
    assert r.passed is True


# --------------------------------------------------------------------------- #
# max_payout
# --------------------------------------------------------------------------- #
def test_payout_pass():
    r = max_payout_criterion(_fund(payout_ratio=0.5), max_payout=0.75)
    assert r.passed is True


def test_payout_fail_over_ceiling():
    r = max_payout_criterion(_fund(payout_ratio=0.9), max_payout=0.75)
    assert r.passed is False


def test_negative_payout_is_fail_not_unverifiable():
    r = max_payout_criterion(_fund(payout_ratio=-0.2), max_payout=0.75)
    assert r.passed is False
    assert "negative" in r.note


def test_payout_unverifiable_when_missing():
    r = max_payout_criterion(_fund(payout_ratio=None), max_payout=0.75)
    assert r.passed is None


# --------------------------------------------------------------------------- #
# market cap
# --------------------------------------------------------------------------- #
def test_market_cap_pass():
    r = min_market_cap_criterion(_fund(market_cap=2e10), min_market_cap=1e10)
    assert r.passed is True


def test_market_cap_fail():
    r = min_market_cap_criterion(_fund(market_cap=5e9), min_market_cap=1e10)
    assert r.passed is False


# --------------------------------------------------------------------------- #
# dividend growth streak
# --------------------------------------------------------------------------- #
def _annual_divs(start_year: int, amounts: list[float]) -> list[DividendEvent]:
    """One dividend per year, ascending years."""
    return [
        DividendEvent(ex_date=date(start_year + i, 6, 1), amount=a)
        for i, a in enumerate(amounts)
    ]


def test_streak_counts_strictly_increasing_complete_years():
    # 2018..2023 amounts increasing; latest (2023) dropped as possibly-partial,
    # so complete years are 2018..2022 -> 4 consecutive increases.
    divs = _annual_divs(2018, [1.0, 1.1, 1.2, 1.3, 1.4, 1.5])
    streak, note = consecutive_dividend_growth_years(divs)
    assert streak == 4
    assert "floor" in note


def test_streak_breaks_on_cut():
    # Years: 2018=1.0 2019=1.1 2020=1.2 2021=0.9(CUT) 2022=1.0 2023=1.1
    # Latest year 2023 is dropped as possibly-partial -> complete years end 2022.
    # Walk back: 2022(1.0)>2021(0.9) up (streak 1); 2021(0.9)>2020(1.2)? no -> stop.
    divs = _annual_divs(2018, [1.0, 1.1, 1.2, 0.9, 1.0, 1.1])
    streak, _ = consecutive_dividend_growth_years(divs)
    assert streak == 1


def test_streak_none_when_no_dividends():
    streak, note = consecutive_dividend_growth_years([])
    assert streak is None
    assert "no dividend" in note


def test_streak_none_when_too_short():
    streak, note = consecutive_dividend_growth_years(_annual_divs(2022, [1.0]))
    assert streak is None
    assert "insufficient" in note


def test_growth_criterion_unverifiable_propagates():
    r = min_growth_streak_criterion([], min_years=25)
    assert r.passed is None


def test_growth_criterion_fail_when_streak_too_short():
    divs = _annual_divs(2018, [1.0, 1.1, 1.2, 1.3, 1.4, 1.5])  # streak 4
    r = min_growth_streak_criterion(divs, min_years=25)
    assert r.passed is False
    assert r.observed == 4.0


# --------------------------------------------------------------------------- #
# aggregate screen
# --------------------------------------------------------------------------- #
def test_screen_flags_unverifiable_streak():
    f = _fund(dividend_per_share=4.0, payout_ratio=0.5, market_cap=2e10)
    result = run_dividend_aristocrat_screen(
        f,
        dividends=[],  # forces streak unverifiable
        min_yield=0.025,
        max_payout=0.75,
        min_market_cap=1e10,
        min_growth_years=25,
        last_close=100.0,
    )
    assert any("unverifiable:min_dividend_growth_streak" in fl for fl in result.flags)
    # The three quantified criteria evaluated and passed...
    assert all(c.passed for c in result.evaluated)
    # ...but the screen is NOT a clean all-pass because the streak is unproven.
    assert len(result.unverifiable) == 1


def test_screen_passes_all_evaluated_with_full_data():
    f = _fund(dividend_per_share=4.0, payout_ratio=0.5, market_cap=2e10)
    divs = _annual_divs(1990, [1.0 + 0.1 * i for i in range(33)])  # long streak
    result = run_dividend_aristocrat_screen(
        f,
        dividends=divs,
        min_yield=0.025,
        max_payout=0.75,
        min_market_cap=1e10,
        min_growth_years=25,
        last_close=100.0,
    )
    assert result.flags == []
    assert result.passes_all_evaluated is True
