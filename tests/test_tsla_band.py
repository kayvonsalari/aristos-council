"""B24-D3 - the valuation band's implausible-multiple guard, in plain words and printed once.

TSLA (2026-10-08): EV/EBIT 265x against a cap of 200x (MULTIPLE_MAX). Its vendor trailing P/E was 353x -
25% away, not within 15% - so the band keeps abstaining, now as "not read: the earnings figure looks
unreliable". A multiple that DOES agree with the trailing P/E is shown, with a plain caution."""
from datetime import date

from aristos_council.tools.valuation_band import (MULTIPLE_MAX, PLAUSIBLE_AGAINST_TRAILING_PE,
                                                  UNRELIABLE_EARNINGS, ValuationBand)


def test_the_guard_numbers_the_report_quotes():
    assert MULTIPLE_MAX == 200.0 and PLAUSIBLE_AGAINST_TRAILING_PE == 0.15
    assert (353.1 - 265) / 353.1 > 0.15                 # TSLA: 265x vs a 353x trailing P/E - not within 15%


def test_an_implausible_band_reads_in_plain_words_everywhere():
    band = ValuationBand(note="multiple implausible (265x); inputs suspect, not stated", implausible=True)
    assert band.display == f"not read: {UNRELIABLE_EARNINGS}" == "not read: the earnings figure looks unreliable"
    assert band.reason_plain == UNRELIABLE_EARNINGS
    assert "265" in band.note and "implausible" in band.note        # the raw reason is kept for the record
    other = ValuationBand(note="insufficient history: 1.4y")
    assert other.display == "not evaluated \u2014 insufficient history: 1.4y" and other.reason_plain == other.note


def test_a_multiple_that_matches_the_trailing_pe_is_shown_with_a_caution():
    band = ValuationBand(percentile=97.0, basis="pe", current=265.0, months_covered=40, months_total=61,
                         years_covered=4.0, caution="very high: 265\u00d7 earnings")
    assert band.available and band.display.endswith("\u2014 very high: 265\u00d7 earnings")
    assert "97th percentile" in band.display


def _band(pe):
    """A real band: a flat history at 40, then a jump to 106 - so today's multiple is 265x against an own-history
    median of 100x (a believable median, an implausible-looking current figure)."""
    from aristos_council.tools.valuation_band import valuation_band
    from tests.test_valuation_band import TODAY, _bars, _fundamentals
    f = _fundamentals(ebit=1e6, debt=0.0, cash=0.0, market_cap=2.65e8, pe_ratio=pe)
    return valuation_band(_bars([40.0] * 60 + [106.0]), f, asof=TODAY)


def test_tsla_shaped_265x_against_a_353x_trailing_pe_abstains_in_plain_words():
    band = _band(353.1)
    assert not band.available and band.implausible
    assert band.display == "not read: the earnings figure looks unreliable"


def test_a_265x_multiple_that_agrees_with_the_trailing_pe_is_shown_with_a_caution():
    for pe in (265.0, 290.0, 240.0):                     # within 15% of 265
        band = _band(pe)
        assert band.available and not band.implausible, pe
        assert band.caution == "very high: 265× earnings"
        assert band.display.endswith("— very high: 265× earnings")


def test_no_trailing_pe_or_one_far_away_keeps_abstaining():
    for pe in (None, 150.0, 353.1, 0.0, -5.0):
        assert not _band(pe).available, pe


def test_an_absurd_own_history_median_is_never_rescued_by_the_trailing_pe():
    from aristos_council.tools.valuation_band import valuation_band
    from tests.test_valuation_band import TODAY, _bars, _fundamentals
    f = _fundamentals(ebit=1e6, debt=0.0, cash=0.0, market_cap=2.65e8, pe_ratio=265.0)
    assert not valuation_band(_bars([100.0] * 61), f, asof=TODAY).available   # median 265x too


def test_the_shortlist_prints_the_reason_once_not_in_two_columns():
    from types import SimpleNamespace

    from aristos_council.pipeline import LensAgreementRow, lens_agreement_table
    row = LensAgreementRow(ticker="TSLA", display="Tesla, Inc. (TSLA)", buy_lenses=("Quality",),
                           sell_lenses=("Magic Formula RAW",), hold_lenses=(), not_ranked=(),
                           check_verdicts={}, band_percentile=None, band_note=UNRELIABLE_EARNINGS)
    ag = SimpleNamespace(rows=[row], check_ids=[], check_labels={})
    cols, rows = lens_agreement_table(ag)
    cell = rows[0]
    assert cell["Valuation percentile"] == "not read: the earnings figure looks unreliable"
    assert cell["Marks"] == ""                                  # not repeated as a mark
    assert sum(1 for v in cell.values() if "unreliable" in str(v)) == 1
