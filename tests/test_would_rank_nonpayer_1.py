"""WOULD-RANK-NONPAYER-1 - no "would rank Nth of M" on a dividend lens for a company that pays none.

Live sweep (2026-10-22), lens table and lens notes:
    VKTX.US | Defensive Income | does not apply | none | dividend yield 0%; the rule requires at least
            1.5% (would rank 29th of 41; not a vote) |
    NVCR.US | Defensive Income | ... dividend yield 0%; ... (would rank 26th of 41; not a vote) |
Where a company paying nothing would sit among dividend payers is not a reading, so the dividend
lenses drop it for a non-payer. A payer keeps it (MSFT at 0.74%: "would rank 4th of 14").
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import aristos_council.company_report as cr
from aristos_council.company_story import table_rows
from aristos_council.pipeline import MultiStrategyCell
from aristos_council.shadow_rank import WouldRank
from tests.test_company_story import _report

SID = "conservative_plus_v1"


def _votes(monkeypatch, yield_observed, *, rule="min_dividend_yield"):
    monkeypatch.setattr(cr, "would_rank", lambda *a, **k: WouldRank(
        available=True, position=29, cohort_size=41, lens_ranked=0))
    reason = "dividend yield 0%; the rule requires at least 1.5%"
    cell = MultiStrategyCell(strategy_id=SID, status="excluded", reason=reason,
                             reason_plain=reason)
    outcomes = {"ACM": {rule: {"passed": False, "observed": yield_observed, "threshold": 0.015}}}
    result = SimpleNamespace(rank_strategy=SimpleNamespace(id=SID, kind="selector", asks=""),
                             ranked=[], shadow_pool={}, screen_outcomes=outcomes)
    multi = SimpleNamespace(rows=[SimpleNamespace(ticker="ACM", cells={SID: cell})],
                            strategy_ids=[SID], results={SID: result},
                            strategy_names={SID: "Defensive Income"})
    return cr.votes_from_multi(multi, "ACM")


def test_viking_shape_a_zero_yield_drops_the_would_rank(monkeypatch):
    vote = _votes(monkeypatch, 0.0)[0]
    assert vote.status == "excluded" and vote.would_rank is None
    row = table_rows(_report([vote]))[0]
    assert row.reason == "dividend yield 0%; the rule requires at least 1.5%"
    assert "would rank" not in row.reason and "would rank" not in row.full_reason


def test_microsoft_shape_a_real_yield_keeps_the_would_rank(monkeypatch):
    vote = _votes(monkeypatch, 0.0074)[0]
    assert vote.would_rank is not None and vote.would_rank.position == 29
    row = table_rows(_report([vote]))[0]
    assert "(would rank 29th of 41; not a vote)" in row.reason
    assert "would rank 29th of the 41 names" in row.full_reason


@pytest.mark.parametrize("observed", [None, "n/a"])
def test_an_unread_yield_is_not_zero_so_the_reading_stays(monkeypatch, observed):
    assert _votes(monkeypatch, observed)[0].would_rank is not None


def test_a_lens_without_a_dividend_rule_keeps_its_would_rank(monkeypatch):
    assert _votes(monkeypatch, 0.0, rule="min_roic")[0].would_rank is not None
