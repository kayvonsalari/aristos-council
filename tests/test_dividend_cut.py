"""CRIT-NOCUT-1 — "no dividend cut in the last N years", a different question from a streak.

Defensive Income (conservative_plus_v1 / conservative_screen_v1) on the 135-name oil
dividend cohort ranked 3 names on 2026-09-16. Its rule table: min_dividend_streak >= 10
failed 126 of 128 tested, while the other five rules were survivable (yield 15, payout-vs-
FCF 33, size 14, debt 9, momentum 2). One rule written for staples aristocrats removed an
entire sector.

The distinction the sector needs, and the reason a low streak threshold is NOT the answer:

  * a streak of 3 asserts the dividend ROSE three times;
  * a company that HELD its dividend flat through a downturn has a streak of 0 and has cut
    nothing — and for a cyclical payer that flat dividend is precisely the evidence of
    durability.

So flat must PASS this criterion and FAIL a streak test. Chevron (a 9-year streak), Kinder
Morgan and Hess Midstream (8) are not cutters and were excluded as though they were; BP,
which halved in 2020, would fail any honest income test and must still fail this one.

Built on the SAME calendar-year totals the streak uses — same event source, same adjusted
per-share values, same "drop the latest, possibly-incomplete year" rule — so the two can
never disagree about what a year paid.
"""

from __future__ import annotations

from datetime import date

import pytest

from aristos_council.data.adapter import DividendEvent
from aristos_council.tools.criteria.registry import REGISTRY, Evidence, run_screen
from aristos_council.tools.screening import (
    dividend_cuts_by_calendar_year,
    max_dividend_cuts_criterion,
    min_growth_streak_criterion,
)


