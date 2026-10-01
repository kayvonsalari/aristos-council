"""PAPER-TRADE-1 — the ONLY place in this repo that can transmit an order, and only to the
PAPER Gateway.

Independent of ``aristos_council.gap_ledger.ibkr`` by design (see the package docstring):
that module is hardcoded ``readonly=True`` and reads the LIVE Gateway (port 4001) for real
pre-market volume under GAP-IBKR-1's personal-use licence — a different job, a different
account, and deliberately not a line of shared code, so neither package can ever leak into
the other's guarantees.

Two guards stand between this module and a real-money order, both enforced in ``connect``
BEFORE anything else runs, not trusted to the caller:

1. **The port.** If the resolved port is 4001 (the live Gateway), ``connect`` refuses
   outright and raises — it never even attempts the socket. The default is 4002.
2. **The account id.** Once connected, IBKR's own ``managedAccounts()`` is read back and
   checked against the "DU" prefix every real IBKR paper account carries (a live account
   starts with "U"). Anything else disconnects immediately and raises
   ``PaperAccountGuardError`` — never a trade placed, never a retry, never a "just this
   once".
"""
from __future__ import annotations

import logging
import os
import time as _time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Sequence

from .config import (FILL_POLL_SECONDS, NY, DEFAULT_CLIENT_ID, DEFAULT_HOST, DEFAULT_PORT,
                     LIVE_PORT_FORBIDDEN)

_log = logging.getLogger(__name__)

# IBKR's own paper-account id prefix. A live account id starts with "U" instead.
PAPER_ACCOUNT_PREFIX = "DU"

# Generic tick 236 = "Shortable": populates Ticker.shortable / .shortableShares.
SHORTABLE_TICK = "236"

# Ticker.marketDataType, as IBKR documents it.
MARKET_DATA_TYPE_NAMES = {1: "live", 2: "frozen", 3: "delayed", 4: "delayed-frozen"}

QUOTE_WAIT_SECONDS = 6.0
QUOTE_POLL_SECONDS = 0.25
SHORTABLE_WAIT_SECONDS = 4.0


class IBKRPaperUnavailable(RuntimeError):
    """The paper Gateway could not be reached, or ib_async is not installed."""


class PaperAccountGuardError(RuntimeError):
    """The connected account id did NOT start with "DU" — refused before any order could be
    placed. This is the one error in this package that should never be caught and retried;
    seeing it means something is pointed at the wrong account and must be fixed by a human,
    not worked around by code."""


class LivePortForbiddenError(RuntimeError):
    """The resolved port was 4001 (the live Gateway) — refused before even attempting to
    connect. This package only ever trades on the paper Gateway."""


@dataclass(frozen=True)
class QuoteReading:
    bid: Optional[float]
    ask: Optional[float]
    # "live" | "frozen" | "delayed" | "delayed-frozen" | "unknown" (no tick arrived at all,
    # or the type was never reported) — recorded on every trade attempt, not just logged.
    data_type: str = "unknown"


@dataclass(frozen=True)
class ShortableReading:
    shortable: Optional[bool]          # None: IBKR never answered in time — an abstention
    shares: Optional[float]


@dataclass(frozen=True)
class FillResult:
    filled: bool
    fill_price: Optional[float] = None
    fill_time: Optional[datetime] = None       # NY-aware
    status: str = ""                            # the raw IBKR order status, for the record


