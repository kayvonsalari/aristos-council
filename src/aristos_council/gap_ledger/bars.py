"""GAP-LEDGER-1 — the two shapes of price data this screener reads, and the day cache.

Two sources, because the screen asks two different questions:

* **Daily bars** — yesterday's close, the 20-session average volume, the length of
  history, and (after the close) the day's open and close. The existing
  ``MarketDataAdapter`` already serves this shape and its ``PriceBar`` already carries
  ``adj_close``, so the pre-filter reuses the repo's DTO rather than inventing one.
* **5-minute bars including pre- and post-market** — the gap, the pre-market volume
  window, and the 10:00/11:30 checkpoints. No adapter in this repo serves that (the
  ``MarketDataAdapter`` contract is daily), so it gets its own narrow protocol here.

Both are PROTOCOLS with a yfinance implementation behind them, so every test injects a
fake and no test reaches the network (TEST-ISOLATION-1). The factory is registered in
``tests/conftest.py`` alongside the other live-provider factories.

The day cache carries the VALBAND-1 scar deliberately: a cached series is served ONLY
when the cached window COVERS the requested one. VALBAND-1 shipped green and produced
"insufficient history" for every name on every run because a day cache ignored the
requested date window and handed back a shorter series someone else had asked for. Here
the window is part of what is CHECKED, not just part of what is stored.
"""
from __future__ import annotations

import json
import logging
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional, Protocol, Sequence

from ..data.adapter import PriceBar
from .config import DEFAULT_CONFIG, NY, GapConfig

_log = logging.getLogger(__name__)


class BarsUnavailable(Exception):
    """This provider could not serve bars for this name. Always carries a reason."""


# --------------------------------------------------------------------------- #
# the intraday bar
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class IntradayBar:
    """One 5-minute bar. ``start`` is TIMEZONE-AWARE and in New York time.

    Aware on purpose: an intraday bar whose timezone is implicit is the single easiest
    way to count 09:35 London volume as 09:35 New York volume. Everything downstream
    reads ``start`` in ET, and the conversion is a no-op when it already is.
    """

    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Quote:
    """A bid/ask snapshot. Either side may be missing — a reading, not a failure."""

    bid: Optional[float] = None
    ask: Optional[float] = None


# --------------------------------------------------------------------------- #
# the protocols
# --------------------------------------------------------------------------- #
class DailySource(Protocol):
    def daily_bars(self, tickers: Sequence[str], *, start: date,
                   end: date) -> dict[str, list[PriceBar]]:
        """Daily bars per ticker for [start, end). A ticker with no data is ABSENT from
        the mapping rather than present-and-empty, so the caller can name it excluded."""


class IntradaySource(Protocol):
    def intraday_bars(self, tickers: Sequence[str], *, start: date,
                      end: date) -> dict[str, list[IntradayBar]]:
        """5-minute bars per ticker, pre/post market INCLUDED, for [start, end)."""

    def quotes(self, tickers: Sequence[str]) -> dict[str, Quote]:
        """Bid/ask per ticker. A name the provider will not quote is simply absent."""


# --------------------------------------------------------------------------- #
# chunking
# --------------------------------------------------------------------------- #
def chunks(items: Sequence[str], size: int) -> list[list[str]]:
    """``items`` in fixed-size pieces. A size below 1 is treated as 1 rather than raising:
    a misconfigured chunk size should slow a run down, not abort it."""
    size = max(1, int(size))
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


