"""MARKET-INDEX-SKIP-1 — one unlistable exchange must not end the whole build.

Live, 2026-09-22: the European build died at Milan. ``/exchange-symbol-list/MI`` answered
HTTP 404, the error propagated out of the per-exchange loop, and every exchange after it was
never attempted. The store was flushed and the reason written down — the module is careful
about that — but the run was over.

(INDEX-SKIP-RETRY-1 later split the cause: HTTP 404 is "not available", and a network failure
is retried and reported separately - see tests/test_market_index_skip_retry.py. The fakes here
raise ``ExchangeNotAvailable``, which is what the real source raises for a 404.)

A listing that fails is a fact about ONE venue, not about the build. So it is skipped, named
in the summary, written to the build log, and the remaining exchanges are fetched.

A QUOTA refusal is different and still stops everything: continuing would burn the remaining
exchanges against an allowance that is already gone.

Probed against ``/exchanges-list`` on 2026-09-23 (70 exchanges): **MI, HK, T and KS are all
absent** on this plan, and there is no Italian exchange in the list at all — so MI is not a
mis-spelling, Milan is not offered. ``market_index.yaml`` keeps all four and says so.
"""
from __future__ import annotations

from datetime import date

import pytest

from aristos_council import market_index as mi


class _Source:
    """An EODHD stand-in: listings from a dict, and named exchanges refused.

    Counts the same two numbers the real one does, because the summary reports them and a
    fake that tracked neither would let a request-accounting bug through.
    """

    def __init__(self, listings: dict, refuse: dict | None = None) -> None:
        self.listings = listings
        self.refuse = refuse or {}
        self.asked: list[str] = []
        self.requests = 0
        self.charged = 0

    def common_stocks(self, exchange: str):
        self.asked.append(exchange)
        self.requests += 1
        self.charged += mi.CHARGE_LISTING
        if exchange in self.refuse:
            raise self.refuse[exchange]
        return list(self.listings.get(exchange, []))

    def general(self, symbol: str):
        self.requests += 1
        self.charged += mi.CHARGE_FUNDAMENTALS
        code = symbol.split(".", 1)[0]
        return {"General": {"Code": code, "Name": f"{code} Inc", "Exchange": "XETRA",
                            "CurrencyCode": "EUR", "CountryISO": "DE",
                            "PrimaryTicker": symbol, "ISIN": f"DE{code:0>10}",
                            "Industry": "Software", "Sector": "Technology"},
                mi.CAP_KEY: 1.0e10}


class _NoUsd:
    """The USD converter, abstaining. Keeps these tests about the skip, not about FX."""

    def apply(self, row):
        return row


def _listing(code: str) -> dict:
    return {"Code": code, "Type": mi.COMMON_STOCK, "Exchange": "XETRA"}


def _build(tmp_path, *, exchanges, listings, refuse=None):
    source = _Source(listings, refuse)
    store = mi.IndexStore(tmp_path)
    outcome = mi.build(exchanges=exchanges, store=store, source=source, usd=_NoUsd(),
                       today=date(2026, 9, 23), venues={})
    return source, store, outcome


# --------------------------------------------------------------------------- #
# the skip
# --------------------------------------------------------------------------- #
def test_an_unlistable_exchange_does_not_end_the_build(tmp_path):
    """The Milan case: MI 404s, and XETRA and LSE after it must still be fetched."""
    source, store, outcome = _build(
        tmp_path, exchanges=["XETRA", "MI", "LSE"],
        listings={"XETRA": [_listing("SAP")], "LSE": [_listing("BP")]},
        refuse={"MI": mi.ExchangeNotAvailable("EODHD /exchange-symbol-list/MI: HTTP 404")})
    assert source.asked == ["XETRA", "MI", "LSE"]
    assert outcome.stopped == ""
    assert outcome.fetched == 2
    assert {r.ticker for r in store.load()} == {"SAP.XETRA", "BP.LSE"}


def test_the_skip_is_recorded_with_its_reason(tmp_path):
    _source, _store, outcome = _build(
        tmp_path, exchanges=["XETRA", "MI"], listings={"XETRA": [_listing("SAP")]},
        refuse={"MI": mi.ExchangeNotAvailable("EODHD /exchange-symbol-list/MI: HTTP 404")})
    assert outcome.skipped_exchanges == [("MI", "HTTP 404")]


