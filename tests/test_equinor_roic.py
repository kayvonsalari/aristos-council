"""EQUINOR-CHECK-1 (b) - a high tax rate is not a degenerate one.

Live, 2026-10-22 sweep, EQNR.OL: Value + Momentum said "return on invested capital not available", Magic
Formula RAW ranked it BUY with return on capital "ranked worst", while Quality ranked it on worst-year ROIC.
The data was there: the through-cycle ROIC refused any tax rate outside [0, 0.6], a guard written for
LOSS-MIXED windows (ENR.DE), and Equinor pays Norway's petroleum tax (about 78%) on five profitable years.
Quality's per-year ROIC has no such band, so the two lenses disagreed about the same accounts.
"""
from __future__ import annotations

import pytest

from aristos_council.tools.screening import nopat_roic, through_cycle_roic

# Equinor-shaped, newest first, USD bn: operating income, tax, pretax, invested capital
OI = [29.0, 32.0, 35.0, 75.0, 33.0]
TAX = [21.0, 24.0, 26.5, 59.0, 23.5]
PRETAX = [28.0, 31.0, 35.0, 76.5, 32.0]
IC = [80.0, 78.0, 76.0, 74.0, 72.0]


def test_equinor_shaped_history_computes_with_its_real_tax_rate():
    roic, note = through_cycle_roic(OI, TAX, PRETAX, IC, window=5)
    assert roic is not None, note
    rate = (sum(TAX) / 5) / (sum(PRETAX) / 5)
    assert 0.6 < rate < 1.0                                   # ~76%: what the old band refused
    assert roic == pytest.approx((sum(OI) / 5) * (1 - rate) / IC[0])
    assert "through-cycle" in note


def test_the_through_cycle_figure_agrees_with_the_per_year_house_roic_on_one_year():
    one, _ = through_cycle_roic(OI[:1], TAX[:1], PRETAX[:1], IC, window=1)
    house, _ = nopat_roic(OI[0], TAX[0], PRETAX[0], IC[0])
    assert one == pytest.approx(house)


def test_a_loss_mixed_window_still_abstains_on_a_degenerate_rate():
    # ENR.DE: pretax [2213, 1822, -3387, -603] - the guard's own fixture keeps abstaining
    roic, note = through_cycle_roic([1500.0, 1200.0, -2000.0, -100.0], [700.0, 600.0, 400.0, 300.0],
                                    [2213.0, 1822.0, -3387.0, -603.0], [20000.0], window=4)
    assert roic is None and "loss-mixed" in note


def test_a_rate_above_one_still_abstains_even_when_profitable():
    roic, note = through_cycle_roic([10.0, 10.0], [12.0, 12.0], [10.0, 10.0], [50.0], window=2)
    assert roic is None and "loss-mixed" in note