# --------------------------------------------------------------------------- #
# the yfinance implementation
# --------------------------------------------------------------------------- #
class YFinanceBars:
    """Daily and 5-minute bars from yfinance, fetched in chunks with polite pacing.

    One class for both protocols because both are one ``yf.download`` call with a
    different interval, and splitting them would duplicate the (fiddly) multi-ticker
    frame unpacking.
    """

    name = "yfinance"

    def __init__(self, config: GapConfig = DEFAULT_CONFIG, *, sleep=_time.sleep) -> None:
        try:
            import yfinance  # noqa: F401
        except ImportError as exc:                       # pragma: no cover
            raise BarsUnavailable(
                "yfinance is not installed; `pip install yfinance`"
            ) from exc
        self.config = config
        self._sleep = sleep

    # -- daily -------------------------------------------------------------- #
    def daily_bars(self, tickers, *, start: date, end: date):
        out: dict[str, list[PriceBar]] = {}
        for frame, ticker in self._download(tickers, start=start, end=end,
                                            interval="1d", prepost=False):
            bars = []
            for idx, row in frame.iterrows():
                values = self._row(row)
                if values is None:
                    continue
                o, h, l, c, adj, v = values
                day = idx.date() if hasattr(idx, "date") else idx
                bars.append(PriceBar(day=day, open=o, high=h, low=l, close=c,
                                     adj_close=adj, volume=v))
            if bars:
                out[ticker] = sorted(bars, key=lambda b: b.day)
        return out

    # -- intraday ----------------------------------------------------------- #
    def intraday_bars(self, tickers, *, start: date, end: date):
        out: dict[str, list[IntradayBar]] = {}
        for frame, ticker in self._download(tickers, start=start, end=end,
                                            interval="5m", prepost=True):
            bars = []
            for idx, row in frame.iterrows():
                values = self._row(row)
                if values is None:
                    continue
                o, h, l, c, _adj, v = values
                when = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
                if when.tzinfo is None:
                    # yfinance normally returns tz-aware intraday stamps. If a provider
                    # ever does not, the stamps ARE exchange-local, so reading them as ET
                    # is the honest interpretation rather than assuming UTC.
                    when = when.replace(tzinfo=NY)
                bars.append(IntradayBar(start=when.astimezone(NY), open=o, high=h,
                                        low=l, close=c, volume=v))
            if bars:
                out[ticker] = sorted(bars, key=lambda b: b.start)
        return out

    # -- quotes ------------------------------------------------------------- #
    def quotes(self, tickers):
        import yfinance as yf

        out: dict[str, Quote] = {}
        for piece in chunks(list(tickers), self.config.chunk_size):
            for ticker in piece:
                try:
                    info = yf.Ticker(ticker).info or {}
                except Exception as exc:                 # yfinance throws many types
                    _log.debug("gap_ledger: no quote for %s: %s", ticker, exc)
                    continue
                bid, ask = _as_float(info.get("bid")), _as_float(info.get("ask"))
                if bid is None and ask is None:
                    continue
                out[ticker] = Quote(bid=bid, ask=ask)
            self._pause()
        return out

    # -- the shared download ------------------------------------------------ #
    def _download(self, tickers, *, start: date, end: date, interval: str,
                  prepost: bool):
        """Yield ``(single-ticker frame, ticker)`` pairs, chunk by chunk.

        A chunk the provider refuses is LOGGED and skipped, not raised: one bad name in
        forty must not cost the other thirty-nine, and every name that ends up without
        data is reported as excluded-with-a-reason by the caller.
        """
        import yfinance as yf

        pieces = chunks([t for t in tickers if t], self.config.chunk_size)
        for n, piece in enumerate(pieces):
            try:
                raw = yf.download(piece, start=start.isoformat(), end=end.isoformat(),
                                  interval=interval, prepost=prepost, auto_adjust=False,
                                  actions=False, progress=False, threads=False,
                                  group_by="ticker")
            except Exception as exc:                     # yfinance throws many types
                _log.warning("gap_ledger: %s download failed for %d names: %s",
                             interval, len(piece), exc)
                raw = None
            if raw is not None:
                for ticker in piece:
                    frame = _slice(raw, ticker, single=len(piece) == 1)
                    if frame is None or frame.empty:
                        continue
                    yield frame, ticker
            if n + 1 < len(pieces):
                self._pause()

    def _pause(self) -> None:
        if self.config.chunk_pause_seconds > 0:
            self._sleep(self.config.chunk_pause_seconds)

    @staticmethod
    def _row(row):
        """``(open, high, low, close, adj_close, volume)`` or None for an incomplete bar.

        Incomplete bars are DROPPED, the same discipline as ``YFinanceAdapter``: yfinance
        includes the current day's row with NaN prices during (pre-)market hours, and one
        NaN close silently poisons every average downstream.
        """
        o = _as_float(row.get("Open"))
        h = _as_float(row.get("High"))
        low = _as_float(row.get("Low"))
        c = _as_float(row.get("Close"))
        if None in (o, h, low, c):
            return None
        volume = _as_float(row.get("Volume"))
        if volume is None:
            return None
        adj = _as_float(row.get("Adj Close"))
        return o, h, low, c, (adj if adj is not None else c), int(volume)


