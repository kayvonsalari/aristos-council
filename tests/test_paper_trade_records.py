"""PAPER-TRADE-1 — the record maths (pure) and the enter/exit JSON state round-trip."""
from __future__ import annotations

import pytest

from aristos_council.paper_trade.config import paths_for
from aristos_council.paper_trade.records import (EntryAttempt, EnterRun, ExitAttempt, ExitRun,
                                                  entry_cost_vs_open_pct, gross_result,
                                                  load_enter_run, load_exit_run, net_result,
                                                  save_enter_run, save_exit_run)


# --------------------------------------------------------------------------- #
# entry_cost_vs_open_pct — signed so positive always means "worse for us"
# --------------------------------------------------------------------------- #
def test_a_long_that_paid_more_than_the_open_reads_positive_worse():
    assert entry_cost_vs_open_pct("long", 101.0, 100.0) == pytest.approx(0.01)


def test_a_long_that_paid_less_than_the_open_reads_negative_better():
    assert entry_cost_vs_open_pct("long", 99.0, 100.0) == pytest.approx(-0.01)


def test_a_short_that_sold_for_less_than_the_open_reads_positive_worse():
    assert entry_cost_vs_open_pct("short", 99.0, 100.0) == pytest.approx(0.01)


def test_a_short_that_sold_for_more_than_the_open_reads_negative_better():
    assert entry_cost_vs_open_pct("short", 101.0, 100.0) == pytest.approx(-0.01)


def test_a_missing_price_is_none_never_a_manufactured_zero():
    assert entry_cost_vs_open_pct("long", None, 100.0) is None
    assert entry_cost_vs_open_pct("long", 101.0, None) is None


# --------------------------------------------------------------------------- #
# gross / net result
# --------------------------------------------------------------------------- #
def test_long_gross_result_is_exit_minus_entry_times_shares():
    assert gross_result("long", 100.0, 105.0, 20) == pytest.approx(100.0)


def test_short_gross_result_is_entry_minus_exit_times_shares():
    assert gross_result("short", 100.0, 95.0, 20) == pytest.approx(100.0)
    assert gross_result("short", 100.0, 105.0, 20) == pytest.approx(-100.0)


def test_gross_result_is_none_without_both_fills():
    assert gross_result("long", None, 105.0, 20) is None
    assert gross_result("long", 100.0, None, 20) is None


def test_net_result_subtracts_both_commissions():
    assert net_result(100.0, 1.0, 1.0) == pytest.approx(98.0)


def test_net_result_treats_a_missing_commission_as_zero_only_when_gross_is_known():
    assert net_result(100.0, None, None) == pytest.approx(100.0)
    assert net_result(None, 1.0, 1.0) is None


# --------------------------------------------------------------------------- #
# the JSON state round-trip ``enter``/``exit``/``record`` share
# --------------------------------------------------------------------------- #
def test_an_enter_run_round_trips_through_json(tmp_path):
    paths = paths_for(__import__("datetime").date(2026, 9, 29), tmp_path)
    run = EnterRun(date="2026-09-29", csv_seen_at_et="2026-09-29T09:32:00-04:00",
                   attempts=[EntryAttempt(date="2026-09-29", ticker="AAA", direction="long",
                                          filled=True, fill_price=10.5)])
    save_enter_run(paths, run)
    back = load_enter_run(paths)
    assert back.date == "2026-09-29" and len(back.attempts) == 1
    assert back.attempts[0].ticker == "AAA" and back.attempts[0].fill_price == 10.5


def test_a_skipped_day_round_trips_its_reason(tmp_path):
    paths = paths_for(__import__("datetime").date(2026, 9, 29), tmp_path)
    run = EnterRun(date="2026-09-29", day_skipped=True, day_skip_reason="kill switch")
    save_enter_run(paths, run)
    assert load_enter_run(paths).day_skip_reason == "kill switch"


def test_loading_an_enter_run_that_was_never_written_is_none(tmp_path):
    paths = paths_for(__import__("datetime").date(2026, 9, 29), tmp_path)
    assert load_enter_run(paths) is None


def test_an_exit_run_round_trips_through_json(tmp_path):
    paths = paths_for(__import__("datetime").date(2026, 9, 29), tmp_path)
    run = ExitRun(date="2026-09-29",
                  exits=[ExitAttempt(date="2026-09-29", ticker="AAA", direction="long",
                                     shares=10, moc_submitted=True)])
    save_exit_run(paths, run)
    back = load_exit_run(paths)
    assert back.exits[0].ticker == "AAA" and back.exits[0].moc_submitted is True
