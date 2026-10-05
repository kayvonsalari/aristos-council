"""NULL-EXCLUDES-2 (Batch 19A, A1) - a screen rule that could not read its input is not a pass.

Live (2026-10-05, the NVDA/AMD/INTC list): Value + Momentum read "Return on invested capital:
passed 1, not tested 1" and AMD - whose ROIC was unavailable - was counted among "2 passed its
rules". The lens must abstain instead: the name is excluded with "<rule> not available" (not a
fail, not a vote), never ranked on a gap the screen had no figure to check.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aristos_council.data.adapter import (
    Fundamentals, MarketDataAdapter, PriceBar, PriceHistory)
from aristos_council.factors import screen_evaluate, screen_unavailable_reason
from aristos_council.pipeline import _rank_stage
from aristos_council.strategy.loader import load_strategy
from aristos_council.strategy.rank_loader import load_rank_strategy
from aristos_council.tools.criteria.registry import REGISTRY

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
MOMENTUM = load_rank_strategy(STRAT_DIR / "magic_formula_momentum_v1.yaml")
SCREEN = load_strategy(STRAT_DIR / f"{MOMENTUM.council_screen_strategy}.yaml")

_FULL = dict(
    market_cap=2e10, sector="Technology", quote_type="EQUITY", currency="USD",
    operating_income=[400.0, 380.0, 360.0, 340.0, 320.0],
    ebit=[400.0, 380.0, 360.0, 340.0, 320.0],
    tax_provision=[80.0, 76.0, 72.0, 68.0, 64.0],
    pretax_income=[400.0, 380.0, 360.0, 340.0, 320.0],
    invested_capital=[1000.0, 1000.0, 1000.0, 1000.0, 1000.0],
    total_revenue=[2000.0, 1900.0, 1800.0, 1700.0, 1600.0],
    total_debt=1e8)


class _Adapter(MarketDataAdapter):
    name = "fake"

    def __init__(self, by_ticker):
        self._by = by_ticker

    def get_fundamentals(self, t):
        return Fundamentals(ticker=t, **self._by[t])

    def get_price_history(self, t, *, start, end):
        return PriceHistory(ticker=t, bars=[
            PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                     close=100 + 0.05 * i, adj_close=100 + 0.05 * i, volume=10)
            for i in range(260)])

    def get_dividend_history(self, t, *, start, end):
        return []


def _stage(by_ticker, *, rank=MOMENTUM, screen=SCREEN):
    return _rank_stage(list(by_ticker), rank, _Adapter(by_ticker), today=date(2026, 6, 30),
                       prefilter_criteria=screen.criteria)


def test_name_with_no_roic_never_reaches_the_ranked_set():
    """The AMD shape: ROIC is not computable (no invested capital), the screen abstains."""
    ranked, excluded, _, _, outcomes = _stage({
        "GOOD": dict(_FULL),
        "AMD": dict(_FULL, invested_capital=[]),
    })
    assert "AMD" not in {r.ticker for r in ranked}
    reasons = dict(excluded)
    assert "not available" in reasons["AMD"]
    assert "return on invested capital" in reasons["AMD"].lower()
    # the abstention is a "not tested" in the rules tally, never a fail
    assert outcomes["AMD"]["min_roic"]["passed"] is None


def test_the_reason_is_a_does_not_apply_not_a_screen_fail():
    _, excluded, _, _, _ = _stage({"GOOD": dict(_FULL), "AMD": dict(_FULL, invested_capital=[])})
    reason = dict(excluded)["AMD"]
    assert not reason.startswith("screen:")        # the scoreboard parses "screen: " as a FAIL


def test_company_report_wording_reads_does_not_apply_roic_not_available():
    from aristos_council.company_report import LensVote
    _, excluded, _, _, _ = _stage({"GOOD": dict(_FULL), "AMD": dict(_FULL, invested_capital=[])})
    vote = LensVote(strategy_id="x", label="Value + Momentum", status="excluded",
                    reason=dict(excluded)["AMD"])
    assert vote.result() == "does not apply - return on invested capital not available"


def test_every_input_present_still_ranks():
    ranked, excluded, _, _, _ = _stage({"A": dict(_FULL), "B": dict(_FULL, market_cap=3e10)})
    assert {r.ticker for r in ranked} == {"A", "B"}
    assert not excluded


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_audit_every_registered_rule_a_missing_input_never_passes_into_the_ranking(name):
    """Audit: for EVERY registered criterion, a name whose input is missing either excludes
    (reason names the rule) or is one of the two documented keep-and-flag abstentions."""
    from aristos_council.factors import FactorInputs
    crit = REGISTRY[name]
    sel = [type("S", (), {"name": name, "threshold": _default_threshold(crit)})()]
    fi = FactorInputs(ticker="X", fundamentals=Fundamentals(ticker="X"), last_close=None,
                      return_12m=None)
    reason, _, _, outcomes = screen_evaluate(sel, fi)
    if reason is not None:                 # a confirmed fail on an empty record: already out
        return
    unavailable = screen_unavailable_reason(outcomes, fi.fundamentals)
    if all(o["passed"] is not None for o in outcomes.values()):
        return                              # the rule evaluated on an empty record (a real pass)
    if name.startswith("max_payout_ratio"):
        assert unavailable is None          # documented keep-and-flag (dividend cover, ITEM 3)
    else:
        assert unavailable is not None and "not available" in unavailable


def test_non_usd_market_cap_abstention_is_not_a_missing_input():
    """Rule 8: the figure is there; only the USD comparison is refused. It must not take every
    foreign name out of every lens."""
    from aristos_council.factors import FactorInputs
    f = Fundamentals(ticker="SKH", **dict(_FULL, market_cap=1.69e15, currency="KRW"))
    fi = FactorInputs(ticker="SKH", fundamentals=f, last_close=100.0, return_12m=0.1)
    _, _, _, outcomes = screen_evaluate(SCREEN.criteria, fi)
    assert outcomes["min_market_cap"]["passed"] is None
    assert screen_unavailable_reason(outcomes, f) is None


def test_payout_abstention_keeps_the_name_with_its_dagger():
    """ITEM 3 stays: a covered-by-nothing payout ratio (no usable cash flow or EPS) is a flagged
    keep, not an exclusion."""
    outcomes = {"max_payout_ratio_fcf": {"passed": None, "basis": "abstained"}}
    assert screen_unavailable_reason(outcomes, Fundamentals(ticker="X")) is None


def _default_threshold(crit):
    spec = crit.threshold_param
    return spec.default if spec is not None and spec.default is not None else 1.0
