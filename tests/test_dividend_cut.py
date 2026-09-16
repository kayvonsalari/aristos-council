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
    assert "no year cut the dividend" in r.note


def test_a_cut_fails_with_the_cut_size_and_the_year():
    """BP's shape: the 2021 total is half the 2020 total."""
    r = _cuts(_events((2020, 2.0), (2021, 1.0), (2022, 1.0), (2023, 1.0),
                      (2024, 1.0), (2025, 1.0), _PARTIAL))
    assert r.passed is False
    assert r.observed == 0.50                      # the size of the cut, as a fraction
    assert "the dividend was cut in 2021" in r.note
    # CRIT-NOCUT-2: the sentence now names BOTH falls. These fixtures pay once a year, so
    # the typical payment IS the total and the two figures agree.
    assert "the year's total fell 50% and the typical payment fell 50%" in r.note
    assert "against 2020" in r.note


def test_the_largest_cut_is_the_one_reported():
    r = _cuts(_events((2020, 2.0), (2021, 1.8), (2022, 0.9), (2023, 1.0),
                      (2024, 1.0), (2025, 1.0), _PARTIAL))
    assert r.passed is False
    assert r.observed == 0.50                      # 1.8 -> 0.9, not 2.0 -> 1.8
    assert "the dividend was cut in 2022" in r.note


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


def test_a_rounding_level_wobble_is_not_a_cut():
    """A sub-1% move in a year total is noise, and was never a cut."""
    wobble = _events((2020, 1.000), (2021, 0.998), (2022, 1.000), (2023, 1.000),
                     (2024, 1.000), (2025, 1.000), _PARTIAL)
    assert _cuts(wobble).passed is True


def test_the_cut_rule_and_the_streak_rule_now_use_DIFFERENT_tolerances_on_purpose():
    """DOCTRINE MOVED, DELIBERATELY (2026-09-16, CRIT-NOCUT-2).

    CRIT-NOCUT-1 held this criterion to ``dividend_streak``'s +/-0.5% flat band, so that
    "the two readings of one history cannot disagree about whether a year was a reduction".
    They now use different bands on purpose, because they answer different questions:

      * ``dividend_streak`` asks "did it RISE?" — any fall beyond rounding ends a growth
        streak, so its band stays at 0.5%;
      * ``max_dividend_cuts`` asks "was it CUT?" — which is a question about MATERIALITY,
        and the diagnosis measured the answer: false readings up to 8.3%, the smallest real
        cut on record 24.8%. A 0.5% band made it fail 67 of 91 names that had not cut.

    They still agree on what a year PAID — same totals, same source — which is what the
    original alignment was protecting. A 2% fall is now a streak-breaker and not a cut, and
    that is the intended reading of both rules.
    """
    from aristos_council.tools.screening import CUT_TOLERANCE, _FLAT_TOL, dividend_streak

    two_percent = _events((2020, 1.000), (2021, 0.980), (2022, 1.000), (2023, 1.000),
                          (2024, 1.000), (2025, 1.000), _PARTIAL)
    assert _cuts(two_percent).passed is True             # not a CUT: 2% < 10%
    annual = {2020: 1.0, 2021: 0.98, 2022: 1.0, 2023: 1.0, 2024: 1.0, 2025: 1.0}
    _streak, last_cut = dividend_streak(annual, 2026)
    assert last_cut == 2021                              # ...but it IS a streak-breaker
    assert _FLAT_TOL < CUT_TOLERANCE


def test_the_tolerance_matches_the_streak_primitives():
    from aristos_council.tools.screening import _FLAT_TOL, dividend_streak
    import inspect

    assert _FLAT_TOL == inspect.signature(
        dividend_streak).parameters["flat_tol"].default


