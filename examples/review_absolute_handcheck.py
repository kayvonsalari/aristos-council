"""ABSOLUTE-BACKTEST-REVIEW, part A item 6: hand-check one name for one round against figures computed independently.

    python examples/review_absolute_handcheck.py TICKER SLUG YYYY-MM-DD [EODHD_FILE_STEM]

Read-only. The "pipeline" side calls ``absolute_backtest.evaluate.evaluate_name`` exactly as the runner does, through the
day cache (no EODHD units are spent when the run's cache is present). The "independent" side reads the cached raw EODHD
payload, the cached raw yfinance bars and the cached bond rows with plain Python, and does the visibility cut, the per-share
conversion, the price conversion and the hurdle by hand. The two are printed side by side.
"""
from __future__ import annotations

import glob
import json
import sys
from bisect import bisect_right
from datetime import date, timedelta
from pathlib import Path

CACHE = Path(".aristos_cache")
DAY = "2026_11_04"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["data"]


def independent(ticker_file: str, yahoo: str, d: date, lag: int = 90):
    f = load(CACHE / f"eodhd_dated_{ticker_file}_{DAY}_fundamentals.json")
    ps, filed = f["period_scalars"], f["filing_dates"]
    ends = f["period_ends"]["total_revenue"]
    fd_by_period = {}
    for name, dates in f["period_ends"].items():           # a period is filed once every statement is: the latest date
        fl = filed.get(name) or []
        if len(fl) == len(dates):
            for p, x in zip(dates, fl):
                if x and x > p:
                    fd_by_period[p] = max(fd_by_period.get(p, ""), x)
    visible, used_fallback = [], {}
    for p in sorted(ps, reverse=True):
        fdt = fd_by_period.get(p)
        ok = (fdt <= d.isoformat()) if fdt else (p <= (d - timedelta(days=lag)).isoformat())
        used_fallback[p] = fdt is None
        if ok:
            visible.append(p)
    wc = dict(zip(f["period_ends"]["change_in_working_capital_annual"], f["change_in_working_capital_annual"]))
    rows = []
    for p in visible[:3]:
        s = ps[p]
        ocf, capex, sh = s["operating_cash_flow"], s["capital_expenditure"], s["shares_outstanding"]
        pre = ocf - wc[p]
        rows.append((p, fd_by_period.get(p), ocf, wc[p], capex, sh, (pre - abs(capex)) / sh))
    vals = [r[-1] for r in rows]
    latest, avg = vals[0], sum(vals) / 3
    base = min(latest, avg)
    # raw close on or before d (10-day tolerance, as the Tape)
    bars = load(CACHE / sorted(glob.glob(str(CACHE / f"yfinance_{yahoo.replace('.', '_').replace('^', '')}_{DAY}_prices_*.json")))[0].split("\\")[-1].split("/")[-1])["bars"]
    days = [b["day"] for b in bars]
    i = bisect_right(days, d.isoformat()) - 1
    px = bars[i]["close"]
    return dict(currency=f["currency"], fin_currency=f["financial_currency"], visible=visible[:4], rows=rows,
                latest=latest, avg=avg, base=base, raw_close=px, close_day=days[i], filing_fallback={p: used_fallback[p] for p in visible[:4]})


def bond(symbol: str, d: date):
    rows = json.loads((CACHE / f"absolute_backtest_bond_{symbol}_{DAY}.json").read_text(encoding="utf-8"))
    rows = sorted((r["date"][:10], float(r["close"])) for r in rows if r.get("close"))
    i = bisect_right([r[0] for r in rows], d.isoformat()) - 1
    return rows[i]


