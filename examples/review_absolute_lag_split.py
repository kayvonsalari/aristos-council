"""ABSOLUTE-BACKTEST-REVIEW, part A items 1, 2 and 7: how stale the signal is, and what the split rule does.

    python examples/review_absolute_lag_split.py [--out backtests_absolute/cuts/lag_split.json]

Read-only over the day cache (no EODHD units). For every member of every built industry it measures

* LAG: how many names carry real filing dates, how late real filings are against period end + 90 days (a real filing
  LATER than that is a name the 90-day rule would show early), and the age of the newest visible accounts at each round for
  dated and undated names;
* SPLIT RULE: ``evaluate_name`` withholds the valuation (so the name can be in neither form 2 nor form 3) for any gate-passer
  whose provider share count jumps by a split-sized ratio in ANY later period, up to today. A name that splits in 2022 is
  withheld in every round before 2022. Per round and industry this prints how many names are withheld and what they went on
  to earn over the 12-month hold, against the names that were not withheld.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

from aristos_council.absolute_backtest.market import Tape
from aristos_council.absolute_backtest.runner import default_feed, list_industries
from aristos_council.backtest import add_months, load_cohort_members, round_dates
from aristos_council.data.asof_adapter import SPLIT_RATIO_BAND, AsOfAdapter
from aristos_council.data.backtest_feed import MemoAdapter, lookback_start
from aristos_council.cohorts.symbols import SymbolError, eodhd_symbol

END = date(2026, 10, 31)


def periods_info(full):
    """sorted ascending [(period, filing or None, shares)] over the provider's full record."""
    ps = full.period_scalars or {}
    filed = AsOfAdapter._period_filing(full)
    return [(p, filed.get(p), (ps[p] or {}).get("shares_outstanding")) for p in sorted(ps)]


def last_jump(info):
    """(period a, ratio b/a) of the LAST split-sized adjacent pair in the full share-count record, else None."""
    pts = [(p, s) for p, _, s in info if s and s > 0]
    out = None
    for (pa, a), (pb, b) in zip(pts, pts[1:]):
        r = b / a
        if not SPLIT_RATIO_BAND[0] <= r <= SPLIT_RATIO_BAND[1]:
            out = (pa, r)
    return out


def visible_newest(info, d: date, lag=90):
    cut = date.fromordinal(d.toordinal() - lag).isoformat()
    best = None
    for p, fd, _ in info:
        ok = (fd <= d.isoformat()) if fd else (p <= cut)
        if ok:
            best = p
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="backtests_absolute/cuts/lag_split.json")
    a = ap.parse_args()
    dates = round_dates(date(END.year - 10, END.month, 1), END, 12, 1)
    feed = default_feed()
    memo = MemoAdapter(feed, start=lookback_start(dates[0]), end=END)
    lag_days, name_dated, ages = [], [], {"dated": [], "undated": []}
    late_vs_90 = [0, 0]
    withheld_rows, per_industry = [], {}
    suffix_dated = defaultdict(lambda: [0, 0])
    for slug, nm in list_industries():
        members, _, _ = load_cohort_members(slug)
        infos, tapes = {}, {}
        for t in members:
            try:
                full = memo.get_fundamentals(t)
                infos[t] = periods_info(full)
                tapes[t] = Tape(memo.get_price_history(t, start=lookback_start(dates[0]), end=END).bars)
            except Exception:                                          # noqa: BLE001
                continue
        for t, info in infos.items():
            dated = [(p, fd) for p, fd, _ in info if fd]
            share = len(dated) / len(info) if info else 0
            name_dated.append(share >= 0.5)
            suf = t.rsplit(".", 1)[-1] if "." in t else "US"
            suffix_dated[suf][0] += share >= 0.5
            suffix_dated[suf][1] += 1
            for p, fd in dated:
                lg = (date.fromisoformat(fd) - date.fromisoformat(p)).days
                lag_days.append(lg)
                late_vs_90[0] += lg > 90
                late_vs_90[1] += 1
        w_gap, w_share = [], []
        for d in dates:
            ex = add_months(d, 12)
            if ex > END:
                continue
            rets_w, rets_n = [], []
            for t, info in infos.items():
                tape = tapes.get(t)
                if tape is None or tape.raw(d) is None:
                    continue
                r = tape.total_return(d, ex)
                newest = visible_newest(info, d)
                if r is None or newest is None:
                    continue
                dated_name = sum(1 for _, fd, _ in info if fd) / len(info) >= 0.5
                newest_end = date.fromisoformat(newest)
                ages["dated" if dated_name else "undated"].append((d - newest_end).days)
                lj = last_jump(info)
                (rets_w if lj and newest <= lj[0] else rets_n).append((r, lj))
            if rets_w and rets_n:
                allr = [r for r, _ in rets_w + rets_n]
                mw, mn, ma = (sum(r for r, _ in x) / len(x) for x in (rets_w, rets_n, rets_w + rets_n))
                withheld_rows.append(dict(slug=slug, date=d.isoformat(), n=len(allr), n_withheld=len(rets_w),
                                          mean_withheld=mw, mean_rest=mn, mean_all=ma,
                                          fwd_split=sum(1 for _, lj in rets_w if lj[1] > 1), rev_split=sum(1 for _, lj in rets_w if lj[1] < 1)))
        memo._fund.clear()
        memo._prices.clear()
    res = {
        "names_with_filing_dates_share": sum(name_dated) / len(name_dated),
        "filing_lag_days": dict(n=len(lag_days), median=statistics.median(lag_days), p10=sorted(lag_days)[len(lag_days) // 10],
                                p90=sorted(lag_days)[9 * len(lag_days) // 10], share_over_90=late_vs_90[0] / late_vs_90[1]),
        "newest_accounts_age_days": {k: dict(n=len(v), mean=sum(v) / len(v), median=statistics.median(v)) for k, v in ages.items() if v},
        "dated_by_suffix": {k: v for k, v in sorted(suffix_dated.items(), key=lambda kv: -kv[1][1])[:12]},
        "split_rows": len(withheld_rows),
    }
    if withheld_rows:
        tot_n = sum(r["n"] for r in withheld_rows)
        res["share_withheld_of_priced"] = sum(r["n_withheld"] for r in withheld_rows) / tot_n
        res["mean_fwd12_withheld"] = sum(r["mean_withheld"] * r["n_withheld"] for r in withheld_rows) / sum(r["n_withheld"] for r in withheld_rows)
        res["mean_fwd12_rest"] = sum(r["mean_rest"] * (r["n"] - r["n_withheld"]) for r in withheld_rows) / (tot_n - sum(r["n_withheld"] for r in withheld_rows))
        # what excluding the withheld names does to the average of the rest against the whole basket, round by round
        diffs = [r["mean_rest"] - r["mean_all"] for r in withheld_rows]
        res["rest_minus_basket_pts_per_year"] = 100 * sum(diffs) / len(diffs)
        res["forward_split_share_of_withheld"] = sum(r["fwd_split"] for r in withheld_rows) / sum(r["n_withheld"] for r in withheld_rows)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    Path(a.out.replace(".json", "_rows.json")).write_text(json.dumps(withheld_rows), encoding="utf-8")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
