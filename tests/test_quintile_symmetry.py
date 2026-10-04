"""QUINTILE-ASYMMETRY-1 — the same number of names at each end of a quintile cut.

Seen: list TM, GM, F under Magic Formula RAW + Quality. #1 of 3 got BUY but #3 of 3 stayed
HOLD, because the top fifth was rounded up and the bottom fifth was not."""
import math

import pytest

from aristos_council.rank_engine import FactorSpec, quintile_end_count, rank_universe

SPEC = [FactorSpec(name="f", direction="high")]


def _verdicts(n):
    rows = [(f"T{i:02d}", {"f": float(n - i)}) for i in range(n)]
    out = rank_universe(rows, SPEC, cut="quintile")
    return [r.verdict for r in out]


@pytest.mark.parametrize("n", range(3, 41))
def test_the_same_count_gets_buy_at_the_top_and_sell_at_the_bottom(n):
    v = _verdicts(n)
    assert len(v) == n
    ends = math.ceil(n / 5)
    assert quintile_end_count(n) == ends
    assert v.count("buy") == ends
    assert v.count("sell") == ends
    assert v[:ends] == ["buy"] * ends            # BUY is the top, SELL the bottom, in order
    assert v[-ends:] == ["sell"] * ends
    assert set(v[ends:-ends]) <= {"hold"}


def test_three_names_give_one_buy_one_hold_one_sell():
    assert _verdicts(3) == ["buy", "hold", "sell"]


def test_the_buy_basket_is_exactly_what_it_was_before_the_change():
    """The backtests measure only the BUY basket; its size was ceil(n/5) before and is now."""
    for n in range(3, 41):
        old_buys = sum(1 for i in range(n) if i < n / 5.0)
        assert _verdicts(n).count("buy") == old_buys
