"""PAPER-TRADE-1 — the ``record`` command: joins ``enter``'s own fill, IBKR's execution
history (for the exit fill and both legs' commissions) and the official open into one CSV
row per name, including every skip with its reason.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime

import pytest

from aristos_council.paper_trade.config import NY, paths_for
from aristos_council.paper_trade.record import run_record
from aristos_council.paper_trade.records import (EntryAttempt, EnterRun, ExitAttempt, ExitRun,
                                                  save_enter_run, save_exit_run)

DAY = date(2026, 9, 29)


@dataclass
class _Execution:
    price: float
    time: datetime
    orderRef: str


@dataclass
class _Commission:
    commission: float


@dataclass
class _Fill:
    execution: _Execution
    commissionReport: _Commission


@dataclass
class _FakeClient:
    opens: dict = field(default_factory=dict)
    fills: list = field(default_factory=list)
    connected: bool = False
    disconnected: bool = False

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.disconnected = True

    def official_open(self, ticker, day):
        return self.opens.get(ticker)

    def executions_for(self, day):
        return list(self.fills)


def _entry_ref(ticker):
    return f"papertrade-entry-{DAY.isoformat()}-{ticker}"


def _exit_ref(ticker):
    return f"papertrade-exit-{DAY.isoformat()}-{ticker}"


def _save(root, attempts, exits=()):
    paths = paths_for(DAY, root)
    save_enter_run(paths, EnterRun(date=DAY.isoformat(), csv_seen_at_et="x",
                                   attempts=list(attempts)))
    save_exit_run(paths, ExitRun(date=DAY.isoformat(), exits=list(exits)))
    return paths


def test_no_enter_run_returns_none(tmp_path):
    assert run_record(day=DAY, root=tmp_path, ibkr=_FakeClient()) is None


def test_a_full_round_trip_long_position(tmp_path):
    _save(tmp_path,
         [EntryAttempt(date=DAY.isoformat(), ticker="AAA", direction="long", shares=100,
                      quote_ask=10.0, quote_data_type="live", order_ref=_entry_ref("AAA"),
                      filled=True, fill_price=10.1, fill_time_et="2026-09-29T09:33:00-04:00")],
         [ExitAttempt(date=DAY.isoformat(), ticker="AAA", direction="long", shares=100,
                     order_ref=_exit_ref("AAA"), moc_submitted=True)])
    client = _FakeClient(
        opens={"AAA": 10.0},
        fills=[_Fill(execution=_Execution(price=10.1, time=datetime(2026, 9, 29, 9, 33),
                                          orderRef=_entry_ref("AAA")),
                    commissionReport=_Commission(commission=1.0)),
              _Fill(execution=_Execution(price=10.8, time=datetime(2026, 9, 29, 16, 0),
                                         orderRef=_exit_ref("AAA")),
                   commissionReport=_Commission(commission=1.0))])
    rows = run_record(day=DAY, root=tmp_path, ibkr=client)
    assert len(rows) == 1
    row = rows[0]
    assert row["official_open"] == 10.0
    assert row["entry_fill_price"] == 10.1
    assert row["entry_cost_vs_open_pct"] == pytest.approx((10.1 - 10.0) / 10.0)
    assert row["exit_fill_price"] == 10.8
    assert row["gross_result"] == pytest.approx((10.8 - 10.1) * 100)
    assert row["net_result"] == pytest.approx((10.8 - 10.1) * 100 - 2.0)
    # an INJECTED client is never disconnected by the caller — same convention enter/exit
    # use: only a client this command opened itself gets closed.
    assert client.connected and not client.disconnected


def test_a_short_position_closes_with_a_buy_and_nets_correctly(tmp_path):
    _save(tmp_path,
         [EntryAttempt(date=DAY.isoformat(), ticker="BBB", direction="short", shares=50,
                      order_ref=_entry_ref("BBB"), filled=True, fill_price=20.0)],
         [ExitAttempt(date=DAY.isoformat(), ticker="BBB", direction="short", shares=50,
                     order_ref=_exit_ref("BBB"), moc_submitted=True)])
    client = _FakeClient(
        opens={"BBB": 20.0},
        fills=[_Fill(execution=_Execution(price=20.0, time=datetime(2026, 9, 29, 9, 33),
                                          orderRef=_entry_ref("BBB")),
                    commissionReport=_Commission(commission=0.5)),
              _Fill(execution=_Execution(price=18.0, time=datetime(2026, 9, 29, 16, 0),
                                         orderRef=_exit_ref("BBB")),
                   commissionReport=_Commission(commission=0.5))])
    rows = run_record(day=DAY, root=tmp_path, ibkr=client)
    row = rows[0]
    assert row["gross_result"] == pytest.approx((20.0 - 18.0) * 50)   # short profits on a fall
    assert row["net_result"] == pytest.approx((20.0 - 18.0) * 50 - 1.0)


def test_a_skipped_entry_carries_its_reason_and_no_financial_fields(tmp_path):
    _save(tmp_path, [EntryAttempt(date=DAY.isoformat(), ticker="CCC", direction="short",
                                  skipped=True, skip_reason="not shortable (real-world)")])
    client = _FakeClient()
    rows = run_record(day=DAY, root=tmp_path, ibkr=client)
    row = rows[0]
    assert row["skipped"] == "true" and row["skip_reason"] == "not shortable (real-world)"
    assert row["entry_fill_price"] is None and row["gross_result"] is None


def test_a_filled_entry_with_no_exit_yet_leaves_exit_fields_blank(tmp_path):
    _save(tmp_path, [EntryAttempt(date=DAY.isoformat(), ticker="DDD", direction="long",
                                  shares=10, order_ref=_entry_ref("DDD"), filled=True,
                                  fill_price=5.0)])
    client = _FakeClient(opens={"DDD": 5.0})
    rows = run_record(day=DAY, root=tmp_path, ibkr=client)
    row = rows[0]
    assert row["exit_fill_price"] is None and row["gross_result"] is None
    assert row["entry_cost_vs_open_pct"] == pytest.approx(0.0)


def test_the_csv_is_actually_written_to_the_right_path(tmp_path):
    _save(tmp_path, [EntryAttempt(date=DAY.isoformat(), ticker="AAA", direction="long",
                                  skipped=True, skip_reason="no usable quote")])
    run_record(day=DAY, root=tmp_path, ibkr=_FakeClient())
    path = tmp_path / f"{DAY.isoformat()}.csv"
    assert path.is_file()
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["ticker"] == "AAA" and rows[0]["skip_reason"] == "no usable quote"


def test_no_attempts_at_all_connects_to_nothing(tmp_path):
    paths = paths_for(DAY, tmp_path)
    save_enter_run(paths, EnterRun(date=DAY.isoformat(), day_skipped=True,
                                   day_skip_reason="kill switch"))
    client = _FakeClient()
    rows = run_record(day=DAY, root=tmp_path, ibkr=client)
    assert rows == [] and not client.connected
