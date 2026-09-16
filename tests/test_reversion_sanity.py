"""BAND-3 — an implied move past ±150% is an artefact, and says so.

OBSERVED 2026-09-15, USD-listed oil cohort, valuation band on:

  KNTK  Kinetik, EV/EBIT 16.4x against its own median of 23.2x, reversion value
        rendered with a gap of **+322%**.
  LNG   Cheniere, +24% on the same page, off comparable inputs.

A 16.4x -> 23.2x reversion is about a 1.4x move on the multiple. It cannot produce a
three-fold move in the price. What it CAN come out of is a thin EBIT year (a small
denominator magnifying everything downstream), a share-count mismatch, or a currency
basis error — none of which the arithmetic can tell apart from the far end, and none of
which is a reading of anything.

The damage is not the number, it is the number's COMPANY: it sits in the table beside
Cheniere's +24% with exactly the same authority, in the same column, formatted the same
way. So the guard is an ABSTENTION, not a clamp — a clamp would keep a wrong number and
hide that it was wrong. Past the bound the value is not stated and the reason travels in
its place.

Deliberately basis-agnostic: it fires on EV/EBIT and P/E alike, and does not overlap
VALBAND-FX-1 (which fixes a specific, identified conversion defect). This one makes no
claim about WHICH input went wrong — only that the output cannot be believed.
"""

from __future__ import annotations

from datetime import date

import pytest

from aristos_council.tools.reversion import GAP_SANITY, reversion_value
from aristos_council.tools.valuation_band import ValuationBand


def _band(median, *, earnings=1_000.0, net_debt=0.0, shares=100.0,
          basis="ev_ebit", current=10.0):
    """A band that has already computed — the reversion value reads only these fields."""
    return ValuationBand(
        percentile=50.0, basis=basis, current=current, median_multiple=median,
        months_covered=61, months_total=61, years_covered=5.0, window_years=5,
        net_debt_basis="asof", current_point=date(2026, 9, 1),
        current_earnings=earnings, current_net_debt=net_debt,
        current_shares=shares, fx_rate_current=1.0, quoted_units=shares)


def _gap_for(median, last_close):
    return reversion_value(_band(median), None, last_close=last_close, currency="USD")


# --------------------------------------------------------------------------- #
# The bound
# --------------------------------------------------------------------------- #
def test_a_three_fold_implied_move_abstains_with_its_reason():
    """The KNTK shape: median x3 the price implies +200%, which is past the bound."""
    # earnings 1000 x median 30 / 100 shares = implied 300.00 against a 100.00 close.
    rev = _gap_for(30.0, 100.0)
    assert not rev.available
    assert rev.price is None and rev.gap is None
    assert "implied move +200%" in rev.note
    assert "exceeds the sanity bound (±150%)" in rev.note
    assert "inputs suspect" in rev.note
    assert "thin EBIT year, share-count or currency mismatch" in rev.note
    assert "not stated" in rev.note
    # ...and the abstention renders as one honest line, not a blank.
    assert rev.display.startswith("reversion value not evaluated — ")


def test_a_modest_gap_is_stated_exactly_as_before():
    """A 0.4 gap is a reading, and BAND-3 must not touch it."""
    # earnings 1000 x median 14 / 100 shares = implied 140.00 against a 100.00 close.
    rev = _gap_for(14.0, 100.0)
    assert rev.available
    assert rev.gap == pytest.approx(0.4) and rev.price == 140.0
    assert "+40%" in rev.display


def test_the_downward_bound_is_now_REACHABLE():
    """DOCTRINE MOVED, DELIBERATELY (2026-09-17, REV-BOUND-1). This test pinned the
    opposite until today.

    BAND-3 wrote its bound on the MAGNITUDE (+/-150%) and observed that only the upward
    side could ever fire: an implied price is never negative, so the gap floors at -100%
    and can never pass -150%. That observation was right about the arithmetic and wrong
    about the consequence -- it left the downward side with no bound at all, and Murphy
    Oil came back at -98% and BP at -96%, both inside +/-150% and both absurd.

    A name that must fall by more than three quarters to reach its OWN median is telling
    you the median is wrong, not that the shares are worth a fifth of their price. So the
    downward bound is now stated separately at -75%, and the two are no longer one number.
    """
    from aristos_council.tools.reversion import GAP_SANITY, GAP_SANITY_DOWN

    assert GAP_SANITY_DOWN == -0.75 and GAP_SANITY == 1.5

    deep = reversion_value(_band(10.0, earnings=1.0), None, last_close=100.0,
                           currency="USD")
    assert not deep.available                      # -99.9%, formerly stated in full
    assert "implied move -100%" in deep.note or "implied move -99%" in deep.note

    # A severe but believable fall is still a reading: a cyclical at a genuine peak can
    # carry a halving to its through-cycle median.
    assert _gap_for(5.0, 100.0).available           # -50%


