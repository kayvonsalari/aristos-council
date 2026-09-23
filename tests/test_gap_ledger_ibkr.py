"""GAP-IBKR-1 — the IB adapter: read-only, paced, and honest about units.

No test here opens a socket. ``gap_ledger.ibkr.IBKRBars`` is registered with the
TEST-ISOLATION-1 guard (constructing the real one inside the suite raises), and the fake
below records what it was ASKED — which is the interesting half, since the properties that
matter are the shape of the request and the pacing around it, not the shape of the answer.

The pacer is exercised over a ten-minute window in microseconds, because ``now`` and
``sleep`` are injected. A pacing test that actually waited ten minutes would be a test nobody
runs.

The whole file carries ``pytest.mark.real_adapter``: it constructs the REAL ``IBKRBars`` on
purpose, which TEST-ISOLATION-1 guards, and that marker is the repo's written opt-out for
tests that are about a provider (docs/TESTING.md). No socket opens — the ``IB`` handle is
injected — and the guard itself is proved to be in place by
``test_gap_ledger_ibkr_guard.py``, which runs without the marker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import pytest

from aristos_council.gap_ledger import ibkr
from aristos_council.gap_ledger.config import NY, at_ny

# The adapter reaches for ``ib_async`` at call time — ``_contract`` imports ``Stock`` — so an
# injected IB handle is not enough to run these without the module. It is an OPTIONAL extra
# (``pip install -e ".[ibkr]"``), so a clean checkout skips this file rather than failing it;
# CI installs the extra, so there these run. Declared the way the rest of the repo declares an
# optional dependency (docs/TESTING.md).
pytest.importorskip("ib_async")

pytestmark = pytest.mark.real_adapter

DAY = date(2026, 9, 22)
TOMORROW = DAY + timedelta(days=1)


# --------------------------------------------------------------------------- #
# a fake gateway
# --------------------------------------------------------------------------- #
@dataclass
class _RawBar:
    """The shape ib_async hands back (``BarData``): a datetime and five numbers."""

    date: datetime
    open: float = 10.0
    high: float = 10.0
    low: float = 10.0
    close: float = 10.0
    volume: float = 0.0


@dataclass
class _FakeIB:
    """Records every request, and answers from ``series`` keyed by symbol."""

    series: dict = field(default_factory=dict)
    unknown: set = field(default_factory=set)
    requests: list = field(default_factory=list)
    qualified: list = field(default_factory=list)
    raise_with: list = field(default_factory=list)      # popped per request
    disconnected: bool = False

    def isConnected(self):
        return not self.disconnected

    def qualifyContracts(self, contract):
        self.qualified.append(contract.symbol)
        return [] if contract.symbol in self.unknown else [contract]

    def reqHistoricalData(self, contract, **kwargs):
        self.requests.append({"symbol": contract.symbol, **kwargs})
        if self.raise_with:
            raise RuntimeError(self.raise_with.pop(0))
        return list(self.series.get(contract.symbol, []))

    def disconnect(self):
        self.disconnected = True


@dataclass
class _Clock:
    """A monotonic clock that only moves when something sleeps."""

    t: float = 1_000.0
    slept: list = field(default_factory=list)

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds

    def tick(self, seconds: float) -> None:
        self.t += seconds


def _adapter(ib: _FakeIB, *, clock: _Clock | None = None) -> ibkr.IBKRBars:
    clock = clock or _Clock()
    pacer = ibkr._Pacer(now=clock.now, sleep=clock.sleep)
    adapter = ibkr.IBKRBars(ib=ib, pacer=pacer, sleep=clock.sleep)
    adapter._clock = clock                               # for the tests' convenience
    return adapter


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def test_the_defaults_are_the_local_live_gateway_and_a_distinct_client_id():
    """17, away from the 0/1 a manual TWS or Gateway session takes — two clients sharing an
    id is not an error you see, the gateway just drops one."""
    assert ibkr.DEFAULT_HOST == "127.0.0.1"
    assert ibkr.DEFAULT_PORT == 4001
    assert ibkr.DEFAULT_CLIENT_ID == 17


def test_the_environment_overrides_the_defaults(monkeypatch):
    monkeypatch.setenv("IBKR_HOST", "10.0.0.5")
    monkeypatch.setenv("IBKR_PORT", "4002")
    monkeypatch.setenv("IBKR_CLIENT_ID", "23")
    adapter = _adapter(_FakeIB())
    assert (adapter.host, adapter.port, adapter.client_id) == ("10.0.0.5", 4002, 23)


def test_an_absent_environment_still_works(monkeypatch):
    """The IBKR_* values are configuration, not secrets, so a machine with no .env entries
    still reaches the local gateway."""
    for key in ("IBKR_HOST", "IBKR_PORT", "IBKR_CLIENT_ID"):
        monkeypatch.delenv(key, raising=False)
    adapter = _adapter(_FakeIB())
    assert (adapter.host, adapter.port, adapter.client_id) == ("127.0.0.1", 4001, 17)


# --------------------------------------------------------------------------- #
# the request
# --------------------------------------------------------------------------- #
def test_the_request_asks_for_trades_five_minute_bars_outside_regular_hours():
    """TRADES because volume only exists on trades; useRTH=False because the whole point is
    the session before the bell."""
    ib = _FakeIB(series={"AAPL": [_RawBar(at_ny(DAY, time(4, 0)))]})
    _adapter(ib).bars_for("AAPL", start=DAY, end=TOMORROW)
    sent = ib.requests[0]
    assert sent["whatToShow"] == "TRADES"
    assert sent["barSizeSetting"] == "5 mins"
    assert sent["useRTH"] is False


def test_the_window_is_one_request_per_name():
    ib = _FakeIB(series={t: [_RawBar(at_ny(DAY, time(4, 0)))] for t in ("AAA", "BBB")})
    _adapter(ib).intraday_bars(["AAA", "BBB"], start=DAY, end=TOMORROW)
    assert [r["symbol"] for r in ib.requests] == ["AAA", "BBB"]


def test_the_end_datetime_is_timezone_aware_new_york():
    """A naive stamp is read by the gateway in its own timezone, which is how a pre-market
    window quietly becomes somebody else's afternoon."""
    stamp = ibkr.end_datetime_for(TOMORROW)
    assert stamp.tzinfo is not None
    assert stamp.astimezone(NY).date() == TOMORROW
    ib = _FakeIB(series={"AAA": [_RawBar(at_ny(DAY, time(4, 0)))]})
    _adapter(ib).bars_for("AAA", start=DAY, end=TOMORROW)
    assert ib.requests[0]["endDateTime"].tzinfo is not None


