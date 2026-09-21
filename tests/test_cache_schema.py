"""Schema-marked cache (cleanup ITEM 2): a cache entry written under an OLD DTO shape is
detected as stale on read and refetched — never silently deserialised with new fields as
None (tonight's root cause: fundamentals cached pre-total_cash read all day as EBIT/mcap).
"""

from __future__ import annotations

import json
from datetime import date

from aristos_council.data.adapter import (
    Fundamentals,
    MarketDataAdapter,
    PriceHistory,
)
from aristos_council.data.cache import (
    ADAPTER_SCHEMA_VERSION,
    CachingAdapter,
    _SCHEMAS,
)

TODAY = date(2026, 7, 5)


class _Counting(MarketDataAdapter):
    name = "fake"

    def __init__(self):
        self.calls = 0

    def get_fundamentals(self, ticker):
        self.calls += 1
        return Fundamentals(ticker=ticker, market_cap=1e10, total_cash=2e9)

    def get_price_history(self, ticker, *, start, end):
        return PriceHistory(ticker=ticker)

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _write(cad, ticker, payload):
    p = cad._path(ticker, "fundamentals")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(payload, encoding="utf-8")
    return p


def test_matching_schema_is_a_hit(tmp_path):
    inner = _Counting()
    cad = CachingAdapter(inner, cache_dir=tmp_path, today=TODAY)
    cad.get_fundamentals("AAPL")             # writes schema-stamped
    cad.get_fundamentals("AAPL")             # matching marker -> HIT
    assert inner.calls == 1


def test_stale_schema_marker_is_a_miss_and_rewrites(tmp_path):
    inner = _Counting()
    cad = CachingAdapter(inner, cache_dir=tmp_path, today=TODAY)
    _write(cad, "AAPL", json.dumps(
        {"_schema": "an,old,shape", "data": {"ticker": "AAPL", "market_cap": 5e9}}))
    f = cad.get_fundamentals("AAPL")
    assert inner.calls == 1                  # stale marker -> refetch
    assert f.market_cap == 1e10              # the FRESH value, not the stale 5e9
    cad.get_fundamentals("AAPL")             # rewritten under current schema -> HIT
    assert inner.calls == 1


def test_pre_marker_bare_file_is_a_miss(tmp_path):
    # a pre-ITEM-2 file: the bare serialized dict, no _schema wrapper
    inner = _Counting()
    cad = CachingAdapter(inner, cache_dir=tmp_path, today=TODAY)
    _write(cad, "AAPL", json.dumps({"ticker": "AAPL", "market_cap": 5e9}))
    cad.get_fundamentals("AAPL")
    assert inner.calls == 1                  # missing marker -> refetch


def test_corrupted_file_is_a_miss_not_a_crash(tmp_path):
    inner = _Counting()
    cad = CachingAdapter(inner, cache_dir=tmp_path, today=TODAY)
    _write(cad, "AAPL", "{ not valid json at all")
    f = cad.get_fundamentals("AAPL")         # must not raise
    assert inner.calls == 1 and f.market_cap == 1e10


# --- ETFCHK-4: explicit hand-bumped adapter schema-version token --------------- #
def _current_marker():
    return _SCHEMAS["fundamentals"]


def test_marker_carries_the_adapter_version_token():
    # the version lives in the marker (documented bump convention in cache.py)
    assert _current_marker().startswith(f"v{ADAPTER_SCHEMA_VERSION}:")


def test_older_version_same_fields_is_a_miss_and_refetches(tmp_path):
    # The ETFCHK-4 case: SAME field set (the field-name marker would still match),
    # only the adapter VERSION differs -> must be detected stale and refetched, so a
    # payload with a mis-derived ETF yield is never silently served.
    inner = _Counting()
    cad = CachingAdapter(inner, cache_dir=tmp_path, today=TODAY)
    fields_part = _current_marker().split(":", 1)[1]          # same field-name set
    older = f"v{ADAPTER_SCHEMA_VERSION - 1}:{fields_part}"    # only the token is older
    _write(cad, "AAPL", json.dumps(
        {"_schema": older, "data": {"ticker": "AAPL", "market_cap": 5e9}}))
    f = cad.get_fundamentals("AAPL")
    assert inner.calls == 1                  # older version -> refetch
    assert f.market_cap == 1e10              # the FRESH value, not the stale 5e9


def test_same_version_still_hits(tmp_path):
    inner = _Counting()
    cad = CachingAdapter(inner, cache_dir=tmp_path, today=TODAY)
    cad.get_fundamentals("AAPL")             # writes under current version marker
    cad.get_fundamentals("AAPL")             # same version -> HIT, no refetch
    assert inner.calls == 1


# --- ABS-READINGS-3 item 3: the bump that was MISSED -------------------------- #
def test_a_v4_entry_holding_the_TTM_free_cash_flow_is_not_served(tmp_path):
    """The reason ADAPTER_SCHEMA_VERSION went 4 -> 5.

    ABS-READINGS-2 changed what ``free_cash_flow`` MEANS — from the provider's headline
    TTM blob to the cash-flow statement's own row — without changing its name, so the
    field-name half of the marker still matched. NFLX's cached entry kept serving
    25,387,552,768 against an operating cash flow of 10,149,273,000, the guard kept
    firing on the merged build, and the adapter fix looked like it had not worked. The
    version token exists for exactly this: a semantics change that leaves the field set
    unchanged.
    """
    class _Fixed(MarketDataAdapter):
        name = "fake"

        def __init__(self):
            self.calls = 0

        def get_fundamentals(self, ticker):
            self.calls += 1
            return Fundamentals(ticker=ticker, operating_cash_flow=10_149_273_000.0,
                                free_cash_flow=9_461_053_000.0)   # the statement row

        def get_price_history(self, ticker, *, start, end):
            return PriceHistory(ticker=ticker)

        def get_dividend_history(self, ticker, *, start, end):
            return []

    inner = _Fixed()
    cad = CachingAdapter(inner, cache_dir=tmp_path, today=TODAY)
    fields_part = _current_marker().split(":", 1)[1]
    _write(cad, "NFLX", json.dumps({"_schema": f"v4:{fields_part}", "data": {
        "ticker": "NFLX", "operating_cash_flow": 10_149_273_000.0,
        "free_cash_flow": 25_387_552_768.0}}))                    # the TTM blob

    f = cad.get_fundamentals("NFLX")
    assert inner.calls == 1, "the stale entry was served instead of being refetched"
    assert f.free_cash_flow == 9_461_053_000.0
    assert f.free_cash_flow < f.operating_cash_flow

    # ...and the reading that abstained on the merged build now prints a figure, longer
    # than the operating-cash-flow one because capital spending has been taken out.
    from aristos_council.abs_readings import debt_and_cash

    out = debt_and_cash(Fundamentals(ticker="NFLX", currency="USD",
                                     total_debt=16_654_660_608.0,
                                     total_cash=9_127_910_400.0,
                                     operating_cash_flow=f.operating_cash_flow,
                                     free_cash_flow=f.free_cash_flow))
    assert out.years_to_repay.available
    assert out.years_to_repay.value > out.net_debt_to_ocf.value