def _events(*pairs, splits=1):
    """Calendar-year (year, total) pairs as ``splits`` equal payments per year."""
    out = []
    for year, total in pairs:
        for i in range(splits):
            out.append(DividendEvent(ex_date=date(year, 1 + i * (12 // splits), 15),
                                     amount=total / splits))
    return out


# The latest calendar year is always dropped as possibly incomplete, so every fixture
# carries a trailing partial year that must NOT be read as a cut.
_PARTIAL = (2026, 0.40)


def _cuts(events, years=5):
    return max_dividend_cuts_criterion(events, years=years)


# --------------------------------------------------------------------------- #
# The four contract outcomes
# --------------------------------------------------------------------------- #
def test_flat_for_five_years_passes():
    """The case the streak rule gets wrong: held, never cut."""
    r = _cuts(_events((2020, 1.0), (2021, 1.0), (2022, 1.0), (2023, 1.0),
                      (2024, 1.0), (2025, 1.0), _PARTIAL))
    assert r.passed is True
    assert r.observed == 0.0
    assert "no calendar year paid less than the year before" in r.note


def test_a_cut_fails_with_the_cut_size_and_the_year():
    """BP's shape: the 2021 total is half the 2020 total."""
    r = _cuts(_events((2020, 2.0), (2021, 1.0), (2022, 1.0), (2023, 1.0),
                      (2024, 1.0), (2025, 1.0), _PARTIAL))
    assert r.passed is False
    assert r.observed == 0.50                      # the size of the cut, as a fraction
    assert "dividend cut in 2021" in r.note
    assert "fell 50% against 2020" in r.note


def test_the_largest_cut_is_the_one_reported():
    r = _cuts(_events((2020, 2.0), (2021, 1.8), (2022, 0.9), (2023, 1.0),
                      (2024, 1.0), (2025, 1.0), _PARTIAL))
    assert r.passed is False
    assert r.observed == 0.50                      # 1.8 -> 0.9, not 2.0 -> 1.8
    assert "dividend cut in 2022" in r.note


def test_too_little_history_is_not_tested_never_failed():
    """House rule 3: a rule that cannot be evaluated never excludes anyone."""
    r = _cuts(_events((2024, 1.0), (2025, 1.0), _PARTIAL))
    assert r.passed is None and r.observed is None
    assert "only 2 complete years of dividend history" in r.note
    assert "the rule needs 6" in r.note


def test_no_dividend_history_is_not_tested_here():
    """A non-payer is failed by the YIELD criterion. Absence is not a cut."""
    r = _cuts([])
    assert r.passed is None and r.observed is None
    assert r.note == "no dividend history"


def test_a_raise_every_year_passes_and_the_streak_criterion_agrees():
    """The two rules must agree on a name that satisfies both."""
    events = _events((2020, 1.0), (2021, 1.1), (2022, 1.2), (2023, 1.3),
                     (2024, 1.4), (2025, 1.5), _PARTIAL)
    assert _cuts(events).passed is True
    assert min_growth_streak_criterion(events, min_years=5).passed is True


def test_flat_passes_here_and_fails_the_streak_rule():
    """The whole point of the new criterion, stated as one assertion."""
    events = _events((2020, 1.0), (2021, 1.0), (2022, 1.0), (2023, 1.0),
                     (2024, 1.0), (2025, 1.0), _PARTIAL)
    assert _cuts(events).passed is True
    assert min_growth_streak_criterion(events, min_years=5).passed is False


# --------------------------------------------------------------------------- #
# Shared arithmetic with the streak: same totals, same rules
# --------------------------------------------------------------------------- #
def test_the_latest_incomplete_year_is_never_read_as_a_cut():
    """A mid-year run has paid only the interim. That is not a cut."""
    r = _cuts(_events((2020, 1.0), (2021, 1.0), (2022, 1.0), (2023, 1.0),
                      (2024, 1.0), (2025, 1.0), (2026, 0.25)))
    assert r.passed is True


def test_a_cadence_change_is_not_a_cut():
    """Annual -> interim+final compares like-for-like YEAR TOTALS, not payments."""
    annual = _events((2020, 1.0), (2021, 1.0), (2022, 1.0))
    semi = _events((2023, 1.0), (2024, 1.0), (2025, 1.0), _PARTIAL, splits=2)
    assert _cuts(annual + semi).passed is True


def test_only_the_window_is_examined():
    """An old cut outside the window does not fail a shorter rule."""
    events = _events((2018, 4.0), (2019, 1.0), (2020, 1.0), (2021, 1.0),
                     (2022, 1.0), (2023, 1.0), (2024, 1.0), (2025, 1.0), _PARTIAL)
    assert _cuts(events, years=5).passed is True        # 2020..2025: flat
    assert _cuts(events, years=7).passed is False       # reaches back to the 2019 cut


def test_the_primitive_and_the_criterion_report_the_same_number():
    events = _events((2020, 2.0), (2021, 1.0), (2022, 1.0), (2023, 1.0),
                     (2024, 1.0), (2025, 1.0), _PARTIAL)
    worst, note = dividend_cuts_by_calendar_year(events, years=5)
    r = _cuts(events)
    assert r.observed == worst and r.note == note


# --------------------------------------------------------------------------- #
# Registered, self-describing, and renderable
# --------------------------------------------------------------------------- #
def test_the_criterion_is_registered_and_self_describes():
    c = REGISTRY.get("max_dividend_cuts")
    assert c is not None
    assert c.label == "Dividend cuts in the last N years"
    assert c.requires == ("dividends",)
    assert c.threshold_param.default == 5
    # The GLOSSARY must draw the distinction the criterion exists for.
    assert "cut" in c.glossary.lower() and "flat" in c.glossary.lower()


def test_the_rule_phrase_reads_as_a_rule_not_as_a_limit():
    """Its threshold is a WINDOW, not a cap on the observed value — the generated
    'at most 5' would compare a cut percentage against a count of years."""
    c = REGISTRY.get("max_dividend_cuts")
    assert c.threshold_text.format(threshold=5) == (
        "no year paid less than the year before, across the last 5 complete years")


def test_a_strategy_can_select_it_by_name():
    events = _events((2020, 2.0), (2021, 1.0), (2022, 1.0), (2023, 1.0),
                     (2024, 1.0), (2025, 1.0), _PARTIAL)
    from aristos_council.tools.criteria.registry import CriterionSelection

    out = run_screen([CriterionSelection(name="max_dividend_cuts", threshold=5)],
                     Evidence(fundamentals=None, dividends=events, last_close=None),
                     ticker="BP")
    result = out.criteria[0]
    assert result.name == "max_dividend_cuts" and result.passed is False
    assert result.observed == 0.50


def test_flat_is_not_a_cut_at_the_same_tolerance_the_streak_uses():
    """A rounding-level wobble in a year total is FLAT, not a reduction — the same
    +/-0.5% tolerance ``dividend_streak`` applies to the same totals. Without it the two
    readings of one history would disagree about whether a year was a cut."""
    wobble = _events((2020, 1.000), (2021, 0.998), (2022, 1.000), (2023, 1.000),
                     (2024, 1.000), (2025, 1.000), _PARTIAL)
    assert _cuts(wobble).passed is True                 # 0.2% -> flat
    real = _events((2020, 1.000), (2021, 0.980), (2022, 1.000), (2023, 1.000),
                   (2024, 1.000), (2025, 1.000), _PARTIAL)
    assert _cuts(real).passed is False                  # 2.0% -> a cut
    assert _cuts(real).observed == pytest.approx(0.02)


def test_the_tolerance_matches_the_streak_primitives():
    from aristos_council.tools.screening import _FLAT_TOL, dividend_streak
    import inspect

    assert _FLAT_TOL == inspect.signature(
        dividend_streak).parameters["flat_tol"].default
