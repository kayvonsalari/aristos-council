"""Price/dividend cache must key on the WINDOW, not just (ticker, kind).

THE BUG (2026-08-22): DayCache/CachingAdapter cached get_price_history under
(provider, ticker, today, "prices") — the start/end window was forwarded to the inner
adapter on a miss but was NOT part of the cache key. gather_factor_inputs fetches two
windows per name — 400 days for momentum/volatility FIRST, then 5 years for the valuation
band — so the band's 5-year request collided with the cached 400-day series and every name
abstained with "insufficient history: 1.1y" (400/365.25). The reverse order is worse: the
ranking legs would silently get 5 years and the verdict of record would change with no
error. The deterministic ranker must never depend on cache fill order.

These tests pin the window into the cache identity and the order-independence that follows.
"""

from __future__ import annotations

import calendar
import inspect
from datetime import date, timedelta

from aristos_council.data.adapter import (
    DividendEvent,
    Fundamentals,
    MarketDataAdapter,
    PriceBar,
    PriceHistory,
)
from aristos_council.data.cache import CachingAdapter
from aristos_council.factors import gather_factor_inputs
from aristos_council.tools.valuation_band import BAND_YEARS

TODAY = date(2026, 8, 22)
_BAND_START = TODAY - timedelta(days=round(365.25 * BAND_YEARS) + 10)   # what the band asks
_400D_START = TODAY - timedelta(days=400)                               # what the legs ask


def _month_end_bars(start: date, end: date, price: float = 100.0) -> list[PriceBar]:
    """One flat bar per month-end across [start, end], newest pinned to ``end`` — so a
    WIDER window genuinely returns MORE bars (the whole point: a 5-year fetch must not be
    answerable from a 400-day one). Flat price -> a computable band sits at the 50th."""
    bars: list[PriceBar] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        d = date(y, m, calendar.monthrange(y, m)[1])
        if d < start:
            d = start
        if d > end:
            d = end
        bars.append(PriceBar(day=d, open=price, high=price, low=price, close=price,
                             adj_close=price * 0.9, volume=1))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    if bars:
        bars[-1] = PriceBar(day=end, open=price, high=price, low=price, close=price,
                            adj_close=price * 0.9, volume=1)
    return bars


def _fiscal_years(n: int, last: int = 2025) -> list[str]:
    return [f"{last - i}-12-31" for i in range(n)]


def _dated_fundamentals(ticker: str) -> Fundamentals:
    """Constant EBIT / debt / cash on the DATED series the band reads — so a real EV/EBIT
    band computes whenever it is handed enough monthly bars."""
    ends = _fiscal_years(6)
    aligned = {"ebit": [100.0] * 6, "total_debt": [200.0] * 6, "cash": [50.0] * 6}
    period_ends = {k: ends for k in aligned}
    return Fundamentals(ticker=ticker, name=ticker, market_cap=1000.0,
                        aligned_annual=aligned, aligned_period_ends=period_ends)


class _RecordingAdapter(MarketDataAdapter):
    """Records every (ticker, start, end) it is asked for, and returns bars that SPAN the
    requested window — so the cache collision is observable end to end."""

    name = "fake"

    def __init__(self):
        self.price_windows: list[tuple[str, date, date]] = []
        self.div_windows: list[tuple[str, date, date]] = []

    def get_fundamentals(self, ticker):
        return _dated_fundamentals(ticker)

    def get_price_history(self, ticker, *, start, end):
        self.price_windows.append((ticker, start, end))
        return PriceHistory(ticker=ticker, bars=_month_end_bars(start, end))

    def get_dividend_history(self, ticker, *, start, end):
        self.div_windows.append((ticker, start, end))
        return [DividendEvent(ex_date=date(2025, 6, 1), amount=1.25)]


def _cache(inner, tmp, *, today=TODAY, refresh=False):
    return CachingAdapter(inner, cache_dir=tmp, today=today, refresh=refresh)


def _gather(adapter, ticker):
    """Call gather_factor_inputs with the band ON, whether the band is opt-in (post
    valband-1-gaps: with_valuation_band=True) or always-on (main). Robust to merge order."""
    kw = {}
    if "with_valuation_band" in inspect.signature(gather_factor_inputs).parameters:
        kw["with_valuation_band"] = True
    return gather_factor_inputs(adapter, ticker, today=TODAY, **kw)


