"""TIES-SELL-1 - one tie rule at both ends of the cut (owner ruling on PR #193).

Live, 2026-10-22 sweep: under B25-3 a tie straddling a cut took its BEST position's verdict - BUY at the top,
HOLD at the bottom - so ties could give BUYs while emptying the SELL slot (KO peer group, Defensive Income: 5
ranked, MDLZ/CL/ADM tied 3rd -> 1 BUY 0 SELL; MSFT, Value + Momentum: 4 of 6 tied 3rd -> 2 BUY 0 SELL).

The rule now: a tie group that straddles a cut line (BUY or SELL) is HOLD; only a tie group lying wholly inside
the top band is BUY, and only one wholly inside the bottom band is SELL. Checked by mirror symmetry over every
list size 5-25 with ties at the top, around both cuts, in the middle and at the bottom.
"""
from __future__ import annotations

import pytest

from aristos_council.rank_engine import FactorSpec, quintile_end_count, rank_universe

SPEC = [FactorSpec("f1", "high")]


def _verdicts(values: list) -> list[str]:
    """Verdicts in best-first order for a list of factor values (higher is better)."""
    rows = [(f"T{i:02d}", {"f1": v}) for i, v in enumerate(values)]
    ranked = rank_universe(rows, SPEC)
    order = sorted(ranked, key=lambda r: (r.combined_rank, r.ticker))
    return [r.verdict for r in order]


def _band(p: int, n: int) -> str:
    ends = quintile_end_count(n)
    return "buy" if p < ends else "sell" if p >= n - ends else "hold"


def _cases():
    """(n, start, size, values): n in 5..25, a tie of 2 or 3 at the top, across the BUY cut, in the middle,
    across the SELL cut and at the bottom."""
    for n in range(5, 26):
        ends = quintile_end_count(n)
        base = list(range(n, 0, -1))
        for size in (2, 3):
            for start in sorted({0, max(0, ends - 1), n // 2 - 1, max(0, n - ends - 1), n - size}):
                if start + size > n:
                    continue
                vals = list(base)
                for p in range(start, start + size):
                    vals[p] = vals[start]
                yield n, start, size, vals


CASES = list(_cases())


@pytest.mark.parametrize("n,start,size,values", CASES)
def test_a_tie_straddling_a_cut_is_hold_and_a_tie_inside_a_band_keeps_it(n, start, size, values):
    v = _verdicts(values)
    tied = v[start:start + size]
    assert len(set(tied)) == 1                              # a tie shares one verdict
    bands = {_band(p, n) for p in range(start, start + size)}
    expected = bands.pop() if len(bands) == 1 else "hold"
    assert tied[0] == expected
    # every untied name keeps its own band
    for p in range(n):
        if not start <= p < start + size:
            assert v[p] == _band(p, n)


@pytest.mark.parametrize("n,start,size,values", CASES)
def test_the_rule_is_the_same_at_both_ends(n, start, size, values):
    """Reverse the list: whatever a tie got at the top it gets mirrored at the bottom."""
    top = _verdicts(values)
    bottom = _verdicts([-x for x in values])
    swap = {"buy": "sell", "sell": "buy", "hold": "hold"}
    assert [swap[x] for x in reversed(bottom)] == top


@pytest.mark.parametrize("n,start,size,values", CASES)
def test_ties_never_give_more_buys_than_the_cut_or_unbalance_the_ends(n, start, size, values):
    v = _verdicts(values)
    ends = quintile_end_count(n)
    assert v.count("buy") <= ends and v.count("sell") <= ends


def test_untied_lists_are_unchanged():
    for n in range(5, 26):
        ends = quintile_end_count(n)
        assert _verdicts(list(range(n, 0, -1))) == ["buy"] * ends + ["hold"] * (n - 2 * ends) + ["sell"] * ends


def test_ko_defensive_income_three_tied_across_the_sell_cut_are_hold():
    # KO.US 2026-10-22: 5 ranked, MDLZ/CL/ADM tied 3rd of 5 (the SELL cut is the 5th place)
    assert _verdicts([5, 4, 1, 1, 1]) == ["buy", "hold", "hold", "hold", "hold"]


def test_msft_value_momentum_four_tied_from_third_of_six_are_hold():
    # MSFT.US 2026-10-22: 2330.TW 1st, AAPL 2nd, MSFT/NVDA/ASML/CSCO tied 3rd of 6 (straddles the SELL cut)
    assert _verdicts([6, 5, 1, 1, 1, 1]) == ["buy", "buy", "hold", "hold", "hold", "hold"]


def test_a_tie_across_the_buy_cut_is_hold_not_buy():
    # 6 names, 2 BUY slots: three tied from 2nd place straddle the BUY cut
    assert _verdicts([10, 8, 8, 8, 4, 1]) == ["buy", "hold", "hold", "hold", "sell", "sell"]


def test_ties_wholly_inside_the_end_bands_keep_buy_and_sell():
    # 10 names, 2 per end: a pair tied 1st-2nd and a pair tied 9th-10th lie inside their bands
    assert _verdicts([9, 9, 8, 7, 6, 5, 4, 3, 1, 1]) == ["buy", "buy"] + ["hold"] * 6 + ["sell", "sell"]


def test_every_name_tied_is_hold():
    assert _verdicts([3, 3, 3, 3, 3]) == ["hold"] * 5
