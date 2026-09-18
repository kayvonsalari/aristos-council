"""COHORT-BAND-1 — a cohort that is expensive says so ONCE.

On the oil runs every shortlisted name sat near the top of its own five-year range, and
that was the single most useful thing those runs told us. The report only ever said it
PER NAME, in a column — so a reader could go down the shortlist row by row, read the fact
seven times, and still not notice that it was a fact about the whole list.

Arithmetic on figures already in the report. Not ranked, not screened, no verdict touched.
"""

from __future__ import annotations

import pytest

from aristos_council.pipeline import (COHORT_BAND_CHEAP, COHORT_BAND_EXPENSIVE,
                                      COHORT_BAND_MIN_NAMES, LensAgreementRow,
                                      cohort_band_line)


def _ag(percentiles):
    class _Agreement:
        rows = [LensAgreementRow(ticker=f"T{i}", display=f"T{i}", buy_lenses=("a",),
                                 band_percentile=p)
                for i, p in enumerate(percentiles)]
    return _Agreement()


# =========================================================================== #
# the three shapes the brief names
# =========================================================================== #
def test_a_median_of_78_reads_as_expensive():
    line = cohort_band_line(_ag([70, 74, 76, 78, 80, 84, 90]))
    assert line == ("The 7 shortlisted names sit at a median 78th percentile of their own "
                    "five-year range — this cohort is expensive against its own history.")


def test_a_median_of_52_is_the_bare_sentence():
    line = cohort_band_line(_ag([40, 45, 50, 52, 54, 60, 65]))
    assert line == ("The 7 shortlisted names sit at a median 52nd percentile of their own "
                    "five-year range.")
    assert "expensive" not in line and "cheap" not in line


def test_two_rows_render_nothing_at_all():
    """A "median" of two names is a rounding of one opinion; printing it would lend it an
    authority it has not got."""
    assert cohort_band_line(_ag([50, 60])) == ""


# =========================================================================== #
# the thresholds, at their edges
# =========================================================================== #
def test_the_cheap_wording_fires_at_or_below_thirty():
    assert "cheap against its own history" in cohort_band_line(_ag([10, 20, 25, 28, 30]))
    assert COHORT_BAND_CHEAP == 30.0


@pytest.mark.parametrize("median,expect", [(70.0, "expensive"), (30.0, "cheap")])
def test_the_thresholds_are_inclusive(median, expect):
    """"at or above 70", "at or below 30" — the boundary belongs to the wording."""
    assert expect in cohort_band_line(_ag([median] * 5))


@pytest.mark.parametrize("median", [31.0, 69.0])
def test_just_inside_the_middle_appends_nothing(median):
    line = cohort_band_line(_ag([median] * 5))
    assert line.endswith("five-year range.")


def test_an_even_number_of_names_takes_the_midpoint_of_the_middle_two():
    line = cohort_band_line(_ag([10, 20, 40, 60]))          # median = 30
    assert "30th percentile" in line and "cheap" in line


# =========================================================================== #
# abstained bands are excluded AND disclosed
# =========================================================================== #
def test_names_whose_band_abstained_are_left_out_of_the_median_and_counted():
    line = cohort_band_line(_ag([70, 74, 78, None, None, 84, 90]))
    assert "The 5 shortlisted names" in line       # the median is over the STATED five
    assert "median 78th percentile" in line
    assert line.endswith("(2 of 7 not stated.)")


def test_a_fully_stated_shortlist_carries_no_disclosure_clause():
    assert "not stated" not in cohort_band_line(_ag([70, 74, 76, 78, 80, 84, 90]))


def test_fewer_than_three_STATED_bands_omits_the_line_even_on_a_long_shortlist():
    """Seven names, two bands: there is no median here worth printing."""
    assert COHORT_BAND_MIN_NAMES == 3
    assert cohort_band_line(_ag([80, 90, None, None, None, None, None])) == ""


def test_an_empty_or_absent_shortlist_renders_nothing():
    assert cohort_band_line(_ag([])) == ""

    class _NoRows:
        rows = None
    assert cohort_band_line(_NoRows()) == ""
    assert cohort_band_line(None) == ""


# =========================================================================== #
# it reaches the page
# =========================================================================== #
def test_the_line_is_in_the_markdown_export_directly_under_the_table():
    pytest.importorskip("streamlit")
    import app
    from aristos_council.pipeline import lens_agreement_table

    from aristos_council.pipeline import LensAgreement

    agreement = LensAgreement(
        voting_ids=["a"], voting_labels={"a": "A"}, check_ids=[], check_labels={},
        rows=_ag([70, 74, 76, 78, 80, 84, 90]).rows)

    markdown = "\n".join(app._shortlist_markdown(agreement, lens_agreement_table))
    expected = cohort_band_line(agreement)
    assert expected in markdown
    # under the table, not above it
    assert markdown.index("T0") < markdown.index(expected)