class PaperIBKR:
    """A connection to the PAPER Gateway only, order-placing enabled. Lazy: constructing
    this reaches nothing; ``connect()`` is the first point anything touches the network, and
    it is also where both guards fire."""

    def __init__(self, *, host: Optional[str] = None, port: Optional[int] = None,
                client_id: Optional[int] = None, timeout: float = 30.0, sleep=_time.sleep,
                ib=None) -> None:
        self.host = host or os.environ.get("PAPER_IBKR_HOST", DEFAULT_HOST)
        self.port = int(port if port is not None
                        else os.environ.get("PAPER_IBKR_PORT", DEFAULT_PORT))
        self.client_id = int(client_id if client_id is not None
                             else os.environ.get("PAPER_IBKR_CLIENT_ID", DEFAULT_CLIENT_ID))
        self.timeout = timeout
        self._sleep = sleep
        self._ib = ib
        self.account_id: str = ""

    # -- connection, with both guards -------------------------------------- #
    def connect(self):
        """Both guards run EVERY call, not only on the first real connection: an injected
        ``ib`` (every test in this package) must be checked exactly as a freshly-opened
        socket is, or the account guard would never fire for anything a test could see.
        Once ``account_id`` has been verified once for this handle, later calls are a
        no-op — ``enter``/``exit``/``record`` each connect once per run and then call every
        reading/order method against the same handle."""
        if self.port == LIVE_PORT_FORBIDDEN:
            raise LivePortForbiddenError(
                f"refusing to connect to port {LIVE_PORT_FORBIDDEN} (the LIVE Gateway) — "
                f"this tool only ever trades on the paper Gateway (default {DEFAULT_PORT}).")
        if self._ib is not None and self.account_id:
            return self._ib
        if self._ib is None:
            try:
                from ib_async import IB
            except ImportError as exc:                   # pragma: no cover
                raise IBKRPaperUnavailable(
                    "ib_async is not installed; `pip install ib_async`") from exc
            client = IB()
            try:
                client.connect(self.host, self.port, clientId=self.client_id, readonly=False,
                               timeout=self.timeout)
            except Exception as exc:
                raise IBKRPaperUnavailable(
                    f"could not reach the paper Gateway at {self.host}:{self.port} "
                    f"(clientId={self.client_id}): {exc}. Is IB Gateway running in PAPER mode "
                    f"and is API access enabled for this client id?") from exc
            self._ib = client
        accounts = []
        try:
            accounts = list(self._ib.managedAccounts())
        except Exception as exc:                          # pragma: no cover
            _log.warning("paper_trade.ibkr_paper: could not read managedAccounts: %s", exc)
        self.account_id = accounts[0] if accounts else ""
        if not self.account_id.startswith(PAPER_ACCOUNT_PREFIX):
            self.disconnect()
            raise PaperAccountGuardError(
                f"connected account id {self.account_id!r} does not start with "
                f"{PAPER_ACCOUNT_PREFIX!r} — refusing to place any order. This almost "
                f"certainly means the Gateway at {self.host}:{self.port} is NOT the paper "
                f"session. Nothing was transmitted.")
        return self._ib

    def disconnect(self) -> None:
        client, self._ib = self._ib, None
        try:
            if client is not None and getattr(client, "isConnected", lambda: False)():
                client.disconnect()
        except Exception as exc:                          # pragma: no cover
            _log.debug("paper_trade.ibkr_paper: disconnect failed: %s", exc)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_exc):
        self.disconnect()
        return False

    # -- contracts ----------------------------------------------------------- #
    def _contract(self, ticker: str):
        from ib_async import Stock
        client = self.connect()
        contract = Stock(ticker.upper().replace("-", " "), "SMART", "USD")
        qualified = client.qualifyContracts(contract)
        return qualified[0] if qualified else None

    # -- reading ------------------------------------------------------------- #
    def quote_for(self, ticker: str) -> QuoteReading:
        """A brief streaming quote AND the market-data type it arrived under (live vs
        delayed) — recorded on every trade so a reader can tell which kind of read the
        order was based on, never silently assumed to be live."""
        contract = self._contract(ticker)
        if contract is None:
            return QuoteReading(bid=None, ask=None, data_type="unknown")
        client = self.connect()
        ticker_feed = client.reqMktData(contract, "", snapshot=False, regulatorySnapshot=False)
        try:
            waited = 0.0
            while waited < QUOTE_WAIT_SECONDS:
                self._pump(QUOTE_POLL_SECONDS)
                waited += QUOTE_POLL_SECONDS
                bid, ask = _usable(ticker_feed.bid), _usable(ticker_feed.ask)
                if bid is not None and ask is not None:
                    return QuoteReading(bid=bid, ask=ask, data_type=_data_type_name(ticker_feed))
            return QuoteReading(bid=_usable(ticker_feed.bid), ask=_usable(ticker_feed.ask),
                                data_type=_data_type_name(ticker_feed))
        finally:
            try:
                client.cancelMktData(contract)
            except Exception:                              # pragma: no cover
                pass

    def shortable_for(self, ticker: str) -> ShortableReading:
        """IBKR's own shortable flag for this name, right now. ``shortable=None`` (never
        False) when IBKR never answers in time — an honest abstention, not a guess; the
        caller treats "could not determine" and "confirmed not shortable" as different
        things (see ``enter``)."""
        contract = self._contract(ticker)
        if contract is None:
            return ShortableReading(shortable=None, shares=None)
        client = self.connect()
        feed = client.reqMktData(contract, SHORTABLE_TICK, snapshot=False,
                                 regulatorySnapshot=False)
        try:
            waited = 0.0
            while waited < SHORTABLE_WAIT_SECONDS:
                self._pump(QUOTE_POLL_SECONDS)
                waited += QUOTE_POLL_SECONDS
                shares = _usable_count(getattr(feed, "shortableShares", None))
                flag = getattr(feed, "shortable", None)
                if shares is not None or flag not in (None, ""):
                    return ShortableReading(shortable=_shortable_bool(flag, shares),
                                            shares=shares)
            return ShortableReading(shortable=None, shares=None)
        finally:
            try:
                client.cancelMktData(contract)
            except Exception:                              # pragma: no cover
                pass

    def official_open(self, ticker: str, day: date) -> Optional[float]:
        """The real 09:30 ET opening print — one daily bar for ``day``, read from history
        (never from a live tick, which would not exist for a day already past). None when
        IBKR has nothing for this name on this day."""
        from datetime import timedelta
        contract = self._contract(ticker)
        if contract is None:
            return None
        client = self.connect()
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=NY)
        try:
            bars = client.reqHistoricalData(
                contract, endDateTime=end, durationStr="1 D", barSizeSetting="1 day",
                whatToShow="TRADES", useRTH=True, formatDate=1)
        except Exception as exc:
            _log.warning("paper_trade.ibkr_paper: official_open failed for %s: %s", ticker, exc)
            return None
        if not bars:
            return None
        try:
            return float(bars[-1].open)
        except (TypeError, ValueError):
            return None

    # -- orders ---------------------------------------------------------------- #
    def place_marketable_limit(self, ticker: str, *, action: str, shares: int,
                               limit_price: float, order_ref: str):
        """``action`` is "BUY" or "SELL" (a short sale is a SELL with no existing position —
        IBKR interprets it as opening short automatically in a margin account). Returns the
        ``Trade`` handle; never waits for a fill (see ``wait_for_fill``)."""
        from ib_async import LimitOrder
        contract = self._contract(ticker)
        if contract is None:
            return None
        order = LimitOrder(action, shares, round(limit_price, 2))
        order.tif = "DAY"
        order.orderRef = order_ref
        client = self.connect()
        return client.placeOrder(contract, order)

    def place_moc(self, ticker: str, *, action: str, shares: int, order_ref: str):
        from ib_async import Order
        contract = self._contract(ticker)
        if contract is None:
            return None
        order = Order(action=action, totalQuantity=shares, orderType="MOC", tif="DAY")
        order.orderRef = order_ref
        client = self.connect()
        return client.placeOrder(contract, order)

    def wait_for_fill(self, trade, *, timeout_seconds: float) -> FillResult:
        """Poll an already-placed order's status until it fills or ``timeout_seconds``
        elapses. Never cancels — that is the caller's call (``enter`` cancels an unfilled
        marketable limit after its own 2-minute window; a MOC is never cancelled here)."""
        if trade is None:
            return FillResult(filled=False, status="no contract")
        waited = 0.0
        while waited < timeout_seconds:
            self._pump(FILL_POLL_SECONDS)
            waited += FILL_POLL_SECONDS
            status = trade.orderStatus.status
            if status == "Filled":
                fill_time = _last_fill_time(trade)
                return FillResult(filled=True, fill_price=trade.orderStatus.avgFillPrice,
                                  fill_time=fill_time, status=status)
            if status in ("Cancelled", "ApiCancelled", "Inactive"):
                return FillResult(filled=False, status=status)
        return FillResult(filled=False, status=trade.orderStatus.status or "Timeout")

    def cancel(self, trade) -> None:
        if trade is None:
            return
        client = self.connect()
        try:
            client.cancelOrder(trade.order)
        except Exception as exc:                          # pragma: no cover
            _log.warning("paper_trade.ibkr_paper: cancel failed: %s", exc)

    def executions_for(self, day: date) -> list:
        """Every execution (fill) IBKR recorded for THIS account today — account-level, not
        session-level, so ``record`` (a fresh connection, possibly hours after ``enter``/
        ``exit`` disconnected) can still read back fills those two commands placed."""
        from ib_async import ExecutionFilter
        client = self.connect()
        start = datetime.combine(day, datetime.min.time(), tzinfo=NY)
        filt = ExecutionFilter(time=start.strftime("%Y%m%d-%H:%M:%S"))
        try:
            return list(client.reqExecutions(filt))
        except Exception as exc:
            _log.warning("paper_trade.ibkr_paper: reqExecutions failed: %s", exc)
            return []

    # -- internal -------------------------------------------------------------- #
    def _pump(self, seconds: float) -> None:
        client = self._ib
        pump = getattr(client, "sleep", None) if client is not None else None
        if callable(pump):
            pump(seconds)
        else:
            self._sleep(seconds)


