"""PROFIT-GUARD-DOC-1 (Batch 19A, A13) - the profit guard says which figure it read.

Ford read "does not apply - no operating profit" on all five profit lenses four days after it read
"SELL on 3 of 3 votes". Nothing said whether the guard looked at the latest fiscal year, the
trailing twelve months or the latest quarter. It reads the LATEST FISCAL YEAR; the reason now says so
and, when the provider carried a dated series, the year-end it belongs to.
"""
from __future__ import annotations

from aristos_council.data.adapter import Fundamentals
from aristos_council.operating_profit import (
    NO_OPERATING_PROFIT_REASON, no_operating_profit_reason, operating_profit_basis)


def test_a_dated_series_names_its_fiscal_year_end():
    f = Fundamentals(ticker="F", operating_income=[-1.0, 5.0],
                     aligned_annual={"operating_income": [-1.0, 5.0]},
                     aligned_period_ends={"operating_income": ["2025-12-31", "2024-12-31"]})
    assert operating_profit_basis(f) == "fiscal year to Dec 2025"
    assert no_operating_profit_reason(f) == "no operating profit (fiscal year to Dec 2025)"


def test_a_hole_in_the_dated_series_does_not_misdate_the_figure():
    """The positional list drops a NaN cell; the dated one keeps it. The date is the newest
    cell that HAS a value - the one the guard actually read."""
    f = Fundamentals(ticker="F", operating_income=[-1.0, 5.0],
                     aligned_annual={"operating_income": [None, -1.0, 5.0]},
                     aligned_period_ends={"operating_income":
                                          ["2026-06-30", "2025-12-31", "2024-12-31"]})
    assert operating_profit_basis(f) == "fiscal year to Dec 2025"


def test_without_dates_it_says_latest_fiscal_year_and_invents_none():
    f = Fundamentals(ticker="F", operating_income=[-1.0])
    assert no_operating_profit_reason(f) == "no operating profit (latest fiscal year)"


def test_the_basis_follows_the_series_the_guard_read():
    """No operating income on file -> EBIT is read, and EBIT's own dates are used."""
    f = Fundamentals(ticker="F", ebit=[-2.0],
                     period_ends={"ebit": ["2025-09-30"], "operating_income": ["2020-01-01"]})
    assert operating_profit_basis(f) == "fiscal year to Sep 2025"


def test_the_reason_still_starts_with_the_constant_the_grouping_matches_on():
    assert no_operating_profit_reason(Fundamentals(ticker="F")).startswith(
        NO_OPERATING_PROFIT_REASON)
