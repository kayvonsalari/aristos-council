"""Tests for deterministic technical tools."""

from aristos_council.tools.technical import (
    annualized_volatility,
    pct_off_high,
    sma,
    technical_snapshot,
)


def test_sma_basic():
    assert sma([1, 2, 3, 4], 2) == 3.5


def test_sma_none_when_short():
    assert sma([1, 2], 5) is None


def test_pct_off_high_at_high():
    assert pct_off_high([1, 2, 3]) == 0.0


def test_pct_off_high_below():
    closes = [10.0] * 5 + [8.0]
    assert abs(pct_off_high(closes) - (-0.2)) < 1e-9


def test_volatility_zero_for_flat_series():
    assert annualized_volatility([5.0, 5.0, 5.0, 5.0]) == 0.0


def test_volatility_none_when_too_short():
    assert annualized_volatility([5.0]) is None


def test_volatility_none_on_nonpositive_price():
    assert annualized_volatility([5.0, 0.0, 5.0]) is None


def test_snapshot_notes_missing_smas():
    snap = technical_snapshot([1.0] * 10)
    assert snap.sma_50 is None
    assert any("sma_50" in n for n in snap.notes)
    assert any("sma_200" in n for n in snap.notes)


# --------------------------------------------------------------------------- #
# Non-finite input handling (live-run regression 2026-06-11: yfinance returned
# today's incomplete bar with NaN close; every snapshot metric became NaN and
# the Technical specialist had to abstain)
# --------------------------------------------------------------------------- #
def test_sma_none_when_window_contains_nan():
    closes = [10.0] * 49 + [float("nan")]
    assert sma(closes, 50) is None


def test_pct_off_high_none_with_nan():
    assert pct_off_high([10.0, float("nan"), 9.0]) is None


def test_volatility_none_with_nan():
    assert annualized_volatility([10.0, float("nan"), 10.5]) is None


def test_snapshot_degrades_to_notes_not_nan():
    closes = [10.0] * 219 + [float("nan")]
    snap = technical_snapshot(closes)
    # No metric may be NaN — unavailable metrics must be None (which the
    # gather/specialist layer surfaces as data-quality information).
    import math as _m
    for v in (snap.sma_50, snap.sma_200, snap.pct_off_52w_high,
              snap.annualized_volatility):
        assert v is None or _m.isfinite(v)
