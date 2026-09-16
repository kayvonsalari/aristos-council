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


def test_the_bound_is_written_on_the_magnitude_but_only_bites_upward():
    """``abs(gap) > GAP_SANITY`` is symmetric as written, and the DOWNSIDE is
    unreachable by construction — worth pinning so nobody later "fixes" a bound they
    think is broken.

    ``gap = implied_price / last_close - 1`` and an implied price is never negative (a
    non-positive implied equity value abstains earlier), so the gap floors at -100% and
    can never pass -150%. A collapse is therefore always stated, however severe; only an
    implausible SPIKE is withheld. That is the honest reach of this guard."""
    deep = reversion_value(_band(10.0, earnings=1.0), None, last_close=100.0,
                           currency="USD")
    assert deep.available                       # -99.9%: extreme, but stateable
    assert deep.gap == pytest.approx(-0.999)
    assert deep.gap > -1.0                      # the floor the symmetry runs into
    # The upward side is the one that fires.
    assert not _gap_for(30.0, 100.0).available


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