def _slice(raw, ticker: str, *, single: bool):
    """The single-ticker frame out of whatever shape ``yf.download`` returned."""
    if raw is None:
        return None
    columns = getattr(raw, "columns", None)
    if columns is not None and getattr(columns, "nlevels", 1) > 1:
        try:
            return raw[ticker].dropna(how="all")
        except Exception:
            return None
    if single:
        return raw.dropna(how="all")
    return None


def _as_float(value) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out                    # NaN


# --------------------------------------------------------------------------- #
# the day cache
# --------------------------------------------------------------------------- #
class DailyCache:
    """Yesterday's daily bars, cached so the pre-filter is cheap on a re-run.

    Keyed by ticker, holding the WINDOW that was fetched. A cached entry is served only
    when it was fetched for the market day we are asking about AND its window covers the
    requested one — the VALBAND-1 rule. An entry that covers less is refetched, never
    served short.
    """

    def __init__(self, root: str | Path, *, source: DailySource) -> None:
        self.root = Path(root)
        self.source = source

    def path_for(self, ticker: str) -> Path:
        return self.root / f"{safe_name(ticker)}.json"

    def daily_bars(self, tickers: Sequence[str], *, start: date, end: date,
                   as_of: date, refresh: bool = False) -> dict[str, list[PriceBar]]:
        hits: dict[str, list[PriceBar]] = {}
        misses: list[str] = []
        for ticker in tickers:
            cached = None if refresh else self._read(ticker, start=start, end=end,
                                                     as_of=as_of)
            if cached is None:
                misses.append(ticker)
            else:
                hits[ticker] = cached
        if misses:
            for ticker, fetched in self.source.daily_bars(misses, start=start,
                                                          end=end).items():
                self._write(ticker, fetched, start=start, end=end, as_of=as_of)
                hits[ticker] = fetched
        return hits

    # -- io ----------------------------------------------------------------- #
    def _read(self, ticker: str, *, start: date, end: date,
              as_of: date) -> Optional[list[PriceBar]]:
        path = self.path_for(ticker)
        if not path.exists():
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("as_of") != as_of.isoformat():
                return None
            cached_start = date.fromisoformat(doc["start"])
            cached_end = date.fromisoformat(doc["end"])
            # VALBAND-1: the cached window must COVER the requested one.
            if cached_start > start or cached_end < end:
                return None
            return [PriceBar(day=date.fromisoformat(b["day"]), open=b["open"],
                             high=b["high"], low=b["low"], close=b["close"],
                             adj_close=b["adj_close"], volume=int(b["volume"]))
                    for b in doc.get("bars", [])]
        except Exception as exc:
            _log.debug("gap_ledger: unreadable cache for %s: %s", ticker, exc)
            return None

    def _write(self, ticker: str, bars: Iterable[PriceBar], *, start: date, end: date,
               as_of: date) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        doc = {
            "ticker": ticker,
            "as_of": as_of.isoformat(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "bars": [{"day": b.day.isoformat(), "open": b.open, "high": b.high,
                      "low": b.low, "close": b.close, "adj_close": b.adj_close,
                      "volume": int(b.volume)} for b in bars],
        }
        target = self.path_for(ticker)
        tmp = target.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(doc), encoding="utf-8")
            tmp.replace(target)
        except OSError as exc:                            # pragma: no cover
            _log.debug("gap_ledger: could not cache %s: %s", ticker, exc)


def safe_name(ticker: str) -> str:
    """A filename that survives a ticker like ``BRK-B`` or ``BF.B`` on Windows."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in ticker.upper())


def lookback_start(as_of: date, sessions: int) -> date:
    """A calendar start date generous enough to contain ``sessions`` trading days.

    Trading days run about 252 per 365, so 1.55 calendar days per session plus a fortnight
    of slack covers the longest holiday stretch. Deliberately generous rather than exact:
    asking for too much history is a cheap mistake, and asking for too little is the
    VALBAND-1 mistake.
    """
    return as_of - timedelta(days=int(sessions * 1.55) + 14)
