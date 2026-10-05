"""COMPANY-FACTS-TABLE-1 (Batch 15 item 1) — price, cash and forward-valuation facts the
council's own evidence pack already fetched, surfaced on Company Check's own page and both
exports. Pure unit tests on ``abs_readings.price_and_cash``: no adapter, no network.

Reference case (EL.PA, 2026-10-03, verified against the real company): last close 142.80,
50-day average 156.25, 200-day average 192.69, 54.4% below the 52-week high, 6-month return
-27.2%, 12-month return -47.1%, volatility 30.3%, FCF FY2022..FY2025 rising 3.2bn..3.8bn,
trailing EPS 5.36, trailing P/E 26.64, forward P/E (this year) 142.80 / 7.25 ~= 19.7x.
"""
from __future__ import annotations

from datetime import date

import pytest

from aristos_council.abs_readings import AnalystTrend, ForecastRow, PriceAndCash, price_and_cash
from aristos_council.data.adapter import Fundamentals
from aristos_council.data.news_fallback import NewsFetchResult
from aristos_council.data.sentiment import NewsItem
from aristos_council.tools.technical import TechnicalSnapshot

TODAY = date(2026, 10, 3)


def _technical(**over) -> TechnicalSnapshot:
    base = dict(last_close=142.80, sma_50=156.25, sma_200=192.69, pct_off_52w_high=-0.544,
               annualized_volatility=0.303, return_6m=-0.272, return_12m=-0.471)
    base.update(over)
    return TechnicalSnapshot(**base)


def _fundamentals(**over) -> Fundamentals:
    base = dict(ticker="EL.PA", name="Estee Lauder Companies Inc", currency="EUR", eps=5.36, pe_ratio=26.64,
               free_cash_flow_annual=[3.8e9, 3.4e9, 3.3e9, 3.2e9],
               aligned_annual={"free_cash_flow": [3.8e9, 3.4e9, 3.3e9, 3.2e9]},
               aligned_period_ends={"free_cash_flow": ["2025-12-31", "2024-12-31",
                                                       "2023-12-31", "2022-12-31"]})
    base.update(over)
    return Fundamentals(**base)


def _trend(this_year=7.25, next_year=7.94) -> AnalystTrend:
    rows = []
    if this_year is not None:
        rows.append(ForecastRow(label="This year", now=this_year, currency="EUR"))
    if next_year is not None:
        rows.append(ForecastRow(label="Next year", now=next_year, currency="EUR"))
    return AnalystTrend(mark="flat", rows=tuple(rows), currency="EUR")


def _news(n=2) -> NewsFetchResult:
    items = tuple(NewsItem(published=date(2026, 9, 28 - i), headline=f"Estee Lauder headline {i}",
                           source="EODHD") for i in range(n))
    return NewsFetchResult(items=items, source="EODHD news", tried=())


# --------------------------------------------------------------------------- #
# the happy path — EL.PA's own real numbers
# --------------------------------------------------------------------------- #
def test_every_reading_matches_the_el_pa_reference_case():
    pac = price_and_cash(_technical(), _fundamentals(), trend=_trend(), news=_news())
    lines = pac.lines()
    assert "last close €142.80" in lines
    assert "50-day average price €156.25" in lines
    assert "200-day average price €192.69" in lines
    assert "54.4% below its 52-week high" in lines
    assert "6-month return -27.2%" in lines
    assert "12-month return -47.1%" in lines
    assert "annualised volatility 30.3%" in lines
    assert any("FY2022" in ln and "FY2025" in ln and "oldest first" in ln for ln in lines)
    assert "trailing EPS €5.36" in lines
    assert "trailing P/E 26.6" in lines


def test_forward_pe_is_close_over_consensus_eps():
    pac = price_and_cash(_technical(), _fundamentals(), trend=_trend(), news=_news())
    assert pac.forward_pe_this_year.value == pytest.approx(142.80 / 7.25)
    assert pac.forward_pe_next_year.value == pytest.approx(142.80 / 7.94)
    assert "19.7x" in pac.forward_pe_this_year.label
    assert "18.0x" in pac.forward_pe_next_year.label


def test_news_is_newest_first_and_capped(tmp_path=None):
    news = NewsFetchResult(
        items=tuple(NewsItem(published=date(2026, 9, 20 + i), headline=f"Estee Lauder H{i}", source="EODHD")
                   for i in range(8)),
        source="EODHD news", tried=())
    pac = price_and_cash(_technical(), _fundamentals(), trend=_trend(), news=news, max_news=5)
    assert len(pac.news) == 5
    assert [item.published for item in pac.news] == sorted(
        (item.published for item in news.items), reverse=True)[:5]


# --------------------------------------------------------------------------- #
# honest abstention — every leg fails independently, never a crash
# --------------------------------------------------------------------------- #
def test_no_technical_snapshot_abstains_every_price_reading():
    pac = price_and_cash(None, _fundamentals(), trend=_trend(), news=_news())
    assert pac.last_close.note and not pac.last_close.available
    assert pac.sma_50.note and pac.sma_200.note and pac.pct_off_high.note
    assert pac.return_6m.note and pac.return_12m.note and pac.volatility.note
    # Forward P/E needs the close, which came from technical — abstains too.
    assert "no current price" in pac.forward_pe_this_year.note