def pipeline(ticker: str, slug: str, d: date):
    from aristos_council.absolute_backtest.evaluate import evaluate_name
    from aristos_council.absolute_backtest.market import HistoricalBonds, Tape
    from aristos_council.absolute_backtest.runner import default_feed, default_end
    from aristos_council.absolute_value.definition import load_definition
    from aristos_council.backtest import _size_floor, load_cohort_members, round_dates
    from aristos_council.data.asof_adapter import AsOfAdapter
    from aristos_council.data.backtest_feed import MemoAdapter, lookback_start
    from aristos_council.gate.definition import load_gate
    end = date(2026, 10, 31)
    members, version, caps = load_cohort_members(slug)
    floor, _ = _size_floor(slug, None, version, caps)
    dates = round_dates(date(end.year - 10, end.month, 1), end, 12, 1)
    memo = MemoAdapter(default_feed(), start=lookback_start(dates[0]), end=end)
    tape = Tape(memo.get_price_history(ticker, start=lookback_start(dates[0]), end=end).bars)
    full = memo.get_fundamentals(ticker)
    asof = AsOfAdapter(memo, d, 90, "filing")
    bonds = HistoricalBonds(cache_dir=".aristos_cache")
    r = evaluate_name(ticker, d, asof, full, tape, cap_today_usd=caps.get(ticker), size_floor=floor,
                      gate_def=load_gate(), value_def=load_definition(), bonds=bonds)
    f = asof.get_fundamentals(ticker)
    return r, f, tape, load_definition(), bonds


def main(argv):
    ticker, slug, ds = argv[1], argv[2], date.fromisoformat(argv[3])
    stem = argv[4] if len(argv) > 4 else ticker.replace(".", "_")
    r, f, tape, vd, bonds = pipeline(ticker, slug, ds)
    print(f"== {ticker} ({slug}) on {ds}")
    print(f"pipeline: eligible={r.eligible} gate={r.gate_state} backtest={r.backtest_state} base_yield={r.base_yield} "
          f"no_hurdle={r.no_hurdle} no_fx={r.no_fx} split_withheld={r.split_withheld} size_basis={r.size_basis}")
    ind = independent(stem, ticker, ds)
    print("independent: currencies", ind["currency"], "/", ind["fin_currency"], " visible periods", ind["visible"])
    print("  filing-date fallback used for:", ind["filing_fallback"])
    for p, fdt, ocf, wc, capex, sh, v in ind["rows"]:
        print(f"  FY {p} filed {fdt}: ocf {ocf:,.0f} - dWC {wc:,.0f} = {ocf-wc:,.0f}; capex {abs(capex):,.0f}; shares {sh:,.0f}"
              f" -> FCF/share {v:.4f}")
    print(f"  latest {ind['latest']:.4f}  3y avg {ind['avg']:.4f}  base {ind['base']:.4f}")
    print(f"  raw close {ind['raw_close']:.4f} on {ind['close_day']}  (quote currency {ind['currency']})")
    br = bonds(ind["fin_currency"], today=ds)
    print(f"  pipeline's hurdle leg: {br.yield_.symbol if br.available else br.reason} yield {br.yield_.value if br.available else None} on {br.yield_.as_of if br.available else None}")
    print("  newest visible period in the pipeline's as-of read:", max(f.period_scalars) if f.period_scalars else None)
    if len(argv) > 5:                                   # FX leg and bond symbol, by hand: rate (to per from), bond symbol
        rate, sym = float(argv[5]), argv[6]
        px = ind["raw_close"] / rate
        when, y = bond(sym, ds)
        print(f"  price in statement currency = {ind['raw_close']:.4f} / {rate} = {px:.4f}; bond {sym} {y} on {when}; hurdle {y/100+0.05:.4f}")
        print(f"  independent base yield = {ind['base']/px:.5f}")
    else:
        sym = argv[5] if len(argv) > 5 else None
        sym = {"USD": "US10Y"}.get(ind["fin_currency"], sym)
        if sym:
            when, y = bond(sym, ds)
            print(f"  independent bond {sym}: {y} on {when}; hurdle {y/100+0.05:.4f}")
        print(f"  independent base yield = {ind['base']/ind['raw_close']:.5f}")


if __name__ == "__main__":
    main(sys.argv)
