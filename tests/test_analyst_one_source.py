"""ANALYST-ONE-SOURCE-1 (Batch 19A A8) — one analyst headcount in the council's text.

VKTX read "20 analysts" in the report table and the Sentiment specialist wrote "23 of 26" off
the Finnhub trend; NVCR read 7 and the specialists wrote 13 "from the recommendation trend
tool". With the report's analyst block in the council facts, the provider trend is neither
fetched nor logged and no other headcount reaches any agent."""
from __future__ import annotations

import re

from aristos_council.agents.nodes import _evidence_block, make_gather_node
from aristos_council.state import ResearchState
from tests.fake_sentiment import FakeSentiment
from tests.test_sentiment_fallback import (STRAT_DIR, _Adapter, _analyst_block, _fetchers,
                                           _frame)


def _analyst_counts(text: str) -> set[int]:
    """Every "N analysts" / analysts_total / latest_period.total headcount in ``text``."""
    found = {int(m) for m in re.findall(r"\b(\d+)\s+analysts\b", text)}
    found |= {int(m) for m in re.findall(r'"analysts_total":\s*(\d+)', text)}
    found |= {int(m) for m in re.findall(r'"total":\s*(\d+)', text)}
    return found


def test_a_report_council_sees_only_the_tables_analyst_block():
    frame = _frame()
    gather = make_gather_node(_Adapter(), frame, FakeSentiment(),
                              news_fallback_fetchers=_fetchers())
    state = gather(ResearchState(ticker="RIO.AX", strategy_id=frame.id,
                                 company_facts_block={"analyst": _analyst_block(True)}))
    names = {tc.tool_name for tc in state.tool_calls}
    assert "get_recommendation_trends" not in names
    assert "analyst_block" in names

    text = _evidence_block(state, frame, narrator=True)
    table_count = 8                      # the block's own "8 analysts: ..." line
    assert _analyst_counts(text) == {table_count}
    snap = next(tc for tc in state.tool_calls if tc.tool_name == "sentiment_snapshot")
    assert snap.output["analysts_total"] is None
    assert not any("no analyst recommendation trend" in n for n in snap.output["notes"])


def test_a_run_without_the_report_block_still_uses_the_provider_trend():
    """Unchanged for every other caller: no company facts -> the trend is fetched and logged
    (FakeSentiment's one row: 3 + 5 + 2 + 1 = 11)."""
    frame = _frame()
    gather = make_gather_node(_Adapter(), frame, FakeSentiment(),
                              news_fallback_fetchers=_fetchers())
    state = gather(ResearchState(ticker="AAPL.US", strategy_id=frame.id))
    assert any(tc.tool_name == "get_recommendation_trends" for tc in state.tool_calls)
    snap = next(tc for tc in state.tool_calls if tc.tool_name == "sentiment_snapshot")
    assert snap.output["analysts_total"] == 11
