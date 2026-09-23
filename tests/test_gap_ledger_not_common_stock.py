"""GAP-UNIVERSE-1 — warrants, units, rights and preferreds are not common stock.

The market index classifies on EODHD's ``Type`` field, which calls all of them "Common
Stock": 196 of the 5,972 US rows on the 2026-09-22 index. They have no pre-market tape
worth screening and yfinance has no data for most of them, so each one cost a line of
provider error noise on every run.

The filter is on the TICKER, and the danger in a filter like that is the FALSE POSITIVE, so
that is what most of this file is about: a bare trailing ``-A``/``-B``/``-C``/``-V`` is a
genuine share class of common stock (BF-B, AKO-A, CIG-C, MKC-V) and dropping one would
silently remove a real company from every run. Only the ``P``/``PR`` marker before the class
letter makes it a preferred.

The index itself is NOT changed — Aristos reads the same table.
"""
from __future__ import annotations

from datetime import date, time

import pytest

from aristos_council.gap_ledger.bars import YFINANCE_LOGGER, quiet_yfinance
from aristos_council.gap_ledger.config import at_ny
from aristos_council.gap_ledger.run import format_report, run_screen
from aristos_council.gap_ledger.universe import (NO_DAILY_BARS, not_common_stock_reason,
                                                 split_common_stock)

from .gap_ledger_fakes import FakeBars, daily_series, intraday_window, prior_sessions

DAY = date(2026, 9, 22)
RUN_AT = at_ny(DAY, time(9, 0))


# --------------------------------------------------------------------------- #
# what is dropped
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ticker, kind", [
    ("ACHR-WS", "warrant"), ("ACHR-WT", "warrant"), ("FDXF-W", "warrant"),
    ("NE-WTA", "warrant"), ("MBGL-WI", "warrant"),
    ("AIIA-U", "unit"), ("BEBE-UN", "unit"),
    ("AIIA-R", "right"), ("CELG-RI", "right"),
    ("DCOM-P", "preferred"), ("BA-P-A", "preferred"), ("PNFP-PR-A", "preferred"),
    ("FITB-PA", "preferred"), ("AHL-PF", "preferred"), ("C-P-R", "preferred"),
])
def test_non_common_stock_tickers_are_named_by_kind(ticker, kind):
    assert not_common_stock_reason(ticker) == kind


def test_a_preferred_series_r_is_a_preferred_not_a_right():
    """C-P-R is Citigroup's preferred series R and ends in "-R". It is dropped either way,
    but the report prints the breakdown BY KIND, so a wrong label is a wrong number."""
    assert not_common_stock_reason("C-P-R") == "preferred"
    assert not_common_stock_reason("AIIA-R") == "right"


def test_the_five_letter_form_fires_only_when_its_root_is_in_the_pool():
    """TFINP is a preferred because TFIN is listed beside it. A five-letter word ending in
    P is just a ticker — "CHEAP" is the case that proves it, and it cost a test failure
    before this rule was corroborated rather than assumed."""
    assert not_common_stock_reason("TFIN-P") == "preferred"
    assert not_common_stock_reason("TFINP", known_roots={"TFIN"}) == "preferred"
    assert not_common_stock_reason("TFINP") == ""            # nothing to corroborate with
    assert not_common_stock_reason("CHEAP", known_roots={"TFIN"}) == ""
    assert not_common_stock_reason("TFIN") == ""


def test_a_five_letter_word_is_not_dropped_just_for_ending_in_p():
    kept, dropped = split_common_stock(["CHEAP", "SHARP", "SCALP", "TFIN", "TFINP"])
    assert kept == ["CHEAP", "SHARP", "SCALP", "TFIN"]
    assert dropped == [("TFINP", "preferred")]


# --------------------------------------------------------------------------- #
# what MUST survive — the false positives that would cost a real company
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ticker", [
    "BF-B", "BRK-B", "AKO-A", "AKO-B", "CIG-C", "MKC-V",   # genuine share classes
    "AAPL", "MSFT", "GOOGL", "TFIN", "WMT", "T", "F",      # ordinary names
    "PLTR", "SOFI", "SNAP",                                # four/five letters, no suffix
])
def test_genuine_common_stock_is_never_dropped(ticker):
    assert not_common_stock_reason(ticker) == ""


def test_a_bare_class_letter_is_not_a_preferred():
    """Only the P/PR marker BEFORE the class letter makes it one. BF-B is Brown-Forman."""
    assert not_common_stock_reason("BF-B") == ""
    assert not_common_stock_reason("BF-P-B") == "preferred"


def test_the_whole_live_index_splits_without_touching_a_share_class():
    """Over the real table, not a fixture: the drop must be ~200 of ~6,000, and every
    known multi-class name must survive."""
    pytest.importorskip("pandas")
    from aristos_council import market_index
    from aristos_council.gap_ledger.universe import common_stock_rows

    store = market_index.IndexStore(market_index.load_config()["root"])
    if not store.path.exists():                      # no local index on this machine
        pytest.skip("no market index built here")
    rows = common_stock_rows(store.load())
    kept, dropped = split_common_stock(
        sorted({(r.yahoo_ticker or r.ticker).upper() for r in rows}))
    assert 50 < len(dropped) < len(kept) / 10        # a filter, not a purge
    for survivor in ("BF-B", "AKO-A", "AKO-B", "CIG-C", "MKC-V"):
        if survivor in {(r.yahoo_ticker or r.ticker).upper() for r in rows}:
            assert survivor in kept


