"""Observability — failure-type tagging + degraded-run banner + health summaries.

The whole point is the HONEST vs TOOL-FAILURE distinction: a FETCH_ERROR /
EMPTY_RESPONSE / MISSING_KEY marks the run degraded and fires the loud banner, while
an honest DATA_ABSENT / CURRENCY_MISMATCH abstention does NOT (no crying wolf). No
network, no LLM — fake adapters inject the failures.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from aristos_council.agents.nodes import make_gather_node
from aristos_council.data.adapter import (
    DataUnavailable,
    DividendEvent,
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.persistence.reports import RunReport, report_from_state
from aristos_council.presentation import (
    batch_health_summary,
    degraded_banner,
    run_health_line,
)
from aristos_council.state import FailureKind, ResearchState, RunIssue
from aristos_council.strategy.loader import load_strategy

STRATEGY = load_strategy(
    Path(__file__).resolve().parents[1]
    / "strategies" / "dividend_aristocrats_v1.yaml"
)


def _state(ticker: str = "TEST") -> ResearchState:
    return ResearchState(ticker=ticker, strategy_id="dividend_aristocrats_v1")


# --------------------------------------------------------------------------- #
# Fake adapters — one healthy, the rest inject one specific failure each
# --------------------------------------------------------------------------- #
class _OKAdapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name="Fake Corp", market_cap=5e10,
                            dividend_yield=0.03, payout_ratio=0.5)

    def get_dividend_history(self, ticker, *, start, end):
        return [DividendEvent(ex_date=date(1995 + i, 6, 1), amount=1.0 + 0.05 * i)
                for i in range(30)]

    def get_price_history(self, ticker, *, start, end):
        bars = [PriceBar(day=date(2026, 1, 1), open=100, high=101, low=99,
                         close=100 + 0.1 * i, adj_close=100 + 0.1 * i, volume=1000)
                for i in range(220)]
        return PriceHistory(ticker=ticker, bars=bars)


class _FundamentalsRaiseAdapter(_OKAdapter):
    def get_fundamentals(self, ticker):
        raise DataUnavailable("yfinance fundamentals timeout")


class _EmptyDividendAdapter(_OKAdapter):
    def get_dividend_history(self, ticker, *, start, end):
        return []   # successful call, but no rows -> EMPTY_RESPONSE


# --------------------------------------------------------------------------- #
# Part 1 — failure-type tagging maps to honest vs degrading correctly
# --------------------------------------------------------------------------- #
def test_failure_kind_degrading_classification():
    degrading = {FailureKind.FETCH_ERROR, FailureKind.EMPTY_RESPONSE,
                 FailureKind.MISSING_KEY}
    honest = {FailureKind.DATA_ABSENT, FailureKind.CURRENCY_MISMATCH}
    for k in degrading:
        assert RunIssue(source="s", reason=k).is_degrading is True
    for k in honest:
        assert RunIssue(source="s", reason=k).is_degrading is False


def test_data_absent_is_honest_not_degraded_no_banner():
    state = _state()
    state.run_issues.append(RunIssue(
        source="min_revenue_cagr", reason=FailureKind.DATA_ABSENT,
        detail="insufficient history"))
    assert state.degraded is False
    assert degraded_banner(state.run_issues) is None


def test_currency_mismatch_is_honest_not_degraded():
    state = _state()
    state.run_issues.append(RunIssue(
        source="min_market_cap", reason=FailureKind.CURRENCY_MISMATCH,
        detail="KRW cap vs USD threshold"))
    assert state.degraded is False
    assert degraded_banner(state.run_issues) is None


# --------------------------------------------------------------------------- #
# Part 2 — continue-but-mark: gather catches per source, tags, and continues
# --------------------------------------------------------------------------- #
def test_fetch_error_sets_degraded_and_renders_banner_at_top():
    state = _state()
    gather = make_gather_node(_FundamentalsRaiseAdapter(), STRATEGY)
    gather(state)   # must NOT raise — the run continues
    assert state.degraded is True
    issues = [i for i in state.run_issues if i.source == "fundamentals"]
    assert issues and issues[0].reason == FailureKind.FETCH_ERROR
    banner = degraded_banner(state.run_issues)
    assert banner is not None
    assert banner.startswith("⚠️ DEGRADED RUN")          # the very first line
    assert "fundamentals" in banner and "FETCH_ERROR" in banner


def test_empty_response_sets_degraded():
    state = _state()
    gather = make_gather_node(_EmptyDividendAdapter(), STRATEGY)
    gather(state)
    issues = [i for i in state.run_issues if i.reason == FailureKind.EMPTY_RESPONSE]
    assert issues and issues[0].source == "dividends"
    assert state.degraded is True


def test_missing_finnhub_key_is_a_degrading_missing_key_issue():
    state = _state()
    gather = make_gather_node(_OKAdapter(), STRATEGY, sentiment_adapter=None,
                              sentiment_missing_key=True)
    gather(state)
    assert state.degraded is True
    issues = [i for i in state.run_issues if i.reason == FailureKind.MISSING_KEY]
    assert issues and issues[0].source == "sentiment"
    assert "FINNHUB_API_KEY" in issues[0].detail
    banner = degraded_banner(state.run_issues)
    assert "sentiment" in banner and "MISSING_KEY" in banner


def test_clean_run_is_not_degraded_and_renders_no_banner():
    state = _state()
    gather = make_gather_node(_OKAdapter(), STRATEGY)   # no missing key
    gather(state)
    assert state.degraded is False
    assert degraded_banner(state.run_issues) is None


# --------------------------------------------------------------------------- #
# Part 3 — run-health line (single name) + batch summary helper
# --------------------------------------------------------------------------- #
def test_run_health_line_reports_counts_and_source_status():
    state = _state()
    gather = make_gather_node(_OKAdapter(), STRATEGY, sentiment_missing_key=True)
    gather(state)
    line = run_health_line(state)
    assert "Run health: DEGRADED" in line
    assert "criteria evaluated" in line and "abstained" in line
    assert "sentiment MISSING_KEY" in line


def test_run_health_line_from_report_uses_stored_screen():
    rep = RunReport(
        ticker="X", run_at=datetime(2026, 6, 28, tzinfo=timezone.utc),
        strategy_id="s",
        screen={"criteria": [{"name": "a", "passed": True},
                             {"name": "b", "passed": None}]},
        run_issues=[RunIssue(source="sentiment", reason=FailureKind.MISSING_KEY)],
        degraded=True)
    line = run_health_line(rep)
    assert "evaluated 1" in line and "abstained 1" in line and "(b)" in line
    assert "DEGRADED" in line


def test_batch_health_summary_counts():
    rows = [
        {"degraded": False, "verdict": "buy"},
        {"degraded": False, "verdict": "hold"},
        {"degraded": True, "verdict": "hold", "reasons": [FailureKind.MISSING_KEY]},
        {"degraded": True, "verdict": "hold", "reasons": ["fetch_error"]},
        {"degraded": False, "verdict": "insufficient_evidence"},
    ]
    s = batch_health_summary(rows)
    assert s.startswith("BATCH HEALTH")
    assert "5 names" in s and "3 clean" in s and "2 degraded" in s
    assert "1 sentiment missing" in s
    assert "1 fetch errors" in s
    assert "1 INSUFFICIENT_EVIDENCE" in s


def test_batch_health_summary_all_clean():
    rows = [{"degraded": False, "verdict": "buy"} for _ in range(3)]
    s = batch_health_summary(rows)
    assert "3 names: 3 clean, 0 degraded" in s


# --------------------------------------------------------------------------- #
# Report plumbing: run_issues + degraded persist and round-trip
# --------------------------------------------------------------------------- #
def test_report_from_state_carries_run_issues_and_degraded():
    state = _state()
    state.run_issues.append(RunIssue(
        source="sentiment", reason=FailureKind.MISSING_KEY, detail="no key"))
    rep = report_from_state(state)
    assert rep.degraded is True
    assert rep.run_issues[0].reason == FailureKind.MISSING_KEY
    back = RunReport.model_validate(json.loads(rep.model_dump_json()))
    assert back.degraded is True
    assert back.run_issues[0].reason == FailureKind.MISSING_KEY


def test_older_report_without_fields_round_trips_clean():
    # A report saved before this field existed parses with degraded False / no issues.
    rep = RunReport(ticker="X",
                    run_at=datetime(2026, 6, 28, tzinfo=timezone.utc),
                    strategy_id="s")
    assert rep.degraded is False and rep.run_issues == []
