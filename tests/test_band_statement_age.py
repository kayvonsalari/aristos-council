"""BAND-STMT-2 — the short-history wording for a genuinely YOUNG company.

BAND-STMT-1 made the abstention name what it saw: "4 annual statements seen, fiscal year
ends June". That is exactly right for Procter & Gamble, where four statements on a company
with decades of published accounts is a fact about the FEED, and the sentence sends a
reader to check the feed.

For Secure Waste it reads like a data fault about a company that has simply not existed
very long. Same sentence, wrong story.

So: when the statements and the price history START TOGETHER, the reason says so instead.

The hard part is not the wording, it is telling the two apart, and this file's fourth test
is the one that matters — the band asks for five years of bars, so for ANY company older
than the window the earliest bar it holds is the window edge, and P&G's four statements
sit within a year of it. Without a second condition the kind sentence would be printed
about a 187-year-old company.
"""

from __future__ import annotations

from datetime import date

from aristos_council.tools.valuation_band import (BAND_YEARS, _young_company_reason,
                                                  valuation_band)
from tests.test_valuation_band import TODAY, _bars, _fundamentals


def _dated(n_statements: int, first_year: int):
    """``(earnings, ...)`` in the shape ``_young_company_reason`` reads: dates newest-first."""
    return ([date(first_year + i, 12, 31) for i in range(n_statements)],)


def _bars_from(first: date, end: date = TODAY):
    """Monthly bars whose FIRST day is ``first`` — i.e. the listing date."""
    months = (end.year - first.year) * 12 + (end.month - first.month) + 1
    return _bars([100.0] * months, end=end)


# =========================================================================== #
# 1. the two shapes, the two sentences
# =========================================================================== #
def test_a_young_company_gets_the_young_company_sentence():
    """Listed three years ago, three statements — the feed returned everything there is."""
    listed = date(TODAY.year - 3, TODAY.month, 1)
    reason = _young_company_reason(_dated(3, TODAY.year - 3), _bars_from(listed),
                                   asof=TODAY, years=BAND_YEARS)
    assert reason == (f"only 3 years of statements exist for this company; the band needs "
                      f"{BAND_YEARS}, so it is not stated")


def test_an_old_company_with_a_short_feed_keeps_the_BAND_STMT_1_wording():
    """P&G: four statements, price history running the whole window — a FEED fact."""
    reason = _young_company_reason(_dated(4, TODAY.year - 4), _bars([100.0] * 61),
                                   asof=TODAY, years=BAND_YEARS)
    assert reason == ""


def test_the_two_shapes_produce_two_different_sentences_end_to_end():
    """The brief's test, through ``valuation_band`` itself rather than the helper."""
    young_bars = _bars_from(date(TODAY.year - 3, TODAY.month, 1))
    young = valuation_band(
        young_bars,
        _fundamentals(ebit=100.0, debt=200.0, cash=50.0, years=3),
        asof=TODAY, min_years=BAND_YEARS)
    old = valuation_band(
        _bars([100.0] * 61),
        _fundamentals(ebit=100.0, debt=200.0, cash=50.0, years=4),
        asof=TODAY, min_years=BAND_YEARS)

    assert young.percentile is None and old.percentile is None    # both abstain
    assert young.note != old.note
    assert young.note.startswith("only ") and "exist for this company" in young.note
    assert "annual statement" in old.note and "the band needs" in old.note


# =========================================================================== #
# 2. the trap
# =========================================================================== #
def test_a_company_older_than_the_window_is_never_called_young():
    """THE test. The band requests ``years`` of bars, so an old company's earliest HELD
    bar is the window edge — and its four statements sit within a year of that edge.
    Reading listing age off a window-clipped series would print the kind sentence about a
    187-year-old company."""
    window_edge = date(TODAY.year - BAND_YEARS, TODAY.month, 1)
    clipped = _bars_from(window_edge)
    # The statements must start NEAR the clipped series for this to discriminate: with
    # them starting a year later the date test alone already rejects it, and the guard
    # under test is never exercised. Removing ``_WINDOW_EDGE_DAYS`` makes THIS line print
    # "only 4 years of statements exist for this company" about a 187-year-old business.
    assert _young_company_reason(_dated(4, TODAY.year - BAND_YEARS), clipped,
                                 asof=TODAY, years=BAND_YEARS) == ""
    # ...and the further-apart case stays rejected too
    assert _young_company_reason(_dated(4, TODAY.year - 4), clipped,
                                 asof=TODAY, years=BAND_YEARS) == ""


def test_statements_that_start_long_after_the_listing_stay_a_feed_gap():
    """Listed ten years ago, statements only for the last two: that IS a feed gap, and the
    reader should be sent to the feed."""
    listed = date(TODAY.year - 10, 1, 31)
    assert _young_company_reason(_dated(2, TODAY.year - 2), _bars_from(listed),
                                 asof=TODAY, years=BAND_YEARS) == ""


# =========================================================================== #
# 3. edges
# =========================================================================== #
def test_one_statement_reads_in_the_singular():
    listed = date(TODAY.year - 2, TODAY.month, 1)
    reason = _young_company_reason(_dated(1, TODAY.year - 2), _bars_from(listed),
                                   asof=TODAY, years=BAND_YEARS)
    assert reason.startswith("only 1 year of statements")     # not "1 years"


def test_no_statements_or_no_bars_says_nothing_rather_than_guessing():
    listed = date(TODAY.year - 2, TODAY.month, 1)
    assert _young_company_reason(([],), _bars_from(listed), asof=TODAY,
                                 years=BAND_YEARS) == ""
    assert _young_company_reason(_dated(2, TODAY.year - 2), [], asof=TODAY,
                                 years=BAND_YEARS) == ""
    assert _young_company_reason(None, None, asof=TODAY, years=BAND_YEARS) == ""


def test_the_reason_names_the_band_length_it_is_short_of():
    listed = date(TODAY.year - 3, TODAY.month, 1)
    reason = _young_company_reason(_dated(3, TODAY.year - 3), _bars_from(listed),
                                   asof=TODAY, years=BAND_YEARS)
    assert f"needs {BAND_YEARS}" in reason


def test_this_is_reason_TEXT_only_and_moves_no_number():
    """The band still abstains, with the same coverage figures; only the sentence moved."""
    listed = date(TODAY.year - 3, TODAY.month, 1)
    bars = _bars_from(listed)
    f = _fundamentals(ebit=100.0, debt=200.0, cash=50.0, years=3)
    band = valuation_band(bars, f, asof=TODAY, min_years=BAND_YEARS)
    assert band.percentile is None
    assert band.available is False
    assert band.months_total > 0            # it still counted what it had