def test_short_history_abstains_sma_and_52_week_but_keeps_the_close():
    short = _technical(sma_50=None, sma_200=None, pct_off_52w_high=None,
                       return_6m=None, return_12m=None)
    pac = price_and_cash(short, _fundamentals(), trend=_trend(), news=_news())
    assert pac.last_close.available and pac.last_close.value == 142.80
    assert "fewer than 50 closes" in pac.sma_50.note
    assert "fewer than 200 closes" in pac.sma_200.note
    assert "insufficient price history" in pac.pct_off_high.note


def test_no_fundamentals_abstains_fcf_and_trailing_figures():
    pac = price_and_cash(_technical(), None, trend=_trend(), news=_news())
    assert pac.fcf_series.note == "no fundamentals"
    assert "not reported" in pac.trailing_eps.note
    assert "not reported" in pac.trailing_pe.note


def test_no_analyst_trend_abstains_forward_pe_only():
    pac = price_and_cash(_technical(), _fundamentals(), trend=None, news=_news())
    assert pac.last_close.available        # unaffected
    assert "no analyst consensus EPS" in pac.forward_pe_this_year.note
    assert "no analyst consensus EPS" in pac.forward_pe_next_year.note


def test_a_non_positive_consensus_eps_abstains_rather_than_a_negative_multiple():
    pac = price_and_cash(_technical(), _fundamentals(), trend=_trend(this_year=-1.0, next_year=None),
                         news=_news())
    assert "not positive" in pac.forward_pe_this_year.note
    assert "no analyst consensus EPS for next year" in pac.forward_pe_next_year.note


def test_no_news_result_at_all_states_why():
    pac = price_and_cash(_technical(), _fundamentals(), trend=_trend(), news=None)
    assert pac.news == () and "not requested" in pac.news_note


def test_an_empty_news_result_names_every_source_tried():
    empty = NewsFetchResult(items=(), source="",
                            tried=("EODHD news: EODHD_API_KEY is not set", "yfinance news: no items"))
    pac = price_and_cash(_technical(), _fundamentals(), trend=_trend(), news=empty)
    assert pac.news == ()
    assert "EODHD_API_KEY is not set" in pac.news_note


def test_lines_never_shows_an_unavailable_reading_as_a_blank():
    pac = price_and_cash(None, None, trend=None, news=None)
    lines = pac.lines()
    assert lines           # abstentions still produce text, never an empty table
    assert all(ln.strip() for ln in lines)
    assert "not stated" in " ".join(lines)


# --------------------------------------------------------------------------- #
# NEWS-SUBJECT-1 (Batch 19A A11) — keep a headline only if the company is its subject
# --------------------------------------------------------------------------- #
_JPM_FIVE = ("Synopsys Initiates $1 Billion Accelerated Share Repurchase Agreement",
             "J.P. Morgan adds Givaudan to Positive Catalyst Watch on strong Q3 outlook",
             "Versana's Digital Loan Voting Platform is Live",
             "Equities Won't Be Dragged by Spike In Bond Yields",
             "Should Vanguard Morningstar Value ETF (VTV) Be on Your Investing Radar?")


def _news_of(*headlines) -> NewsFetchResult:
    items = tuple(NewsItem(published=date(2026, 9, 28 - i), headline=h, source="EODHD")
                  for i, h in enumerate(headlines))
    return NewsFetchResult(items=items, source="EODHD news", tried=())


def _jpm():
    return _fundamentals(ticker="JPM", name="JPMorgan Chase & Co.", currency="USD")


def test_jpms_five_headlines_about_other_companies_are_all_dropped():
    pac = price_and_cash(_technical(), _jpm(), trend=_trend(), news=_news_of(*_JPM_FIVE))
    assert pac.news == ()
    assert pac.news_note == "no headlines about this company in the window"
    assert "Recent news: no headlines about this company in the window" in pac.lines()
    assert not any("Givaudan" in ln for ln in pac.lines())


def test_headlines_about_the_company_survive_and_the_analyst_note_does_not():
    news = _news_of("JPMorgan Chase profit beats estimates", _JPM_FIVE[1],
                    "Layoffs at JPMorgan hit trading desk", "Shares of JPM rise")
    pac = price_and_cash(_technical(), _jpm(), trend=_trend(), news=news)
    shown = [item.headline for item in pac.news]
    assert shown == ["JPMorgan Chase profit beats estimates",
                     "Layoffs at JPMorgan hit trading desk", "Shares of JPM rise"]


def test_one_surviving_headline_is_not_a_news_block():
    news = _news_of("JPMorgan Chase profit beats estimates", *_JPM_FIVE)
    pac = price_and_cash(_technical(), _jpm(), trend=_trend(), news=news)
    assert pac.news == () and "no headlines about this company" in pac.news_note


def test_a_company_that_raises_prices_is_still_about_the_company():
    """Weak verbs ("raises", "adds") are ordinary company actions unless a rating-note word
    is beside them."""
    from aristos_council.news_subject import is_about_company
    assert is_about_company("Tesla raises prices in China", name="Tesla, Inc.", ticker="TSLA")
    assert not is_about_company("Acme Capital raises price target on Tesla",
                                name="Acme Capital Group", ticker="ACME")