# --------------------------------------------------------------------------- #
# CRIT-NOCUT-2 — a cut is a fall in BOTH the year total AND the typical payment
# --------------------------------------------------------------------------- #
# The first version compared year TOTALS alone and failed 67 of 91 names on the oil cohort,
# not one of which had cut its dividend (docs/diagnosis_dividend_history_2026-09-16.md).
# Each ordinary artefact moves exactly ONE of the two measures; a real cut moves BOTH.
def _pmts(*pairs):
    """(year, [amounts]) -> events, one payment per month from January."""
    out = []
    for year, amounts in pairs:
        for i, a in enumerate(amounts):
            out.append(DividendEvent(ex_date=date(year, min(1 + i, 12), 15), amount=a))
    return out


_TAIL = (2026, [0.40])          # the partial latest year, always dropped


def test_the_CNQ_shape_passes_a_year_with_one_fewer_payment():
    """5 payments then 4, the per-payment amount RISING. Total -22%, median +16%."""
    r = _cuts(_pmts((2020, [.15] * 4), (2021, [.19] * 4),
                    (2022, [.29, .29, .29, .29, .58]), (2023, [.335] * 4),
                    (2024, [.389] * 4), (2025, [.421] * 4), _TAIL))
    assert r.passed is True and r.observed == 0.0


def test_the_Eni_shape_passes_a_cadence_change():
    """2 payments a year then 4 at half the amount, the TOTAL rising. This is the case the
    median ALONE gets wrong (-44%), which is why the rule needs both measures."""
    r = _cuts(_pmts((2020, [.61, .61]), (2021, [.80, .80]), (2022, [.45] * 4),
                    (2023, [.479] * 4), (2024, [.531] * 4), (2025, [.545] * 4), _TAIL))
    assert r.passed is True and r.observed == 0.0


def test_the_EOG_shape_passes_a_special_dividend():
    """Specials in year N, the REGULAR dividend rising in N+1. Total -34%, regular +10%."""
    r = _cuts(_pmts((2020, [.375] * 4), (2021, [.413] * 4 + [2.0]),
                    (2022, [.75, 1.0, .75, 1.8, .75, 1.5, .75, 1.5]),
                    (2023, [.825, 1.0, .825, .825, .825, 1.5]),
                    (2024, [.91] * 4), (2025, [.975] * 4), _TAIL))
    assert r.passed is True and r.observed == 0.0


def test_the_BP_shape_fails_with_the_year_and_BOTH_falls_named():
    """The per-payment amount halved mid-2020, so 2021 falls on BOTH measures."""
    r = _cuts(_pmts((2020, [.63, .63, .315, .315]), (2021, [.315] * 4), (2022, [.33] * 4),
                    (2023, [.36] * 4), (2024, [.40] * 4), (2025, [.43] * 4), _TAIL))
    assert r.passed is False
    assert r.observed == pytest.approx(1 / 3)
    assert r.note == ("the dividend was cut in 2021: the year's total fell 33% and the "
                      "typical payment fell 33% against 2020")


def test_the_data_hole_shape_passes_because_the_typical_payment_held():
    """The provider's ADR record drops the last payments of a year (CNQ and Eni both stop
    mid-2025). The total falls; the typical payment does not; this rule passes the name.

    The YIELD criterion reading the same short record is NOT protected this way — that is
    a separate queued item and is deliberately not addressed here."""
    r = _cuts(_pmts((2020, [.15] * 4), (2021, [.19] * 4), (2022, [.29] * 4),
                    (2023, [.335] * 4), (2024, [.389] * 4), (2025, [.41, .432]), _TAIL))
    assert r.passed is True and r.observed == 0.0


def test_a_nine_percent_fall_in_both_passes_at_the_default_tolerance():
    events = _pmts((2020, [.25] * 4), (2021, [.25] * 4), (2022, [.25] * 4),
                   (2023, [.2275] * 4), (2024, [.2275] * 4), (2025, [.2275] * 4), _TAIL)
    assert _cuts(events).passed is True
    # ...and the SAME history fails once the tolerance is tightened, so the parameter is
    # doing the work rather than the shape of the data.
    tight = max_dividend_cuts_criterion(events, years=5, tolerance=0.05)
    assert tight.passed is False
    assert "total fell 9% and the typical payment fell 9%" in tight.note


