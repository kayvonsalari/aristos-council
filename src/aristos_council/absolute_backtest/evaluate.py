"""One round, one industry: the gate and the valuation on accounts published by the round date.

``evaluate_round`` reads every member through an ``AsOfAdapter`` in filing-date mode (period end + 90 days where EODHD
gives no filing date, counted by the adapter), sizes it, runs the gate (version 2) and, for names that pass, the valuation
(version 3) with the hurdle of the reporting currency on that date and no analyst figures. The result is a list of
``NameReading``.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Callable, Optional

from ..abs_readings import currency_relation, growth_record, latest_accounts_fx
from ..absolute_value.engine import NOT_ENOUGH_DATA, build_absolute
from ..data.asof_adapter import SPLIT_RATIO_BAND, AsOfAdapter
from ..growth_history import GrowthHistory
from ..gate.engine import QUALIFIES, evaluate_gate
from . import LAG_DAYS
from .selection import NameReading

AS_OF_MODE = "filing"


def share_jumps(f) -> list[int]:
    """Positions ``i`` (newest first) where the share count of period ``i`` against the next older period moved by a
    split-sized jump: per-share figures on either side of it are not in the same units."""
    sh = list(getattr(f, "shares_outstanding_annual", None) or [])
    return [i for i in range(len(sh) - 1)
            if sh[i] and sh[i + 1] and not (SPLIT_RATIO_BAND[0] <= sh[i] / sh[i + 1] <= SPLIT_RATIO_BAND[1])]


RECENT_PERIODS = 3          # a split-sized jump among the newest periods spoils the free cash flow base

# Ratios a split is made of (a jump of the share count is taken for a split only when it is within SPLIT_TOLERANCE of one).
SPLIT_RATIOS = tuple(sorted({float(k) for k in (2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50, 100)}
                            | {1.0 / k for k in (2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50, 100)}))
SPLIT_TOLERANCE = 0.12


def nearest_split_ratio(ratio: float) -> Optional[float]:
    """The split ratio a share-count jump of ``ratio`` stands for, or None when it is near none (a merger, a large issue
    or buyback, a spin-off: not a split, so not restated)."""
    best = min(SPLIT_RATIOS, key=lambda r: abs(ratio / r - 1.0))
    return best if abs(ratio / best - 1.0) <= SPLIT_TOLERANCE else None


def later_split_factor(f, full) -> Optional[float]:
    """The product of the splits in the provider's full record AFTER the newest period visible in ``f`` (1.0 when none),
    or None when a jump there is not a split ratio.

    The provider's share counts are as reported; the closes are adjusted to today's shares. A period reported before a
    split therefore needs its shares multiplied by the split to stand on the same basis as the price."""
    scalars = getattr(full, "period_scalars", None) or {}
    visible = getattr(f, "period_scalars", None) or {}
    if not scalars or not visible:
        return None
    newest = max(visible)
    later = [(p, (scalars[p] or {}).get("shares_outstanding")) for p in sorted(scalars) if p >= newest]
    counts = [c for _, c in later if c and c > 0]
    factor = 1.0
    for a, b in zip(counts, counts[1:]):
        r = b / a
        if SPLIT_RATIO_BAND[0] <= r <= SPLIT_RATIO_BAND[1]:
            continue
        nice = nearest_split_ratio(r)
        if nice is None:
            return None
        factor *= nice
    return factor


def restate_shares(f, factor: float):
    """``f`` with every share count of the visible accounts multiplied by ``factor`` (the provider's as-reported counts
    brought to today's basis). Uniform, so ratios between periods, and with them the earnings-per-share growth, are unchanged."""
    def scale(xs):
        return [None if v is None else v * factor for v in (xs or [])]
    scalars = {p: ({**s, "shares_outstanding": s["shares_outstanding"] * factor} if (s or {}).get("shares_outstanding") else s)
               for p, s in (f.period_scalars or {}).items()}
    aligned = {k: (scale(v) if k in ("shares_outstanding", "diluted_shares") else v)
               for k, v in (getattr(f, "aligned_annual", None) or {}).items()}
    return replace(f, period_scalars=scalars, aligned_annual=aligned,
                   shares_outstanding_annual=scale(getattr(f, "shares_outstanding_annual", None)),
                   diluted_shares_annual=scale(getattr(f, "diluted_shares_annual", None)))


def asof_growth_history(f) -> GrowthHistory:
    """Revenue and earnings per share by fiscal year from the accounts visible at the round date (newest first).

    Earnings per share is net income over the period's reported share count, matched by period end. A split-sized
    jump in the share count makes every older earnings-per-share figure incomparable with the newer ones, so those
    are left out (None) and a growth window that crosses the split is not worked out. Revenue is untouched."""
    ends = (getattr(f, "period_ends", None) or {})
    ni_dates, ni = ends.get("net_income") or [], list(getattr(f, "net_income", None) or [])
    if not ni_dates or len(ni_dates) != len(ni):
        return GrowthHistory()
    shares = dict(zip(ends.get("shares_outstanding_annual") or [], getattr(f, "shares_outstanding_annual", None) or []))
    rev = dict(zip(ends.get("total_revenue") or [], getattr(f, "total_revenue", None) or []))
    jumps = share_jumps(f)
    cut = (min(jumps) + 1) if jumps else None            # periods from this position on are older than the split
    sh_dates = ends.get("shares_outstanding_annual") or []
    cut_date = sh_dates[cut] if cut is not None and cut < len(sh_dates) else None
    years, revenue, eps = [], [], []
    for d, profit in zip(ni_dates, ni):
        years.append(f"FY{d[:4]}")
        revenue.append(rev.get(d))
        n = shares.get(d)
        spoiled = cut_date is not None and d <= cut_date
        eps.append(profit / n if profit is not None and n and not spoiled else None)
    return GrowthHistory(years=tuple(years), revenue=tuple(revenue), eps=tuple(eps), source="EODHD, as of the round date",
                         reports=sum(1 for v in revenue if v is not None))


def latest_shares(full) -> Optional[float]:
    """The share count of the newest period of the provider's full (not cut) record."""
    scalars = getattr(full, "period_scalars", None) or {}
    for p in sorted(scalars, reverse=True):
        v = (scalars[p] or {}).get("shares_outstanding")
        if v and v > 0:
            return float(v)
    return None


def size_usd(f, tape, d: date, cap_today_usd: Optional[float], shares_now: Optional[float]) -> tuple[Optional[float], str]:
    """The company's market value on ``d`` in US dollars, and how it was worked out.

    Historical share count times the price on the date (the as-of reader's ``market_cap``). A US-dollar listing uses it
    as is; any other is scaled by today's index value over today's shares times price, so no exchange rate is needed and
    none is guessed. When the as-of reader withholds it (a split after the visible accounts), today's value scaled by the
    price move is used and said so."""
    mc = getattr(f, "market_cap", None)
    raw_now = tape.raw_latest() if tape is not None else None
    if mc and mc > 0:
        if (getattr(f, "currency", None) or "").strip().upper() == "USD":
            return float(mc), "shares x price"
        if cap_today_usd and shares_now and raw_now:
            return cap_today_usd * float(mc) / (shares_now * raw_now), "shares x price, scaled to dollars"
    p_d = tape.raw(d) if tape is not None else None
    if cap_today_usd and p_d and raw_now:
        return cap_today_usd * p_d / raw_now, "price only"
    return None, ""


def evaluate_name(ticker: str, d: date, asof: AsOfAdapter, full, tape, *, cap_today_usd, size_floor,
                  gate_def, value_def, bonds: Callable) -> NameReading:
    p_d = tape.raw(d) if tape is not None else None
    try:
        f = asof.get_fundamentals(ticker)
    except Exception:                                                  # noqa: BLE001 - no accounts, counted
        f = None
    if f is None:
        return NameReading(ticker, eligible=False, no_accounts=True)
    cap, basis = size_usd(f, tape, d, cap_today_usd, latest_shares(full))
    eligible = p_d is not None and (size_floor is None or (cap is not None and cap >= size_floor))
    if size_floor is None and cap is None:
        eligible = p_d is not None
    no_accounts = not (f.period_ends and f.period_scalars)
    withheld = "market cap withheld" in (getattr(f, "provenance", None) or "")
    try:
        gate = evaluate_gate(f, gate_def)
    except Exception:                                                  # noqa: BLE001
        gate = None
    state = getattr(gate, "state", "") if gate is not None else NOT_ENOUGH_DATA
    reading = dict(eligible=eligible, gate_state=state, size_basis=basis, no_accounts=no_accounts)
    if not eligible or gate is None or state != QUALIFIES:
        return NameReading(ticker, **reading)
    recent_jump = any(i < RECENT_PERIODS for i in share_jumps(f))
    if withheld and not recent_jump:
        # a split AFTER the visible accounts (so, at the round date, in the future): restate, never withhold, so that the
        # names that later split are in the forms exactly as often as the names that did not
        factor = later_split_factor(f, full)
        if factor is not None:
            f, withheld = restate_shares(f, factor), False
    if withheld or recent_jump:
        return NameReading(ticker, split_withheld=True, backtest_state=NOT_ENOUGH_DATA, **reading)
    quote_fx = None
    if currency_relation(getattr(f, "currency", ""), getattr(f, "financial_currency", ""))[0] == "mixed":
        # price and accounts in different currencies: the month-end rate on or before the round date, as the live page does
        quote_fx = latest_accounts_fx(asof, f.financial_currency, f.currency, d)
    try:
        av = build_absolute(f, gate=gate, last_close=p_d, quote_fx=quote_fx, growth_record=growth_record(f, asof_growth_history(f)),
                            history=None, trend=None, today=d, definition=value_def, bond_source=bonds)
    except Exception:                                                  # noqa: BLE001 - not evaluated, never a crash
        return NameReading(ticker, backtest_state=NOT_ENOUGH_DATA, **reading)
    if av is None:
        return NameReading(ticker, backtest_state=NOT_ENOUGH_DATA, **reading)
    bt = av.backtest_verdict
    return NameReading(ticker, backtest_state=(bt.state if bt is not None else NOT_ENOUGH_DATA),
                       base_yield=av.yield_on_base,
                       no_hurdle=not av.hurdle.available,
                       no_fx="exchange rate" in (av.price.note or ""), **reading)


def evaluate_round(d: date, members, memo, fulls: dict, tapes: dict, *, member_caps: dict, size_floor,
                   gate_def, value_def, bonds: Callable, lag_days: int = LAG_DAYS):
    """``(readings, asof)`` for round date ``d``; ``asof`` carries the filing-date counts."""
    asof = AsOfAdapter(memo, d, lag_days, AS_OF_MODE)
    out = [evaluate_name(t, d, asof, fulls.get(t), tapes.get(t), cap_today_usd=member_caps.get(t),
                         size_floor=size_floor, gate_def=gate_def, value_def=value_def, bonds=bonds)
           for t in members]
    return out, asof
