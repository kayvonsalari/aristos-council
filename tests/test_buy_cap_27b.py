"""Batch 27B-1 - the BUY cap (``buy_requires``) and the Growth v3 definition files.

The cap is a general rank-engine feature (any factor, top_half or top_third), a cap and never a
screen: it only turns a would-be BUY into HOLD, with the reason shown. A strategy without
``buy_requires`` must produce byte-identical output. Growth v3 ships dormant: hidden, under test.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from aristos_council.backtest import track_record
from aristos_council.rank_engine import FactorSpec, rank_universe, ranked_table_rows
from aristos_council.strategy.discovery import lens_strategy_ids
from aristos_council.strategy.picker import strategy_choices
from aristos_council.strategy.rank_loader import RankStrategy, load_rank_strategy

STRATEGIES = Path(__file__).resolve().parents[1] / "strategies"
SPECS = [FactorSpec("revenue_growth"), FactorSpec("roic"), FactorSpec("earnings_yield")]


def _rows(n=10, seed=3):
    rng = random.Random(seed)
    return [(f"T{i:02d}", {"revenue_growth": rng.random(), "roic": rng.random(),
                           "earnings_yield": rng.random()}) for i in range(n)]


def _buys(ranked):
    return [r.ticker for r in ranked if r.verdict == "buy"]


def _slow_grower_rows():
    """SLOW is cheap and capital-efficient but the slowest grower: first on combined rank, last on growth."""
    rows = [("SLOW", {"revenue_growth": 0.0, "roic": 1.0, "earnings_yield": 1.0})]
    rows += [(f"T{i}", {"revenue_growth": 0.1 + i * 0.01, "roic": 0.1, "earnings_yield": 0.1})
             for i in range(9)]
    return rows


def test_a_buy_whose_growth_rank_is_in_the_bottom_half_is_capped_at_hold_with_the_reason():
    rows = _slow_grower_rows()
    plain = rank_universe(rows, SPECS)
    assert plain[0].ticker == "SLOW" and plain[0].verdict == "buy"
    capped = rank_universe(rows, SPECS, buy_requires={"revenue_growth": "top_half"})
    slow = next(r for r in capped if r.ticker == "SLOW")
    assert slow.verdict == "hold"
    assert slow.cap_reason == "ranked 1st, but growing slower than the industry median, so HOLD"
    # nothing else moved: same order, same ranks, verdicts equal except the capped name
    assert [r.ticker for r in capped] == [r.ticker for r in plain]
    assert ([r.verdict for r in capped if r.ticker != "SLOW"]
            == [r.verdict for r in plain if r.ticker != "SLOW"])


def test_the_cap_never_excludes_never_sells_and_never_promotes():
    plain = rank_universe(_rows(), SPECS)
    capped = rank_universe(_rows(), SPECS, buy_requires={"revenue_growth": "top_half"})
    for a, b in zip(plain, capped):
        assert a.excluded == b.excluded
        if a.verdict != "buy":
            assert b.verdict == a.verdict and b.cap_reason == ""
        else:
            assert b.verdict in ("buy", "hold")


def test_top_third_is_stricter_than_top_half_and_the_median_name_passes_top_half():
    rows = _rows(n=15, seed=9)
    half = _buys(rank_universe(rows, SPECS, buy_requires={"revenue_growth": "top_half"}))
    third = _buys(rank_universe(rows, SPECS, buy_requires={"revenue_growth": "top_third"}))
    assert set(third) <= set(half) <= set(_buys(rank_universe(rows, SPECS)))
    from aristos_council.rank_engine import _passes_buy_requirement as ok
    assert ok(3.0, 5, "top_half") and not ok(3.5, 5, "top_half")        # the median of 5 is rank 3
    assert ok(2.0, 6, "top_third") and not ok(3.0, 6, "top_third")


def test_any_factor_can_carry_the_cap():
    out = rank_universe(_rows(), SPECS, buy_requires={"roic": "top_third"})
    capped = [r for r in out if r.cap_reason]
    assert capped and all("top third" in r.cap_reason for r in capped)


@pytest.mark.parametrize("cut", ["quintile", "top_k", "top_percentile"])
@pytest.mark.parametrize("seed", range(5))
def test_without_buy_requires_the_output_is_byte_identical(cut, seed):
    rows = _rows(n=12, seed=seed)
    a = rank_universe(rows, SPECS, cut=cut)
    assert a == rank_universe(rows, SPECS, cut=cut, buy_requires=None)
    assert a == rank_universe(rows, SPECS, cut=cut, buy_requires={})
    assert all(r.cap_reason == "" for r in a)


def test_every_published_strategy_has_no_cap_and_no_status_except_v3():
    checked = 0
    for path in sorted(STRATEGIES.glob("*.yaml")):
        try:
            s = load_rank_strategy(path)
        except Exception:                      # a screen/council file, not a rank strategy
            continue
        checked += 1
        if s.id == "growth_garp_v3":
            continue
        assert s.buy_requires == {} and s.status == "", s.id
    assert checked >= 10


def test_the_loader_rejects_a_cap_on_a_factor_the_strategy_does_not_rank_or_an_unknown_rule():
    base = dict(id="x_v1", name="x", version=1, factors=[{"name": "roic"}])
    RankStrategy.model_validate({**base, "buy_requires": {"roic": "top_half"}})
    with pytest.raises(ValueError):
        RankStrategy.model_validate({**base, "buy_requires": {"revenue_growth": "top_half"}})
    with pytest.raises(ValueError):
        RankStrategy.model_validate({**base, "buy_requires": {"roic": "top_quarter"}})
    with pytest.raises(ValueError):
        RankStrategy.model_validate({**base, "status": "nonsense"})


def test_the_ranked_table_shows_the_reason_beside_the_verdict():
    table, _ = ranked_table_rows(rank_universe(_slow_grower_rows(), SPECS,
                                               buy_requires={"revenue_growth": "top_half"}))
    assert "growing slower than the industry median, so HOLD" in table[0]["Verdict"]


# --------------------------------------------------------------------------- #
# the v3 files
# --------------------------------------------------------------------------- #
def test_growth_v3_is_the_v2_factors_with_one_cap_hidden_and_under_test():
    v2, v3 = (load_rank_strategy(STRATEGIES / f"growth_garp_v{n}.yaml") for n in (2, 3))
    assert [f.name for f in v3.factors] == [f.name for f in v2.factors] == [
        "revenue_growth", "roic", "earnings_yield", "momentum_12m"]
    assert (v3.version, v3.display_name, v3.cut, v3.min_market_cap, v3.prefilter_screen) == (
        3, "Growth", "quintile", 5.0e9, True)
    assert v3.council_screen_strategy == "growth_screen_v3"
    assert v3.buy_requires == {"revenue_growth": "top_half"}
    assert v3.ui == "hidden" and v3.status == "under_test"
    assert v3.asks.startswith(
        "Growing faster than most of its industry at a fair price: a BUY needs sales growth")
    assert strategy_choices([v3]) == []                        # never offered while hidden


def test_growth_screen_v3_is_absolute_floors_only_and_has_no_peg():
    import yaml
    data = yaml.safe_load((STRATEGIES / "growth_screen_v3.yaml").read_text(encoding="utf-8"))
    assert {c["name"]: c["threshold"] for c in data["criteria"]} == {
        "min_revenue_cagr": 0.0, "min_roic": 0.0, "min_market_cap": 5e9}
    from aristos_council.strategy.loader import load_strategy
    load_strategy(STRATEGIES / "growth_screen_v3.yaml")        # validates against the criterion registry
    assert "growth_screen_v3" in lens_strategy_ids(STRATEGIES)


def test_an_under_test_lens_has_no_badge_and_borrows_none_from_v2():
    b = track_record("tech_semiconductors", "growth_garp_v3")
    assert b.label == "under test here" and b.verdict is None
    assert track_record("tech_semiconductors", "growth_garp_v2").label != "under test here"


def test_the_grid_cell_carries_the_cap_reason_and_is_unchanged_without_one():
    from aristos_council.pipeline import MultiStrategyCell
    base = dict(strategy_id="x_v1", status="ranked", position=1, cohort_size=10, verdict="hold")
    assert MultiStrategyCell(**base).render() == "#1 of 10 · HOLD"
    capped = MultiStrategyCell(**base, cap_reason="ranked 1st, but growing slower than the industry median, so HOLD")
    assert capped.render() == "#1 of 10 · HOLD · ranked 1st, but growing slower than the industry median, so HOLD"
