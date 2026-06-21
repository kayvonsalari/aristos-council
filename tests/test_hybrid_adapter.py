"""HybridAdapter — EODHD dividends + yfinance fundamentals/prices.

Composition tests with FAKES for both wrapped adapters (no network). The streak
DATA-SHAPE tag and the per-source provenance plumbing are the load-bearing
behaviours: the streak is computed from EODHD dividends, so it must use the EODHD
method, and the ledger must record which provider produced each figure.
"""

from __future__ import annotations

from datetime import date

import pytest

from aristos_council.data.adapter import (
    DividendEvent,
    Fundamentals,
    PriceBar,
    PriceHistory,
)
from aristos_council.data.hybrid_adapter import HybridAdapter
from aristos_council.data.provider import select_market_adapter

RANGE = dict(start=date(1980, 1, 1), end=date(2026, 6, 1))


# --------------------------------------------------------------------------- #
# Fakes for the two wrapped sources. Each raises if asked for data it must NOT
# serve, so a mis-wired delegation fails loudly.
# --------------------------------------------------------------------------- #
class _FakeEODHD:
    name = "eodhd"
    dividend_streak_method = "calendar_year_sum"

    def __init__(self):
        self.calls = []

    def get_dividend_history(self, ticker, *, start, end):
        self.calls.append(("dividends", ticker))
        return [DividendEvent(ex_date=date(2000 + i, 6, 1), amount=1.0 + 0.1 * i)
                for i in range(27)]

    def get_fundamentals(self, ticker):
        raise AssertionError("hybrid must NOT take fundamentals from EODHD")

    def get_price_history(self, ticker, *, start, end):
        raise AssertionError("hybrid must NOT take prices from EODHD")


class _FakeYF:
    name = "yfinance"

    def __init__(self):
        self.calls = []

    def get_fundamentals(self, ticker):
        self.calls.append(("fundamentals", ticker))
        return Fundamentals(ticker=ticker, name="Coca-Cola", market_cap=2.6e11,
                            currency="USD", dividend_per_share=1.94,
                            payout_ratio=0.5, eps=2.47)

    def get_price_history(self, ticker, *, start, end):
        self.calls.append(("prices", ticker))
        bars = [PriceBar(day=date(2026, 1, 1), open=60, high=61, low=59,
                         close=60 + 0.1 * i, adj_close=60 + 0.1 * i, volume=1000)
                for i in range(220)]
        return PriceHistory(ticker=ticker, bars=bars)

    def get_dividend_history(self, ticker, *, start, end):
        raise AssertionError("hybrid must NOT take dividends from yfinance")


def _hybrid():
    return HybridAdapter(eodhd=_FakeEODHD(), yfinance=_FakeYF())


# --------------------------------------------------------------------------- #
# Delegation + identity
# --------------------------------------------------------------------------- #
def test_delegates_each_method_to_the_correct_source():
    eodhd, yf = _FakeEODHD(), _FakeYF()
    h = HybridAdapter(eodhd=eodhd, yfinance=yf)

    divs = h.get_dividend_history("KO", **RANGE)
    fund = h.get_fundamentals("KO")
    prices = h.get_price_history("KO", **RANGE)

    assert len(divs) == 27 and isinstance(divs[0], DividendEvent)  # from EODHD fake
    assert fund.name == "Coca-Cola"                                # from yfinance fake
    assert len(prices.bars) == 220                                 # from yfinance fake
    # the right source saw each call, the wrong one was never touched
    assert eodhd.calls == [("dividends", "KO")]
    assert yf.calls == [("fundamentals", "KO"), ("prices", "KO")]


def test_name_is_hybrid():
    assert _hybrid().name == "hybrid"


def test_streak_method_is_eodhd_shape():
    # The streak comes from EODHD dividends -> must use the calendar-year method,
    # NOT the yfinance per-payment default (which would false-break a cadence change).
    assert HybridAdapter.dividend_streak_method == "calendar_year_sum"
    assert _hybrid().dividend_streak_method == "calendar_year_sum"


def test_provider_for_is_per_source_not_flattened():
    h = _hybrid()
    assert h.provider_for("dividends") == "eodhd"
    assert h.provider_for("fundamentals") == "yfinance"
    assert h.provider_for("prices") == "yfinance"


def test_select_market_adapter_hybrid(monkeypatch):
    pytest.importorskip("yfinance")
    monkeypatch.setenv("ARISTOS_MARKET_PROVIDER", "hybrid")
    a = select_market_adapter()
    assert isinstance(a, HybridAdapter) and a.name == "hybrid"


# --------------------------------------------------------------------------- #
# End-to-end through the graph: the ledger records per-source provenance, and the
# streak used the EODHD method (the whole point of the hybrid).
# --------------------------------------------------------------------------- #
class _Specialists:
    def __init__(self, outs):
        self._outs = list(outs)

    def invoke(self, system, user):
        return self._outs.pop(0)


class _Static:
    def __init__(self, out):
        self._out = out

    def invoke(self, system, user):
        return self._out


def test_hybrid_run_records_eodhd_dividends_and_yfinance_fundamentals():
    from pathlib import Path

    from aristos_council.agents.schemas import (
        CriticOutput,
        DecisionOutput,
        SpecialistOutput,
    )
    from aristos_council.graph import build_council
    from aristos_council.state import Recommendation, ResearchState, Stance
    from aristos_council.strategy.loader import load_strategy

    strategy = load_strategy(
        Path(__file__).resolve().parents[1]
        / "strategies" / "dividend_aristocrats_v1.yaml")
    runners = {
        "specialist": _Specialists([
            SpecialistOutput(stance=Stance.BULLISH, confidence=0.8, thesis="up")
            for _ in range(4)]),
        "critic": _Static(CriticOutput(counter_thesis="c")),
        "decision": _Static(DecisionOutput(
            recommendation=Recommendation.BUY, confidence=0.9, rationale="r")),
    }
    app = build_council(_hybrid(), strategy, runners)
    state = ResearchState.model_validate(
        app.invoke(ResearchState(ticker="KO", strategy_id=strategy.id)))

    by_tool = {tc.tool_name: tc for tc in state.tool_calls}
    # per-source provenance is visible in the ledger, NOT flattened to "hybrid"
    assert by_tool["get_dividend_history"].inputs["provider"] == "eodhd"
    assert by_tool["get_fundamentals"].inputs["provider"] == "yfinance"
    assert by_tool["get_price_history"].inputs["provider"] == "yfinance"

    # and the streak was computed by the EODHD method (passes the 20yr floor)
    screen = by_tool["run_strategy_screen"].output
    streak = next(c for c in screen["criteria"]
                  if c["name"] == "min_dividend_growth_streak")
    assert streak["passed"] is True
    assert "calendar_year_sum" in streak["note"]
