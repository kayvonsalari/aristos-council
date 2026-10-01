"""PAPER-TRADE-1 — ``ibkr_paper.PaperIBKR``: the two guards (port, account id), quote/
shortable reading (and the live-vs-delayed flag), order placement, and fill waiting.

No socket opens — the ``IB`` handle is injected throughout. The whole file carries
``pytest.mark.real_adapter`` for the same reason ``test_gap_ledger_ibkr.py`` does: it
constructs the real ``PaperIBKR`` on purpose, which TEST-ISOLATION-1 guards (see
``test_paper_trade_ibkr_paper_guard.py``, which proves the guard without this marker).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import pytest

from aristos_council.paper_trade import config as pt_config
from aristos_council.paper_trade.ibkr_paper import (LivePortForbiddenError,
                                                     PaperAccountGuardError, PaperIBKR)

pytest.importorskip("ib_async")

pytestmark = pytest.mark.real_adapter

DAY = date(2026, 9, 22)


# --------------------------------------------------------------------------- #
# a fake paper gateway
# --------------------------------------------------------------------------- #
@dataclass
class _Contract:
    symbol: str


@dataclass
class _Ticker:
    bid: float = float("nan")
    ask: float = float("nan")
    marketDataType: int = 0
    shortable: object = None
    shortableShares: float = float("nan")


@dataclass
class _Bar:
    open: float


@dataclass
class _OrderStatus:
    status: str = "Submitted"
    avgFillPrice: float = 0.0


@dataclass
class _Execution:
    price: float
    time: datetime
    orderRef: str = ""
    shares: float = 0.0


@dataclass
class _CommissionReport:
    commission: float = 0.0


@dataclass
class _Fill:
    execution: _Execution
    commissionReport: _CommissionReport = field(default_factory=_CommissionReport)


@dataclass
class _Trade:
    order: object
    orderStatus: _OrderStatus = field(default_factory=_OrderStatus)
    fills: list = field(default_factory=list)


@dataclass
class _FakeIB:
    accounts: list = field(default_factory=lambda: ["DU1234567"])
    unknown_symbols: set = field(default_factory=set)
    ticker_by_symbol: dict = field(default_factory=dict)
    bars_by_symbol: dict = field(default_factory=dict)
    executions: list = field(default_factory=list)
    status_sequence: dict = field(default_factory=dict)    # symbol -> list[str] of statuses
    disconnected: bool = False
    cancelled: list = field(default_factory=list)
    placed: list = field(default_factory=list)

    def isConnected(self):
        return not self.disconnected

    def disconnect(self):
        self.disconnected = True

    def managedAccounts(self):
        return list(self.accounts)

    def qualifyContracts(self, contract):
        return [] if contract.symbol in self.unknown_symbols else [contract]

    def reqMktData(self, contract, genericTickList="", snapshot=False,
                   regulatorySnapshot=False):
        return self.ticker_by_symbol.get(contract.symbol, _Ticker())

    def cancelMktData(self, contract):
        pass

    def reqHistoricalData(self, contract, **kwargs):
        return list(self.bars_by_symbol.get(contract.symbol, []))

    def placeOrder(self, contract, order):
        order.orderId = len(self.placed) + 1
        trade = _Trade(order=order)
        statuses = self.status_sequence.get(contract.symbol, ["Filled"])
        trade.orderStatus.status = statuses[-1]
        if statuses[-1] == "Filled":
            trade.orderStatus.avgFillPrice = getattr(order, "lmtPrice", 100.0) or 100.0
            trade.fills = [_Fill(execution=_Execution(
                price=trade.orderStatus.avgFillPrice, time=datetime(2026, 9, 22, 13, 32),
                orderRef=order.orderRef))]
        self.placed.append((contract.symbol, order))
        return trade

    def cancelOrder(self, order):
        self.cancelled.append(order.orderId)

    def reqExecutions(self, filt):
        return list(self.executions)

    def sleep(self, seconds):
        pass


def _client(ib: _FakeIB, **kw) -> PaperIBKR:
    return PaperIBKR(ib=ib, **kw)


# --------------------------------------------------------------------------- #
# the two guards
# --------------------------------------------------------------------------- #
def test_connecting_to_the_live_port_is_refused_before_anything_else():
    client = PaperIBKR(port=4001, ib=_FakeIB())
    with pytest.raises(LivePortForbiddenError, match="4001"):
        client.connect()


def test_a_non_paper_account_id_is_refused_and_disconnects():
    ib = _FakeIB(accounts=["U7654321"])                    # a LIVE account id
    client = _client(ib)
    with pytest.raises(PaperAccountGuardError, match="DU"):
        client.connect()
    assert ib.disconnected                                 # hung up immediately


def test_a_paper_account_id_connects_cleanly():
    ib = _FakeIB(accounts=["DU1234567"])
    client = _client(ib)
    client.connect()
    assert client.account_id == "DU1234567"
    assert not ib.disconnected


def test_default_port_is_the_paper_gateway():
    assert pt_config.DEFAULT_PORT == 4002
    assert pt_config.LIVE_PORT_FORBIDDEN == 4001


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def test_quote_for_reports_the_market_data_type():
    ib = _FakeIB(ticker_by_symbol={"AAA": _Ticker(bid=9.9, ask=10.1, marketDataType=1)})
    client = _client(ib)
    reading = client.quote_for("AAA")
    assert reading.bid == 9.9 and reading.ask == 10.1 and reading.data_type == "live"


def test_quote_for_reports_delayed_data():
    ib = _FakeIB(ticker_by_symbol={"AAA": _Ticker(bid=9.9, ask=10.1, marketDataType=3)})
    reading = _client(ib).quote_for("AAA")
    assert reading.data_type == "delayed"


def test_quote_for_an_unqualified_symbol_is_unknown_not_a_crash():
    ib = _FakeIB(unknown_symbols={"ZZZ"})
    reading = _client(ib).quote_for("ZZZ")
    assert reading.bid is None and reading.ask is None and reading.data_type == "unknown"


def test_shortable_for_reads_a_positive_share_count_as_shortable():
    ib = _FakeIB(ticker_by_symbol={"AAA": _Ticker(shortableShares=50_000.0)})
    reading = _client(ib).shortable_for("AAA")
    assert reading.shortable is True and reading.shares == 50_000.0


def test_shortable_for_reads_zero_shares_as_not_shortable():
    ib = _FakeIB(ticker_by_symbol={"AAA": _Ticker(shortableShares=0.0)})
    reading = _client(ib).shortable_for("AAA")
    assert reading.shortable is False


def test_official_open_reads_the_daily_bar():
    ib = _FakeIB(bars_by_symbol={"AAA": [_Bar(open=12.34)]})
    assert _client(ib).official_open("AAA", DAY) == 12.34


def test_official_open_with_no_bars_is_none_not_a_crash():
    ib = _FakeIB()
    assert _client(ib).official_open("AAA", DAY) is None


# --------------------------------------------------------------------------- #
# orders
# --------------------------------------------------------------------------- #
def test_a_marketable_limit_that_fills_reports_the_fill():
    ib = _FakeIB(status_sequence={"AAA": ["Filled"]})
    client = _client(ib)
    trade = client.place_marketable_limit("AAA", action="BUY", shares=10, limit_price=10.10,
                                          order_ref="ref-1")
    result = client.wait_for_fill(trade, timeout_seconds=10)
    assert result.filled and result.fill_price == 10.10


def test_an_unfilled_order_can_be_cancelled():
    ib = _FakeIB(status_sequence={"AAA": ["Submitted"]})
    client = _client(ib)
    trade = client.place_marketable_limit("AAA", action="BUY", shares=10, limit_price=10.10,
                                          order_ref="ref-1")
    result = client.wait_for_fill(trade, timeout_seconds=0.001)
    assert not result.filled
    client.cancel(trade)
    assert ib.cancelled == [trade.order.orderId]


def test_place_moc_tags_the_order_ref():
    ib = _FakeIB()
    client = _client(ib)
    client.place_moc("AAA", action="SELL", shares=10, order_ref="papertrade-exit-x")
    _symbol, order = ib.placed[-1]
    assert order.orderType == "MOC" and order.orderRef == "papertrade-exit-x"


def test_executions_for_returns_todays_fills():
    fill = _Fill(execution=_Execution(price=11.0, time=datetime(2026, 9, 22, 20, 1),
                                      orderRef="ref-1"))
    ib = _FakeIB(executions=[fill])
    assert _client(ib).executions_for(DAY) == [fill]
