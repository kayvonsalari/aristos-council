"""Loss-mixed ROIC abstains, not a "-0" artifact (VERIFY-2 ITEM 2).

A loss-mixed pretax window can make the through-cycle effective tax rate degenerate, so
NOPAT collapses to a "-0" (ENR.DE). The roic criterion ABSTAINS then. But sign-mixed
ALONE is NOT the trigger — a single loss year inside a net-positive window is the
through-cycle design (000660.KS / SK Hynix), which MUST keep computing. Abstain only on a
degenerate rate (tax_mean/pretax_mean outside [0, 0.6]) or a non-positive pretax sum.
"""

from __future__ import annotations

from aristos_council.tools.screening import nopat_roic, through_cycle_roic

# ENR.DE fixture (M EUR), from the real freeze-record payload — pretax mean ~11.25, so any
# real tax provision blows the through-cycle rate past [0, 0.6].
_ENR_PRETAX = [2213.0, 1822.0, -3387.0, -603.0]
_ENR_TAX = [600.0, 500.0, 0.0, 100.0]        # tax_mean 300 / pretax_mean 11.25 -> ~26.7
_ENR_OI = [2500.0, 2000.0, -3000.0, -500.0]
_ENR_IC = [40000.0]


def test_enr_de_abstains_with_the_note():
    roic, note = through_cycle_roic(_ENR_OI, _ENR_TAX, _ENR_PRETAX, _ENR_IC, window=4)
    assert roic is None                                        # abstains, no "-0"
    assert "effective tax rate not computable" in note


def test_000660_ks_one_loss_year_in_a_net_positive_window_computes():
    # SK Hynix shape: a NEGATIVE prior year but a net-positive window with a healthy tax
    # rate -> the through-cycle dampening is the design; it MUST compute, not abstain.
    oi = [30000.0, 20000.0, -5000.0, 15000.0]                  # one down year
    tax = [6000.0, 4000.0, 0.0, 3000.0]                        # rate 3250/15000 = 0.217
    pretax = [28000.0, 19000.0, -6000.0, 14000.0]             # net positive, sign-mixed
    ic = [120000.0]
    roic, note = through_cycle_roic(oi, tax, pretax, ic, window=4)
    assert roic is not None                                    # computes (feature preserved)
    assert "through-cycle" in note
    # and it is the DAMPENED value: below the peak (latest-only) roic
    peak, _ = nopat_roic(oi[0], tax[0], pretax[0], ic[0])
    assert roic < peak


def test_fully_loss_making_series_abstains():
    roic, note = through_cycle_roic(
        [-2000.0, -1500.0, -1800.0, -1000.0], [0.0] * 4,
        [-3000.0, -2500.0, -2800.0, -2000.0], [150000.0], window=4)
    assert roic is None and "effective tax rate not computable" in note


def test_clean_profitable_series_computes_unchanged():
    oi = [1000.0, 950.0, 900.0, 850.0]
    tax = [200.0, 190.0, 180.0, 170.0]
    pretax = [980.0, 930.0, 880.0, 830.0]                     # all positive, rate ~0.204
    ic = [5000.0]
    roic, note = through_cycle_roic(oi, tax, pretax, ic, window=4)
    # unchanged: NOPAT on the through-cycle means, rate = tax_mean/pretax_mean, clamped
    oi_m = sum(oi) / 4
    tax_m = sum(tax) / 4
    pre_m = sum(pretax) / 4
    expected, _ = nopat_roic(oi_m, tax_m, pre_m, ic[0])
    assert roic is not None and abs(roic - expected) < 1e-12
    assert "through-cycle" in note
