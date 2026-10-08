"""B25-3 - a tie at a verdict boundary takes the verdict of the tie group's BEST position (TIE_VERDICT_RULE).

MSFT, Value + Momentum, 2026-10-08: AAPL 1, TSMC 1, MSFT/ASML/CSCO tied 3rd, NVDA 6th. The alphabet sent MSFT to
SELL (and the company page read "SELL - 3rd of 6"), giving 2 BUY vs 4 SELL. 3rd of 6 is HOLD."""
import pytest

from aristos_council import rank_engine
from aristos_council.rank_engine import FactorSpec, rank_universe

SPEC = [FactorSpec("f1", "high")]


def _run(values):
    rows = [(t, {"f1": v}) for t, v in values.items()]
    return {r.ticker: r.verdict for r in rank_universe(rows, SPEC)}


MSFT_CASE = {"AAPL": 10, "TSMC": 10, "MSFT": 5, "ASML": 5, "CSCO": 5, "NVDA": 1}


def test_the_setting_is_one_named_constant_defaulting_to_best_position():
    assert rank_engine.TIE_VERDICT_RULE == "best_position"


def test_the_msft_case_the_three_tied_names_are_hold_not_sell():
    v = _run(MSFT_CASE)
    assert v == {"AAPL": "buy", "TSMC": "buy", "MSFT": "hold", "ASML": "hold", "CSCO": "hold", "NVDA": "sell"}
    assert list(v.values()).count("buy") == 2 and list(v.values()).count("sell") == 1


def test_the_buy_end_a_tie_straddling_the_buy_cut_is_all_buy():
    # 6 names: the top fifth rounds up to 2 BUYs. B, C, D tie from 2nd place, so all three take 2nd's verdict.
    v = _run({"A": 10, "B": 8, "C": 8, "D": 8, "E": 4, "F": 1})
    assert v["A"] == v["B"] == v["C"] == v["D"] == "buy"
    assert v["E"] == v["F"] == "sell"


@pytest.mark.tie_rule("alphabetical")
def test_the_old_alphabetical_rule_is_still_available_and_splits_the_tie():
    v = _run(MSFT_CASE)
    assert v["ASML"] == "hold" and v["CSCO"] == "hold" and v["MSFT"] == "sell"


def test_without_ties_nothing_changes():
    v = _run({"A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1})
    assert [v[t] for t in "ABCDEF"] == ["buy", "buy", "hold", "hold", "sell", "sell"]


def test_a_tie_wholly_inside_one_verdict_is_unchanged():
    v = _run({"A": 10, "B": 9, "C": 5, "D": 5, "E": 2, "F": 1})
    assert v["C"] == v["D"] == "hold"
