"""BACKTEST-2 — plain-English track-record badges.

Badges are read-only: derived from the committed ``backtests/`` CSVs (or an explicit ``root``,
used throughout here so nothing touches the real committed directory), never fed back into a
vote, a rank or a verdict. Pinned here: the five-label rule on fixture summaries; the company ->
cohort industry match (including the no-match and GICS-narrowed cases); a missing ``backtests/``
folder degrading to "untested here" without raising; and the summary-count line.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aristos_council.backtest import (BADGE_LABELS, BADGE_MEANINGS, Badge, BacktestResult, Round,
                                      add_months, cohort_for_industry, format_track_record_summary,
                                      has_track_record, to_csv, track_record, track_record_caption)

COHORT = "Test Cohort"
SLUG = "test_cohort"
LENS = "some_lens_v1"


def _rounds(excess_by_year: dict, per_year: int = 12) -> list:
    """Ten (or however many) years of rounds, each month the same excess for its year — enough
    rounds/years to clear INSUFFICIENT_BELOW_YEARS/ROUNDS on its own when every year is included."""
    out = []
    for year, ex in excess_by_year.items():
        for m in range(1, per_year + 1):
            d = date(year, m, 28)
            out.append(Round(d, 3, 0.10, 0.10 - ex, ex, ("A", "B", "C"), add_months(d, 12), 10))
    return out


def _write(tmp_path: Path, *, excess_by_year: dict, luck_pct_mean, lens=LENS, cohort=COHORT
          ) -> None:
    result = BacktestResult(cohort, lens, date(2016, 9, 1), date(2026, 9, 1),
                            rounds=_rounds(excess_by_year), random_baskets=500,
                            luck_pct_mean=luck_pct_mean)
    to_csv(result, tmp_path)


TEN_YEARS_ALL_POSITIVE = {y: 0.03 for y in range(2016, 2026)}     # bar comfortably met
TEN_YEARS_ALL_NEGATIVE = {y: -0.01 for y in range(2016, 2026)}    # bar comfortably failed


# =========================================================================== #
# the five labels
# =========================================================================== #
def test_proven_needs_the_bar_and_luck_5_pct_or_under(tmp_path):
    _write(tmp_path, excess_by_year=TEN_YEARS_ALL_POSITIVE, luck_pct_mean=0.03)
    b = track_record(SLUG, LENS, root=tmp_path)
    assert b.label == "proven here" and b.verdict == "proven"
    assert b.mean_excess == pytest.approx(0.03) and b.years_positive == 10
    assert b.years_measured == 10 and b.rounds_held == 120
    assert b.luck_pct == pytest.approx(0.03)


def test_promising_needs_the_bar_and_luck_between_5_and_25_pct(tmp_path):
    _write(tmp_path, excess_by_year=TEN_YEARS_ALL_POSITIVE, luck_pct_mean=0.15)
    b = track_record(SLUG, LENS, root=tmp_path)
    assert b.label == "promising here" and b.verdict == "not beyond luck"


def test_worked_against_you_fires_on_luck_alone_bar_or_no_bar(tmp_path):
    # Both files written BEFORE any read: track_record caches a root's directory listing on its
    # first call, so a file added to an already-read root would not (correctly) be picked up.
    _write(tmp_path, excess_by_year=TEN_YEARS_ALL_NEGATIVE, luck_pct_mean=0.95)
    _write(tmp_path, excess_by_year=TEN_YEARS_ALL_POSITIVE, luck_pct_mean=0.95, lens="bar_met_v1")
    b = track_record(SLUG, LENS, root=tmp_path)
    assert b.label == "worked against you here"
    # BACKTEST-2: "regardless of the bar" - a lens that ALSO clears the bar still reads this way
    # once 9-in-10 random pickers matched or beat it.
    b2 = track_record(SLUG, "bar_met_v1", root=tmp_path)
    assert b2.label == "worked against you here"


def test_no_edge_shown_is_the_catch_all_for_everything_else_tested(tmp_path):
    _write(tmp_path, excess_by_year=TEN_YEARS_ALL_NEGATIVE, luck_pct_mean=0.5)
    b = track_record(SLUG, LENS, root=tmp_path)
    assert b.label == "no edge shown here" and b.verdict == "not proven"


def test_untested_fires_on_a_genuinely_insufficient_result(tmp_path):
    # Only 3 years measured -> verdict() itself is "insufficient", whatever the luck reads.
    _write(tmp_path, excess_by_year={2024: 0.05, 2025: 0.05, 2026: 0.05}, luck_pct_mean=0.01)
    b = track_record(SLUG, LENS, root=tmp_path)
    assert b.label == "untested here" and b.verdict == "insufficient"
    assert "not enough history" in b.note


def test_untested_fires_on_no_committed_result_at_all(tmp_path):
    _write(tmp_path, excess_by_year=TEN_YEARS_ALL_POSITIVE, luck_pct_mean=0.03)
    b = track_record(SLUG, "some_other_lens_v1", root=tmp_path)     # never written
    assert b.label == "untested here" and b.verdict is None
    assert b.mean_excess is None and b.note == "no backtest result for this cohort and lens"


def test_every_label_has_exactly_one_sentence_and_matches_the_badge_scale():
    assert set(BADGE_MEANINGS) == set(BADGE_LABELS)
    for label in BADGE_LABELS:
        sentence = BADGE_MEANINGS[label]
        assert sentence.endswith(".") and sentence.count(".") == 1     # exactly one sentence


# =========================================================================== #
# reading the committed directory
# =========================================================================== #
def test_missing_backtests_folder_degrades_to_untested_without_raising(tmp_path):
    ghost = tmp_path / "does_not_exist"
    b = track_record(SLUG, LENS, root=ghost)
    assert b.label == "untested here" and b.note == "no backtest results found"
    assert has_track_record(SLUG, root=ghost) is False


def test_has_track_record_and_caption_read_off_the_committed_file(tmp_path):
    _write(tmp_path, excess_by_year=TEN_YEARS_ALL_POSITIVE, luck_pct_mean=0.03)
    assert has_track_record(SLUG, root=tmp_path) is True
    assert has_track_record("nope", root=tmp_path) is False
    caption = track_record_caption(SLUG, root=tmp_path)
    assert caption == "Track record from the Test Cohort cohort, 10 years to Sep 2026"
    assert track_record_caption("nope", root=tmp_path) is None


def test_a_two_part_cohort_name_shows_only_the_part_after_the_dash(tmp_path):
    _write(tmp_path, excess_by_year=TEN_YEARS_ALL_POSITIVE, luck_pct_mean=0.03,
          cohort="Materials - Diversified Mining", lens=LENS)
    caption = track_record_caption("materials_diversified_mining", root=tmp_path)
    assert caption == "Track record from the Diversified Mining cohort, 10 years to Sep 2026"


def test_format_track_record_summary_counts_in_badge_scale_order(tmp_path):
    badges = [Badge("proven here", "proven", 0.03, 0.02, 8, 10, 100, ""),
             Badge("proven here", "proven", 0.04, 0.01, 9, 10, 100, ""),
             Badge("promising here", "not beyond luck", 0.03, 0.15, 7, 10, 100, ""),
             Badge("no edge shown here", "not proven", -0.01, 0.5, 3, 10, 100, ""),
             Badge("no edge shown here", "not proven", -0.02, 0.6, 2, 10, 100, "")]
    assert (format_track_record_summary(badges)
           == "Track record: 2 proven, 1 promising, 2 no edge shown")
    assert format_track_record_summary([]) == ""


# =========================================================================== #
# company -> cohort industry matching (BACKTEST-2 item 2)
# =========================================================================== #
def test_industry_matches_the_one_cohort_it_belongs_to():
    assert cohort_for_industry("Other Industrial Metals & Mining") == "materials_diversified_mining"


def test_no_industry_or_an_unlisted_one_matches_nothing():
    assert cohort_for_industry(None) is None
    assert cohort_for_industry("") is None
    assert cohort_for_industry("Not A Real Industry Label") is None


def test_a_gics_narrowed_cohort_needs_the_right_sub_industry():
    # Two live cohorts share the EODHD code "Specialty Industrial Machinery" and are
    # disambiguated ONLY by GICS sub-industry - the same split the cohort builder itself makes.
    assert (cohort_for_industry("Specialty Industrial Machinery", "Heavy Electrical Equipment")
           == "industrials_grid_electrical_machinery")
    assert (cohort_for_industry("Specialty Industrial Machinery",
                                "Industrial Machinery & Supplies & Components")
           == "industrials_industrial_machinery")
    assert cohort_for_industry("Specialty Industrial Machinery", "Something Unrelated") is None
    assert cohort_for_industry("Specialty Industrial Machinery", None) is None