# --------------------------------------------------------------------------- #
# STEP 1 — the reproduction. FAILS on the pre-fix cache (band served the 400-day series).
# --------------------------------------------------------------------------- #
def test_band_gets_a_five_year_series_through_the_day_cache(tmp_path):
    inner = _RecordingAdapter()
    fi = _gather(_cache(inner, tmp_path), "PFE")

    # the inner adapter must have been asked for BOTH windows for this ticker — the 5-year
    # band window was NOT collapsed into the cached 400-day fetch.
    windows = [(s, e) for (t, s, e) in inner.price_windows if t == "PFE"]
    assert (_400D_START, TODAY) in windows, windows
    assert (_BAND_START, TODAY) in windows, windows          # FAILS pre-fix: never fetched

    # and the band the ranker would show is a real ~5-year band, not the 1.1y abstention.
    band = fi.valuation_band
    assert band is not None and band.available, \
        f"band abstained: {getattr(band, 'note', None)!r}"   # FAILS pre-fix: "…1.1y"
    assert band.years_covered >= 3.0
    assert band.months_covered >= 36


# --------------------------------------------------------------------------- #
# Order independence — the silent-ranking-change hazard. The 400-day CONSUMER must get
# 400 days regardless of which window is fetched first. FAILS on pre-fix code too.
# --------------------------------------------------------------------------- #
def _bar_count(cache, ticker, start, end):
    return len(cache.get_price_history(ticker, start=start, end=end).bars)


def test_the_400_day_consumer_gets_400_days_even_if_the_band_fetched_first(tmp_path):
    inner = _RecordingAdapter()
    cache = _cache(inner, tmp_path)
    # BAND (5-year) fetched FIRST, then the momentum/volatility leg (400-day) SECOND.
    band_bars = _bar_count(cache, "PFE", _BAND_START, TODAY)
    legs_bars = _bar_count(cache, "PFE", _400D_START, TODAY)
    # each window returns its OWN span — the 400-day leg is NOT handed the 5-year series
    # (which, feeding annualized_volatility, would change low_volatility and the ranking).
    assert band_bars == len(_month_end_bars(_BAND_START, TODAY))
    assert legs_bars == len(_month_end_bars(_400D_START, TODAY))
    assert band_bars > legs_bars                              # ~60 months vs ~14


def test_both_fetch_orders_yield_the_same_two_series(tmp_path):
    fwd = _cache(_RecordingAdapter(), tmp_path / "fwd")
    a1 = _bar_count(fwd, "PFE", _400D_START, TODAY)
    b1 = _bar_count(fwd, "PFE", _BAND_START, TODAY)
    rev = _cache(_RecordingAdapter(), tmp_path / "rev")
    b2 = _bar_count(rev, "PFE", _BAND_START, TODAY)
    a2 = _bar_count(rev, "PFE", _400D_START, TODAY)
    assert (a1, b1) == (a2, b2)                               # order cannot change a window


# --------------------------------------------------------------------------- #
# Cache identity — two windows are two entries; the SAME window still hits.
# --------------------------------------------------------------------------- #
def test_two_windows_are_two_entries_same_window_is_one(tmp_path):
    inner = _RecordingAdapter()
    cache = _cache(inner, tmp_path)
    cache.get_price_history("PFE", start=_400D_START, end=TODAY)
    cache.get_price_history("PFE", start=_BAND_START, end=TODAY)   # different window -> miss
    assert len(inner.price_windows) == 2                          # two distinct fetches
    cache.get_price_history("PFE", start=_400D_START, end=TODAY)   # repeat -> HIT
    cache.get_price_history("PFE", start=_BAND_START, end=TODAY)   # repeat -> HIT
    assert len(inner.price_windows) == 2                          # cache still saves calls


def test_the_window_is_in_the_cache_key_and_path(tmp_path):
    cache = _cache(_RecordingAdapter(), tmp_path)
    k400 = cache.cache_key("PFE", "prices", "2025-07-18_2026-08-22")
    k5y = cache.cache_key("PFE", "prices", "2021-08-12_2026-08-22")
    assert k400 != k5y
    assert k400.endswith(":2025-07-18_2026-08-22")
    # window-less kinds are keyed exactly as before (byte-identical filenames).
    assert cache.cache_key("PFE", "fundamentals") == "fake:PFE:2026-08-22:fundamentals"


def test_dividends_key_on_the_window_too(tmp_path):
    inner = _RecordingAdapter()
    cache = _cache(inner, tmp_path)
    cache.get_dividend_history("PFE", start=date(2020, 1, 1), end=TODAY)
    cache.get_dividend_history("PFE", start=date(2024, 1, 1), end=TODAY)   # diff -> miss
    assert len(inner.div_windows) == 2
    cache.get_dividend_history("PFE", start=date(2020, 1, 1), end=TODAY)   # repeat -> HIT
    assert len(inner.div_windows) == 2


