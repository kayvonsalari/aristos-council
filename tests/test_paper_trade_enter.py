"""PAPER-TRADE-1 — the ``enter`` command: direction mapping, sizing, the not-shortable
skip, the unfilled-cancel path, the kill switch, and waiting for today's Gap Ledger CSV.

A duck-typed fake stands in for ``PaperIBKR`` throughout (``ibkr=`` is injected directly),
so the real class — and the TEST-ISOLATION-1 guard on its construction — is never reached.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from aristos_council.paper_trade.config import NY, paths_for, stop_file
from aristos_council.paper_trade.enter import run_enter
from aristos_council.paper_trade.ibkr_paper import FillResult, QuoteReading, ShortableReading
from aristos_council.paper_trade.records import load_enter_run

DAY = date(2026, 9, 29)
GL_FIELDS = ("date", "ticker", "company", "group", "source", "gap_pct", "ib_gap_pct")


def _write_gl_csv(root, rows):
    path = root / f"{DAY.isoformat()}.csv"
    root.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=GL_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _gl_row(ticker, gap, company="Example Co"):
    return {"date": DAY.isoformat(), "ticker": ticker, "company": company,
           "group": "candidate", "source": "ibkr", "gap_pct": gap, "ib_gap_pct": gap}


@dataclass
class _FakeClient:
    quotes: dict = field(default_factory=dict)              # ticker -> QuoteReading
    shortable: dict = field(default_factory=dict)            # ticker -> ShortableReading
    fills: dict = field(default_factory=dict)                 # ticker -> FillResult
    connected: bool = False
    disconnected: bool = False
    placed: list = field(default_factory=list)
    cancelled: list = field(default_factory=list)

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.disconnected = True

    def quote_for(self, ticker):
        return self.quotes.get(ticker, QuoteReading(bid=9.9, ask=10.1, data_type="live"))

    def shortable_for(self, ticker):
        return self.shortable.get(ticker, ShortableReading(shortable=True, shares=10_000.0))

    def place_marketable_limit(self, ticker, *, action, shares, limit_price, order_ref):
        self.placed.append((ticker, action, shares, limit_price, order_ref))
        return ("trade", ticker)

    def wait_for_fill(self, trade, *, timeout_seconds):
        ticker = trade[1]
        return self.fills.get(ticker, FillResult(filled=True, fill_price=10.1,
                                                  fill_time=datetime(2026, 9, 29, 9, 33,
                                                                     tzinfo=NY),
                                                  status="Filled"))

    def cancel(self, trade):
        self.cancelled.append(trade[1])

    def place_moc(self, *a, **kw):                          # pragma: no cover - unused here
        raise AssertionError("enter must never place a MOC order")


class _Clock:
    def __init__(self, start: datetime):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)


def _at(hour, minute):
    return datetime(2026, 9, 29, hour, minute, tzinfo=NY)


# --------------------------------------------------------------------------- #
# the kill switch
# --------------------------------------------------------------------------- #
def test_the_kill_switch_places_nothing(tmp_path):
    (tmp_path / "paper_trade").mkdir()
    stop_file(tmp_path / "paper_trade").touch()
    gl_root = tmp_path / "gap_ledger"
    _write_gl_csv(gl_root, [_gl_row("AAA", 0.05)])
    client = _FakeClient()
    run = run_enter(day=DAY, root=tmp_path / "paper_trade", gap_ledger_root=gl_root,
                    ibkr=client, now=_Clock(_at(9, 32)).now, sleep=lambda s: None,
                    wait_for_target=False)
    assert run.day_skipped and "STOP" in run.day_skip_reason
    assert not client.connected and client.placed == []


# --------------------------------------------------------------------------- #
# direction mapping and sizing
# --------------------------------------------------------------------------- #
def test_a_gap_up_is_bought_a_gap_down_is_sold_short(tmp_path):
    gl_root = tmp_path / "gap_ledger"
    _write_gl_csv(gl_root, [_gl_row("UP", 0.05), _gl_row("DOWN", -0.06)])
    client = _FakeClient()
    run_enter(day=DAY, root=tmp_path, gap_ledger_root=gl_root, ibkr=client,
             now=_Clock(_at(9, 32)).now, sleep=lambda s: None, wait_for_target=False)
    actions = {ticker: action for ticker, action, *_ in client.placed}
    assert actions == {"UP": "BUY", "DOWN": "SELL"}


def test_shares_are_fixed_usd_10000_whole_shares(tmp_path):
    gl_root = tmp_path / "gap_ledger"
    _write_gl_csv(gl_root, [_gl_row("AAA", 0.05)])
    client = _FakeClient(quotes={"AAA": QuoteReading(bid=24.0, ask=30.0, data_type="live")})
    run = run_enter(day=DAY, root=tmp_path, gap_ledger_root=gl_root, ibkr=client,
                    now=_Clock(_at(9, 32)).now, sleep=lambda s: None, wait_for_target=False)
    # ask=30.0 is the reference for a long; 10_000 // 30.0 = 333 whole shares
    assert run.attempts[0].shares == 333
    ticker, action, shares, limit, ref = client.placed[0]
    assert shares == 333 and limit == 30.0 * 1.01


def test_a_name_whose_one_share_costs_more_than_the_position_is_skipped(tmp_path):
    gl_root = tmp_path / "gap_ledger"
    _write_gl_csv(gl_root, [_gl_row("PRICEY", 0.05)])
    client = _FakeClient(quotes={"PRICEY": QuoteReading(bid=9000, ask=15000, data_type="live")})
    run = run_enter(day=DAY, root=tmp_path, gap_ledger_root=gl_root, ibkr=client,
                    now=_Clock(_at(9, 32)).now, sleep=lambda s: None, wait_for_target=False)
    attempt = run.attempts[0]
    assert attempt.skipped and "costs more than" in attempt.skip_reason
    assert client.placed == []


# --------------------------------------------------------------------------- #
# shorting
# --------------------------------------------------------------------------- #
def test_a_name_ibkr_says_is_not_shortable_is_skipped_with_the_real_world_reason(tmp_path):
    gl_root = tmp_path / "gap_ledger"
    _write_gl_csv(gl_root, [_gl_row("NOSHORT", -0.08)])
    client = _FakeClient(shortable={"NOSHORT": ShortableReading(shortable=False, shares=0.0)})
    run = run_enter(day=DAY, root=tmp_path, gap_ledger_root=gl_root, ibkr=client,
                    now=_Clock(_at(9, 32)).now, sleep=lambda s: None, wait_for_target=False)
    attempt = run.attempts[0]
    assert attempt.skipped and attempt.skip_reason == "not shortable (real-world)"
    assert client.placed == []


def test_an_undetermined_shortable_flag_is_skipped_not_guessed(tmp_path):
    gl_root = tmp_path / "gap_ledger"
    _write_gl_csv(gl_root, [_gl_row("UNKNOWN", -0.08)])
    client = _FakeClient(shortable={"UNKNOWN": ShortableReading(shortable=None, shares=None)})
    run = run_enter(day=DAY, root=tmp_path, gap_ledger_root=gl_root, ibkr=client,
                    now=_Clock(_at(9, 32)).now, sleep=lambda s: None, wait_for_target=False)
    assert run.attempts[0].skipped and "undetermined" in run.attempts[0].skip_reason


def test_a_long_candidate_is_never_checked_for_shortability(tmp_path):
    gl_root = tmp_path / "gap_ledger"
    _write_gl_csv(gl_root, [_gl_row("UP", 0.05)])

    class _NoShortCheck(_FakeClient):
        def shortable_for(self, ticker):
            raise AssertionError("a long must never call shortable_for")

    run_enter(day=DAY, root=tmp_path, gap_ledger_root=gl_root, ibkr=_NoShortCheck(),
             now=_Clock(_at(9, 32)).now, sleep=lambda s: None, wait_for_target=False)


# --------------------------------------------------------------------------- #
# unfilled -> cancel
# --------------------------------------------------------------------------- #
def test_an_unfilled_order_is_cancelled_and_recorded(tmp_path):
    gl_root = tmp_path / "gap_ledger"
    _write_gl_csv(gl_root, [_gl_row("AAA", 0.05)])
    client = _FakeClient(fills={"AAA": FillResult(filled=False, status="Submitted")})
    run = run_enter(day=DAY, root=tmp_path, gap_ledger_root=gl_root, ibkr=client,
                    now=_Clock(_at(9, 32)).now, sleep=lambda s: None, wait_for_target=False)
    attempt = run.attempts[0]
    assert not attempt.filled and attempt.skip_reason == "not filled"
    assert client.cancelled == ["AAA"]


# --------------------------------------------------------------------------- #
# waiting for today's CSV
# --------------------------------------------------------------------------- #
def test_the_csv_arriving_late_is_traded_and_the_actual_time_is_recorded(tmp_path):
    gl_root = tmp_path / "gap_ledger"
    clock = _Clock(_at(9, 32))

    class _LateWriteSleep:
        """The CSV appears the first time anything sleeps — simulates Gap Ledger's run
        finishing a few seconds after enter started polling."""
        calls = 0

        def __call__(self, seconds):
            clock.t += timedelta(seconds=seconds)
            self.calls += 1
            if self.calls == 1:
                _write_gl_csv(gl_root, [_gl_row("AAA", 0.05)])

    client = _FakeClient()
    run = run_enter(day=DAY, root=tmp_path, gap_ledger_root=gl_root, ibkr=client,
                    now=clock.now, sleep=_LateWriteSleep(), wait_for_target=False)
    assert not run.day_skipped and len(run.attempts) == 1
    assert run.csv_seen_at_et                                # a real timestamp was recorded


def test_a_csv_that_never_arrives_by_the_deadline_skips_the_day(tmp_path):
    gl_root = tmp_path / "gap_ledger"               # never written
    clock = _Clock(_at(9, 32))
    client = _FakeClient()
    run = run_enter(day=DAY, root=tmp_path, gap_ledger_root=gl_root, ibkr=client,
                    now=clock.now, sleep=clock.sleep, wait_for_target=False)
    assert run.day_skipped and "never appeared" in run.day_skip_reason
    assert not client.connected


def test_a_csv_with_zero_ibkr_candidates_is_a_real_empty_day_not_a_skip(tmp_path):
    gl_root = tmp_path / "gap_ledger"
    _write_gl_csv(gl_root, [])                        # the file exists; nothing qualified
    client = _FakeClient()
    run = run_enter(day=DAY, root=tmp_path, gap_ledger_root=gl_root, ibkr=client,
                    now=_Clock(_at(9, 32)).now, sleep=lambda s: None, wait_for_target=False)
    assert not run.day_skipped and run.attempts == []


def test_saved_state_can_be_read_back(tmp_path):
    gl_root = tmp_path / "gap_ledger"
    _write_gl_csv(gl_root, [_gl_row("AAA", 0.05)])
    client = _FakeClient()
    run_enter(day=DAY, root=tmp_path, gap_ledger_root=gl_root, ibkr=client,
             now=_Clock(_at(9, 32)).now, sleep=lambda s: None, wait_for_target=False)
    loaded = load_enter_run(paths_for(DAY, tmp_path))
    assert loaded is not None and loaded.attempts[0].ticker == "AAA"