# --------------------------------------------------------------------------- #
# the split
# --------------------------------------------------------------------------- #
def test_the_split_is_exhaustive_and_order_preserving():
    offered = ["AAPL", "ACHR-WT", "BF-B", "AIIA-U", "MSFT"]
    kept, dropped = split_common_stock(offered)
    assert kept == ["AAPL", "BF-B", "MSFT"]
    assert dropped == [("ACHR-WT", "warrant"), ("AIIA-U", "unit")]
    assert len(kept) + len(dropped) == len(offered)


# --------------------------------------------------------------------------- #
# the run counts them
# --------------------------------------------------------------------------- #
def _world() -> FakeBars:
    return FakeBars(
        daily={"MOVE": daily_series(end=DAY, sessions=300, volume=3_000_000)},
        intraday={"MOVE": intraday_window(DAY, end=time(9, 0), price=55.0,
                                          volume_per_bar=10_000)
                          + prior_sessions(before=DAY, count=20,
                                           premarket_volume_per_bar=1_000)})


def _run(tmp_path, pool, **kwargs):
    bars = _world()
    return bars, run_screen(pool=pool, pool_source="a test list", daily=bars,
                            intraday=bars, day=DAY, run_at=RUN_AT, root=tmp_path, **kwargs)


def test_a_warrant_is_never_asked_about(tmp_path):
    """Dropped BEFORE any fetch — the point is not to ask the provider about a warrant."""
    bars, result = _run(tmp_path, ["MOVE", "ACHR-WT", "AIIA-U", "TFIN", "TFINP"])
    asked = {t for call in bars.daily_calls for t in call[0]}
    assert asked == {"MOVE", "TFIN"}
    assert len(result.not_common_stock) == 3


def test_the_run_counts_the_drop_rather_than_shrinking_quietly(tmp_path):
    _bars, result = _run(tmp_path, ["MOVE", "ACHR-WT", "AIIA-U", "TFIN", "TFINP"])
    assert result.pool_size == 5                     # what was OFFERED, not what survived
    assert dict(result.not_common_stock) == {"ACHR-WT": "warrant", "AIIA-U": "unit",
                                             "TFINP": "preferred"}


def test_the_report_says_how_many_and_of_what_kind(tmp_path):
    _bars, result = _run(tmp_path, ["MOVE", "ACHR-WT", "AIIA-U", "TFIN", "TFINP"])
    text = format_report(result)
    assert "3 not common stock" in text
    assert "1 preferred, 1 unit, 1 warrant" in text


def test_a_pool_with_no_warrants_says_nothing_about_them(tmp_path):
    _bars, result = _run(tmp_path, ["MOVE"])
    assert result.not_common_stock == []
    assert "not common stock" not in format_report(result)


def test_a_hand_written_list_is_filtered_too_and_told_so(tmp_path):
    """A --tickers file naming a warrant is not silently screened; it is counted."""
    _bars, result = _run(tmp_path, ["ACHR-WT"])
    assert result.not_common_stock == [("ACHR-WT", "warrant")]
    assert result.candidates == []


# --------------------------------------------------------------------------- #
# the provider noise
# --------------------------------------------------------------------------- #
def test_names_with_no_data_are_counted_once_instead_of_logged_each(tmp_path):
    bars = _world()
    result = run_screen(pool=["MOVE", "GONE", "ALSOGONE"], pool_source="t", daily=bars,
                        intraday=bars, day=DAY, run_at=RUN_AT, root=tmp_path)
    assert result.no_provider_data == 2
    assert "2 of them returned no data" in format_report(result)


def test_the_no_data_count_still_names_every_name_in_the_excluded_list(tmp_path):
    """The summary REPLACES the provider's noise, it does not replace the record: a name
    the provider served nothing for is still individually accounted for."""
    bars = _world()
    result = run_screen(pool=["MOVE", "GONE"], pool_source="t", daily=bars, intraday=bars,
                        day=DAY, run_at=RUN_AT, root=tmp_path)
    assert ("GONE", NO_DAILY_BARS) in result.excluded


def test_the_run_emits_one_progress_line_for_the_no_data_names(tmp_path):
    said: list[str] = []
    bars = _world()
    run_screen(pool=["MOVE", "GONE", "ALSOGONE"], pool_source="t", daily=bars,
               intraday=bars, day=DAY, run_at=RUN_AT, root=tmp_path, progress=said.append)
    assert sum("returned no data" in line for line in said) == 1
    assert "2 names returned no data" in said


def test_quiet_yfinance_silences_and_then_restores():
    import logging

    logger = logging.getLogger(YFINANCE_LOGGER)
    logger.setLevel(logging.WARNING)
    logger.propagate = True
    with quiet_yfinance():
        assert logger.level == logging.CRITICAL
        assert logger.propagate is False
    assert logger.level == logging.WARNING
    assert logger.propagate is True


def test_quiet_yfinance_restores_even_when_the_body_raises():
    import logging

    logger = logging.getLogger(YFINANCE_LOGGER)
    logger.setLevel(logging.WARNING)
    with pytest.raises(RuntimeError):
        with quiet_yfinance():
            raise RuntimeError("provider blew up")
    assert logger.level == logging.WARNING
