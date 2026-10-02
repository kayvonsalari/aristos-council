"""SMALLCAP-VIEW-1 — the opt-in peer band for a company below the $5bn lens gate.

Fully isolated (TEST-ISOLATION-1): a fake market-index store, a fake cohort definitions
file written to ``tmp_path``, and a fake adapter for the liquidity check. No real index, no
real cohort file, no network.

What is pinned: the band keeps names inside [the cohort's own floor, $5bn), drops anyone
outside that range or outside the cohort's own industry/GICS scope, drops an illiquid name,
and degrades honestly (empty list, a stated reason, never a crash) when the company's
industry matches no cohort or the cohort has no USD floor.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aristos_council.data.adapter import MarketDataAdapter, PriceBar, PriceHistory
from aristos_council.market_index import SOURCE_EODHD_LISTING, IndexRow
from aristos_council.smallcap_band import SMALLCAP_CEILING_USD, build_smallcap_peer_band, \
    live_adv_usd

TODAY = date(2026, 6, 30)


def _row(ticker, *, industry="Biotechnology", sub="Biotechnology", cap_usd=2e9,
        market="US", gics_sub=None):
    return IndexRow(
        ticker=ticker, yahoo_ticker=ticker.rpartition(".")[0], name=f"{ticker} Inc",
        exchange=market, market=market, currency="USD", sector="Healthcare",
        industry=industry, gics_sector="Health Care", gics_industry="Biotechnology",
        gics_subindustry=gics_sub if gics_sub is not None else sub,
        market_cap=cap_usd, market_cap_usd=cap_usd, market_cap_usd_source="computed",
        primary_ticker=ticker, isin=f"XX{abs(hash(ticker)) % 10**10:010d}",
        fetched_at="2026-06-29", source=SOURCE_EODHD_LISTING)


class _Store:
    def __init__(self, rows):
        self._rows = rows

    def load(self):
        return list(self._rows)


class _LiquidityAdapter(MarketDataAdapter):
    """ADV per ticker set directly by the test: $close * volume, same every bar."""

    name = "fake-liquidity"

    def __init__(self, adv_by_ticker: dict):
        self._adv = adv_by_ticker

    def get_price_history(self, ticker, *, start, end):
        adv = self._adv.get(ticker)
        if adv is None:
            return PriceHistory(ticker=ticker, bars=[])
        close, volume = 100.0, adv / 100.0
        return PriceHistory(ticker=ticker, bars=[
            PriceBar(day=date(2026, 6, i % 28 + 1), open=close, high=close, low=close,
                     close=close, adj_close=close, volume=volume) for i in range(30)])

    def get_fundamentals(self, ticker):
        raise NotImplementedError

    def get_dividend_history(self, ticker, *, start, end):
        return []


def _definitions_yaml(tmp_path: Path, *, floor_usd=1_000_000_000, gics_subindustry=None) -> Path:
    gics_line = (f"  gics_subindustry: {gics_subindustry}\n" if gics_subindustry else "")
    path = tmp_path / "cohort_definitions.yaml"
    path.write_text(
        "cohorts:\n"
        "- name: \"Test Biotech\"\n"
        "  industry: [\"Biotechnology\"]\n"
        "  exchanges: [ALL]\n"
        f"  min_market_cap_usd: {floor_usd}\n"
        "  min_history_years: 1\n"
        "  exclude: []\n"
        f"{gics_line}"
        "  watch: false\n",
        encoding="utf-8")
    return path


def _subject(cap_usd=2e9, **kw):
    return _row("CO.US", cap_usd=cap_usd, **kw)


# --------------------------------------------------------------------------- #
# live_adv_usd — a direct, small read of (close, volume), no history-bisecting
# --------------------------------------------------------------------------- #
def test_live_adv_usd_is_mean_close_times_volume():
    adapter = _LiquidityAdapter({"LIQ": 5_000_000.0})
    assert live_adv_usd(adapter, "LIQ", today=TODAY) == pytest.approx(5_000_000.0)


def test_live_adv_usd_is_none_when_the_fetch_fails():
    class _Boom(MarketDataAdapter):
        name = "boom"

        def get_price_history(self, ticker, *, start, end):
            raise RuntimeError("no data")

        def get_fundamentals(self, ticker):
            raise NotImplementedError

        def get_dividend_history(self, ticker, *, start, end):
            return []

    assert live_adv_usd(_Boom(), "X", today=TODAY) is None


def test_live_adv_usd_is_none_with_no_bars():
    assert live_adv_usd(_LiquidityAdapter({}), "GHOST", today=TODAY) is None


# --------------------------------------------------------------------------- #
# build_smallcap_peer_band — the full band
# --------------------------------------------------------------------------- #
def test_the_band_keeps_names_between_the_cohort_floor_and_5bn(tmp_path):
    defs_path = _definitions_yaml(tmp_path)
    rows = [
        _subject(),                                  # CO, $2bn — the subject, never in the band
        _row("A.US", cap_usd=1.2e9),                 # inside the band
        _row("B.US", cap_usd=4.9e9),                 # inside the band, near the ceiling
        _row("C.US", cap_usd=0.5e9),                 # BELOW the cohort's own $1bn floor
        _row("D.US", cap_usd=6.0e9),                 # AT/ABOVE $5bn — the gate already covers it
    ]
    adapter = _LiquidityAdapter({"A": 5_000_000.0, "B": 5_000_000.0,
                                 "C": 5_000_000.0, "D": 5_000_000.0})
    band = build_smallcap_peer_band(rows[0], adapter=adapter, today=TODAY,
                                    store=_Store(rows), definitions_path=defs_path)
    assert band.cohort_slug == "test_biotech"
    assert band.cohort_name == "Test Biotech"
    assert band.floor_usd == 1_000_000_000
    assert set(band.tickers) == {"A", "B"}
    assert "C" not in band.tickers and "D" not in band.tickers


def test_the_band_drops_an_illiquid_name(tmp_path):
    defs_path = _definitions_yaml(tmp_path)
    rows = [_subject(), _row("A.US", cap_usd=1.5e9), _row("B.US", cap_usd=1.5e9)]
    adapter = _LiquidityAdapter({"A": 5_000_000.0, "B": 100_000.0})      # B under $3m ADV
    band = build_smallcap_peer_band(rows[0], adapter=adapter, today=TODAY,
                                    store=_Store(rows), definitions_path=defs_path)
    assert band.tickers == ["A"]
    assert band.dropped_illiquid == ["B"]


def test_the_band_excludes_a_different_industry(tmp_path):
    defs_path = _definitions_yaml(tmp_path)
    rows = [_subject(), _row("A.US", cap_usd=1.5e9),
            _row("Z.US", cap_usd=1.5e9, industry="Steel")]      # wrong industry
    adapter = _LiquidityAdapter({"A": 5_000_000.0, "Z": 5_000_000.0})
    band = build_smallcap_peer_band(rows[0], adapter=adapter, today=TODAY,
                                    store=_Store(rows), definitions_path=defs_path)
    assert band.tickers == ["A"]


def test_the_band_narrows_by_gics_subindustry_when_the_cohort_names_one(tmp_path):
    defs_path = _definitions_yaml(tmp_path, gics_subindustry="[Biotechnology]")
    rows = [_subject(sub="Biotechnology"), _row("A.US", cap_usd=1.5e9, sub="Biotechnology"),
            _row("B.US", cap_usd=1.5e9, sub="Pharmaceuticals")]        # narrower cohort excludes it
    adapter = _LiquidityAdapter({"A": 5_000_000.0, "B": 5_000_000.0})
    band = build_smallcap_peer_band(rows[0], adapter=adapter, today=TODAY,
                                    store=_Store(rows), definitions_path=defs_path)
    assert band.tickers == ["A"]


def test_no_cohort_match_degrades_honestly(tmp_path):
    defs_path = _definitions_yaml(tmp_path)
    subject = _row("CO.US", industry="Unmatched Industry", cap_usd=2e9)
    band = build_smallcap_peer_band(subject, adapter=_LiquidityAdapter({}), today=TODAY,
                                    store=_Store([subject]), definitions_path=defs_path)
    assert band.tickers == [] and band.cohort_slug is None and band.reasons


def test_a_cohort_with_no_usd_floor_degrades_honestly(tmp_path):
    path = tmp_path / "cohort_definitions.yaml"
    path.write_text(
        "cohorts:\n"
        "- name: \"No Floor Cohort\"\n"
        "  industry: [\"Biotechnology\"]\n"
        "  exchanges: [US]\n"
        "  min_market_cap: 1000000000\n"       # own-currency floor, NOT min_market_cap_usd
        "  min_history_years: 1\n"
        "  exclude: []\n"
        "  watch: false\n",
        encoding="utf-8")
    subject = _subject()
    band = build_smallcap_peer_band(subject, adapter=_LiquidityAdapter({}), today=TODAY,
                                    store=_Store([subject]), definitions_path=path)
    assert band.tickers == [] and band.cohort_slug == "no_floor_cohort" and band.reasons


def test_the_subject_itself_never_appears_in_its_own_band(tmp_path):
    defs_path = _definitions_yaml(tmp_path)
    rows = [_subject(), _row("A.US", cap_usd=1.5e9)]
    adapter = _LiquidityAdapter({"A": 5_000_000.0, "CO": 5_000_000.0})
    band = build_smallcap_peer_band(rows[0], adapter=adapter, today=TODAY,
                                    store=_Store(rows), definitions_path=defs_path)
    assert "CO" not in band.tickers
