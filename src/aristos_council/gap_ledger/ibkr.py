"""GAP-IBKR-1 — Interactive Brokers as the source of REAL pre-market volume.

Why this exists: yfinance serves pre-market PRICES but never pre-market VOLUME (probed
2026-09-22 — every extended-hours bar comes back ``Volume == 0``), so Gap Ledger's
relative-volume leg has never run and the trust gate in ``screen.py`` is a workaround built
from tape density. IB's ``reqHistoricalData(useRTH=False)`` returns 5-minute TRADES bars WITH
volume, which is the reading the screen was designed around.

This module is a READER. ``connect(readonly=True)`` is not decoration: a read-only client
cannot transmit an order even if some future caller asked it to, and that is the guarantee
worth having in a repo where nothing should ever place a trade.

Three things it is careful about, because each has bitten someone:

**A distinct client id.** ``IBKR_CLIENT_ID`` defaults to 17, away from the 0/1 a manual TWS
or Gateway session takes. Two clients sharing an id is not an error you see — the gateway
drops one of them.

**Pacing.** IB's historical limits are not advisory: exceed them and requests start failing
for the rest of the window. ``_Pacer`` holds the two the brief names — no more than 60
requests per 10 minutes, and no identical request inside 15 seconds — and a pacing violation
from the gateway WAITS and retries rather than aborting. A screen that dies at name 40 of 60
has lost the morning.

**Volume units, MEASURED rather than assumed.** IB historical stock volume is in SHARES —
but it is a PARTIAL count. Summing IB's regular-session bars and comparing against the
consolidated daily volume, on 2026-09-18/21/22:

    AAPL 2026-09-18  IB 29,696,415  vs  86,588,200   ratio 0.343
    AAPL 2026-09-21  IB 20,110,489  vs  34,999,200   ratio 0.575
    AAPL 2026-09-22  IB 23,780,283  vs  40,599,377   ratio 0.586
    VKTX 2026-09-22  IB 27,358,635  vs  40,950,705   ratio 0.668

Same order of magnitude as shares (so it is not round lots, which the first draft of this
module wrongly said), but only a third to two thirds of the tape, and the fraction MOVES from
day to day. Two consequences, and they shape how this data may be used:

* an ABSOLUTE pre-market volume threshold on IB numbers is not safe, and an IB figure must
  never be compared against a figure from another provider;
* a RATIO of one IB window to the median of twenty other IB windows is the right
  construction, because the coverage factor largely cancels — though not perfectly, since
  it varies by day, so the ratio carries more noise than a full tape would give.

Nothing here rescales anything. The figure is stored exactly as served and the unit is named
in ``VOLUME_UNIT`` so a consumer records it instead of assuming.
"""
from __future__ import annotations

import logging
import os
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional, Sequence

from .bars import IntradayBar, Quote
from .config import NY

_log = logging.getLogger(__name__)

# The gateway, and the client id that keeps this out of a manual session's way.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4001                      # live Gateway; 4002 is paper, 7496/7497 are TWS
DEFAULT_CLIENT_ID = 17

# IB historical pacing, as the brief states it.
MAX_REQUESTS_PER_WINDOW = 60
PACING_WINDOW_SECONDS = 600.0
IDENTICAL_REQUEST_COOLDOWN = 15.0
# How long to wait when the GATEWAY says we paced badly anyway, and how many times to try.
# Ten minutes is the length of IB's own window, so one full wait clears it.
PACING_BACKOFF_SECONDS = (30.0, 120.0, 600.0)

# What a request asks for. TRADES (not MIDPOINT) because volume only exists on trades, and
# useRTH=False because the whole point is the session before the bell.
WHAT_TO_SHOW = "TRADES"
BAR_SIZE = "5 mins"
USE_RTH = False

# MEASURED (see the module docstring): shares, but a partial count — roughly 0.34x to 0.67x
# consolidated volume, varying by day. Named here and carried into the record rather than
# silently converted, because the number is only meaningful against ANOTHER IB number.
VOLUME_UNIT = "shares (IB partial tape, ~0.3-0.7x consolidated; compare only with IB)"

# The gateway's own pacing complaint. Code 162 covers "Historical Market Data Service error"
# which is what a pacing violation arrives as.
_PACING_MARKERS = ("pacing violation", "max rate of messages", "too many requests")


class IBKRUnavailable(RuntimeError):
    """IB could not be reached or refused. Never a per-name state — a name IB simply has no
    trades for comes back as an empty series, which is a reading and not a failure."""


def looks_like_pacing(message: str) -> bool:
    """Is this gateway error IB telling us to slow down (rather than something real)?"""
    lowered = (message or "").lower()
    return any(marker in lowered for marker in _PACING_MARKERS)


