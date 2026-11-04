"""ABSOLUTE-BACKTEST-2, stage 2: the new analyses, pure over the name tables ``v2_extract`` wrote. No data access.

Definitions are those frozen in ``backtests_absolute/v2/PREREGISTRATION.md`` before any number was computed. The
12-month hold only; every basket and every benchmark pays the same 0.5% round trip; pooling is the corrected run's
(inside an industry by calendar year of entry, across industries that have positions that year, across years).
"""
from __future__ import annotations

import csv
import gzip
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from statistics import median
from typing import Optional

import numpy as np

from ..backtest import PROOF_TOLERANCE, _TurnoverBaskets, _year_group_means
from . import COST_BPS, MIN_HELD, PASS_MAX_LUCK, PASS_MIN_EXCESS
from .selection import NameReading, cheapest_third, select_forms

COST = COST_BPS / 10_000.0
N_RANDOM = 500
BENCHES = ("industry", "passers", "size")
BENCH_LABELS = {"industry": "industry basket", "passers": "gate-passer basket", "size": "size-matched basket"}
FORMS_ALL = (1, 2, 3, 4)
REGIMES = (("2016-2019", 2016, 2019), ("2020-2021", 2020, 2021), ("2022-2026", 2022, 2026))
SPREAD_BASE_BEFORE = date(2021, 10, 1)       # the first five years of rounds: entries before this
SPREAD_MIN_NAMES = 6                         # gate-passers with a base yield needed for a spread (thirds of >= 2 names)
SPREAD_MIN_ROUNDS = 12                       # defined spreads needed in the first five years to set a threshold
BOOT_DRAWS, BOOT_BLOCK, BOOT_SEED = 1000, 12, 20261104


# --------------------------------------------------------------------------- #
# reading the tables
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Rec:
    ticker: str
    gate_state: str
    backtest_state: str
    base_yield: Optional[float]
    cap: Optional[float]
    gp: Optional[float]
    ret: Optional[float]


def _f(x: str) -> Optional[float]:
    return float(x) if x not in ("", None) else None


def load_names(path) -> dict:
    """``{round date: [Rec, ...]}`` of the eligible names, from a ``names.csv.gz``."""
    out: dict = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            d = date.fromisoformat(r["date"])
            out.setdefault(d, []).append(Rec(r["ticker"], r["gate_state"], r["backtest_state"], _f(r["base_yield"]),
                                             _f(r["cap_usd"]), _f(r["gp_assets"]), _f(r["ret12"])))
    return out


def as_readings(recs) -> list:
    return [NameReading(r.ticker, eligible=True, gate_state=r.gate_state, backtest_state=r.backtest_state,
                        base_yield=r.base_yield) for r in recs]


def select_form4(recs) -> tuple:
    """``(names, status)``: gate, cheapest third on the base yield, then the half (rounded up) with the higher gross profit
    over total assets. A name without a reading cannot be chosen (not failed). Fewer than 3 chosen: skipped."""
    by = {r.ticker: r for r in recs}
    passers = [x for x in as_readings(recs) if x.gate_state == "qualifies"]
    cheap = cheapest_third(passers)
    if not cheap:
        return (), "no cheap third"
    readable = sorted((t for t in cheap if by[t].gp is not None), key=lambda t: (-by[t].gp, t))
    chosen = readable[:math.ceil(len(cheap) / 2)]
    if len(chosen) < MIN_HELD:
        return (), "fewer than 3 chosen"
    return tuple(sorted(chosen)), "chosen"


def selections(recs) -> dict:
    """{form: sorted tuple of tickers} for forms 1 to 4 (before pricing)."""
    s = select_forms(as_readings(recs))
    f4, _ = select_form4(recs)
    return {1: tuple(s[1]), 2: tuple(s[2]), 3: tuple(s[3]), 4: f4}