def _usable(value) -> Optional[float]:
    """A PRICE tick: zero is a placeholder (no real quote prints at $0), so it reads as
    absent exactly like NaN does."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0:                   # NaN or placeholder zero
        return None
    return number


def _usable_count(value) -> Optional[float]:
    """A SHARE COUNT tick (shortable shares): zero is a real, meaningful answer — "nothing
    is available to borrow" — so only NaN/negative/missing reads as absent, never zero."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < 0:                    # NaN or a nonsense negative
        return None
    return number


def _data_type_name(ticker_feed) -> str:
    code = getattr(ticker_feed, "marketDataType", None)
    return MARKET_DATA_TYPE_NAMES.get(code, "unknown")


def _shortable_bool(flag, shares: Optional[float]) -> Optional[bool]:
    """IBKR's "shortable" generic tick reports a code (commonly 1/2/3 meaning not/maybe/
    available-with-shares, or a bare availability string depending on client version) rather
    than a clean boolean. The one fact worth trusting either way: if IBKR reports ZERO
    shortable shares, it is not shortable, whatever the flag code says; if it reports a
    positive share count, it is. A flag with no usable share count is reported as-is only
    when the flag itself is unambiguous, else None (abstain)."""
    if shares is not None:
        return shares > 0
    if flag in (None, ""):
        return None
    try:
        code = float(flag)
    except (TypeError, ValueError):
        return None
    if code <= 1.0:
        return False
    if code >= 3.0:
        return True
    return None


def _last_fill_time(trade) -> Optional[datetime]:
    if not trade.fills:
        return None
    raw = trade.fills[-1].execution.time
    if raw is None:
        return None
    if raw.tzinfo is None:
        return raw.replace(tzinfo=NY)
    return raw.astimezone(NY)