# --------------------------------------------------------------------------- #
# pacing — pure, so the whole of it is testable without a gateway or a clock
# --------------------------------------------------------------------------- #
@dataclass
class _Pacer:
    """How long to wait before the next historical request may go out.

    Pure and injectable: ``now`` and ``sleep`` are parameters, so the tests exercise a
    ten-minute window in microseconds. Both of IB's rules are enforced BEFORE a request,
    which is the only place enforcing them helps — after the violation the window is already
    poisoned.
    """

    max_requests: int = MAX_REQUESTS_PER_WINDOW
    window_seconds: float = PACING_WINDOW_SECONDS
    identical_cooldown: float = IDENTICAL_REQUEST_COOLDOWN
    now: Callable[[], float] = _time.monotonic
    sleep: Callable[[float], None] = _time.sleep
    _sent: list = field(default_factory=list)          # timestamps, oldest first
    _last_identical: dict = field(default_factory=dict)

    def wait_for(self, key: str) -> float:
        """Seconds the caller must wait before sending ``key``. Pure — changes nothing."""
        moment = self.now()
        waits = [0.0]
        seen = self._last_identical.get(key)
        if seen is not None:
            waits.append(self.identical_cooldown - (moment - seen))
        recent = [t for t in self._sent if moment - t < self.window_seconds]
        if len(recent) >= self.max_requests:
            # The oldest request in the window has to age out before there is room.
            waits.append(self.window_seconds - (moment - recent[0]))
        return max(waits)

    def before(self, key: str) -> float:
        """Sleep as long as the limits require, then record the request. Returns the wait."""
        waited = self.wait_for(key)
        if waited > 0:
            _log.debug("gap_ledger.ibkr: pacing — waiting %.1fs before %s", waited, key)
            self.sleep(waited)
        moment = self.now()
        self._sent = [t for t in self._sent if moment - t < self.window_seconds]
        self._sent.append(moment)
        self._last_identical[key] = moment
        return waited


def request_key(ticker: str, start: date, end: date) -> str:
    """What makes two historical requests IDENTICAL for the 15-second rule."""
    return f"{ticker.upper()}|{start.isoformat()}|{end.isoformat()}|{BAR_SIZE}|{WHAT_TO_SHOW}"


# --------------------------------------------------------------------------- #
# translating IB's answer
# --------------------------------------------------------------------------- #
def duration_for(start: date, end: date) -> str:
    """IB's ``durationStr`` covering [start, end). Days, because 5-minute bars are asked for
    in days and a day is the unit the screen thinks in.

    At least 1 D — a same-day window is still one day of history, and asking for "0 D" is an
    error rather than an empty answer.
    """
    days = max(1, (end - start).days)
    return f"{days} D"


def end_datetime_for(end: date) -> datetime:
    """The ``endDateTime`` for a window ending at ``end`` (exclusive), in New York time.

    IB takes the END of the window, so a window that runs up to but not including ``end``
    ends at midnight on ``end``. Timezone-aware on purpose: a naive stamp is interpreted by
    the gateway in its own timezone, which is how a pre-market window quietly becomes
    somebody else's afternoon.
    """
    return datetime.combine(end, datetime.min.time(), tzinfo=NY)


def to_intraday_bar(raw) -> Optional[IntradayBar]:
    """One IB ``BarData`` as an ``IntradayBar`` in New York time, or None if unusable.

    A bar missing a price is dropped rather than defaulted, the same discipline the yfinance
    reader applies: one invented number poisons every average downstream.
    """
    when = getattr(raw, "date", None)
    if isinstance(when, date) and not isinstance(when, datetime):
        when = datetime.combine(when, datetime.min.time())
    if not isinstance(when, datetime):
        return None
    when = when.replace(tzinfo=NY) if when.tzinfo is None else when.astimezone(NY)
    try:
        prices = [float(getattr(raw, name)) for name in ("open", "high", "low", "close")]
    except (TypeError, ValueError):
        return None
    if any(p != p for p in prices):                    # NaN
        return None
    try:
        volume = float(getattr(raw, "volume", 0) or 0)
    except (TypeError, ValueError):
        volume = 0.0
    if volume != volume or volume < 0:                 # NaN or nonsense
        volume = 0.0
    o, h, low, c = prices
    return IntradayBar(start=when, open=o, high=h, low=low, close=c, volume=int(volume))