def test_a_fall_in_only_one_measure_is_never_a_cut():
    """The rule in one assertion, both ways round."""
    total_only = _pmts((2020, [.25] * 4), (2021, [.25] * 4), (2022, [.25] * 4),
                       (2023, [.25] * 4), (2024, [.25] * 5), (2025, [.25] * 4), _TAIL)
    assert _cuts(total_only).passed is True          # total -20%, median flat
    median_only = _pmts((2020, [.50, .50]), (2021, [.50, .50]), (2022, [.50, .50]),
                        (2023, [.50, .50]), (2024, [.50, .50]), (2025, [.25] * 5), _TAIL)
    assert _cuts(median_only).passed is True         # median -50%, total +25%


# --- the single-payment case ---------------------------------------------- #
def test_a_single_payment_year_is_compared_on_the_total_alone():
    """A median of one number is not a TYPICAL payment — it is that payment. So an annual
    payer following a quarterly one is judged on the total, or every switch to annual
    payment would read as a cut."""
    rose = _cuts(_pmts((2020, [.25] * 4), (2021, [.25] * 4), (2022, [.25] * 4),
                       (2023, [.25] * 4), (2024, [.25] * 4), (2025, [1.05]), _TAIL))
    assert rose.passed is True                       # one payment, but MORE money

    halved = _cuts(_pmts((2020, [.25] * 4), (2021, [.25] * 4), (2022, [.25] * 4),
                         (2023, [.25] * 4), (2024, [.25] * 4), (2025, [0.50]), _TAIL))
    assert halved.passed is False                    # one payment, HALF the money
    assert "total fell 50%" in halved.note


def test_the_tolerance_is_the_value_the_diagnosis_defends():
    """10%: above the largest FALSE reading it found (8.3%) and well below the smallest
    REAL cut on record (Equinor 2020, 24.8% in NOK). Not 40% — that premise was disproved."""
    from aristos_council.tools.screening import CUT_TOLERANCE

    assert CUT_TOLERANCE == 0.10
    spec = next(p for p in REGISTRY.get("max_dividend_cuts").params
                if p.name == "cut_tolerance")
    assert spec.default == CUT_TOLERANCE and spec.type == "float"


# --------------------------------------------------------------------------- #
# P5 — the display bug: a cut SIZE is a fraction, not a year count
# --------------------------------------------------------------------------- #
def test_the_cut_size_renders_as_a_whole_percentage():
    """It used to render with the THRESHOLD's unit, which for this criterion is a count of
    YEARS — so a 32% cut printed as "the largest fall was 0 of the prior year's total" and
    a 55% cut as "was 1". "0" reads as NO cut, the opposite of what fired the rule."""
    from aristos_council.report_language import UNIT_PERCENT0, format_value

    assert format_value(0.523, UNIT_PERCENT0) == "52%"
    assert format_value(0.0, UNIT_PERCENT0) == "0%"
    assert format_value(0.32, UNIT_PERCENT0) == "32%"
    assert REGISTRY.get("max_dividend_cuts").observed_unit == UNIT_PERCENT0


def test_no_other_criterion_declares_a_separate_observed_unit():
    """For every other criterion the observed IS compared to the threshold, so they share a
    unit and nothing about their sentences changes."""
    odd = [n for n, c in REGISTRY.items() if getattr(c, "observed_unit", "")]
    assert odd == ["max_dividend_cuts"]


def test_the_exclusion_sentence_reads_as_a_percentage_end_to_end():
    from aristos_council.pipeline import exclusion_sentence

    class _Res:
        screen_outcomes = {"X": {"max_dividend_cuts": {
            "passed": False, "observed": 0.523, "threshold": 5.0,
            "note": "the dividend was cut in 2021", "basis": "", "borderline": False}}}
        names = {"X": "X"}

    sentence = exclusion_sentence(
        _Res(), "X", "screen: max_dividend_cuts (observed 0.523 vs threshold 5.0)")
    assert "the largest fall was 52% of the prior year's total" in sentence
    assert "was 0 of" not in sentence and "was 1 of" not in sentence
