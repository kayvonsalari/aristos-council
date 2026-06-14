"""Tests for the yfinance adapter's statement-series parsing (_annual_series).

The network-facing parts aren't unit-tested; the pure, provider-shaped parsing
helper is. pandas comes with the yfinance extra, so this module skips cleanly
when it isn't installed.
"""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")

from aristos_council.data.yfinance_adapter import (  # noqa: E402
    _annual_series,
    _as_float,
)

_C = [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31"),
      pd.Timestamp("2023-12-31"), pd.Timestamp("2022-12-31")]  # newest-first


def _income() -> "pd.DataFrame":
    return pd.DataFrame(
        [
            [133.0, 121.0, 110.0, 100.0],            # Total Revenue
            [13.0, 12.0, float("nan"), 10.0],        # Operating Income (a NaN yr)
        ],
        index=["Total Revenue", "Operating Income"],
        columns=_C,
    )


def test_annual_series_is_newest_first_and_clean():
    assert _annual_series(_income(), "Total Revenue") == [133.0, 121.0, 110.0, 100.0]


def test_annual_series_drops_nan_years():
    # the 2023 NaN is dropped; remaining order preserved newest-first
    assert _annual_series(_income(), "Operating Income") == [13.0, 12.0, 10.0]


def test_annual_series_sorts_columns_newest_first():
    # provider hands columns oldest-first -> we still return newest-first
    df = pd.DataFrame(
        [[100.0, 110.0, 121.0, 133.0]],
        index=["Total Revenue"],
        columns=list(reversed(_C)),
    )
    assert _annual_series(df, "Total Revenue") == [133.0, 121.0, 110.0, 100.0]


def test_annual_series_missing_label_is_empty():
    assert _annual_series(_income(), "Invested Capital") == []


def test_annual_series_none_or_empty_frame_is_empty():
    assert _annual_series(None, "Total Revenue") == []
    assert _annual_series(pd.DataFrame(), "Total Revenue") == []


def test_as_float_treats_nan_as_absent():
    assert _as_float(float("nan")) is None
    assert _as_float(None) is None
    assert _as_float("not a number") is None
    assert _as_float(5) == 5.0