# --------------------------------------------------------------------------- #
# the adapter
# --------------------------------------------------------------------------- #
class IBKRBars:
    """5-minute TRADES bars from a local IB Gateway, read-only and paced.

    Satisfies the same shape as ``bars.IntradaySource`` so it can stand where the yfinance
    reader stands. ``quotes`` deliberately returns nothing: IB can serve a book, the brief
    does not ask for one, and an empty mapping is the honest "this provider will not quote"
    that ``screen.spread_flag`` already handles as *spread unknown*.
    """

    name = "ibkr"
    volume_unit = VOLUME_UNIT

    def __init__(self, *, host: Optional[str] = None, port: Optional[int] = None,
                 client_id: Optional[int] = None, timeout: float = 30.0,
                 pacer: Optional[_Pacer] = None, sleep=_time.sleep, ib=None) -> None:
        self.host = host or os.environ.get("IBKR_HOST", DEFAULT_HOST)
        self.port = int(port if port is not None
                        else os.environ.get("IBKR_PORT", DEFAULT_PORT))
        self.client_id = int(client_id if client_id is not None
                             else os.environ.get("IBKR_CLIENT_ID", DEFAULT_CLIENT_ID))
        self.timeout = timeout
        self.pacer = pacer or _Pacer(sleep=sleep)
        self._sleep = sleep
        self._ib = ib                                   # injected in tests; built on demand

    # -- connection --------------------------------------------------------- #
    def _client(self):
        """The connected ``IB`` handle, built on first use.

        Lazy so that merely constructing this adapter reaches nothing, and READ-ONLY because
        a client that cannot transmit an order is a stronger guarantee than a caller who
        promises not to.
        """
        if self._ib is not None:
            return self._ib
        try:
            from ib_async import IB
        except ImportError as exc:                       # pragma: no cover
            raise IBKRUnavailable(
                "ib_async is not installed; `pip install ib_async`") from exc
        client = IB()
        try:
            client.connect(self.host, self.port, clientId=self.client_id, readonly=True,
                           timeout=self.timeout)
        except Exception as exc:
            raise IBKRUnavailable(
                f"could not reach IB Gateway at {self.host}:{self.port} "
                f"(clientId={self.client_id}): {exc}. Is the gateway running and is API "
                f"access enabled for this client id?") from exc
        self._ib = client
        return client

    @property
    def connected(self) -> bool:
        return bool(self._ib is not None and getattr(self._ib, "isConnected", bool)())

    def disconnect(self) -> None:
        """Hang up if we ever picked up. Never raises — a failed disconnect must not be the
        thing that ends a run that already has its answers."""
        client, self._ib = self._ib, None
        try:
            if client is not None and getattr(client, "isConnected", lambda: False)():
                client.disconnect()
        except Exception as exc:                         # pragma: no cover
            _log.debug("gap_ledger.ibkr: disconnect failed: %s", exc)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.disconnect()
        return False

    # -- contracts ---------------------------------------------------------- #
    def _contract(self, ticker: str):
        """A qualified US stock contract, or None when IB does not know the symbol.

        Qualifying is what turns an ambiguous ticker into one listing; a symbol IB cannot
        place is an honest absence, not an exception, because the screen already knows how to
        report a name it got no data for.
        """
        from ib_async import Stock

        client = self._client()
        contract = Stock(ticker.upper().replace("-", " "), "SMART", "USD")
        try:
            qualified = client.qualifyContracts(contract)
        except Exception as exc:
            _log.warning("gap_ledger.ibkr: could not qualify %s: %s", ticker, exc)
            return None
        return qualified[0] if qualified else None

    # -- the request -------------------------------------------------------- #
    def bars_for(self, ticker: str, *, start: date, end: date) -> list[IntradayBar]:
        """ONE historical request: 5-minute TRADES bars over [start, end), pre-market included.

        A pacing complaint from the gateway waits and retries; anything else is logged and
        returns nothing, so one bad symbol costs its own row and not the run.
        """
        contract = self._contract(ticker)
        if contract is None:
            return []
        key = request_key(ticker, start, end)
        for attempt, backoff in enumerate((None,) + PACING_BACKOFF_SECONDS):
            if backoff is not None:
                _log.warning("gap_ledger.ibkr: pacing violation on %s — waiting %.0fs "
                             "(attempt %d)", ticker, backoff, attempt)
                self._sleep(backoff)
            self.pacer.before(key)
            try:
                raw = self._client().reqHistoricalData(
                    contract, endDateTime=end_datetime_for(end),
                    durationStr=duration_for(start, end), barSizeSetting=BAR_SIZE,
                    whatToShow=WHAT_TO_SHOW, useRTH=USE_RTH, formatDate=1)
            except Exception as exc:
                if looks_like_pacing(str(exc)):
                    continue                             # wait and try again, never abort
                _log.warning("gap_ledger.ibkr: %s failed: %s", ticker, exc)
                return []
            bars = [bar for bar in (to_intraday_bar(r) for r in (raw or []))
                    if bar is not None]
            return sorted(bars, key=lambda b: b.start)
        _log.warning("gap_ledger.ibkr: %s still pacing-limited after %d waits; no data",
                     ticker, len(PACING_BACKOFF_SECONDS))
        return []

    # -- the IntradaySource shape ------------------------------------------- #
    def intraday_bars(self, tickers: Sequence[str], *, start: date,
                      end: date) -> dict[str, list[IntradayBar]]:
        """One request per name. A name with no trades is ABSENT from the mapping, which is
        how every other reader in this package says "nothing for this one"."""
        out: dict[str, list[IntradayBar]] = {}
        for ticker in tickers:
            bars = self.bars_for(ticker, start=start, end=end)
            if bars:
                out[ticker] = bars
        return out

    def quotes(self, tickers: Sequence[str]) -> dict[str, Quote]:
        """Nothing, deliberately — see the class docstring. Reads as *spread unknown*."""
        return {}