# --------------------------------------------------------------------------- #
# Old-schema (window-less) cache dir is invalidated, not misread.
# --------------------------------------------------------------------------- #
def test_a_pre_v4_window_less_price_entry_is_never_misread(tmp_path):
    import json

    from aristos_council.data.cache import ADAPTER_SCHEMA_VERSION
    inner = _RecordingAdapter()
    cache = _cache(inner, tmp_path)
    # a pre-v4 file: window-less key, and a stale schema token. Write it where the OLD
    # code would have (no window in the path).
    stale_path = cache._path("PFE", "prices")                     # window="" -> old name
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_text(json.dumps({
        "_schema": f"v{ADAPTER_SCHEMA_VERSION - 1}:junk",
        "data": {"ticker": "PFE", "bars": [
            {"day": "2026-01-31", "open": 1, "high": 1, "low": 1, "close": 1,
             "adj_close": 1, "volume": 1}]}}), encoding="utf-8")
    # the new windowed fetch neither reads that file (different path) nor is misled by it.
    bars = cache.get_price_history("PFE", start=_400D_START, end=TODAY).bars
    assert len(inner.price_windows) == 1                          # a real fetch happened
    assert len(bars) == len(_month_end_bars(_400D_START, TODAY))  # fresh series, not the 1-bar stale


# --------------------------------------------------------------------------- #
# Honest abstention survives the fix: a genuinely short-history name still abstains.
# --------------------------------------------------------------------------- #
class _ShortHistoryAdapter(_RecordingAdapter):
    """Only ~1.4 years of price history exist, whatever window is asked — a recent IPO."""

    def get_price_history(self, ticker, *, start, end):
        self.price_windows.append((ticker, start, end))
        ipo = TODAY - timedelta(days=round(365.25 * 1.4))
        lo = max(start, ipo)
        return PriceHistory(ticker=ticker, bars=_month_end_bars(lo, end))


def test_a_short_history_name_still_abstains_with_its_true_span(tmp_path):
    fi = _gather(_cache(_ShortHistoryAdapter(), tmp_path), "IPO")
    band = fi.valuation_band
    assert band is not None and not band.available            # genuinely too short
    assert "insufficient history" in band.note
    assert band.years_covered < 3.0                           # its REAL span, not 1.1y noise


# --------------------------------------------------------------------------- #
# MUST NOT BREAK — the ranking legs read the 400-day window and nothing else.
# annualized_volatility consumes the WHOLE close list, so a 5-year series reaching it
# (the reverse-order hazard) would change low_volatility and every strategy's ranking.
# Asserted, not assumed. Daily bars so the legs are all computable and the two windows
# give genuinely different volatilities.
# --------------------------------------------------------------------------- #
_EPOCH = _BAND_START


def _daily_rising_bars(start: date, end: date) -> list[PriceBar]:
    """One bar per calendar day; price is a function of the DAY (not of the window), so the
    recent tail is identical across windows and only the WHOLE-list volatility differs."""
    bars: list[PriceBar] = []
    d = start
    while d <= end:
        px = 100.0 + 0.1 * (d - _EPOCH).days
        bars.append(PriceBar(day=d, open=px, high=px, low=px, close=px, adj_close=px,
                             volume=1))
        d += timedelta(days=1)
    return bars


class _DailyRisingAdapter(_RecordingAdapter):
    def get_price_history(self, ticker, *, start, end):
        self.price_windows.append((ticker, start, end))
        return PriceHistory(ticker=ticker, bars=_daily_rising_bars(start, end))


def test_the_ranking_legs_read_the_400_day_window_not_the_5_year(tmp_path):
    from aristos_council.tools.technical import _TD_6M, _TD_12M, annualized_volatility, \
        total_return

    inner = _DailyRisingAdapter()
    fi = _gather(_cache(inner, tmp_path), "PFE")             # band ON: legs 400d, band 5y

    ref = _DailyRisingAdapter()
    closes_400 = ref.get_price_history("PFE", start=_400D_START, end=TODAY).closes
    closes_5y = ref.get_price_history("PFE", start=_BAND_START, end=TODAY).closes

    # the WINDOWS genuinely differ on volatility — so this is not a vacuous equality.
    assert annualized_volatility(closes_400) != annualized_volatility(closes_5y)

    # the legs read the 400-day series: every leg equals its 400-day computation, and the
    # volatility is the 400-day one, never the 5-year one that would move low_volatility.
    assert fi.annualized_volatility == annualized_volatility(closes_400)
    assert fi.annualized_volatility != annualized_volatility(closes_5y)
    assert fi.return_6m == total_return(closes_400, _TD_6M)
    assert fi.return_12m == total_return(closes_400, _TD_12M)   # = momentum_12m's input

    # and the band still got its own 5-year window (both legs fetched, no collision).
    windows = {(s, e) for (t, s, e) in inner.price_windows if t == "PFE"}
    assert (_400D_START, TODAY) in windows and (_BAND_START, TODAY) in windows
