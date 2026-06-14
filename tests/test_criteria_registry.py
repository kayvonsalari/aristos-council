"""Criterion registry tests, with the EQUIVALENCE TEST as the safety net.

The registry refactor's whole correctness claim is: the generic, registry-driven
screen produces BYTE-IDENTICAL results to the original hardcoded
``run_dividend_aristocrat_screen``. test_equivalent_to_legacy_screen pins that
field-for-field on a fixed set of fundamentals shapes (JNJ/MO/BRK-B/O + the edge
cases that exercise pass / fail / null / no-dividend / unverifiable paths).
"""

from __future__ import annotations

from datetime import date

import pytest

from aristos_council.data.adapter import DividendEvent, Fundamentals
from aristos_council.tools.criteria.registry import (
    REGISTRY,
    Criterion,
    CriterionSelection,
    Evidence,
    run_screen,
    validate_selections,
)
from aristos_council.tools.screening import run_dividend_aristocrat_screen

# The dividend strategy's selections at the shipped v1 thresholds.
DIVIDEND = [
    CriterionSelection("min_dividend_yield", 0.025),
    CriterionSelection("max_payout_ratio", 0.75),
    CriterionSelection("min_market_cap", 10_000_000_000),
    CriterionSelection("min_dividend_growth_streak", 25),
]


def _fund(ticker, **kw) -> Fundamentals:
    base = dict(ticker=ticker)
    base.update(kw)
    return Fundamentals(**base)  # type: ignore[arg-type]


def _annual(start_year: int, amounts: list[float]) -> list[DividendEvent]:
    return [DividendEvent(ex_date=date(start_year + i, 6, 1), amount=a)
            for i, a in enumerate(amounts)]


def _monthly(start_year: int, years: int, base: float, step: float):
    out = []
    for y in range(years):
        for m in range(1, 13):
            out.append(DividendEvent(ex_date=date(start_year + y, m, 1),
                                     amount=base + step * y))
    return out


# (fundamentals, dividends, last_close) shapes exercising every branch.
_long = [1.0 + 0.1 * i for i in range(30)]   # 30 rising years -> streak passes
_short = [1.0 + 0.05 * i for i in range(16)]  # 16 rising years -> streak fails 25

FIXTURES = [
    # JNJ: payer, passes yield/payout/mcap, long streak
    (_fund("JNJ", market_cap=3.8e11, dividend_per_share=4.96, payout_ratio=0.45),
     _annual(1996, _long), 155.0),
    # MO: high-yield payer, payout FAILS the ceiling, shorter streak
    (_fund("MO", market_cap=7.7e10, dividend_per_share=3.92, payout_ratio=0.88),
     _annual(2010, _short), 44.0),
    # BRK-B: no current dividend, no history (yield FAIL, payout NOT-EVAL)
    (_fund("BRK-B", market_cap=8.8e11), [], 410.0),
    # O: monthly payer, high yield, payout near ceiling
    (_fund("O", market_cap=5.0e10, dividend_per_share=3.08, payout_ratio=0.76),
     _monthly(2014, 10, 0.20, 0.01), 55.0),
    # payer but NO price -> yield UNVERIFIABLE
    (_fund("NOPRICE", market_cap=2e10, dividend_per_share=2.0, payout_ratio=0.5),
     _annual(2010, _short), None),
    # payer, EMPTY history -> streak null (unverifiable)
    (_fund("EMPTY", market_cap=2e10, dividend_per_share=2.0, payout_ratio=0.5),
     [], 100.0),
    # negative payout -> payout FAIL (not unverifiable)
    (_fund("NEG", market_cap=2e10, dividend_per_share=2.0, payout_ratio=-0.2),
     _annual(2018, [1.0, 1.1, 1.2, 1.3, 1.4]), 100.0),
    # missing market cap -> market_cap NOT-EVAL
    (_fund("NOMC", dividend_per_share=2.0, payout_ratio=0.5),
     _annual(1996, _long), 100.0),
]


@pytest.mark.parametrize("fund,divs,last_close", FIXTURES,
                         ids=[f[0].ticker for f in FIXTURES])
def test_equivalent_to_legacy_screen(fund, divs, last_close):
    """Registry-driven screen == original hardcoded screen, field-for-field."""
    new = run_screen(DIVIDEND, Evidence(fund, divs, last_close),
                     ticker=fund.ticker)
    legacy = run_dividend_aristocrat_screen(
        fund, divs, min_yield=0.025, max_payout=0.75,
        min_market_cap=10_000_000_000, min_growth_years=25,
        last_close=last_close,
    )
    assert new == legacy                      # whole ScreenResult (frozen dataclass)
    assert new.criteria == legacy.criteria    # explicit: per-criterion identity
    assert new.flags == legacy.flags


# --------------------------------------------------------------------------- #
# Registry contents + validation
# --------------------------------------------------------------------------- #
def test_registry_holds_the_four_dividend_criteria():
    assert set(REGISTRY) == {
        "min_dividend_yield", "max_payout_ratio",
        "min_market_cap", "min_dividend_growth_streak",
    }
    assert all(isinstance(c, Criterion) for c in REGISTRY.values())


def test_each_criterion_declares_required_evidence():
    assert REGISTRY["min_dividend_yield"].requires == ("fundamentals",)
    assert REGISTRY["min_dividend_growth_streak"].requires == ("dividends",)


def test_validate_ok_for_dividend_strategy():
    assert validate_selections(DIVIDEND) == []


def test_validate_flags_unknown_criterion():
    problems = validate_selections([CriterionSelection("ebitda_coverage", 3.0)])
    assert any("unknown criterion" in p for p in problems)


def test_validate_flags_out_of_range_threshold():
    # yield must be in [0, 1]
    problems = validate_selections([CriterionSelection("min_dividend_yield", 1.5)])
    assert any("out of range" in p for p in problems)


def test_validate_flags_missing_evidence():
    # the streak criterion needs dividend history; deny it and it must complain
    problems = validate_selections(
        [CriterionSelection("min_dividend_growth_streak", 25)],
        available=("fundamentals", "last_close"),
    )
    assert any("requires evidence not available" in p for p in problems)


def test_run_screen_raises_on_unknown_criterion():
    with pytest.raises(KeyError):
        run_screen([CriterionSelection("nope", 1.0)], Evidence(), ticker="X")
