"""JUDGE-PANEL-3 - score the panel's log against what happened: ``python -m aristos_council.judges score``.

For every row of ``judge_summary.csv`` it fetches each name's price on the judgement date and today,
computes the return since and the return of the company's cohort (the lens universe the panel was
shown, equal-weighted, over the same dates) as the benchmark, and marks each verdict right or wrong
by ONE rule, the same for a judge and for the lenses' verdict of record:

    BUY   is right if the name beat its cohort by more than 2 percentage points over the window
    SELL  is right if it trailed its cohort by more than 2
    HOLD  is right if it finished within +/-2
    ABSTAIN is never scored (nor an unstable, errored or missing judge, nor a MIXED record)

Windows are 3, 6 and 12 months from the judgement date. A window that has not ended yet is
"too early". Nothing here touches the page or any verdict: scoring is read-only analysis, and no
judge earns a vote until it has 12-month results across at least 30 names (docs/JUDGE_PANEL.md).

Prices come through the same day-cached adapter the rest of the app uses; the fetcher is a
parameter so the suite scores a fixture and never reaches a provider.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

from .log import FAMILY_COLUMNS, judges_dir
from .report import read_summary

SCORE_NAME = "score.csv"
WINDOWS = (3, 6, 12)
BAND_PP = 2.0                      # percentage points
MIN_PEERS = 3                      # fewer peers with prices than this is "no benchmark"
VOTE_BAR_NAMES = 30                # names with a 12-month result before any judge earns a vote
START_SLACK_DAYS = 7               # the price "on" the judgement date: first close within this many days after

SCORE_COLUMNS = ("run_date", "ticker", "subject", "verdict", "window_months", "status",
                 "company_return", "benchmark_return", "excess_pp", "n_peers",
                 "since_return", "since_excess_pp")

RIGHT, WRONG, TOO_EARLY, NOT_SCORED, NO_DATA = "right", "wrong", "too early", "not scored", "no data"
SCORABLE = ("BUY", "HOLD", "SELL")

# a price fetcher: ticker -> [(date, price), ...] oldest first (empty when unavailable)
PriceFetcher = Callable[[str], list]


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last))


def outcome(verdict: str, excess_pp: float) -> str:
    """right | wrong for a BUY/HOLD/SELL against the excess return in percentage points."""
    if verdict == "BUY":
        return RIGHT if excess_pp > BAND_PP else WRONG
    if verdict == "SELL":
        return RIGHT if excess_pp < -BAND_PP else WRONG
    if verdict == "HOLD":
        return RIGHT if abs(excess_pp) <= BAND_PP else WRONG
    return NOT_SCORED


def _price_at(series: list, d: date, *, after: bool) -> Optional[float]:
    """The close on ``d`` or the first close after it (``after``), else the last close on or before."""
    if not series:
        return None
    if after:
        for day, px in series:
            if day >= d:
                return px if (day - d).days <= START_SLACK_DAYS else None
        return None
    best = None
    for day, px in series:
        if day <= d:
            best = px
        else:
            break
    return best


def _return(series: list, start: date, end: date) -> Optional[float]:
    p0, p1 = _price_at(series, start, after=True), _price_at(series, end, after=False)
    if not p0 or p1 is None or p0 <= 0:
        return None
    return p1 / p0 - 1.0


def _cohort_return(prices: dict, peers: list, start: date, end: date):
    rets = [r for r in (_return(prices.get(p, []), start, end) for p in peers) if r is not None]
    return (sum(rets) / len(rets), len(rets)) if len(rets) >= MIN_PEERS else (None, len(rets))


def _day(text: str) -> date:
    return date.fromisoformat(str(text)[:10])


def subjects_of(row: dict) -> list[tuple[str, str]]:
    """``[(subject, verdict), ...]`` for one summary row: the lenses' record, then each family."""
    out = [("lenses", row.get("verdict_of_record", ""))]
    for _family, name in FAMILY_COLUMNS:
        out.append((name, (row.get(f"{name}_verdict") or "").strip()))
    return out


