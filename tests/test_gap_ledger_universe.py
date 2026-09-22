"""GAP-LEDGER-1 — the pool and the liquidity pre-filter.

Three things are pinned here:

* the index is read from ITS OWN CONFIG, never from an assumed path, and a missing index
  stops with the two ways forward rather than a traceback;
* US common stocks on the named venues only — an OTC row is not a US row (MARKET-INDEX-2
  split ``market`` from ``exchange`` for exactly this), and a fund is never screened;
* the pre-filter's thresholds, and the adjusted-close reading that keeps an ex-dividend
  morning from looking like a 2% gap down on no news at all.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from aristos_council import market_index
from aristos_council.data.adapter import PriceBar
from aristos_council.gap_ledger.config import GapConfig
from aristos_council.gap_ledger.universe import (UniverseUnavailable, common_stock_rows,
                                                 completed_sessions, index_root,
                                                 looks_like_fund, pool_from_index,
                                                 pre_filter, pre_filter_one,
                                                 read_ticker_file)

from .gap_ledger_fakes import daily_series

DAY = date(2026, 9, 22)


def _row(ticker="AAA.US", *, market="US", exchange="NYSE", name="Alpha Inc",
         yahoo="AAA", industry="Software") -> market_index.IndexRow:
    return market_index.IndexRow(ticker=ticker, yahoo_ticker=yahoo, name=name,
                                 exchange=exchange, market=market, industry=industry,
                                 market_cap=1e10, source=market_index.SOURCE_EODHD_LISTING,
                                 fetched_at=DAY.isoformat())


# --------------------------------------------------------------------------- #
# the pool
# --------------------------------------------------------------------------- #
def test_index_root_is_read_from_the_config_not_assumed(tmp_path):
    config = tmp_path / "market_index.yaml"
    config.write_text("exchanges: [US]\nroot: somewhere/else\n", encoding="utf-8")
    assert index_root(config) == __import__("pathlib").Path("somewhere/else")


def test_index_root_falls_back_to_the_default_when_there_is_no_config(tmp_path):
    assert index_root(tmp_path / "absent.yaml") == market_index.DEFAULT_ROOT


def test_only_us_rows_on_the_named_venues_survive():
    rows = [_row("AAA.US"),
            _row("BBB.US", exchange="NASDAQ", yahoo="BBB"),
            _row("CCC.US", exchange="PINK", yahoo="CCC"),      # OTC
            _row("DDD.XETRA", market="XETRA", exchange="XETRA", yahoo="DDD.DE")]
    kept = {r.yahoo_ticker for r in common_stock_rows(rows)}
    assert kept == {"AAA", "BBB"}


def test_an_otc_row_is_not_a_us_row_even_though_its_market_says_us():
    """MARKET-INDEX-2's whole point: the venue is what says OTC, and it is a separate field."""
    assert common_stock_rows([_row(exchange="OTCQB")]) == []


@pytest.mark.parametrize("name, classification", [
    ("SPDR S&P 500 ETF Trust", ""),
    ("Vanguard Total Stock Market Index Fund", ""),
    ("Alpha Inc", "Exchange Traded Fund"),
])
def test_funds_are_excluded_by_asset_kind(name, classification):
    assert looks_like_fund(name, classification)
    assert common_stock_rows([_row(name=name, industry=classification)]) == []


def test_an_operating_company_is_not_mistaken_for_a_fund():
    assert not looks_like_fund("Northern Trust Corporation", "Banks - Regional")
    assert len(common_stock_rows([_row(name="Northern Trust Corporation",
                                       industry="Banks - Regional")])) == 1


def test_a_missing_market_cap_does_not_exclude_a_name():
    """The brief is explicit: a gap is not about size, and the cap column is best-effort."""
    row = _row()
    row.market_cap = None
    assert len(common_stock_rows([row])) == 1


def test_a_missing_index_stops_with_both_ways_forward(tmp_path):
    config = tmp_path / "market_index.yaml"
    config.write_text(f"exchanges: [US]\nroot: {tmp_path.as_posix()}/nothing\n",
                      encoding="utf-8")
    with pytest.raises(UniverseUnavailable) as caught:
        pool_from_index(config_path=config)
    message = str(caught.value)
    assert "market_index build" in message and "--tickers" in message


