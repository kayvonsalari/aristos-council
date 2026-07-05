"""Per-run input freezing + offline replay (hardening ITEM 4).

"Auditable" only means something if it means REPRODUCIBLE. Every rank run can freeze
the RAW adapter payloads it consumed — fundamentals, prices, dividends, and street
consensus per ticker — into a run record, and any run can then be replayed OFFLINE from
those frozen inputs, with NO network and byte-for-byte identical verdicts.

Layout (one run record):
    runs/<run_id>/manifest.json         provider, streak method, tickers, created-at
    runs/<run_id>/inputs/<TICKER>.json.gz   the four raw payloads for one ticker (gzip)

``RecordingAdapter`` wraps a live adapter and captures each fetch; ``freeze_run`` writes
the record; ``FrozenAdapter`` serves the record back with no network. Serialization is
shared with the daily cache (``data.cache``), so a frozen input and a cache entry are the
same bytes.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

from ..data.adapter import (
    DataUnavailable,
    MarketDataAdapter,
    PriceHistory,
)
from ..data.cache import (
    _deser_consensus,
    _deser_dividends,
    _deser_fundamentals,
    _deser_prices,
    _ser_consensus,
    _ser_dividends,
    _ser_fundamentals,
    _ser_prices,
)

RUNS_DIRNAME = "runs"


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name)


# --------------------------------------------------------------------------- #
# Recording — capture every raw fetch during a live run
# --------------------------------------------------------------------------- #
class RecordingAdapter(MarketDataAdapter):
    """Wrap an adapter; delegate every fetch AND capture its raw payload (keyed by
    ticker) for freezing. Identity (name/streak method/provenance) mirrors the inner
    adapter so the run is byte-identical to an unwrapped one."""

    def __init__(self, inner: MarketDataAdapter):
        self._inner = inner
        self.name = inner.name
        self.dividend_streak_method = inner.dividend_streak_method
        self.records: dict[str, dict] = {}

    def provider_for(self, data_kind: str) -> str:
        return self._inner.provider_for(data_kind)

    def _rec(self, ticker: str) -> dict:
        return self.records.setdefault(ticker, {"ticker": ticker})

    def get_fundamentals(self, ticker):
        obj = self._inner.get_fundamentals(ticker)
        self._rec(ticker)["fundamentals"] = _ser_fundamentals(obj)
        return obj

    def get_price_history(self, ticker, *, start, end):
        obj = self._inner.get_price_history(ticker, start=start, end=end)
        self._rec(ticker)["prices"] = _ser_prices(obj)
        return obj

    def get_dividend_history(self, ticker, *, start, end):
        obj = self._inner.get_dividend_history(ticker, start=start, end=end)
        self._rec(ticker)["dividends"] = _ser_dividends(obj)
        return obj

    def get_street_consensus(self, ticker):
        obj = self._inner.get_street_consensus(ticker)
        self._rec(ticker)["consensus"] = _ser_consensus(obj)
        return obj


def make_run_id(strategy_id: str, *, now: datetime | None = None) -> str:
    """A run id: UTC timestamp slug + strategy, unique enough to name a record dir."""
    ts = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{ts}_{strategy_id}"


def freeze_run(recording: RecordingAdapter, *, run_id: str, runs_dir: str | Path,
               created: str | None = None) -> Path:
    """Write a recorded run to ``runs_dir/<run_id>/`` (inputs + manifest). Returns the
    run-record directory."""
    d = Path(runs_dir) / run_id
    (d / "inputs").mkdir(parents=True, exist_ok=True)
    for ticker, rec in recording.records.items():
        with gzip.open(d / "inputs" / f"{_safe(ticker)}.json.gz", "wt",
                       encoding="utf-8") as fh:
            json.dump(rec, fh)
    manifest = {
        "run_id": run_id,
        "provider": recording.name,
        "streak_method": recording.dividend_streak_method,
        "created": created or datetime.now(timezone.utc).isoformat(),
        "tickers": sorted(recording.records),
    }
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return d


# --------------------------------------------------------------------------- #
# Replay — serve a frozen run with no network
# --------------------------------------------------------------------------- #
class FrozenAdapter(MarketDataAdapter):
    """Serve a frozen run record back with NO network. A fetch for a ticker/kind not in
    the record raises ``DataUnavailable`` (never a silent guess)."""

    def __init__(self, run_dir: str | Path):
        d = Path(run_dir)
        manifest_path = d / "manifest.json"
        if not manifest_path.exists():
            raise DataUnavailable(f"no frozen run record at {d}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.name = manifest.get("provider", "frozen")
        self.dividend_streak_method = manifest.get("streak_method",
                                                   "per_payment_median")
        self._inputs: dict[str, dict] = {}
        for p in sorted((d / "inputs").glob("*.json.gz")):
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                rec = json.load(fh)
            self._inputs[rec["ticker"]] = rec       # key by the ORIGINAL ticker

    def _rec(self, ticker: str) -> dict:
        if ticker not in self._inputs:
            raise DataUnavailable(f"no frozen input for {ticker} (replay)")
        return self._inputs[ticker]

    def get_fundamentals(self, ticker):
        rec = self._rec(ticker)
        if "fundamentals" not in rec:
            raise DataUnavailable(f"no frozen fundamentals for {ticker}")
        return _deser_fundamentals(rec["fundamentals"])

    def get_price_history(self, ticker, *, start, end):
        rec = self._rec(ticker)
        if "prices" not in rec:
            raise DataUnavailable(f"no frozen prices for {ticker}")
        ph = _deser_prices(rec["prices"])
        bars = [b for b in ph.bars if start <= b.day <= end]
        return PriceHistory(ticker=ph.ticker, bars=bars)

    def get_dividend_history(self, ticker, *, start, end):
        rec = self._rec(ticker)
        evs = _deser_dividends(rec.get("dividends", []))
        return [e for e in evs if start <= e.ex_date <= end]

    def get_street_consensus(self, ticker):
        rec = self._rec(ticker)
        if "consensus" not in rec:
            from ..data.adapter import StreetConsensus
            return StreetConsensus(ticker=ticker)
        return _deser_consensus(rec["consensus"])