def score_rows(rows: list, prices: dict, today: date) -> list[dict]:
    """The score rows: one per (summary row, subject, window). ``prices`` maps ticker -> series."""
    out = []
    for row in rows:
        d0 = _day(row["run_date"])
        peers = [p for p in (row.get("peers") or "").split(";") if p]
        company = prices.get(row["ticker"], [])
        since = _return(company, d0, today)
        bench_since, _n = _cohort_return(prices, peers, d0, today)
        since_ex = (since - bench_since) * 100 if since is not None and bench_since is not None else None
        for subject, verdict in subjects_of(row):
            for months in WINDOWS:
                base = {"run_date": row["run_date"], "ticker": row["ticker"], "subject": subject,
                        "verdict": verdict, "window_months": months, "company_return": "",
                        "benchmark_return": "", "excess_pp": "", "n_peers": "",
                        "since_return": "" if since is None else f"{since:.4f}",
                        "since_excess_pp": "" if since_ex is None else f"{since_ex:.2f}"}
                end = add_months(d0, months)
                if verdict not in SCORABLE:
                    out.append({**base, "status": NOT_SCORED})
                elif end > today:
                    out.append({**base, "status": TOO_EARLY})
                else:
                    ret = _return(company, d0, end)
                    bench, n = _cohort_return(prices, peers, d0, end)
                    if ret is None or bench is None:
                        out.append({**base, "status": NO_DATA, "n_peers": n})
                        continue
                    excess = (ret - bench) * 100
                    out.append({**base, "status": outcome(verdict, excess), "company_return": f"{ret:.4f}",
                                "benchmark_return": f"{bench:.4f}", "excess_pp": f"{excess:.2f}",
                                "n_peers": n})
    return out


def disagreements(scored: list[dict]) -> list[dict]:
    """Every row where a judge's BUY/HOLD/SELL differs from the lenses' record, with who was right
    at the longest window that has a result (``None`` winner while every window is too early)."""
    index = defaultdict(dict)
    for r in scored:
        index[(r["run_date"], r["ticker"], r["subject"])][int(r["window_months"])] = r
    out = []
    for (run_date, ticker, subject), windows in sorted(index.items()):
        if subject == "lenses":
            continue
        rec = index.get((run_date, ticker, "lenses"), {})
        any_w = next(iter(windows.values()))
        any_rec = next(iter(rec.values()), None)
        if not any_rec or any_w["verdict"] not in SCORABLE or any_rec["verdict"] not in SCORABLE \
                or any_w["verdict"] == any_rec["verdict"]:
            continue
        winner, window = None, None
        for months in sorted(WINDOWS, reverse=True):
            j, l = windows.get(months), rec.get(months)
            if j and l and j["status"] in (RIGHT, WRONG) and l["status"] in (RIGHT, WRONG):
                window = months
                winner = ("judge" if j["status"] == RIGHT else "lenses" if l["status"] == RIGHT else "neither")
                break
        out.append({"run_date": run_date, "ticker": ticker, "subject": subject,
                    "judge_verdict": any_w["verdict"], "record": any_rec["verdict"],
                    "window": window, "right": winner})
    return out


def tally(scored: list[dict]) -> dict:
    """``{subject: {window: {"right", "wrong", "too early", "not scored", "no data"}}}``."""
    t: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for r in scored:
        t[r["subject"]][int(r["window_months"])][r["status"]] += 1
    return t


def names_with_12m(scored: list[dict]) -> dict:
    names: dict = defaultdict(set)
    for r in scored:
        if int(r["window_months"]) == 12 and r["status"] in (RIGHT, WRONG):
            names[r["subject"]].add(r["ticker"])
    return names