def test_the_boundary_itself_is_stated_not_abstained():
    """``> GAP_SANITY`` — exactly at the bound is still a reading (no off-by-one)."""
    # implied 250.00 against a 100.00 close = +150% exactly.
    at = _gap_for(25.0, 100.0)
    assert at.available and abs(at.gap - GAP_SANITY) < 1e-12
    just_over = _gap_for(25.1, 100.0)
    assert not just_over.available


def test_the_guard_is_basis_agnostic():
    """It fires on the P/E route too — the artefact is not an EV/EBIT speciality."""

    class _F:
        eps = 10.0

    band = _band(40.0, basis="pe")             # eps 10 x median 40 = 400 vs a 100 close
    rev = reversion_value(band, _F(), last_close=100.0, currency="USD")
    assert not rev.available and "exceeds the sanity bound" in rev.note


# --------------------------------------------------------------------------- #
# What the guard must NOT disturb
# --------------------------------------------------------------------------- #
def test_the_band_row_keeps_its_percentile():
    """Only the reversion and gap cells go quiet; the percentile is computed from the
    distribution, not from this arithmetic, and is a separate reading."""
    from aristos_council.pipeline import valuation_band_table

    class _Row:
        ticker = "KNTK"
        excluded = False
        price = None
        valuation_band = _band(30.0)
        reversion = _gap_for(30.0, 100.0)

    class _Res:
        ranked = [_Row()]
        names = {"KNTK": "Kinetik Holdings"}

    table = valuation_band_table(_Res())
    row = table.rows[0]
    assert "50th" in row["Percentile"]                       # the band still speaks
    assert row["Reversion value"].startswith("not evaluated — ")
    assert "exceeds the sanity bound" in row["Reversion value"]
    assert row["Gap"] == "—"                                 # no number where none is due


def test_an_abstaining_band_still_gives_its_own_reason_not_this_one():
    """BAND-3 sits AFTER the existing abstentions and must not mask them."""
    out = ValuationBand(note="insufficient history: 1.1y")
    rev = reversion_value(out, None, last_close=100.0, currency="USD")
    assert not rev.available
    assert "the valuation band abstained for this name" in rev.note
    assert "sanity bound" not in rev.note


# --------------------------------------------------------------------------- #
# REV-BOUND-1 — the downward bound BAND-3 left unreachable
# --------------------------------------------------------------------------- #
# BAND-3 wrote its bound on the magnitude and noted only the upward side could fire, because
# an implied price is never negative so the gap floors at -100%. That was right about the
# arithmetic and wrong about the consequence: Murphy Oil came back at -98% and BP at -96%,
# both inside the bound and both absurd. A name that must fall by more than three quarters
# to reach its OWN median is saying the median is wrong.
def test_a_ninety_percent_fall_now_abstains():
    from aristos_council.tools.reversion import GAP_SANITY_DOWN

    assert GAP_SANITY_DOWN == -0.75
    # earnings 1000 x median 1 / 100 shares = implied 10.00 against a 100.00 close = -90%
    rev = _gap_for(1.0, 100.0)
    assert not rev.available
    assert "implied move -90%" in rev.note
    assert "exceeds the sanity bound" in rev.note


def test_the_murphy_and_bp_shapes_abstain():
    """-98% and -96%: inside BAND-3's magnitude bound, outside this one."""
    for median, close, expected in ((0.2, 100.0, "-98%"), (0.4, 100.0, "-96%")):
        rev = _gap_for(median, close)
        assert not rev.available, expected
        assert expected in rev.note


def test_a_forty_percent_fall_is_still_stated():
    rev = _gap_for(6.0, 100.0)                  # implied 60.00 vs 100.00 = -40%
    assert rev.available and rev.gap == pytest.approx(-0.4)


def test_a_halving_to_the_own_median_is_still_a_reading():
    """-75% is the bound and -50% is well inside it: a cyclical at a genuine peak can carry
    a halving to its through-cycle median, and that IS a reading."""
    rev = _gap_for(5.0, 100.0)                  # implied 50.00 vs 100.00 = -50%
    assert rev.available and rev.gap == pytest.approx(-0.5)


def test_the_upward_bound_is_unchanged():
    assert not _gap_for(30.0, 100.0).available      # +200%
    assert _gap_for(25.0, 100.0).available          # +150% exactly, still stated


def test_the_boundary_itself_is_stated_on_the_downward_side_too():
    from aristos_council.tools.reversion import GAP_SANITY_DOWN

    at = _gap_for(2.5, 100.0)                   # implied 25.00 = -75% exactly
    assert at.available and at.gap == pytest.approx(GAP_SANITY_DOWN)
    assert not _gap_for(2.4, 100.0).available   # just past it