# --------------------------------------------------------------------------- #
# the manual override
# --------------------------------------------------------------------------- #
def test_ticker_file_is_comment_tolerant_and_de_duplicated(tmp_path):
    path = tmp_path / "names.txt"
    path.write_text("AAPL\n# a comment\n\nmsft  # trailing note\nAAPL\n", encoding="utf-8")
    assert read_ticker_file(path) == ["AAPL", "MSFT"]


def test_an_empty_ticker_file_is_an_error_not_an_empty_run(tmp_path):
    path = tmp_path / "names.txt"
    path.write_text("# nothing but a comment\n", encoding="utf-8")
    with pytest.raises(UniverseUnavailable):
        read_ticker_file(path)


# --------------------------------------------------------------------------- #
# the pre-filter
# --------------------------------------------------------------------------- #
def test_todays_part_formed_bar_is_never_yesterdays_close():
    """yfinance serves today's row mid-session. Counting it would make "yesterday's close"
    a half-finished today."""
    bars = daily_series(end=DAY, sessions=300) + [
        PriceBar(day=DAY, open=1.0, high=1.0, low=1.0, close=1.0, adj_close=1.0, volume=1)]
    assert completed_sessions(bars, as_of=DAY)[-1].day < DAY
    row = pre_filter_one("AAA", bars, as_of=DAY)
    assert row.previous_close == 50.0


def test_a_clean_name_passes_with_all_three_readings():
    row = pre_filter_one("AAA", daily_series(end=DAY, sessions=300), as_of=DAY)
    assert row.passed is True
    assert row.previous_close == 50.0
    assert row.average_volume == 2_000_000
    assert row.history_days == 300


def test_short_history_is_excluded_with_the_count_in_the_reason():
    row = pre_filter_one("AAA", daily_series(end=DAY, sessions=100), as_of=DAY)
    assert row.passed is False
    assert "only 100 trading days" in row.reason


def test_a_cheap_stock_is_excluded():
    row = pre_filter_one("AAA", daily_series(end=DAY, sessions=300, close=4.0), as_of=DAY)
    assert row.passed is False
    assert "below $10.00" in row.reason


def test_a_thin_stock_is_excluded():
    row = pre_filter_one("AAA", daily_series(end=DAY, sessions=300, volume=100_000),
                         as_of=DAY)
    assert row.passed is False
    assert "average volume" in row.reason


def test_the_price_test_reads_the_adjusted_close():
    """An ex-dividend morning: the raw close is above the threshold, the adjusted one is
    not. The adjusted reading is the one that governs, because it is the one that does not
    invent a gap."""
    bars = daily_series(end=DAY, sessions=300)
    last = bars[-1]
    bars[-1] = PriceBar(day=last.day, open=last.open, high=last.high, low=last.low,
                        close=10.40, adj_close=9.90, volume=last.volume)
    row = pre_filter_one("AAA", bars, as_of=DAY)
    assert row.passed is False
    assert "$9.90" in row.reason


def test_average_volume_uses_the_configured_window_only():
    bars = daily_series(end=DAY, sessions=300, volume=100_000)
    for bar in bars[-20:]:
        bars[bars.index(bar)] = PriceBar(day=bar.day, open=bar.open, high=bar.high,
                                         low=bar.low, close=bar.close,
                                         adj_close=bar.adj_close, volume=5_000_000)
    row = pre_filter_one("AAA", bars, as_of=DAY, config=GapConfig())
    assert row.passed is True
    assert row.average_volume == 5_000_000


def test_a_name_with_no_bars_is_excluded_with_that_as_its_reason_not_silently_dropped():
    result = pre_filter(["AAA", "BBB"], {"AAA": daily_series(end=DAY, sessions=300)},
                        as_of=DAY)
    assert result.tickers == ["AAA"]
    assert [(r.ticker, r.reason) for r in result.excluded] == [
        ("BBB", "no daily bars from the provider")]


def test_the_pool_is_split_exhaustively():
    """Every name in goes into exactly one of the two lists — an unaccountable absence is
    indistinguishable from a bug."""
    names = ["AAA", "BBB", "CCC"]
    result = pre_filter(names, {"AAA": daily_series(end=DAY, sessions=300),
                                "BBB": daily_series(end=DAY, sessions=10)}, as_of=DAY)
    assert sorted(result.tickers + [r.ticker for r in result.excluded]) == names


def test_no_completed_session_at_all_is_excluded():
    row = pre_filter_one("AAA", [PriceBar(day=DAY, open=1, high=1, low=1, close=1,
                                          adj_close=1, volume=1)], as_of=DAY)
    assert row.passed is False
    assert "no completed daily session" in row.reason