def test_the_summary_names_the_skipped_exchange(tmp_path):
    _source, _store, outcome = _build(
        tmp_path, exchanges=["MI"], listings={},
        refuse={"MI": mi.ExchangeNotAvailable("EODHD /exchange-symbol-list/MI: HTTP 404")})
    assert outcome.skipped_sentence() == "1 exchange skipped - not available (404): MI"
    assert "1 exchange skipped - not available (404): MI" in outcome.summary()


def test_all_four_absent_exchanges_are_skipped_and_named(tmp_path):
    """MI, HK, T and KS are all absent from EODHD's list on this plan. Before the fix the
    build died at the first of them and never learned about the other three."""
    refused = mi.ExchangeNotAvailable("EODHD /exchange-symbol-list/X: HTTP 404")
    source, _store, outcome = _build(
        tmp_path, exchanges=["US", "MI", "HK", "T", "KS", "AU"],
        listings={"US": [_listing("AAPL")], "AU": [_listing("BHP")]},
        refuse={code: refused for code in ("MI", "HK", "T", "KS")})
    assert source.asked == ["US", "MI", "HK", "T", "KS", "AU"]
    assert [code for code, _ in outcome.skipped_exchanges] == ["MI", "HK", "T", "KS"]
    assert "4 exchanges skipped" in outcome.summary()
    assert outcome.fetched == 2                      # US and AU still built


def test_the_skip_is_written_to_the_build_log(tmp_path):
    _source, store, _outcome = _build(
        tmp_path, exchanges=["MI"], listings={},
        refuse={"MI": mi.ExchangeNotAvailable("EODHD /exchange-symbol-list/MI: HTTP 404")})
    log = mi.build_log_path(store).read_text(encoding="utf-8")
    assert "MI: SKIPPED" in log
    assert "HTTP 404" in log
    assert "continuing with the remaining exchanges" in log


def test_a_clean_build_says_nothing_about_skips(tmp_path):
    _source, _store, outcome = _build(tmp_path, exchanges=["XETRA"],
                                      listings={"XETRA": [_listing("SAP")]})
    assert outcome.skipped_exchanges == []
    assert outcome.skipped_sentence() == ""
    assert "skipped:" not in outcome.summary()


# --------------------------------------------------------------------------- #
# what still stops everything
# --------------------------------------------------------------------------- #
def test_a_quota_refusal_still_stops_the_whole_build(tmp_path):
    """A quota is a STOP, not a skip: continuing would burn the remaining exchanges against
    an allowance that is already gone."""
    source, _store, outcome = _build(
        tmp_path, exchanges=["XETRA", "MI", "LSE"], listings={"XETRA": [_listing("SAP")]},
        refuse={"MI": mi.QuotaExhausted("EODHD refused: quota reached")})
    assert source.asked == ["XETRA", "MI"]           # LSE never attempted
    assert "quota" in outcome.stopped.lower()
    assert outcome.skipped_exchanges == []


def test_the_store_is_still_flushed_when_an_exchange_is_skipped(tmp_path):
    """The module's existing discipline — no silent exits, always flush — is not weakened."""
    _source, store, _outcome = _build(
        tmp_path, exchanges=["XETRA", "MI"], listings={"XETRA": [_listing("SAP")]},
        refuse={"MI": mi.ExchangeNotAvailable("EODHD /exchange-symbol-list/MI: HTTP 404")})
    assert store.path.exists()
    assert [r.ticker for r in store.load()] == ["SAP.XETRA"]


# --------------------------------------------------------------------------- #
# the config records the finding
# --------------------------------------------------------------------------- #
def test_the_tracked_config_lists_the_venues_worth_asking_about():
    """Superseded in part by INDEX-CAP-RETRY-1. Two things were learned after this test was
    first written: HK is NOT absent (``/exchanges-list`` omits it, but
    ``/exchange-symbol-list/HK`` serves 3,512 names — build log 2026-09-23T09:22:20), and
    Milan has no working code at all (MI/MTA/BIT/MIL/IT/XMIL all 404, probed 2026-09-24), so
    MI was deleted rather than skipped forever. T and KS stay: they 404 today but are named in
    every skip summary and have a plausible route to being added."""
    config = mi.load_config(mi.DEFAULT_CONFIG)
    assert "MI" not in config["exchanges"]
    for code in ("HK", "T", "KS"):
        assert code in config["exchanges"]


def test_the_config_explains_milans_removal_and_the_others_staying():
    text = mi.DEFAULT_CONFIG.read_text(encoding="utf-8")
    assert "MARKET-INDEX-SKIP-1" in text
    assert "MILAN IS REMOVED" in text
    assert "MTA -> 404" in text
    assert "not on this plan" in text
