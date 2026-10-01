"""PAPER-TRADE-1 — the ``exit`` command: closes every position ``enter`` actually filled
with a market-on-close order, the right direction, and nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from aristos_council.paper_trade.config import NY, paths_for, stop_file
from aristos_council.paper_trade.exit import run_exit
from aristos_council.paper_trade.records import (EntryAttempt, EnterRun, load_exit_run,
                                                  save_enter_run)

DAY = date(2026, 9, 29)


@dataclass
class _FakeClient:
    connected: bool = False
    disconnected: bool = False
    placed: list = field(default_factory=list)
    fail_for: set = field(default_factory=set)

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.disconnected = True

    def place_moc(self, ticker, *, action, shares, order_ref):
        if ticker in self.fail_for:
            raise RuntimeError("gateway refused the order")
        self.placed.append((ticker, action, shares, order_ref))
        return ("trade", ticker)

    def place_marketable_limit(self, *a, **kw):              # pragma: no cover - unused here
        raise AssertionError("exit must never place an entry order")


class _Clock:
    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)


def _at(hour, minute):
    return datetime(2026, 9, 29, hour, minute, tzinfo=NY)


def _save_filled(root, *attempts):
    paths = paths_for(DAY, root)
    save_enter_run(paths, EnterRun(date=DAY.isoformat(), csv_seen_at_et="x",
                                   attempts=list(attempts)))


def test_the_kill_switch_places_nothing(tmp_path):
    stop_file(tmp_path).touch()
    _save_filled(tmp_path, EntryAttempt(date=DAY.isoformat(), ticker="AAA", direction="long",
                                        filled=True, shares=10))
    client = _FakeClient()
    run = run_exit(day=DAY, root=tmp_path, ibkr=client, now=_Clock(_at(15, 40)).now,
                   sleep=lambda s: None, wait_for_target=False)
    assert run.day_skipped and "STOP" in run.day_skip_reason
    assert not client.connected and client.placed == []


def test_no_enter_run_at_all_skips_with_a_reason(tmp_path):
    client = _FakeClient()
    run = run_exit(day=DAY, root=tmp_path, ibkr=client, now=_Clock(_at(15, 40)).now,
                   sleep=lambda s: None, wait_for_target=False)
    assert run.day_skipped and "no entries" in run.day_skip_reason
    assert not client.connected


def test_a_skipped_enter_day_means_exit_skips_too(tmp_path):
    paths = paths_for(DAY, tmp_path)
    save_enter_run(paths, EnterRun(date=DAY.isoformat(), day_skipped=True,
                                   day_skip_reason="kill switch"))
    client = _FakeClient()
    run = run_exit(day=DAY, root=tmp_path, ibkr=client, now=_Clock(_at(15, 40)).now,
                   sleep=lambda s: None, wait_for_target=False)
    assert run.day_skipped
    assert not client.connected


def test_nothing_filled_is_a_legitimate_no_op(tmp_path):
    _save_filled(tmp_path, EntryAttempt(date=DAY.isoformat(), ticker="AAA", direction="long",
                                        filled=False, skipped=True, skip_reason="not filled"))
    client = _FakeClient()
    run = run_exit(day=DAY, root=tmp_path, ibkr=client, now=_Clock(_at(15, 40)).now,
                   sleep=lambda s: None, wait_for_target=False)
    assert not run.day_skipped and run.exits == []
    assert not client.connected                               # never even connects for nothing


def test_a_filled_long_is_closed_with_a_sell_moc(tmp_path):
    _save_filled(tmp_path, EntryAttempt(date=DAY.isoformat(), ticker="AAA", direction="long",
                                        filled=True, shares=37))
    client = _FakeClient()
    run = run_exit(day=DAY, root=tmp_path, ibkr=client, now=_Clock(_at(15, 40)).now,
                   sleep=lambda s: None, wait_for_target=False)
    assert client.placed == [("AAA", "SELL", 37, f"papertrade-exit-{DAY.isoformat()}-AAA")]
    assert run.exits[0].moc_submitted


def test_a_filled_short_is_closed_with_a_buy_moc(tmp_path):
    _save_filled(tmp_path, EntryAttempt(date=DAY.isoformat(), ticker="BBB", direction="short",
                                        filled=True, shares=50))
    client = _FakeClient()
    run_exit(day=DAY, root=tmp_path, ibkr=client, now=_Clock(_at(15, 40)).now,
            sleep=lambda s: None, wait_for_target=False)
    assert client.placed == [("BBB", "BUY", 50, f"papertrade-exit-{DAY.isoformat()}-BBB")]


def test_an_unfilled_attempt_is_not_closed_only_the_filled_one_is(tmp_path):
    _save_filled(tmp_path,
                EntryAttempt(date=DAY.isoformat(), ticker="AAA", direction="long",
                            filled=True, shares=10),
                EntryAttempt(date=DAY.isoformat(), ticker="BBB", direction="short",
                            filled=False, skipped=True, skip_reason="not filled"))
    client = _FakeClient()
    run_exit(day=DAY, root=tmp_path, ibkr=client, now=_Clock(_at(15, 40)).now,
            sleep=lambda s: None, wait_for_target=False)
    assert [p[0] for p in client.placed] == ["AAA"]


def test_a_placement_failure_is_recorded_not_raised(tmp_path):
    _save_filled(tmp_path, EntryAttempt(date=DAY.isoformat(), ticker="AAA", direction="long",
                                        filled=True, shares=10))
    client = _FakeClient(fail_for={"AAA"})
    run = run_exit(day=DAY, root=tmp_path, ibkr=client, now=_Clock(_at(15, 40)).now,
                   sleep=lambda s: None, wait_for_target=False)
    assert not run.exits[0].moc_submitted and "gateway refused" in run.exits[0].error


def test_saved_state_can_be_read_back(tmp_path):
    _save_filled(tmp_path, EntryAttempt(date=DAY.isoformat(), ticker="AAA", direction="long",
                                        filled=True, shares=10))
    client = _FakeClient()
    run_exit(day=DAY, root=tmp_path, ibkr=client, now=_Clock(_at(15, 40)).now,
            sleep=lambda s: None, wait_for_target=False)
    loaded = load_exit_run(paths_for(DAY, tmp_path))
    assert loaded is not None and loaded.exits[0].ticker == "AAA"
