"""SENT-FALLBACK-1 (Batch 12 item 1) — the sentiment specialist no longer says "not
assessed" for every non-US company just because Finnhub's plan is US-only.

Three layers, each tested on its own:
  1. ``fetch_eodhd_news`` / ``fetch_yfinance_news`` — pure parsers, fixture-based, no live
     call (one yfinance fixture is frozen from a real, free, no-key probe of RIO.AX —
     see news_fallback.py's own docstring).
  2. ``gather_news_with_fallback`` — the orchestration: Finnhub wins if it has anything;
     else EODHD news; else yfinance news; else empty with every attempt named.
  3. ``make_gather_node``'s ``gather()`` — end to end: the analyst block (item 1a) and the
     news fallback (item 1b) widen the Sentiment specialist's channel so it is "not
     assessed" (item 1c) only when every one of them is genuinely empty, and the tool
     calls record which source actually answered (item 1d).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from aristos_council.agents.nodes import (SPECIALIST_CHANNELS, _channel_absent_reason,
                                          channel_is_empty, make_gather_node)
from aristos_council.data.adapter import Fundamentals, MarketDataAdapter, PriceBar, PriceHistory
from aristos_council.data.news_fallback import (fetch_eodhd_news, fetch_yfinance_news,
                                                gather_news_with_fallback)
from aristos_council.state import ResearchState, SpecialistName
from tests.fake_sentiment import EmptySentiment, FailingSentiment, FakeSentiment

ROOT = Path(__file__).resolve().parents[1]
STRAT_DIR = ROOT / "strategies"
SENTIMENT_TOOLS = SPECIALIST_CHANNELS["sentiment"]


class _Adapter(MarketDataAdapter):
    name = "fake"

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, name=ticker, market_cap=2e10, sector="Materials",
                            ebit=[1000.0], pe_ratio=12.0)

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 1, 1), open=10, high=11, low=9,
                     close=10 + 0.01 * i, adj_close=10 + 0.01 * i, volume=10)
            for i in range(220)])

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _frame():
    from aristos_council.pipeline import _screenless_frame
    from aristos_council.strategy.rank_loader import load_rank_strategy
    return _screenless_frame(load_rank_strategy(STRAT_DIR / "magic_formula_raw_v1.yaml"))


# =========================================================================== #
# 1. the two pure fetchers
# =========================================================================== #
def test_eodhd_news_parses_a_well_formed_reply():
    doc = [{"date": "2026-09-30 08:00:00", "title": "Rio Tinto lifts iron ore guidance",
           "source": "Reuters"},
          {"date": "2026-09-28", "title": "Rio Tinto board meets", "source": ""}]
    items, reason = fetch_eodhd_news(
        "RIO.AX", start=date(2026, 9, 1), end=date(2026, 9, 30), api_key="k",
        opener=lambda url, timeout: _FakeResponse(doc))
    assert reason == "" and len(items) == 2
    assert items[0].headline == "Rio Tinto lifts iron ore guidance"
    assert items[0].source == "Reuters"
    assert items[1].source == "EODHD"                  # a missing source is named honestly


def test_eodhd_news_skips_malformed_rows_and_keeps_the_rest():
    doc = [{"date": "", "title": "no date, dropped"}, {"date": "2026-09-01", "title": ""},
          {"date": "2026-09-02", "title": "kept"}]
    items, reason = fetch_eodhd_news(
        "RIO.AX", start=date(2026, 9, 1), end=date(2026, 9, 30), api_key="k",
        opener=lambda url, timeout: _FakeResponse(doc))
    assert reason == "" and [i.headline for i in items] == ["kept"]


def test_eodhd_news_with_no_key_abstains_honestly():
    items, reason = fetch_eodhd_news("RIO.AX", start=date(2026, 9, 1), end=date(2026, 9, 30),
                                     api_key="")
    assert items == [] and "EODHD_API_KEY" in reason


def test_eodhd_news_an_unexpected_reply_shape_degrades_not_crashes():
    items, reason = fetch_eodhd_news(
        "RIO.AX", start=date(2026, 9, 1), end=date(2026, 9, 30), api_key="k",
        opener=lambda url, timeout: _FakeResponse({"not": "a list"}))
    assert items == [] and "unexpected reply shape" in reason


# A real, frozen probe of yfinance's Ticker.news shape (RIO.AX, free, no key — see
# news_fallback.py's own docstring for the live command this was taken from).
_YFINANCE_FIXTURE = [
    {"id": "abc", "content": {"title": "Kodal Minerals And 2 Other British Lithium Stocks",
                              "pubDate": "2026-09-30T14:11:46Z",
                              "provider": {"displayName": "Simply Wall St."}}},
    {"id": "def", "content": {"title": "European Indexes Open Higher",
                              "pubDate": "2026-09-30T09:00:00Z",
                              "provider": {"displayName": "The Wall Street Journal"}}},
]


def test_yfinance_news_parses_the_real_probed_shape():
    items, reason = fetch_yfinance_news("RIO.AU", fetcher=lambda sym: _YFINANCE_FIXTURE)
    assert reason == "" and len(items) == 2
    assert items[0].headline == "Kodal Minerals And 2 Other British Lithium Stocks"
    assert items[0].source == "Simply Wall St."
    assert items[0].published == date(2026, 9, 30)


def test_yfinance_news_a_raised_error_abstains_not_crashes():
    def boom(sym):
        raise RuntimeError("rate limited")
    items, reason = fetch_yfinance_news("RIO.AU", fetcher=boom)
    assert items == [] and "RuntimeError" in reason


def test_yfinance_news_empty_list_is_a_real_finding():
    items, reason = fetch_yfinance_news("RIO.AU", fetcher=lambda sym: [])
    assert items == [] and reason == ""


class _FakeResponse:
    def __init__(self, doc):
        import json
        self._body = json.dumps(doc).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


# =========================================================================== #
# 2. the orchestration
# =========================================================================== #
def _news(headline="h", published=date(2026, 9, 29)):
    from aristos_council.data.sentiment import NewsItem
    return NewsItem(published=published, headline=headline, source="x")


def test_a_finnhub_success_is_never_second_guessed():
    calls = []
    result = gather_news_with_fallback(
        "AAPL.US", start=date(2026, 9, 1), end=date(2026, 9, 30), finnhub_items=[_news()],
        finnhub_reason="",
        eodhd_fetcher=lambda *a, **k: calls.append("eodhd") or ([], ""),
        yfinance_fetcher=lambda *a, **k: calls.append("yfinance") or ([], ""))
    assert result.source == "Finnhub" and result.available
    assert calls == []                                  # neither fallback was even called


def test_eodhd_wins_when_finnhub_is_empty():
    result = gather_news_with_fallback(
        "RIO.AX", start=date(2026, 9, 1), end=date(2026, 9, 30), finnhub_items=[],
        finnhub_reason="Finnhub data is US-only on the current plan; not requested for RIO.AX",
        eodhd_fetcher=lambda *a, **k: ([_news("eodhd item")], ""),
        yfinance_fetcher=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    assert result.source == "EODHD news" and result.items[0].headline == "eodhd item"
    assert any("Finnhub" in t for t in result.tried)


def test_yfinance_wins_when_finnhub_and_eodhd_are_both_empty():
    result = gather_news_with_fallback(
        "RIO.AX", start=date(2026, 9, 1), end=date(2026, 9, 30), finnhub_items=[],
        finnhub_reason="skipped (non-US)",
        eodhd_fetcher=lambda *a, **k: ([], "EODHD_API_KEY is not set"),
        yfinance_fetcher=lambda *a, **k: ([_news("yf item")], ""))
    assert result.source == "yfinance news" and result.items[0].headline == "yf item"


def test_nothing_anywhere_names_every_source_tried():
    result = gather_news_with_fallback(
        "RIO.AX", start=date(2026, 9, 1), end=date(2026, 9, 30), finnhub_items=[],
        finnhub_reason="skipped (non-US)",
        eodhd_fetcher=lambda *a, **k: ([], "EODHD_API_KEY is not set"),
        yfinance_fetcher=lambda *a, **k: ([], "no items"))
    assert not result.available and result.source == ""
    assert len(result.tried) == 3
    assert any("Finnhub" in t for t in result.tried)
    assert any("EODHD" in t for t in result.tried)
    assert any("yfinance" in t for t in result.tried)


# =========================================================================== #
# 3. end to end through gather() — item 1a/1c/1d
# =========================================================================== #
def _fetchers(eodhd=None, yfin=None):
    return {"eodhd_fetcher": eodhd or (lambda *a, **k: ([], "EODHD_API_KEY is not set")),
           "yfinance_fetcher": yfin or (lambda *a, **k: ([], "no items"))}


def _analyst_block(available: bool, note: str = "EODHD via RIO.US"):
    if not available:
        return {"available": False, "lines": [], "source_note": "no analyst estimate is available"}
    return {"available": True, "lines": ["8 analysts: 3 strong buy, 1 buy, 3 hold, 1 sell",
                                         "Average price target $104.43"],
           "source_note": note}


def test_non_us_ticker_with_eodhd_analyst_data_and_no_finnhub_is_assessed():
    frame = _frame()
    gather = make_gather_node(_Adapter(), frame, None,            # no Finnhub adapter at all
                              sentiment_missing_key=True,
                              news_fallback_fetchers=_fetchers())
    state = gather(ResearchState(ticker="RIO.AX", strategy_id=frame.id,
                                 company_facts_block={"analyst": _analyst_block(True)}))
    assert channel_is_empty(state, SpecialistName.SENTIMENT) is False
    block = next(tc for tc in state.tool_calls if tc.tool_name == "analyst_block")
    assert block.ok and block.output["available"]


def test_nothing_anywhere_is_not_assessed_with_sources_named():
    """Finnhub WIRED but REFUSED (FailingSentiment — the real skip_non_us shape: the
    owner's key is set, the plan just will not serve this symbol) and both fallbacks also
    empty -> every source tried is named in the abstention. (An EMPTY-but-SUCCESSFUL
    Finnhub reply is a different, pre-existing case — "wired, reachable, found nothing" —
    and does NOT abstain; see fake_sentiment.EmptySentiment's own docstring.)"""
    frame = _frame()
    gather = make_gather_node(_Adapter(), frame, FailingSentiment(),
                              news_fallback_fetchers=_fetchers())
    state = gather(ResearchState(ticker="RIO.AX", strategy_id=frame.id,
                                 company_facts_block={"analyst": _analyst_block(False)}))
    assert channel_is_empty(state, SpecialistName.SENTIMENT) is True
    reason = _channel_absent_reason(state, SpecialistName.SENTIMENT)
    assert "Finnhub" in reason
    assert "EODHD" in reason
    assert "yfinance" in reason


def test_no_sentiment_adapter_at_all_tries_no_fallback_either():
    """Pre-Finnhub behaviour, preserved exactly (the comment this module's gather() keeps):
    no adapter configured at all -> no fallback attempt either, so a test env with simply
    no Finnhub key never reaches for yfinance by default (TEST-ISOLATION-1)."""
    frame = _frame()
    gather = make_gather_node(_Adapter(), frame, None, sentiment_missing_key=True,
                              news_fallback_fetchers=_fetchers())
    state = gather(ResearchState(ticker="RIO.AX", strategy_id=frame.id))
    assert not any(tc.tool_name == "get_company_news_fallback" for tc in state.tool_calls)


def test_a_successful_eodhd_news_fallback_names_its_own_source():
    frame = _frame()
    eodhd = lambda *a, **k: ([_news("RIO lifts guidance")], "")
    gather = make_gather_node(_Adapter(), frame, EmptySentiment(),
                              news_fallback_fetchers=_fetchers(eodhd=eodhd))
    state = gather(ResearchState(ticker="RIO.AX", strategy_id=frame.id,
                                 company_facts_block={"analyst": _analyst_block(False)}))
    assert channel_is_empty(state, SpecialistName.SENTIMENT) is False
    fb = next(tc for tc in state.tool_calls if tc.tool_name == "get_company_news_fallback")
    assert fb.ok and fb.output["source"] == "EODHD news"


def test_an_empty_finnhub_reply_still_tries_the_fallback_chain():
    """Finnhub WIRED and REACHABLE but genuinely quiet (EmptySentiment) must not be treated
    as "assessed, nothing found" when a fallback source actually has something."""
    frame = _frame()
    yf = lambda *a, **k: ([_news("yfinance item")], "")
    gather = make_gather_node(_Adapter(), frame, EmptySentiment(),
                              news_fallback_fetchers=_fetchers(yfin=yf))
    state = gather(ResearchState(ticker="RIO.AX", strategy_id=frame.id,
                                 company_facts_block={"analyst": _analyst_block(False)}))
    fb = next(tc for tc in state.tool_calls if tc.tool_name == "get_company_news_fallback")
    assert fb.ok and fb.output["source"] == "yfinance news"


def test_a_real_finnhub_success_skips_the_fallback_chain_entirely():
    frame = _frame()
    gather = make_gather_node(_Adapter(), frame, FakeSentiment(),
                              news_fallback_fetchers=_fetchers())
    state = gather(ResearchState(ticker="AAPL.US", strategy_id=frame.id))
    assert not any(tc.tool_name == "get_company_news_fallback" for tc in state.tool_calls)
