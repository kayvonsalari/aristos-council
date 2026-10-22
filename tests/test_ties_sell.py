"""TIES-SELL-1 - tied ranks hid SELLs.

Live, 2026-10-22 sweep: under B25-3 a tie straddling a cut took its BEST position's verdict at BOTH ends -
BUY at the top, HOLD at the bottom - so ties emptied the SELL slot while BUYs were still given:

* KO peer group, Defensive Income: 5 ranked, MDLZ/CL/ADM tied 3rd -> 1 BUY, 0 SELL;
* MSFT, Value + Momentum: 4 of 6 tied 3rd -> 2 BUY, 0 SELL;
* NVCR, Earnings Power Value: 6 tied at 18th of 23 -> 5 BUY, 0 SELL;
* VKTX, Earnings Power Value: 5 tied at 3rd of 7.

The rule is now the same at both ends: a tie that includes first place is BUY, a tie that includes last place
is SELL, any other tie straddling a cut is HOLD. Checked here by mirror symmetry over every list size 5-25.

(b) The EPV ties are NOT a clamp: there is no floor on the margin of safety. They are the names the lens could
not value (normalised operating profit <= 0, no enterprise value, under 3 years of margins), which
``missing: worst`` places last TOGETHER - the lens's stated rule ("ranks last, reason shown"). Pinned below.
"""
from __future__ import annotations

import pytest

from aristos_council.rank_engine import FactorSpec, quintile_end_count, rank_universe

SPEC = [FactorSpec("f1", "high")]


def _verdicts(values: list[float]) -> list[str]:
    """Verdicts in best-first order for a list of factor values (higher is better)."""
    rows = [(f"T{i:02d}", {"f1": v}) for i, v in enumerate(values)]
    ranked = rank_universe(rows, SPEC)
    order = sorted(ranked, key=lambda r: (r.combined_rank, r.ticker))
    return [r.verdict for r in order]


def _mirror(values: list[float]) -> list[float]:
    return [-v for v in values]


def _cases():
    """(n, values) for n in 5..25 with a tie of 2 or 3 at the top, around the BUY cut, in the middle, around
    the SELL cut, and at the bottom."""
    for n in range(5, 26):
        ends = quintile_end_count(n)
        base = list(range(n, 0, -1))                       # n .. 1, no ties
        for size in (2, 3):
            for start in {0, max(0, ends - 1), n // 2 - 1, max(0, n - ends - 1), n - size}:
                if start < 0 or start + size > n:
                    continue
                vals = list(base)
                for p in range(start, start + size):
                    vals[p] = vals[start]
                yield n, start, size, vals


@pytest.mark.parametrize("n,start,size,values", list(_cases()))
def test_the_cut_is_symmetric_under_ties(n, start, size, values):
    """Reverse the list: a tie that took BUY at the top must take SELL at the bottom, and HOLD stays HOLD."""
    top = _verdicts(values)
    bottom = _verdicts(_mirror(values))
    swap = {"buy": "sell", "sell": "buy", "hold": "hold"}
    assert [swap[v] for v in reversed(bottom)] == top


@pytest.mark.parametrize("n,start,size,values", list(_cases()))
def test_tied_names_share_one_verdict_and_ties_never_empty_one_end_only(n, start, size, values):
    v = _verdicts(values)
    tied = v[start:start + size]
    assert len(set(tied)) == 1
    if start + size == n:                                  # a tie that includes last place
        assert tied[0] == "sell"
    if start == 0:                                         # a tie that includes first place
        assert tied[0] == "buy"
    if 0 < start and start + size < n:                     # any other tie: never more than its ends allow
        ends = quintile_end_count(n)
        assert v.count("buy") <= ends and v.count("sell") <= ends


def test_untied_lists_are_unchanged():
    for n in range(5, 26):
        ends = quintile_end_count(n)
        v = _verdicts(list(range(n, 0, -1)))
        assert v == ["buy"] * ends + ["hold"] * (n - 2 * ends) + ["sell"] * ends


def test_ko_defensive_income_three_tied_for_last_of_five_are_sell():
    # KO.US 2026-10-22: 5 ranked, the leader alone, then a 4th... MDLZ/CL/ADM tied 3rd (of 5) -> was 1 BUY 0 SELL
    v = _verdicts([5, 4, 1, 1, 1])
    assert v == ["buy", "hold", "sell", "sell", "sell"]


def test_msft_value_momentum_four_tied_from_third_of_six_are_sell():
    # MSFT.US 2026-10-22: 2330.TW 1st, AAPL 2nd, MSFT/NVDA/ASML/CSCO tied 3rd of 6 -> was 2 BUY 0 SELL
    v = _verdicts([6, 5, 1, 1, 1, 1])
    assert v == ["buy", "buy", "sell", "sell", "sell", "sell"]


def test_the_b25_3_msft_case_still_holds_a_tie_that_does_not_reach_last_place():
    # MSFT 2026-10-08: AAPL/TSMC tied 1st, MSFT/ASML/CSCO tied 3rd, NVDA 6th: the tie is HOLD, NVDA SELL
    v = _verdicts([10, 10, 5, 5, 5, 1])
    assert v == ["buy", "buy", "hold", "hold", "hold", "sell"]


def test_nvcr_epv_six_unvalued_names_tied_last_of_23_are_sell():
    # NVCR.US 2026-10-22, Earnings Power Value: 17 valued names, 6 the lens could not value -> was 5 BUY 0 SELL
    v = _verdicts(list(range(17, 0, -1)) + [None] * 6)
    assert v.count("buy") == 5 and v[-6:] == ["sell"] * 6


def test_every_name_tied_is_hold():
    assert _verdicts([3, 3, 3, 3, 3]) == ["hold"] * 5


def test_epv_ties_are_abstentions_ranked_last_together_not_a_clamp():
    """No floor on the margin of safety: any two distinct values rank apart, however negative; only names
    with no value at all share the last rank (``missing: worst``)."""
    from aristos_council.tools.quality_epv import earnings_power_value
    from aristos_council.data.adapter import Fundamentals

    def mos(ebit, ev):
        f = Fundamentals(ticker="X", total_revenue=[100.0] * 5, ebit=[ebit] * 5)
        return earnings_power_value(f, ev).margin_of_safety

    deep = [mos(10.0, ev) for ev in (500.0, 1000.0, 5000.0)]
    assert len(set(deep)) == 3 and all(m < -0.5 for m in deep)      # -83%, -92%, -98%: three ranks
    assert mos(-5.0, 100.0) is None                                 # nothing to capitalise: abstains
    v = _verdicts([*deep, 1.0, 0.5, None, None])
    assert len(set(v[-2:])) == 1