def summary_text(scored: list[dict]) -> str:
    if not scored:
        return "No judge panel results recorded yet, so there is nothing to score."
    t, names = tally(scored), names_with_12m(scored)
    lines = []
    n_rows = len({(r["run_date"], r["ticker"]) for r in scored})
    lines.append(f"Scored {n_rows} panel run(s). A BUY is right if the name beat its cohort by more than "
                 f"{BAND_PP:g} points, a SELL if it trailed by more than {BAND_PP:g}, a HOLD if it finished "
                 f"within {BAND_PP:g}; an abstention is never scored.")
    lines.append("")
    lines.append(f"{'subject':<8} {'window':>7} {'scored':>7} {'right':>6} {'wrong':>6} {'too early':>10} {'no data':>8}")
    for subject in ("lenses", *[n for _, n in FAMILY_COLUMNS]):
        for months in WINDOWS:
            c = t.get(subject, {}).get(months, {})
            right, wrong = c.get(RIGHT, 0), c.get(WRONG, 0)
            lines.append(f"{subject:<8} {str(months) + 'm':>7} {right + wrong:>7} {right:>6} {wrong:>6} "
                         f"{c.get(TOO_EARLY, 0):>10} {c.get(NO_DATA, 0):>8}")
    total_early = sum(c.get(TOO_EARLY, 0) for s in t.values() for c in s.values())
    total_done = sum(c.get(RIGHT, 0) + c.get(WRONG, 0) for s in t.values() for c in s.values())
    lines.append("")
    if not total_done and total_early:
        lines.append("Every verdict is still too early to score: the first window (3 months) has not ended.")
    dis = disagreements(scored)
    if dis:
        lines.append("")
        lines.append("Where a judge disagreed with the lenses' verdict of record:")
        for d in dis:
            who = {"judge": f"the judge was right ({d['window']}m)", "lenses": f"the lenses were right ({d['window']}m)",
                   "neither": f"neither was right ({d['window']}m)", None: "too early to say"}[d["right"]]
            lines.append(f"  {d['run_date'][:10]} {d['ticker']}: {d['subject']} said {d['judge_verdict']}, "
                         f"the record said {d['record']} - {who}")
    lines.append("")
    lines.append(f"No judge earns a vote until it has 12-month results across at least {VOTE_BAR_NAMES} names.")
    for _family, name in FAMILY_COLUMNS:
        lines.append(f"  {name}: {len(names.get(name, ()))} of {VOTE_BAR_NAMES} names with a 12-month result")
    return "\n".join(lines)


def write_scores(scored: list[dict], directory=None) -> Path:
    path = judges_dir(directory) / SCORE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SCORE_COLUMNS)
        w.writeheader()
        w.writerows(scored)
    return path


def needed_tickers(rows: list) -> set:
    out = set()
    for r in rows:
        out.add(r["ticker"])
        out.update(p for p in (r.get("peers") or "").split(";") if p)
    return out


def production_fetcher(start: date, end: date, today: Optional[date] = None) -> PriceFetcher:
    """Prices through the app's day-cached adapter, ONE request per ticker for the whole window."""
    from ..company_report import _default_adapter

    adapter = _default_adapter(today or date.today())

    def fetch(ticker: str) -> list:
        try:
            hist = adapter.get_price_history(ticker, start=start, end=end)
        except Exception:                                    # noqa: BLE001 - a missing name is "no data"
            return []
        return [(b.day, float(b.adj_close if getattr(b, "adj_close", None) else b.close))
                for b in hist.bars]
    return fetch


def run_score(directory=None, *, today: Optional[date] = None, fetcher: Optional[PriceFetcher] = None):
    """``(scored rows, summary text, path)``. Reads judge_summary.csv, fetches prices, writes score.csv."""
    today = today or date.today()
    rows = read_summary(directory)
    if not rows:
        return [], summary_text([]), None
    if fetcher is None:
        start = min(_day(r["run_date"]) for r in rows) - timedelta(days=START_SLACK_DAYS + 3)
        fetcher = production_fetcher(start, today, today)
    prices = {t: fetcher(t) for t in sorted(needed_tickers(rows))}
    scored = score_rows(rows, prices, today)
    return scored, summary_text(scored), write_scores(scored, directory)
