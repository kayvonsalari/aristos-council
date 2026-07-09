"""growth_screen_v2 / growth_garp_v2 — non-gating momentum (4C-FIX-1).

v1's growth screen gated on min_price_momentum, which double-counted the momentum_12m
rank factor and hard-vetoed dip names (ADBE, -41% 12m). v2 removes the momentum GATE:
dip names are dragged down the order by the factor, not screened out.
"""

from __future__ import annotations

from pathlib import Path

from aristos_council.strategy.loader import load_strategy

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"


def test_growth_screen_v2_is_v1_minus_the_momentum_gate():
    v1 = load_strategy(STRAT_DIR / "growth_screen_v1.yaml")
    v2 = load_strategy(STRAT_DIR / "growth_screen_v2.yaml")
    v1_names = [c.name for c in v1.criteria]
    v2_names = [c.name for c in v2.criteria]
    # exactly one change: min_price_momentum removed
    assert "min_price_momentum" in v1_names
    assert "min_price_momentum" not in v2_names
    assert v2_names == [n for n in v1_names if n != "min_price_momentum"]
    # v1 is untouched semantically (still gates momentum)
    assert v1.id == "growth_screen_v1" and v2.id == "growth_screen_v2" and v2.version == 2
    # the other four criteria keep their thresholds byte-for-byte
    v1_by = {c.name: c.threshold for c in v1.criteria}
    for c in v2.criteria:
        assert c.threshold == v1_by[c.name]
