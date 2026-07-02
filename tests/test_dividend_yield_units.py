"""Dividend-yield unit normalisation — Fundamentals.dividend_yield is ALWAYS a
DECIMAL (0.0289 = 2.89%), regardless of provider.

The bug: yfinance's info['dividendYield'] became a PERCENT NUMBER (2.89), passed
through untouched -> ~100x too high (PG read 289% instead of 2.89%). Fixed per-adapter
(normalise to decimal at the source), with a >100% backstop for future drift. No
network: pure helpers / payload parsers.
"""

from __future__ import annotations

from aristos_council.data.adapter import sane_dividend_yield
from aristos_council.data.eodhd_adapter import fundamentals_from_payload
from aristos_council.data.yfinance_adapter import _dividend_yield


def test_sane_dividend_yield_backstop():
    # > 100% is impossible for a real equity yield -> percent that slipped through
    assert abs(sane_dividend_yield(2.89) - 0.0289) < 1e-9
    assert sane_dividend_yield(0.0289) == 0.0289          # decimal unchanged
    assert sane_dividend_yield(0.0) == 0.0
    assert sane_dividend_yield(None) is None


def test_yfinance_dividend_yield_normalised_to_decimal():
    # dividendYield is now a PERCENT (2.89) -> /100 = 0.0289 (NOT 2.89, the 100x bug)
    v = _dividend_yield({"dividendYield": 2.89})
    assert 0.01 < v < 0.10 and abs(v - 0.0289) < 1e-9
    # PREFER the decimal trailingAnnualDividendYield when present
    v2 = _dividend_yield({"trailingAnnualDividendYield": 0.024, "dividendYield": 2.4})
    assert abs(v2 - 0.024) < 1e-9
    # a THIN yield in percent form (0.5% == 0.5) normalises correctly — the per-source
    # /100 handles it (a bare >1 guard alone would not, since 0.5 < 1.0)
    assert abs(_dividend_yield({"dividendYield": 0.5}) - 0.005) < 1e-9
    assert _dividend_yield({}) is None                    # no field -> None


def test_eodhd_dividend_yield_is_decimal_preserved_with_drift_guard():
    # EODHD returns a DECIMAL already -> preserved untouched
    f = fundamentals_from_payload("PG.US", {"Highlights": {"DividendYield": 0.0289}})
    assert abs(f.dividend_yield - 0.0289) < 1e-9
    # drift protection: were EODHD ever to return a percent, the backstop normalises
    f2 = fundamentals_from_payload("X.US", {"Highlights": {"DividendYield": 2.89}})
    assert abs(f2.dividend_yield - 0.0289) < 1e-9


def test_a_normal_payer_reads_two_to_six_percent_not_hundreds():
    # The headline regression: a ~2.9% payer must land near 0.029, never ~2.9.
    for pct in (2.4, 2.89, 3.5, 5.9):
        v = _dividend_yield({"dividendYield": pct})
        assert 0.01 < v < 0.10, f"{pct}% -> {v} (should be a decimal ~0.0{int(pct)}x)"
