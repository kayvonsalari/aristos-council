"""ANALYST-RATINGS-1 - "What analysts say": the buy/hold/sell counts and the average price target.

The block is probed LIVE (2026-09-26, 10 units each): a US line answers
``{"Rating": 4.04, "TargetPrice": 328.22, "StrongBuy": 23, "Buy": 7, "Hold": 16, "Sell": 1,
"StrongSell": 1}``; every London / Hong Kong / Taiwan / Swiss line probed answers the string "NA".
It carries no currency, so the request also asks for ``General::CurrencyCode``. Nothing here reaches
the network: the opener is injected and the price is handed in.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from aristos_council.abs_readings import (RatingsView, analyst_trend, plain_company_name,
                                          ratings_view)
from aristos_council.data.analyst_trend import (CHARGE_FUNDAMENTALS, AnalystRatings, TrendData,
                                                TrendPeriod, fetch_analyst_trend, parse_ratings,
                                                parse_response)

TODAY = date(2026, 9, 26)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "analyst_trend"
AAPL_BLOCK = {"Rating": 4.0417, "TargetPrice": 328.2221, "StrongBuy": 23, "Buy": 7, "Hold": 16,
              "Sell": 1, "StrongSell": 1}
AZN_US_BLOCK = {"Rating": 4.6, "TargetPrice": 212.1727, "StrongBuy": 7, "Buy": 2, "Hold": 1,
                "Sell": 0, "StrongSell": 0}


def _trend_block():
    return json.loads((FIXTURES / "aapl_us.json").read_text(encoding="utf-8"))


def _ratings(**kw):
    base = dict(strong_buy=9, buy=8, hold=6, sell=1, strong_sell=0, target_price=135.20,
                rating=4.0, currency="GBP", symbol="AZN.LSE")
    base.update(kw)
    return AnalystRatings(**base)


# =========================================================================== #
# parsing the probed block
# =========================================================================== #
def test_the_probed_us_block_becomes_counts_a_target_and_the_listings_currency():
    ratings, why = parse_ratings(AAPL_BLOCK, "USD", symbol="AAPL.US")
    assert why == "" and ratings.total == 48
    assert (ratings.strong_buy, ratings.buy, ratings.hold, ratings.sell, ratings.strong_sell) == (
        23, 7, 16, 1, 1)
    assert ratings.target_price == pytest.approx(328.2221) and ratings.currency == "USD"


@pytest.mark.parametrize("block", ["NA", None, {}, [], "n/a"])
def test_a_listing_with_no_ratings_block_abstains_with_a_reason(block):
    ratings, why = parse_ratings(block, "GBX", symbol="AZN.LSE")
    assert ratings is None and "no analyst ratings for AZN.LSE" in why


def test_fewer_than_three_analysts_is_not_a_consensus():
    ratings, why = parse_ratings({"StrongBuy": 1, "Buy": 1, "Hold": 0, "Sell": 0, "StrongSell": 0,
                                  "TargetPrice": 10}, "USD", symbol="X.US")
    assert ratings is None and "only 2 analyst(s)" in why
    ok, _ = parse_ratings({"StrongBuy": 1, "Buy": 1, "Hold": 1, "Sell": 0, "StrongSell": 0}, "USD")
    assert ok is not None and ok.total == 3


def test_a_block_with_no_counts_is_not_a_block_of_zeros():
    ratings, why = parse_ratings({"TargetPrice": 10.0, "Rating": 3.0}, "USD")
    assert ratings is None and "no counts" in why


def test_the_combined_reply_carries_the_forecasts_and_the_ratings():
    doc = {"Earnings::Trend": _trend_block(), "AnalystRatings": AAPL_BLOCK,
           "General::CurrencyCode": "USD"}
    data = parse_response(doc, today=date(2026, 9, 25), symbol="AAPL.US")
    assert data.available and data.ratings.total == 48 and data.ratings_note == ""


# =========================================================================== #
# ONE request: no extra call, no extra charge
# =========================================================================== #
class _Opener:
    def __init__(self, replies):
        self.replies, self.urls = replies, []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        symbol = url.split("/fundamentals/")[1].split("?")[0]
        reply = self.replies[symbol]
        outer = json.dumps(reply).encode("utf-8")

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return outer
        return _Resp()


def _reply(block, currency):
    return {"Earnings::Trend": _trend_block(), "AnalystRatings": block, "General::CurrencyCode": currency}


def test_the_ratings_ride_on_the_same_request_as_the_forecasts_one_call_one_charge(tmp_path):
    opener = _Opener({"AAPL.US": _reply(AAPL_BLOCK, "USD")})
    data = fetch_analyst_trend("AAPL", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener)
    assert len(opener.urls) == 1                                  # ONE request...
    assert data.units_charged == CHARGE_FUNDAMENTALS == 10        # ...charged once
    assert "AnalystRatings" in opener.urls[0] and "Earnings" in opener.urls[0]
    assert "General%3A%3ACurrencyCode" in opener.urls[0]
    assert data.available and data.ratings.total == 48 and data.ratings.symbol == "AAPL.US"


def test_a_second_call_the_same_day_costs_nothing_and_asks_nothing(tmp_path):
    opener = _Opener({"AAPL.US": _reply(AAPL_BLOCK, "USD")})
    fetch_analyst_trend("AAPL", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener)
    again = fetch_analyst_trend("AAPL", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener)
    assert len(opener.urls) == 1 and again.units_charged == 0 and again.ratings.total == 48


def test_a_reply_cached_before_the_ratings_existed_is_refetched_once(tmp_path):
    (tmp_path / "eodhd_AAPL.US_2026_09_26_trend.json").write_text(
        json.dumps(_trend_block()), encoding="utf-8")            # the old, trend-only shape
    opener = _Opener({"AAPL.US": _reply(AAPL_BLOCK, "USD")})
    data = fetch_analyst_trend("AAPL", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener)
    assert len(opener.urls) == 1 and data.ratings is not None
    fetch_analyst_trend("AAPL", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener)
    assert len(opener.urls) == 1                                  # and now it is cached


def test_an_own_listing_with_ratings_never_uses_the_fallback(tmp_path):
    opener = _Opener({"AAPL.US": _reply(AAPL_BLOCK, "USD"), "AAPL2.US": _reply(AZN_US_BLOCK, "USD")})
    fetch_analyst_trend("AAPL", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener,
                        ratings_fallback_symbol="AAPL2.US")
    assert len(opener.urls) == 1


def test_a_listing_with_no_ratings_takes_them_from_the_companys_us_line_and_says_so(tmp_path):
    opener = _Opener({"AZN.LSE": _reply("NA", "GBX"), "AZN.US": {"AnalystRatings": AZN_US_BLOCK,
                                                                "General::CurrencyCode": "USD"}})
    data = fetch_analyst_trend("AZN.L", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener,
                               ratings_fallback_symbol="AZN.US")
    assert [u.split("/fundamentals/")[1].split("?")[0] for u in opener.urls] == ["AZN.LSE", "AZN.US"]
    assert data.units_charged == 2 * CHARGE_FUNDAMENTALS          # the second request is stated
    assert data.ratings.symbol == "AZN.US" and data.ratings.currency == "USD"
    again = fetch_analyst_trend("AZN.L", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener,
                                ratings_fallback_symbol="AZN.US")
    assert len(opener.urls) == 2 and again.units_charged == 0     # both cached for the day


def test_without_a_fallback_a_non_us_listing_abstains_with_the_reason(tmp_path):
    opener = _Opener({"AZN.LSE": _reply("NA", "GBX")})
    data = fetch_analyst_trend("AZN.L", api_key="k", cache_dir=tmp_path, today=TODAY, opener=opener)
    assert data.ratings is None and "no analyst ratings for AZN.LSE" in data.ratings_note
    assert data.available                                        # the forecasts still stand


# =========================================================================== #
# the words
# =========================================================================== #
def test_the_counts_line_and_the_one_row_table():
    view = ratings_view(_ratings(), "", price=120.70, price_currency="GBP")
    assert view.summary_line() == "24 analysts: 9 strong buy, 8 buy, 6 hold, 1 sell, 0 strong sell"
    head, row = view.table()
    assert head == ["Strong buy", "Buy", "Hold", "Sell", "Strong sell", "Total"]
    assert row == ["9", "8", "6", "1", "0", "24"]


def test_the_target_reads_against_todays_price_in_plain_words():
    view = ratings_view(_ratings(currency="GBP"), "", price=120.71, price_currency="GBP")
    assert view.target_sentence == "Average price target £135.20, 12% above today's price"
    below = ratings_view(_ratings(target_price=100.0, currency="USD"), "", price=125.0,
                         price_currency="USD")
    assert below.target_sentence == "Average price target $100.00, 20% below today's price"
    same = ratings_view(_ratings(target_price=100.2, currency="USD"), "", price=100.0,
                        price_currency="USD")
    assert same.target_sentence == "Average price target $100.20, about the same as today's price"


def test_london_target_and_price_are_compared_in_ONE_unit_pounds_against_pence():
    """EODHD may give the target in GBP while the price feed quotes GBp: compare like with like."""
    pounds_target = ratings_view(_ratings(currency="GBP", target_price=135.20), "",
                                 price=12070.0, price_currency="GBp")
    assert pounds_target.target_sentence == "Average price target £135.20, 12% above today's price"
    pence_target = ratings_view(_ratings(currency="GBX", target_price=13520.0), "",
                                price=12070.0, price_currency="GBp")
    assert pence_target.target_sentence == "Average price target £135.20, 12% above today's price"
    assert pounds_target.currency == pence_target.currency == "GBP"


def test_a_target_in_another_currency_than_the_price_is_shown_but_not_compared():
    view = ratings_view(_ratings(currency="USD", target_price=212.17), "", price=12070.0,
                        price_currency="GBp")
    assert view.available and view.price is None
    assert "the target is in USD but the price is in GBp" in view.target_sentence


def test_a_target_whose_currency_cannot_be_established_is_never_compared():
    view = ratings_view(_ratings(currency=""), "", price=100.0, price_currency="USD")
    assert view.target is None
    assert "currency of the average price target is not stated by the source" in view.target_sentence
    assert view.summary_line().startswith("24 analysts")          # the counts still stand


def test_a_hundredfold_gap_means_a_unit_mismatch_and_is_not_compared():
    """Same currency code on both, but 135 against 12070: pounds against pence, unlabelled."""
    view = ratings_view(_ratings(currency="GBp", target_price=135.20), "", price=12070.0,
                        price_currency="GBp")
    assert view.price is None and "disagree by a factor no target explains" in view.target_sentence


def test_no_price_shows_the_target_alone_with_the_reason():
    view = ratings_view(_ratings(currency="USD"), "", price=None, price_currency="")
    assert view.target_sentence.startswith("Average price target $135.20")
    assert "today's price is not available" in view.target_sentence


def test_no_ratings_says_why_and_shows_no_counts():
    view = ratings_view(None, "EODHD has no analyst ratings for SHEL.LSE")
    assert not view.available
    assert view.lines() == ["Analyst ratings are not shown: EODHD has no analyst ratings for "
                            "SHEL.LSE"]


def test_the_us_line_is_labelled_when_the_ratings_are_not_the_companys_own():
    view = ratings_view(_ratings(symbol="AZN.US", currency="USD"), "", price=167.0,
                        price_currency="USD", own_listing=False)
    assert "these are the ratings for the US listing AZN.US" in " ".join(view.lines())


def test_plain_company_names():
    assert plain_company_name("AstraZeneca PLC") == "AstraZeneca"
    assert plain_company_name("Micron Technology, Inc.") == "Micron Technology"
    assert plain_company_name("Nestlé S.A.") == "Nestlé"
    assert plain_company_name("Berkshire Hathaway") == "Berkshire Hathaway"
    assert plain_company_name("") == ""


# =========================================================================== #
# it is still a mark; the summary may mention it; the Sources line
# =========================================================================== #
def test_the_section_is_a_mark_it_changes_no_verdict_whatever_the_ratings_say():
    from tests.test_analyst_trend import _MU, STRAT_DIR, UNIV_DIR, _OneName, run_company_check
    def check(data):
        return run_company_check("MU", "magic_formula_momentum_v1", "", adapter=_OneName(_MU),
                                 strategies_dir=STRAT_DIR, universes_dir=UNIV_DIR,
                                 runs_dir=Path("runs"), today=date(2026, 6, 30),
                                 with_analyst_trend=True, analyst_fetcher=lambda t, *, today: data)
    import dataclasses
    bullish = TrendData(current=TrendPeriod("2026-12-31", 12.0, 10.0, 20), as_of="2026-06-30",
                        ratings=_ratings(strong_buy=30, buy=1, hold=0, sell=0, strong_sell=0,
                                         currency="USD", symbol="MU.US"))
    bearish = TrendData(current=TrendPeriod("2026-12-31", 8.0, 10.0, 20), as_of="2026-06-30",
                        ratings=_ratings(strong_buy=0, buy=0, hold=1, sell=10, strong_sell=20,
                                         currency="USD", symbol="MU.US"))
    a, b = check(bullish), check(bearish)
    assert dataclasses.replace(a, analyst_trend=None) == dataclasses.replace(b, analyst_trend=None)


def test_the_sources_line_names_the_ratings_and_the_us_listing_when_it_was_used():
    from aristos_council.company_check import company_sources
    from tests.test_company_page_batch8 import _check
    result = _check()
    result.analyst_trend = analyst_trend(
        TrendData(current=TrendPeriod("2026-12-31", 10.25, 10.30, 24), as_of="2026-09-26",
                  source="EODHD", ratings=_ratings(symbol="AZN.US", currency="USD")),
        currency="USD", company="AstraZeneca PLC", price=167.0, price_currency="USD",
        own_listing=False)
    lines = dict((s.topic, s.text) for s in company_sources(result))
    assert lines["Analyst ratings and forecasts"] == (
        "EODHD, as of 2026-09-26 (ratings from the US listing AZN.US)")
    assert "Analyst forecasts" not in lines


def test_the_summary_facts_pack_carries_the_counts_and_the_target(tmp_path, monkeypatch):
    import aristos_council.data.analyst_trend as at
    from tests.test_company_report import RAW, _run
    from aristos_council.company_report import company_facts_pack

    def fake(ticker, *, today, ratings_fallback_symbol=None):
        return TrendData(current=TrendPeriod("2026-12-31", 10.25, 10.30, 24),
                         next_year=TrendPeriod("2027-12-31", 11.45, 11.64, 24), as_of="2026-09-26",
                         source="EODHD", ratings=_ratings(currency="USD", symbol="CO.US"))
    monkeypatch.setattr(at, "fetch_analyst_trend", fake)
    report = _run([RAW], tmp_path=tmp_path, save=False)
    say = company_facts_pack(report)["what_analysts_say"]
    assert say["ratings"]["summary"] == "24 analysts: 9 strong buy, 8 buy, 6 hold, 1 sell, 0 strong sell"
    assert (say["ratings"]["strong_buy"], say["ratings"]["total"]) == (9, 24)
    assert say["ratings"]["average_price_target"].startswith("Average price target $135.20")
    assert len(say["forecasts"]) >= 2 and "per share this year" in say["forecasts"][0]
    text = "\n".join(__import__("aristos_council.company_report", fromlist=["x"])
                     .format_company_report(report).splitlines())
    assert "24 analysts: 9 strong buy" in text and "WHAT ANALYSTS SAY" in text