@pytest.mark.parametrize("start, end, duration", [
    (DAY, TOMORROW, "1 D"),
    (DAY, DAY, "1 D"),                               # never "0 D"
    (DAY - timedelta(days=30), TOMORROW, "31 D"),
])
def test_the_duration_covers_the_requested_window(start, end, duration):
    assert ibkr.duration_for(start, end) == duration


def test_a_symbol_ib_does_not_know_is_an_absence_not_an_exception():
    ib = _FakeIB(unknown={"NOSUCH"})
    assert _adapter(ib).bars_for("NOSUCH", start=DAY, end=TOMORROW) == []
    assert ib.requests == []                          # never even asked for history


def test_a_name_with_no_trades_is_absent_from_the_mapping():
    """How every other reader in this package says "nothing for this one"."""
    ib = _FakeIB(series={"AAA": [_RawBar(at_ny(DAY, time(4, 0)))], "BBB": []})
    out = _adapter(ib).intraday_bars(["AAA", "BBB"], start=DAY, end=TOMORROW)
    assert set(out) == {"AAA"}


def test_one_failing_symbol_does_not_cost_the_others():
    ib = _FakeIB(series={t: [_RawBar(at_ny(DAY, time(4, 0)))] for t in ("AAA", "BBB")},
                 raise_with=["something broke"])
    out = _adapter(ib).intraday_bars(["AAA", "BBB"], start=DAY, end=TOMORROW)
    assert set(out) == {"BBB"}


# --------------------------------------------------------------------------- #
# translating the answer
# --------------------------------------------------------------------------- #
def test_bars_come_back_in_new_york_time_and_sorted():
    ib = _FakeIB(series={"AAA": [_RawBar(at_ny(DAY, time(8, 0)), close=11.0),
                                 _RawBar(at_ny(DAY, time(4, 0)), close=10.0)]})
    bars = _adapter(ib).bars_for("AAA", start=DAY, end=TOMORROW)
    assert [b.start.astimezone(NY).time() for b in bars] == [time(4, 0), time(8, 0)]
    assert all(b.start.tzinfo is not None for b in bars)


def test_a_naive_stamp_from_the_gateway_is_read_as_new_york():
    ib = _FakeIB(series={"AAA": [_RawBar(datetime(2026, 9, 22, 4, 0))]})
    bar = _adapter(ib).bars_for("AAA", start=DAY, end=TOMORROW)[0]
    assert bar.start.utcoffset() == at_ny(DAY, time(4, 0)).utcoffset()


