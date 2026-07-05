"""Borderline-exclusion visibility (hardening ITEM 2): a screen fail within 5%
(relative) of its threshold is tagged ``[borderline]`` in the exclusion reason — a
legibility flag only, the floor is unchanged. The tag lives in the reason string, so
it flows to every render site (CLI, Universe Run tab, snapshot notes) unchanged.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.data.adapter import Fundamentals
from aristos_council.factors import (
    FactorInputs,
    is_borderline_fail,
    screen_prefilter_fail,
)
from aristos_council.strategy.loader import load_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


def test_is_borderline_fail_relative_margin_both_directions():
    # min_* fail (below the floor)
    assert is_borderline_fail(0.1198, 0.12) is True        # 0.17% below -> borderline
    assert is_borderline_fail(0.106, 0.12) is False        # 11.7% below -> not
    # direction-aware for a max_* criterion (fail is ABOVE the ceiling)
    assert is_borderline_fail(0.87, 0.85) is True          # 2.4% over -> borderline
    assert is_borderline_fail(0.95, 0.85) is False         # 11.8% over -> not
    # guards: non-numeric / zero threshold -> no meaningful margin
    assert is_borderline_fail(None, 0.12) is False
    assert is_borderline_fail(0.1, 0) is False
    # the tolerance edge is inclusive
    assert is_borderline_fail(0.114, 0.12) is True         # exactly 5% -> borderline


def _roic_fi(ticker, op_income):
    # ROIC = NOPAT / invested_capital, tax 0 -> NOPAT == operating_income; IC 100.
    return FactorInputs(ticker=ticker, fundamentals=Fundamentals(
        ticker=ticker, market_cap=1e11, operating_income=[op_income] * 4,
        tax_provision=[0.0] * 4, pretax_income=[op_income] * 4,
        invested_capital=[100.0] * 4))


def test_screen_prefilter_tags_a_near_miss_roic_fail():
    lens = load_strategy(STRAT_DIR / "magic_value_screen_v1.yaml")
    # ROIC 0.1198 < the 0.12 floor by <5% -> excluded AND tagged borderline
    reason = screen_prefilter_fail(lens.criteria, _roic_fi("PFE", 11.98))
    assert reason is not None
    assert "min_roic" in reason and "[borderline]" in reason


def test_screen_prefilter_does_not_tag_a_clear_fail():
    lens = load_strategy(STRAT_DIR / "magic_value_screen_v1.yaml")
    # ROIC 0.106 < 0.12 by ~11.7% -> excluded, NOT borderline
    reason = screen_prefilter_fail(lens.criteria, _roic_fi("XYZ", 10.6))
    assert reason is not None
    assert "min_roic" in reason and "[borderline]" not in reason
