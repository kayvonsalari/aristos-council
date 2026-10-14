"""MILAN-1 - Borsa Italiana (.MI, EUR) in the local index, so a Milan ticker is recognised, checked and run
like a .PA / .DE one. EODHD serves no Milan on this plan, so the rows come from Yahoo (milan_index.py)."""
from datetime import date

import pytest

from aristos_council import list_input
from aristos_council.market_index import (LISTING_SOURCES, SOURCE_EODHD_LISTING, SOURCE_YAHOO_LISTING, IndexRow,
                                          IndexStore, _UsdConverter)
from aristos_council.milan_index import build_milan, listed_symbols, row_from_quote

TODAY = date(2026, 10, 15)


def _quote(sym, name, cap, currency="EUR", qtype="EQUITY"):
    return {"symbol": sym, "longName": name, "marketCap": cap, "currency": currency, "quoteType": qtype}


QUOTES = [_quote("LDO.MI", "Leonardo S.p.a.", 27.3e9), _quote("UCG.MI", "UniCredit S.p.A.", 115.8e9),
          _quote("ISP.MI", "Intesa Sanpaolo S.p.A.", 95e9), _quote("ENI.MI", "Eni S.p.A.", 50e9)]
INFO = {"LDO.MI": {"sector": "Industrials", "industry": "Aerospace & Defense"},
        "UCG.MI": {"sector": "Financial Services", "industry": "Banks - Regional"},
        "ISP.MI": {"sector": "Financial Services", "industry": "Banks - Regional"},
        "ENI.MI": {"sector": "Energy", "industry": "Oil & Gas Integrated"}}


def _screen(pages):
    """A fake screener: page n of ``pages`` (a list of quote lists), then nothing."""
    def call(offset):
        n = offset // 250
        return {"quotes": pages[n] if n < len(pages) else [], "total": 30656}
    return call


def _usd():
    usd = _UsdConverter()
    usd._rates["EUR"] = 1.1
    return usd


def test_the_listing_reads_every_page_until_the_screener_leaves_milan_and_ignores_its_total():
    pad = [_quote(f"X{i}.MI", f"Co {i}", 1e9) for i in range(250)]
    other = [_quote("AAPL", "Apple", 3e12)]                      # the screener drifts off Milan
    got = listed_symbols(_screen([pad, QUOTES, other]))
    assert len(got) == 254 and {q["symbol"] for q in got} >= {"LDO.MI", "ENI.MI"}


def test_funds_and_non_milan_lines_are_not_listed():
    quotes = QUOTES + [_quote("SPYI.MI", "Some ETF", 1e9, qtype="ETF"), _quote("SAP.DE", "SAP", 2e11)]
    got = listed_symbols(_screen([quotes]))
    assert {q["symbol"] for q in got} == {"LDO.MI", "UCG.MI", "ISP.MI", "ENI.MI"}


def test_a_milan_row_is_a_eur_home_listing_tagged_with_its_source():
    row = row_from_quote(QUOTES[0], INFO["LDO.MI"], today=TODAY)
    assert (row.ticker, row.yahoo_ticker, row.market, row.currency) == ("LDO.MI", "LDO.MI", "MI", "EUR")
    assert row.primary_ticker == "LDO.MI" and row.isin == ""            # no ISIN on file: blank, not invented
    assert row.industry == "Aerospace & Defense" and row.sector == "Industrials"
    assert row.source == SOURCE_YAHOO_LISTING and row.source in LISTING_SOURCES and row.complete


def test_the_usd_cap_converts_exactly_like_every_other_eur_exchange():
    usd = _usd()
    milan = usd.apply(row_from_quote(QUOTES[0], INFO["LDO.MI"], today=TODAY))
    paris = usd.apply(IndexRow(ticker="HO.PA", currency="EUR", market_cap=27.3e9, source=SOURCE_EODHD_LISTING))
    assert milan.market_cap_usd == paris.market_cap_usd == pytest.approx(27.3e9 * 1.1)
    assert milan.market_cap_usd_source == paris.market_cap_usd_source == "computed"


def test_instrument_codes_with_no_name_or_market_cap_are_not_listed_as_companies():
    """Yahoo files ~2,600 warrants and certificates under Milan as EQUITY, named by a code, with no market cap."""
    junk = [{"symbol": "F68736.MI", "quoteType": "EQUITY", "currency": "EUR"},
            {"symbol": "BT301U.MI", "quoteType": "EQUITY", "currency": "EUR", "marketCap": 0}]
    got = listed_symbols(_screen([QUOTES + junk]))
    assert {q["symbol"] for q in got} == {"LDO.MI", "UCG.MI", "ISP.MI", "ENI.MI"}


def test_a_name_with_no_industry_is_stored_and_counted_not_dropped(tmp_path):
    quotes = [_quote("AAA.MI", "A", 2e9), _quote("BBB.MI", "B", 5e9)]
    store = IndexStore(tmp_path)
    counts = build_milan(store=store, usd=_usd(), today=TODAY, screen=_screen([quotes]),
                         info_fn=lambda s: {"industry": "Banks - Regional"} if s == "AAA.MI" else {})
    assert counts["listed"] == 2 and counts["no_industry"] == 1
    rows = {r.ticker: r for r in store.load()}
    assert set(rows) == {"AAA.MI", "BBB.MI"} and rows["BBB.MI"].industry == ""


def test_a_rebuild_keeps_fresh_rows_and_does_not_refetch_them(tmp_path):
    store = IndexStore(tmp_path)
    calls = []
    kw = dict(store=store, usd=_usd(), today=TODAY, screen=_screen([QUOTES]),
              info_fn=lambda s: calls.append(s) or INFO[s])
    build_milan(**kw)
    assert len(calls) == 4
    again = build_milan(**kw)
    assert len(calls) == 4 and again["fresh"] == 4 and again["fetched"] == 0


def _lookup(tmp_path):
    store = IndexStore(tmp_path)
    build_milan(store=store, usd=_usd(), today=TODAY, screen=_screen([QUOTES]), info_fn=lambda s: INFO[s])
    return list_input.IndexLookup.from_rows(store.load())


def test_milan_tickers_are_recognised_in_a_pasted_list_and_one_name_check(tmp_path):
    lookup = _lookup(tmp_path)
    res = list_input.resolve_tickers(["LDO.MI", "UCG.MI", "ISP.MI", "ENI.MI"], lookup)
    assert not res.unrecognised and res.send == ["LDO.MI", "UCG.MI", "ISP.MI", "ENI.MI"]
    assert [r.name for r in res.recognised][0] == "Leonardo S.p.a."
    assert "4 recognised" in res.line() and "not covered" not in res.line()


def test_a_milan_ticker_the_index_does_not_hold_is_not_recognised_but_no_longer_not_covered(tmp_path):
    res = list_input.resolve_tickers(["ZZZZ.MI"], _lookup(tmp_path))
    assert len(res.unrecognised) == 1 and res.unrecognised[0].why == ""


def test_tokyo_is_still_not_covered(tmp_path):
    res = list_input.resolve_tickers(["7203.T"], _lookup(tmp_path))
    assert "Tokyo is not covered" in res.unrecognised[0].why


def test_milan_is_a_readable_exchange_name():
    from aristos_council.exchange_names import exchange_name
    assert exchange_name("MI") == "Milan"