def test_the_volume_arrives_and_is_not_silently_rescaled():
    """MEASURED against consolidated daily volume on three days: IB serves SHARES, but only
    0.34x-0.67x of the tape and the fraction moves. So nothing rescales it, the unit is named,
    and the figure is only ever meaningful against another IB figure — which is why the
    relative-volume leg (a ratio of IB windows) is the right construction and an absolute
    threshold is not."""
    ib = _FakeIB(series={"AAA": [_RawBar(at_ny(DAY, time(4, 0)), volume=1234.0)]})
    adapter = _adapter(ib)
    assert adapter.bars_for("AAA", start=DAY, end=TOMORROW)[0].volume == 1234
    assert "shares" in ibkr.VOLUME_UNIT and "partial" in ibkr.VOLUME_UNIT
    # The adapter names its unit, so a consumer can record it rather than assume shares.
    assert adapter.volume_unit == ibkr.VOLUME_UNIT


def test_a_bar_missing_a_price_is_dropped_rather_than_defaulted():
    """One invented number poisons every average downstream."""
    good = _RawBar(at_ny(DAY, time(4, 0)), close=10.0)
    broken = _RawBar(at_ny(DAY, time(4, 5)), close=float("nan"))
    ib = _FakeIB(series={"AAA": [good, broken]})
    assert len(_adapter(ib).bars_for("AAA", start=DAY, end=TOMORROW)) == 1


def test_a_negative_or_absent_volume_reads_as_zero_not_as_nonsense():
    ib = _FakeIB(series={"AAA": [_RawBar(at_ny(DAY, time(4, 0)), volume=-5.0)]})
    assert _adapter(ib).bars_for("AAA", start=DAY, end=TOMORROW)[0].volume == 0


def test_an_unparseable_stamp_is_dropped():
    assert ibkr.to_intraday_bar(_RawBar(date="not a datetime")) is None


# --------------------------------------------------------------------------- #
# pacing — the whole ten-minute window, in microseconds
# --------------------------------------------------------------------------- #
def test_an_identical_request_waits_out_the_fifteen_second_cooldown():
    clock = _Clock()
    pacer = ibkr._Pacer(now=clock.now, sleep=clock.sleep)
    pacer.before("same")
    assert pacer.before("same") == pytest.approx(15.0)
    assert clock.slept == [pytest.approx(15.0)]


def test_a_different_request_does_not_wait():
    clock = _Clock()
    pacer = ibkr._Pacer(now=clock.now, sleep=clock.sleep)
    pacer.before("one")
    assert pacer.before("two") == 0.0
    assert clock.slept == []


def test_an_identical_request_after_the_cooldown_does_not_wait():
    clock = _Clock()
    pacer = ibkr._Pacer(now=clock.now, sleep=clock.sleep)
    pacer.before("same")
    clock.tick(15.1)
    assert pacer.before("same") == 0.0


def test_the_sixty_first_request_waits_for_the_window_to_age_out():
    clock = _Clock()
    pacer = ibkr._Pacer(now=clock.now, sleep=clock.sleep)
    for n in range(60):
        pacer.before(f"key{n}")
        clock.tick(1.0)                                 # a minute's worth of requests
    waited = pacer.before("key60")
    assert waited == pytest.approx(600.0 - 60.0)
    assert clock.slept == [pytest.approx(540.0)]


def test_the_window_slides_rather_than_resetting():
    clock = _Clock()
    pacer = ibkr._Pacer(now=clock.now, sleep=clock.sleep)
    for n in range(60):
        pacer.before(f"key{n}")
    clock.tick(600.1)                                   # the whole window has aged out
    assert pacer.before("key60") == 0.0


def test_wait_for_changes_nothing():
    """It is a question, not an action — asking twice must give the same answer."""
    clock = _Clock()
    pacer = ibkr._Pacer(now=clock.now, sleep=clock.sleep)
    pacer.before("same")
    assert pacer.wait_for("same") == pytest.approx(pacer.wait_for("same"))
    assert clock.slept == []


def test_the_limits_are_ibkrs_stated_ones():
    assert ibkr.MAX_REQUESTS_PER_WINDOW == 60
    assert ibkr.PACING_WINDOW_SECONDS == 600.0
    assert ibkr.IDENTICAL_REQUEST_COOLDOWN == 15.0


def test_every_request_goes_through_the_pacer():
    clock = _Clock()
    ib = _FakeIB(series={"AAA": [_RawBar(at_ny(DAY, time(4, 0)))]})
    adapter = _adapter(ib, clock=clock)
    adapter.bars_for("AAA", start=DAY, end=TOMORROW)
    adapter.bars_for("AAA", start=DAY, end=TOMORROW)     # identical → cooldown
    assert clock.slept == [pytest.approx(15.0)]


