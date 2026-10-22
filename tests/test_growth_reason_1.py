"""GROWTH-REASON-1 - Growth names the cause when its screen could not read return on capital or
revenue growth.

Live sweep (2026-10-22), lens table:
    F.US     | Growth[^7] | does not apply | none | return on invested capital not available |
    NVCR.US  | Growth[^7] | does not apply | none | return on invested capital not available |
    VKTX.US  | Growth[^7] | does not apply | none | revenue growth (annual average) not available |
while five other lenses on the same page read "no operating profit (fiscal year to Dec 2025)".
The cause is known (a loss-maker, a company with no sales), so the reason says it. Only the
reason TEXT changes: the name was excluded before and is excluded now.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

from aristos_council.company_report import LensVote
from aristos_council.data.adapter import Fundamentals
from aristos_council.factors import screen_unavailable_reason
from aristos_council.operating_profit import (has_no_revenue, no_operating_profit_reason,
                                              no_revenue_reason)
from aristos_council.pipeline import _rank_stage, exclusion_rows
from aristos_council.strategy.loader import load_strategy
from aristos_council.strategy.rank_loader import load_rank_strategy
from aristos_council.summary_counts import topics
from tests.test_company_story import _report, _skip
from aristos_council.company_story import answer_lines
from tests.test_null_excludes_2 import _FULL, _Adapter

STRAT_DIR = Path(__file__).resolve().parents[1] / "strategies"
GROWTH = load_rank_strategy(STRAT_DIR / "growth_garp_v3.yaml")
GROWTH_SCREEN = load_strategy(STRAT_DIR / f"{GROWTH.council_screen_strategy}.yaml")
MOMENTUM = load_rank_strategy(STRAT_DIR / "magic_formula_momentum_v1.yaml")

_DATES = ["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31"]

# Ford / NovoCure shape: sales growing, an operating LOSS in the latest year and a loss-mixed
# pretax history that leaves the through-cycle tax rate degenerate -> ROIC abstains (null != false).
_LOSS_MAKER = dict(_FULL,
                   operating_income=[-500.0, 300.0, 200.0, 100.0, 50.0],
                   ebit=[-500.0, 300.0, 200.0, 100.0, 50.0],
                   pretax_income=[-900.0, 100.0, 50.0, -400.0, 10.0],
                   period_ends={"operating_income": _DATES, "total_revenue": _DATES})
# Viking shape: no revenue at all, so a growth rate is undefined.
_NO_SALES = dict(_FULL, total_revenue=[0.0, 0.0, 0.0, 0.0, 0.0],
                 operating_income=[-300.0, -200.0, -90.0, -80.0, -70.0],
                 ebit=[-300.0, -200.0, -90.0, -80.0, -70.0],
                 pretax_income=[-280.0, -190.0, -85.0, -75.0, -66.0],
                 period_ends={"operating_income": _DATES, "total_revenue": _DATES})


def _stage(by_ticker, rank=GROWTH):
    screen = load_strategy(STRAT_DIR / f"{rank.council_screen_strategy}.yaml")
    return _rank_stage(list(by_ticker), rank, _Adapter(by_ticker), today=date(2026, 6, 30),
                       prefilter_criteria=screen.criteria)


def test_ford_shape_growth_says_no_operating_profit_like_the_other_lenses():
    ranked, excluded, _, _, outcomes = _stage({"GOOD": dict(_FULL), "F": _LOSS_MAKER})
    reason = dict(excluded)["F"]
    assert reason == "no operating profit (fiscal year to Dec 2025)"
    assert reason == no_operating_profit_reason(Fundamentals(ticker="F", **_LOSS_MAKER))
    # the exclusion itself is unchanged: still out, and the rule is still NOT-EVAL, never a fail
    assert "F" not in {r.ticker for r in ranked}
    assert outcomes["F"]["min_roic"]["passed"] is None


def test_ford_shape_growth_reason_is_the_same_string_the_profit_guard_writes():
    """One string, so the company page counts Growth in the same reason group as the others."""
    _, growth, _, _, _ = _stage({"GOOD": dict(_FULL), "F": _LOSS_MAKER})
    _, momentum, _, _, _ = _stage({"GOOD": dict(_FULL), "F": _LOSS_MAKER}, rank=MOMENTUM)
    assert dict(growth)["F"] == dict(momentum)["F"]


def test_viking_shape_growth_says_no_revenue():
    ranked, excluded, _, _, outcomes = _stage({"GOOD": dict(_FULL), "VKTX": _NO_SALES})
    reason = dict(excluded)["VKTX"]
    assert reason == "no revenue (fiscal year to Dec 2025)"
    assert "VKTX" not in {r.ticker for r in ranked}
    assert outcomes["VKTX"]["min_revenue_cagr"]["passed"] is None


def test_a_gap_with_no_known_cause_still_says_not_available():
    """ROIC missing on a PROFITABLE company (no invested capital line) is a real data gap."""
    outcomes = {"min_revenue_cagr": {"passed": True}, "min_roic": {"passed": None}}
    f = Fundamentals(ticker="AMD", **dict(_FULL, invested_capital=[]))
    assert screen_unavailable_reason(outcomes, f) == "return on invested capital not available"
    # revenue CAGR missing on short history (latest revenue positive) is not "no revenue"
    short = Fundamentals(ticker="NEW", **dict(_FULL, total_revenue=[100.0]))
    assert not has_no_revenue(short)
    assert screen_unavailable_reason({"min_revenue_cagr": {"passed": None}}, short) == \
        "revenue growth (annual average) not available"


def test_no_revenue_reason_without_dates_invents_none():
    assert no_revenue_reason(Fundamentals(ticker="X", total_revenue=[0.0])) == \
        "no revenue (latest fiscal year)"


def test_company_page_lens_table_and_story_read_the_new_reason():
    _, excluded, _, _, _ = _stage({"GOOD": dict(_FULL), "F": _LOSS_MAKER})
    growth = LensVote("growth_garp_v3", "Growth", status="excluded", reason=dict(excluded)["F"])
    assert growth.result() == "does not apply - no operating profit (fiscal year to Dec 2025)"
    profit = no_operating_profit_reason(Fundamentals(ticker="F", **_LOSS_MAKER))
    votes = [_skip("Quality", profit), _skip("Magic Formula RAW", profit),
             _skip("Growth", growth.reason)]
    _first, second = answer_lines(_report(votes))
    assert second == f"None of the three lenses could apply: {profit}."


def test_list_report_exclusion_line_reads_the_new_reason():
    _, excluded, _, names, outcomes = _stage({"GOOD": dict(_FULL), "VKTX": _NO_SALES})
    result = SimpleNamespace(excluded=excluded, names=names, screen_outcomes=outcomes)
    rows = {r["ticker"]: r for r in exclusion_rows(result)}
    assert rows["VKTX"]["sentence"] == "no revenue (fiscal year to Dec 2025)"


def test_summary_check_reads_no_revenue_as_the_revenue_growth_topic():
    assert "revenue growth" in topics("no revenue (fiscal year to Dec 2025)")