# --------------------------------------------------------------------------- #
# one round
# --------------------------------------------------------------------------- #
def size_thirds(caps: dict) -> dict:
    """{ticker: 0|1|2}: the names with a size, ranked by it (ties by ticker) and cut into three equal groups."""
    ranked = sorted(caps, key=lambda t: (caps[t], t))
    n = len(ranked)
    return {t: min(2, (i * 3) // n) for i, t in enumerate(ranked)} if n else {}


def _net(mean_ret: float) -> float:
    return max(-1.0, mean_ret - COST)


@dataclass
class Round:
    date: date
    pool_ret: dict                       # {ticker: 12-month total return} eligible and priced both ends
    sel: dict                            # {form: tuple}
    passers: tuple                       # gate-passers (before pricing)
    pass_ret: dict
    third_mean: dict                     # {ticker: mean return of the names in its size third}
    pool_mean: float
    rec: dict                            # {ticker: Rec}
    status4: str = ""


def build_round(d: date, recs) -> Round:
    rec = {r.ticker: r for r in recs}
    pool = {r.ticker: r.ret for r in recs if r.ret is not None}
    caps = {t: rec[t].cap for t in pool if rec[t].cap is not None and rec[t].cap > 0}
    th = size_thirds(caps)
    groups: dict = {}
    for t, g in th.items():
        groups.setdefault(g, []).append(pool[t])
    gmean = {g: sum(v) / len(v) for g, v in groups.items()}
    pm = sum(pool.values()) / len(pool) if pool else 0.0
    sel = selections(recs)
    status4 = select_form4(recs)[1]
    passers = tuple(sorted(r.ticker for r in recs if r.gate_state == "qualifies"))
    return Round(d, pool, sel, passers, {t: pool[t] for t in passers if t in pool},
                 {t: gmean[th[t]] for t in th}, pm, rec, status4)


def bench_values(rd: Round, chosen) -> dict:
    """The three benchmarks' net returns for the names ``chosen`` (the form's picks, or a random basket's)."""
    out = {"industry": _net(rd.pool_mean)}
    out["passers"] = _net(sum(rd.pass_ret.values()) / len(rd.pass_ret)) if len(rd.pass_ret) >= MIN_HELD else None
    out["size"] = _net(sum(rd.third_mean.get(t, rd.pool_mean) for t in chosen) / len(chosen))
    return out


@dataclass
class FormRound:
    date: date
    n_sel: int
    n_held: int
    n_new: int
    positioned: bool
    net: Optional[float] = None
    ex: dict = field(default_factory=dict)         # {bench: excess or None}
    fallbacks: int = 0                              # picks with no size third (their size leg used the industry basket)
    held: tuple = ()


def score_form(rounds, form: int) -> list:
    out, prev = [], None
    for rd in rounds:
        sel = rd.sel[form]
        held = tuple(t for t in sel if t in rd.pool_ret)
        names = set(held)
        n_new = len(names) if prev is None else len(names - prev)
        prev = names
        if len(held) >= MIN_HELD and rd.pool_ret:
            net = _net(sum(rd.pool_ret[t] for t in held) / len(held))
            b = bench_values(rd, held)
            out.append(FormRound(rd.date, len(sel), len(held), n_new, True, net,
                                 {k: (None if v is None else net - v) for k, v in b.items()},
                                 sum(1 for t in held if t not in rd.third_mean), held))
        else:
            out.append(FormRound(rd.date, len(sel), len(held), n_new, False, held=held))
    return out


def random_excess(rounds, scored, form: int, slug: str, n_baskets: int = N_RANDOM) -> dict:
    """{bench: (years, (n_years, n_baskets) matrix)}: yearly mean excess of random baskets of the same size and turnover
    (the corrected run's seeds), each against its own benchmark, re-centred on zero per year with its spread kept."""
    tb = _TurnoverBaskets(slug, f"abs_form{form}_h12", n_baskets)
    cols: dict = {b: [] for b in BENCHES}
    dts: dict = {b: [] for b in BENCHES}
    for rd, s in zip(rounds, scored):
        if not s.positioned:
            continue
        pool = sorted(rd.pool_ret)
        sets = tb.draw(pool, s.n_held, s.n_new)
        idx = {t: i for i, t in enumerate(pool)}
        ret = np.array([rd.pool_ret[t] for t in pool])
        tm = np.array([rd.third_mean.get(t, rd.pool_mean) for t in pool])
        ix = np.array([[idx[t] for t in chosen] for chosen in sets])
        nets = np.maximum(-1.0, ret[ix].mean(axis=1) - COST)
        bs = {"industry": np.full(n_baskets, _net(rd.pool_mean)),
              "size": np.maximum(-1.0, tm[ix].mean(axis=1) - COST)}
        if len(rd.pass_ret) >= MIN_HELD:
            bs["passers"] = np.full(n_baskets, _net(sum(rd.pass_ret.values()) / len(rd.pass_ret)))
        for b, v in bs.items():
            cols[b].append(nets - v)
            dts[b].append(rd.date)
    out = {}
    for b in BENCHES:
        if not cols[b]:
            continue
        yearly = _year_group_means(np.column_stack(cols[b]), dts[b])
        yearly = yearly - yearly.mean(axis=1, keepdims=True)
        out[b] = (tuple(sorted({d.year for d in dts[b]})), yearly.astype(np.float32))
    return out


# --------------------------------------------------------------------------- #
# head to head and spread, per industry
# --------------------------------------------------------------------------- #
def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def basket_stats(rd: Round, names) -> dict:
    return {"n": len(names), "size": _avg([rd.rec[t].cap for t in names]),
            "yield": _avg([rd.rec[t].base_yield for t in names]),
            "ret": _net(sum(rd.pool_ret[t] for t in names) / len(names))}


def trim_by_yield(rd: Round, names, k: int) -> tuple:
    """The ``k`` highest base yields among ``names`` (ties by ticker)."""
    return tuple(sorted(sorted(names, key=lambda t: (-(rd.rec[t].base_yield or -1e9), t))[:k]))


def head_to_head(rounds, s2, s3) -> list:
    rows = []
    for rd, a, b in zip(rounds, s2, s3):
        if not (a.positioned and b.positioned):
            continue
        k = min(a.n_held, b.n_held)
        row = {"date": rd.date, "n2": a.n_held, "n3": b.n_held}
        for tag, n2, n3 in (("", a.held, b.held),
                            ("m", trim_by_yield(rd, a.held, k), trim_by_yield(rd, b.held, k))):
            x, y = basket_stats(rd, n2), basket_stats(rd, n3)
            for key in ("n", "size", "yield", "ret"):
                row[f"{tag}{key}2"], row[f"{tag}{key}3"] = x[key], y[key]
        rows.append(row)
    return rows


def spread_of(recs) -> Optional[float]:
    """Median base yield of the cheapest third minus that of the dearest third, over the gate-passers with a base yield."""
    ys = sorted((r.base_yield for r in recs if r.gate_state == "qualifies" and r.base_yield is not None), reverse=True)
    if len(ys) < SPREAD_MIN_NAMES:
        return None
    k = len(ys) // 3
    return float(median(ys[:k]) - median(ys[-k:]))


# --------------------------------------------------------------------------- #
# one industry, end to end
# --------------------------------------------------------------------------- #
def analyse_industry(slug: str, names_path, n_baskets: int = N_RANDOM) -> dict:
    by_date = load_names(names_path)
    dates = sorted(by_date)
    rounds = [build_round(d, by_date[d]) for d in dates]
    scored = {f: score_form(rounds, f) for f in FORMS_ALL}
    luck = {f: random_excess(rounds, scored[f], f, slug, n_baskets) for f in FORMS_ALL}
    spreads = [(rd.date, spread_of(by_date[rd.date])) for rd in rounds]
    base = [s for d, s in spreads if d < SPREAD_BASE_BEFORE and s is not None]
    return {
        "slug": slug, "dates": dates, "sel": {f: [rd.sel[f] for rd in rounds] for f in FORMS_ALL},
        "rounds": {f: [{"date": s.date, "n_sel": s.n_sel, "n_held": s.n_held, "positioned": s.positioned,
                        "net": s.net, "ex": s.ex, "fallbacks": s.fallbacks} for s in scored[f]] for f in FORMS_ALL},
        "luck": luck,
        "h2h": head_to_head(rounds, scored[2], scored[3]),
        "spreads": spreads,
        "spread_threshold": float(median(base)) if len(base) >= SPREAD_MIN_ROUNDS else None,
        "status4": [rd.status4 for rd in rounds],
        "pool": [len(rd.pool_ret) for rd in rounds],
        "passers": [len(rd.passers) for rd in rounds]}


# --------------------------------------------------------------------------- #
# pooling across industries
# --------------------------------------------------------------------------- #
def year_means(vals) -> dict:
    """``vals``: iterable of (date, value or None) -> {year: mean}."""
    by: dict = {}
    for d, v in vals:
        if v is not None:
            by.setdefault(d.year, []).append(v)
    return {y: sum(v) / len(v) for y, v in sorted(by.items())}


def industry_year_excess(res: dict, form: int, bench: str, keep=None) -> dict:
    return year_means((r["date"], r["ex"].get(bench)) for r in res["rounds"][form]
                      if r["positioned"] and (keep is None or keep(r["date"])))


def pool_years(per_industry) -> dict:
    """[{year: value}] per industry -> {year: mean over the industries that have the year}."""
    by: dict = {}
    for d in per_industry:
        for y, v in d.items():
            by.setdefault(y, []).append(v)
    return {y: sum(v) / len(v) for y, v in sorted(by.items())}


def mean_of(d: dict, years=None) -> Optional[float]:
    xs = [v for y, v in d.items() if years is None or y in years]
    return sum(xs) / len(xs) if xs else None


def pooled_luck(results, form: int, bench: str, ex: Optional[float], years=None) -> Optional[float]:
    if ex is None:
        return None
    rand: dict = {}
    for res in results:
        got = res["luck"][form].get(bench)
        if not got:
            continue
        ys, mat = got
        for i, y in enumerate(ys):
            if years is None or y in years:
                rand.setdefault(y, []).append(mat[i].astype(np.float64))
    if not rand:
        return None
    series = np.mean(np.vstack([np.mean(np.vstack(v), axis=0) for _, v in sorted(rand.items())]), axis=0)
    return float(np.mean(series >= ex - PROOF_TOLERANCE))


def industry_luck(res: dict, form: int, bench: str, ex: Optional[float]) -> Optional[float]:
    got = res["luck"][form].get(bench)
    if not got or ex is None:
        return None
    return float(np.mean(got[1].astype(np.float64).mean(axis=0) >= ex - PROOF_TOLERANCE))


def pooled(results, form: int, bench: str, keep=None, years=None) -> dict:
    per = [industry_year_excess(r, form, bench, keep) for r in results]
    py = pool_years(per)
    ex = mean_of(py, years)
    return {"ex": ex, "years": py, "luck": pooled_luck(results, form, bench, ex, years),
            "n_industries": sum(1 for d in per if d)}


# --------------------------------------------------------------------------- #
# bootstrap
# --------------------------------------------------------------------------- #
def monthly_series(results, form: int, bench: str = "industry") -> tuple:
    """(dates, values): each entry month, the mean over industries with a position that month of the excess."""
    by: dict = {}
    for res in results:
        for r in res["rounds"][form]:
            v = r["ex"].get(bench) if r["positioned"] else None
            if v is not None:
                by.setdefault(r["date"], []).append(v)
    ds = sorted(by)
    return ds, np.array([sum(by[d]) / len(by[d]) for d in ds])


def block_bootstrap(values, *, draws: int = BOOT_DRAWS, block: int = BOOT_BLOCK, seed: int = BOOT_SEED) -> dict:
    """Moving-block bootstrap over time of the mean of ``values``: blocks of ``block`` consecutive months."""
    v = np.asarray(values, dtype=float)
    n = len(v)
    if n < block + 1:
        return {"point": float(v.mean()) if n else None, "lo": None, "hi": None, "n": n}
    rng = np.random.default_rng(seed)
    nb = -(-n // block)
    starts = rng.integers(0, n - block + 1, size=(draws, nb))
    means = np.array([np.concatenate([v[s:s + block] for s in row])[:n].mean() for row in starts])
    return {"point": float(v.mean()), "lo": float(np.percentile(means, 5)), "hi": float(np.percentile(means, 95)), "n": n}


# --------------------------------------------------------------------------- #
# distribution summaries
# --------------------------------------------------------------------------- #
def dist(diffs) -> dict:
    xs = [x for x in diffs if x is not None]
    if not xs:
        return {"n": 0, "mean": None, "median": None, "share_pos": None}
    return {"n": len(xs), "mean": sum(xs) / len(xs), "median": float(median(xs)),
            "share_pos": sum(1 for x in xs if x > 0) / len(xs)}


def h2h_diffs(rows, prefix: str = "", key: str = "ret") -> list:
    out = []
    for r in rows:
        a, b = r[f"{prefix}{key}2"], r[f"{prefix}{key}3"]
        out.append(None if a is None or b is None else b - a)
    return out


def bar(ex: Optional[float], luck: Optional[float]) -> str:
    if ex is None:
        return "insufficient"
    return "PASS" if ex >= PASS_MIN_EXCESS - PROOF_TOLERANCE and luck is not None and luck < PASS_MAX_LUCK else "FAIL"