def test_the_request_key_distinguishes_ticker_and_window():
    a = ibkr.request_key("AAA", DAY, TOMORROW)
    assert a == ibkr.request_key("aaa", DAY, TOMORROW)   # case is not a difference
    assert a != ibkr.request_key("BBB", DAY, TOMORROW)
    assert a != ibkr.request_key("AAA", DAY - timedelta(days=20), TOMORROW)


# --------------------------------------------------------------------------- #
# a pacing violation waits and retries, never aborts
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("message", [
    "Historical Market Data Service error message:Historical data request pacing violation",
    "Max rate of messages exceeded",
    "too many requests in a short period",
])
def test_a_gateway_pacing_complaint_is_recognised(message):
    assert ibkr.looks_like_pacing(message)


def test_something_that_is_not_pacing_is_not_treated_as_pacing():
    assert not ibkr.looks_like_pacing("No security definition has been found")


def test_a_pacing_violation_waits_and_then_succeeds():
    """A screen that dies at name 40 of 60 has lost the morning."""
    clock = _Clock()
    ib = _FakeIB(series={"AAA": [_RawBar(at_ny(DAY, time(4, 0)))]},
                 raise_with=["Historical data request pacing violation"])
    bars = _adapter(ib, clock=clock).bars_for("AAA", start=DAY, end=TOMORROW)
    assert len(bars) == 1                               # it got the data in the end
    assert ibkr.PACING_BACKOFF_SECONDS[0] in clock.slept
    assert len(ib.requests) == 2


def test_repeated_pacing_violations_back_off_further_and_then_give_up_quietly():
    """Giving up on ONE name returns nothing for it; the run carries on and the screen
    reports it as a name it has no data for."""
    clock = _Clock()
    ib = _FakeIB(series={"AAA": [_RawBar(at_ny(DAY, time(4, 0)))]},
                 raise_with=["pacing violation"] * 10)
    assert _adapter(ib, clock=clock).bars_for("AAA", start=DAY, end=TOMORROW) == []
    assert clock.slept == [pytest.approx(s) for s in ibkr.PACING_BACKOFF_SECONDS]
    assert len(ib.requests) == 1 + len(ibkr.PACING_BACKOFF_SECONDS)


def test_a_non_pacing_error_is_not_retried():
    """Retrying a symbol IB has never heard of is a way to spend ten minutes learning
    nothing."""
    clock = _Clock()
    ib = _FakeIB(raise_with=["No security definition has been found"])
    assert _adapter(ib, clock=clock).bars_for("AAA", start=DAY, end=TOMORROW) == []
    assert len(ib.requests) == 1
    assert clock.slept == []


# --------------------------------------------------------------------------- #
# read-only, and hanging up
# --------------------------------------------------------------------------- #
def test_the_connection_is_read_only(monkeypatch):
    """Not decoration: a read-only client cannot transmit an order even if some future
    caller asked it to."""
    seen = {}

    class _Client:
        def connect(self, host, port, clientId=None, readonly=None, timeout=None):
            seen.update(host=host, port=port, clientId=clientId, readonly=readonly)

        def isConnected(self):
            return True

    import sys
    import types
    module = types.ModuleType("ib_async")
    module.IB = _Client
    module.Stock = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "ib_async", module)

    adapter = ibkr.IBKRBars()
    adapter._client()
    assert seen["readonly"] is True
    assert seen["clientId"] == 17


def test_an_unreachable_gateway_says_what_to_check(monkeypatch):
    class _Client:
        def connect(self, *a, **k):
            raise OSError("connection refused")

    import sys
    import types
    module = types.ModuleType("ib_async")
    module.IB = _Client
    monkeypatch.setitem(sys.modules, "ib_async", module)

    adapter = ibkr.IBKRBars()
    with pytest.raises(ibkr.IBKRUnavailable) as caught:
        adapter._client()
    message = str(caught.value)
    assert "127.0.0.1:4001" in message and "clientId=17" in message
    assert "gateway running" in message


def test_the_adapter_hangs_up_on_exit():
    ib = _FakeIB()
    with _adapter(ib) as adapter:
        assert adapter.connected
    assert ib.disconnected


def test_a_failed_disconnect_is_not_the_thing_that_ends_a_run():
    class _Stubborn(_FakeIB):
        def disconnect(self):
            raise RuntimeError("socket already gone")

    adapter = _adapter(_Stubborn())
    adapter.disconnect()                                 # must not raise
    assert not adapter.connected


def test_quotes_are_an_honest_nothing():
    """IB can serve a book; the brief does not ask for one. An empty mapping is what
    ``screen.spread_flag`` already reads as *spread unknown*."""
    assert _adapter(_FakeIB()).quotes(["AAA"]) == {}
