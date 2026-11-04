"""ABSOLUTE-BACKTEST-2, stage 1: one table per industry of what each name looked like on each round date.

Nothing here decides anything new. It runs the SAME per-name evaluation as the corrected run (``evaluate.evaluate_name``,
unchanged) and records, beside the reading the forms select on, three things the new analyses need: the market value in
US dollars on the date, the Quality lens's gross profit over total assets on the accounts visible then, and the name's
12-month total return from the round date. The forms' own selections are rebuilt from the table by ``selection`` and
checked against the corrected run's files (``check_against_corrected``).

Data: the day cache dated ``CACHE_DATE`` only. The cache date is fixed here, not read from the clock, so the job gives the same
answer after midnight; the EODHD key is blanked by the caller before any import; a name whose files are not cached is
skipped and counted. No units are spent: the offline feed raises on any call that would reach a provider.
"""
from __future__ import annotations

import csv
import gzip
import json
from datetime import date
from pathlib import Path
from typing import Optional

from ..backtest import _size_floor, add_months, load_cohort_members, round_dates
from ..data.adapter import MarketDataAdapter
from ..data.asof_adapter import AsOfAdapter
from ..data.backtest_feed import MemoAdapter, lookback_start
from ..gate.definition import load_gate
from ..absolute_value.definition import load_definition
from ..tools.quality_epv import gross_profitability
from . import LAG_DAYS, YEARS
from .evaluate import AS_OF_MODE, evaluate_name, latest_shares, size_usd
from .market import HistoricalBonds, Tape

CACHE_DATE = date(2026, 11, 4)
END = date(2026, 10, 31)
HOLD = 12
NAMES_FILE = "names.csv.gz"
META_FILE = "meta.json"
COLUMNS = ["date", "ticker", "eligible", "gate_state", "backtest_state", "base_yield", "cap_usd", "size_basis",
           "gp_assets", "ret12"]


class NotCached(RuntimeError):
    """A fetch that would have left the day cache."""


class OfflineFeed(MarketDataAdapter):
    """The inner adapter under the cache: every call is a cache miss, and a miss is an error, never a purchase."""
    name = "offline"

    def get_fundamentals(self, ticker):
        raise NotCached(ticker)

    def get_price_history(self, ticker, *, start, end):
        raise NotCached(ticker)

    def get_dividend_history(self, ticker, *, start, end):
        raise NotCached(ticker)

    def provider_for(self, data_kind: str) -> str:
        # must match the live feed's provider names or the cache keys differ
        return "eodhd-dated" if data_kind == "fundamentals" else "yfinance"


def pinned_feed():
    from ..data.cache import DEFAULT_CACHE_DIR, CachingAdapter
    return CachingAdapter(OfflineFeed(), cache_dir=DEFAULT_CACHE_DIR, today=CACHE_DATE)


def pinned_bonds():
    from ..data.cache import DEFAULT_CACHE_DIR
    return HistoricalBonds(cache_dir=DEFAULT_CACHE_DIR, today=CACHE_DATE)


class RecordingAsOf(AsOfAdapter):
    """The as-of adapter that remembers the accounts it served for each ticker, so the size and the gross-profit reading
    come from the very same cut the gate and the valuation saw."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.served: dict = {}

    def get_fundamentals(self, ticker):
        f = super().get_fundamentals(ticker)
        self.served[ticker] = f
        return f


def gp_assets_of(f) -> Optional[float]:
    """The Quality lens's gross profit over total assets on the accounts ``f`` (None when not readable)."""
    if f is None:
        return None
    try:
        return gross_profitability(f)[0]
    except Exception:                                                  # noqa: BLE001 - not readable, not failed
        return None


def extract_industry(slug: str, out_root, *, cohorts_root=None, adapter=None, bonds=None,
                     members=None, member_caps=None, end: date = END, name: Optional[str] = None) -> dict:
    """Write ``<out_root>/<slug>/names.csv.gz`` and ``meta.json``; return the meta dict."""
    gate_def, value_def = load_gate(), load_definition()
    version = None
    if members is None:
        members, version, member_caps = load_cohort_members(slug, cohorts_root)
    members, member_caps = list(members), dict(member_caps or {})
    size_floor, floor_note = _size_floor(slug, cohorts_root, version, member_caps) if member_caps else (None, "")
    start = date(end.year - YEARS, end.month, 1)
    dates = round_dates(start, end, HOLD, 1)
    memo = MemoAdapter(adapter if adapter is not None else pinned_feed(), start=lookback_start(dates[0]), end=end)
    bonds = bonds or pinned_bonds()

    tapes, fulls = {}, {}
    for t in members:
        try:
            tapes[t] = Tape(memo.get_price_history(t, start=lookback_start(dates[0]), end=end).bars)
        except Exception:                                              # noqa: BLE001 - unpriced or not cached
            tapes[t] = None
        try:
            fulls[t] = memo.get_fundamentals(t)
        except Exception:                                              # noqa: BLE001 - no accounts or not cached
            fulls[t] = None

    folder = Path(out_root) / slug
    folder.mkdir(parents=True, exist_ok=True)
    rows = 0
    with gzip.open(folder / NAMES_FILE, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(COLUMNS)
        for d in dates:
            asof = RecordingAsOf(memo, d, LAG_DAYS, AS_OF_MODE)
            exit_d = add_months(d, HOLD)
            for t in members:
                tape = tapes.get(t)
                r = evaluate_name(t, d, asof, fulls.get(t), tape, cap_today_usd=member_caps.get(t),
                                  size_floor=size_floor, gate_def=gate_def, value_def=value_def, bonds=bonds)
                if not r.eligible:
                    continue                                           # not in the pool this month (no accounts, unpriced, under the floor)
                f = asof.served.get(t)
                cap, basis = (size_usd(f, tape, d, member_caps.get(t), latest_shares(fulls.get(t)))
                              if f is not None else (None, ""))
                ret = tape.total_return(d, exit_d) if (tape is not None and r.eligible) else None
                gp = gp_assets_of(f) if r.gate_state == "qualifies" else None
                w.writerow([d.isoformat(), t, int(r.eligible), r.gate_state, r.backtest_state,
                            "" if r.base_yield is None else repr(float(r.base_yield)),
                            "" if cap is None else repr(float(cap)), basis,
                            "" if gp is None else repr(float(gp)), "" if ret is None else repr(float(ret))])
                rows += 1
    meta = {"slug": slug, "industry": name or slug, "members": len(members),
            "names_without_accounts": sum(1 for f in fulls.values() if f is None),
            "names_unpriced": sum(1 for t in tapes.values() if t is None),
            "rounds": len(dates), "first": dates[0].isoformat(), "last": dates[-1].isoformat(), "rows": rows,
            "cache_date": CACHE_DATE.isoformat(), "gate": gate_def.id, "valuation": value_def.id}
    (folder / META_FILE).write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta
