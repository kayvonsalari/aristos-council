"""The runner: one industry at a time, files written and memory freed after each, resumable.

``run_industry`` does the work for one cohort and returns its result dict; ``run_all`` walks the built cohorts, skips the
ones already finished (a ``DONE.json`` in their folder), stops cleanly when the EODHD unit budget would be passed, and
keeps a heartbeat file for ``status``.
"""
from __future__ import annotations

import csv
import gc
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import yaml

from ..backtest import _size_floor, add_months, code_commit, load_cohort_members, month_end, round_dates
from ..data.backtest_feed import BacktestFeed, MemoAdapter, lookback_start
from . import (BACKTEST_DIR, COST_BPS, FORM_FILES, FORM_NAMES, FORMS, HOLDS, INDEX_TICKER, CASH_TICKER, LAG_DAYS,
               SURVIVORSHIP, UNITS_PER_FUNDAMENTALS, YEARS)
from .evaluate import AS_OF_MODE, evaluate_round
from .market import HistoricalBonds, Tape, cash_return
from .scoring import RANDOM_BASKETS, RoundInput, bar_verdict, luck_test, metrics_for, score_rounds
from .selection import select_forms

DONE_FILE, RESULT_FILE, LUCK_FILE, COUNTS_FILE, PROGRESS_FILE = "DONE.json", "result.json", "luck.npz", "counts.csv", \
    "_progress.json"


# --------------------------------------------------------------------------- #
# the industries
# --------------------------------------------------------------------------- #
def default_cohorts_root() -> Path:
    from ..cohorts.builder import DEFAULT_ROOT
    return Path(DEFAULT_ROOT)


def list_industries(cohorts_root=None) -> list[tuple[str, str]]:
    """``[(slug, display name)]`` for every built cohort, in slug order. The cohort is the industry."""
    from ..cohorts.freeze import DEFINITION_FILE, MEMBERS_FILE, current_version, version_dir
    root = Path(cohorts_root) if cohorts_root else default_cohorts_root()
    out = []
    for child in sorted(root.iterdir()) if root.exists() else []:
        if not child.is_dir():
            continue
        v = current_version(root, child.name)
        if v is None or not (version_dir(root, child.name, v) / MEMBERS_FILE).exists():
            continue
        name = child.name
        try:
            doc = yaml.safe_load((version_dir(root, child.name, v) / DEFINITION_FILE).read_text(encoding="utf-8")) or {}
            name = str(doc.get("name") or name)
        except Exception:                                              # noqa: BLE001
            pass
        out.append((child.name, name))
    return out


def default_end(today: Optional[date] = None) -> date:
    """The last full month's end."""
    t = today or date.today()
    return month_end(t.replace(day=1) - timedelta(days=1))


# --------------------------------------------------------------------------- #
# the data feed, counting what it spends
# --------------------------------------------------------------------------- #
class CountingFeed(BacktestFeed):
    """The backtest feed that counts the EODHD fundamentals calls that reach the provider (below the day cache)."""
    fundamentals_calls = 0

    def get_fundamentals(self, ticker: str):
        from ..cohorts.symbols import SymbolError, eodhd_symbol
        type(self).fundamentals_calls += 1
        try:
            symbol = eodhd_symbol(ticker)       # EODHD wants SHEL.LSE where Yahoo says SHEL.L
        except SymbolError:
            symbol = ticker
        return super().get_fundamentals(symbol)


def default_feed():
    from ..data.cache import DEFAULT_CACHE_DIR, CachingAdapter
    from ..data.retry import RetryAdapter
    return CachingAdapter(RetryAdapter(CountingFeed()), cache_dir=DEFAULT_CACHE_DIR, today=date.today())


class Benchmarks:
    """The index fund and cash legs, fetched once for the whole run."""

    def __init__(self, index: Optional[Tape], irx: Optional[Tape]) -> None:
        self.index, self.irx = index, irx

    @classmethod
    def fetch(cls, start: date, end: date, market=None) -> "Benchmarks":
        if market is None:
            from ..data.yfinance_adapter import YFinanceAdapter
            market = YFinanceAdapter()
        def tape(sym):
            try:
                return Tape(market.get_price_history(sym, start=start, end=end).bars)
            except Exception:                                          # noqa: BLE001 - the leg is absent, said so
                return None
        return cls(tape(INDEX_TICKER), tape(CASH_TICKER))


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #
def _fmt(x) -> str:
    if x is None:
        return ""
    if isinstance(x, float):
        return f"{x:.6f}"
    return str(x)


def write_csv(path: Path, header_lines: list[str], columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        for line in header_lines:
            fh.write("# " + line + "\n")
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            w.writerow([_fmt(r.get(c)) for c in columns])


def read_csv(path) -> list[dict]:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(line for line in fh if not line.startswith("#")))


def filing_share(full) -> Optional[float]:
    """The share of a name's annual periods that carry a usable filing date (after the period end)."""
    ends = getattr(full, "period_ends", None) or {}
    filed = getattr(full, "filing_dates", None) or {}
    key = "total_revenue" if ends.get("total_revenue") else next((k for k, v in ends.items() if v), None)
    if key is None or not ends[key]:
        return None
    fd = filed.get(key) or []
    if len(fd) != len(ends[key]):
        return 0.0
    return sum(1 for p, f in zip(ends[key], fd) if f and f > p) / len(ends[key])


def _progress(out_root: Path, **kw) -> None:
    try:
        out_root.mkdir(parents=True, exist_ok=True)
        (out_root / PROGRESS_FILE).write_text(
            json.dumps({**kw, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# one industry
# --------------------------------------------------------------------------- #
def run_industry(slug: str, *, end: date, out_root, adapter=None, bonds: Optional[HistoricalBonds] = None,
                 benchmarks: Optional[Benchmarks] = None, cohorts_root=None, max_rounds: Optional[int] = None,
                 random_baskets: int = RANDOM_BASKETS, gate_def=None, value_def=None,
                 progress: Optional[Callable[[str], None]] = None, members=None, member_caps=None,
                 name: Optional[str] = None, write: bool = True) -> dict:
    """Backtest one industry on all three forms and both holds; write its files; return the result dict.

    ``members`` / ``member_caps`` replace the cohort lookup (tests); ``adapter`` replaces the data feed."""
    from ..absolute_value.definition import load_definition
    from ..gate.definition import load_gate
    say = progress or (lambda m: None)
    out_root = Path(out_root)
    gate_def = gate_def or load_gate()
    value_def = value_def or load_definition()
    version = None
    if members is None:
        members, version, member_caps = load_cohort_members(slug, cohorts_root)
    members, member_caps = list(members), dict(member_caps or {})
    size_floor, floor_note = _size_floor(slug, cohorts_root, version, member_caps) if member_caps else (None, "")
    start = date(end.year - YEARS, end.month, 1)
    dates = round_dates(start, end, 12, 1)
    if max_rounds:
        dates = dates[:max_rounds]
    if not dates:
        raise ValueError("no round dates in the window")
    calls0 = CountingFeed.fundamentals_calls
    bonds_units0 = bonds.units if bonds is not None else 0
    memo = MemoAdapter(adapter if adapter is not None else default_feed(), start=lookback_start(dates[0]), end=end)
    bonds = bonds or HistoricalBonds()
    benchmarks = benchmarks or Benchmarks.fetch(lookback_start(dates[0]), end)

    tapes, fulls = {}, {}
    for i, t in enumerate(members, 1):
        _progress(out_root, industry=slug, stage="fetching", done=i, of=len(members))
        try:
            tapes[t] = Tape(memo.get_price_history(t, start=lookback_start(dates[0]), end=end).bars)
        except Exception:                                              # noqa: BLE001 - unpriced
            tapes[t] = None
        try:
            fulls[t] = memo.get_fundamentals(t)
        except Exception:                                              # noqa: BLE001 - no accounts
            fulls[t] = None
        if i % 10 == 0:
            say(f"{slug}: fetched {i}/{len(members)}")
    name_filing = [filing_share(f) for f in fulls.values() if f is not None]
    name_filing = [s for s in name_filing if s is not None]

    readings_by_date, count_rows, decisions, fallbacks = {}, [], 0, 0
    for k, d in enumerate(dates, 1):
        _progress(out_root, industry=slug, stage="rounds", done=k, of=len(dates))
        readings, asof = evaluate_round(d, members, memo, fulls, tapes, member_caps=member_caps, size_floor=size_floor,
                                        gate_def=gate_def, value_def=value_def, bonds=bonds)
        decisions += asof.filing_decisions
        fallbacks += asof.filing_date_fallbacks
        sel = select_forms(readings)
        readings_by_date[d] = (readings, sel)
        pool = [r for r in readings if r.eligible]
        count_rows.append({
            "date": d, "members": len(members), "pool": len(pool),
            "no_accounts": sum(1 for r in pool if r.no_accounts),
            "gate_qualifies": sum(1 for r in pool if r.gate_state == "qualifies"),
            "gate_excluded": sum(1 for r in pool if r.gate_state == "excluded"),
            "gate_not_enough_data": sum(1 for r in pool if r.gate_state == "not_enough_data"),
            "gate_not_covered": sum(1 for r in pool if r.gate_state == "not_covered"),
            "form1": len(sel[1]), "form2": len(sel[2]), "form3": len(sel[3]),
            "no_hurdle": sum(1 for r in pool if r.no_hurdle), "no_fx": sum(1 for r in pool if r.no_fx),
            "split_withheld": sum(1 for r in pool if r.split_withheld),
            "size_price_only": sum(1 for r in pool if r.size_basis == "price only")})
        if k % 12 == 0:
            say(f"{slug}: round {k}/{len(dates)} ({d}): pool {len(pool)}, gate {count_rows[-1]['gate_qualifies']}, "
                f"form 2 {len(sel[2])}, form 3 {len(sel[3])}")

    cost = COST_BPS / 10_000.0
    rows, year_ex, luck_arrays, form_rounds = [], {}, {}, {}
    for hold in HOLDS:
        scorable = [d for d in dates if add_months(d, hold) <= end]
        for form in FORMS:
            inputs = []
            for d in scorable:
                readings, sel = readings_by_date[d]
                ex = add_months(d, hold)
                pool_returns = {}
                for r in readings:
                    if r.eligible and tapes.get(r.ticker) is not None:
                        ret = tapes[r.ticker].total_return(d, ex)
                        if ret is not None:
                            pool_returns[r.ticker] = ret
                idx = benchmarks.index.total_return(d, ex) if benchmarks.index is not None else None
                inputs.append(RoundInput(d, ex, pool_returns, tuple(sel[form]), idx, cash_return(benchmarks.irx, d, ex)))
            scores = score_rounds(inputs, hold, cost)
            m = metrics_for(scores)
            lk = luck_test(inputs, scores, hold, cost, industry_slug=slug, label=f"abs_form{form}_h{hold}", metrics=m,
                           n_baskets=random_baskets) if random_baskets > 0 else None
            m.luck = lk.luck if lk else None
            m.drop_best_vs_random = lk.drop_best_vs_random if lk else None
            verdict = bar_verdict(m, m.luck) if hold == 12 else "reported only"
            form_rounds[(form, hold)] = scores
            key = f"f{form}_h{hold}"
            year_ex[key] = {c: {str(y): v for y, v in getattr(m, a).items()}
                            for c, a in (("pool", "year_ex_pool"), ("index", "year_ex_index"), ("cash", "year_ex_cash"),
                                         ("drop_best", "year_drop_best"))}
            if lk is not None and lk.random_year_means is not None:
                luck_arrays[key + "_years"] = np.array(lk.years, dtype=np.int32)
                luck_arrays[key + "_rand"] = lk.random_year_means
            rows.append({"form": form, "form_name": FORM_NAMES[form], "hold_months": hold, "verdict": verdict,
                         "n_rounds": m.n_rounds, "n_positions": m.n_positions, "years_measured": m.years_measured,
                         "years_positive": m.years_positive, "mean_form_return": m.mean_form_return,
                         "mean_basket_return": m.mean_pool_return, "ex_basket": m.ex_pool, "ex_index": m.ex_index,
                         "ex_cash": m.ex_cash, "luck": m.luck, "drop_best": m.drop_best,
                         "drop_best_vs_random": m.drop_best_vs_random, "share_rounds_under_4": m.share_thin,
                         "median_names_held": m.median_held})

    decisions_filing = (1.0 - fallbacks / decisions) if decisions else None
    result = {
        "industry": name or slug, "slug": slug, "cohort_version": version, "members": len(members),
        "window": [dates[0].isoformat(), end.isoformat()], "rounds": len(dates),
        "gate": gate_def.id, "valuation": value_def.id, "as_of_mode": AS_OF_MODE, "lag_days": LAG_DAYS,
        "size_floor_usd": size_floor, "size_floor_note": floor_note, "cost_bps": COST_BPS,
        "names_with_filing_dates": (sum(1 for s in name_filing if s >= 0.5) / len(name_filing)) if name_filing else None,
        "decisions_on_filing_date": decisions_filing,
        "names_without_accounts": sum(1 for f in fulls.values() if f is None),
        "names_unpriced": sum(1 for t in tapes.values() if t is None),
        "index": INDEX_TICKER if benchmarks.index is not None else None,
        "cash": CASH_TICKER if benchmarks.irx is not None else None,
        "units": (CountingFeed.fundamentals_calls - calls0) * UNITS_PER_FUNDAMENTALS
                 + (bonds.units - bonds_units0),
        "rows": rows, "year_excess": year_ex}
    if write:
        _write_industry(out_root, slug, result, count_rows, form_rounds, readings_by_date, luck_arrays, end)
    del memo, tapes, fulls, readings_by_date, form_rounds
    gc.collect()
    return result


def header(slug_name: str, result: dict, extra: str = "") -> list[str]:
    return [SURVIVORSHIP,
            f"Industry: {slug_name}. Gate {result['gate']}, valuation {result['valuation']} (backtest form: own 5-year, "
            f"own 10-year, downside from the own 5-year rate; no consensus; own-record rule applies).",
            f"Accounts as of each round: {result['as_of_mode']} dates, period end + {result['lag_days']} days where no "
            f"filing date; cost {result['cost_bps']:g} bps round trip; window {result['window'][0]} to {result['window'][1]}."
            + (" " + extra if extra else "")]


ROUND_COLUMNS = ["date", "members", "pool", "n_selected", "names"]
HOLD_COLUMNS = ["exit", "n_held", "form_ann", "basket_ann", "index_ann", "cash_ann", "ex_basket", "ex_index", "ex_cash",
                "drop_best"]


def _write_industry(out_root: Path, slug: str, result: dict, count_rows, form_rounds, readings_by_date, luck_arrays,
                    end: date) -> None:
    folder = out_root / slug
    folder.mkdir(parents=True, exist_ok=True)
    head = header(result["industry"], result)
    cols_c = ["date", "members", "pool", "no_accounts", "gate_qualifies", "gate_excluded", "gate_not_enough_data",
              "gate_not_covered", "form1", "form2", "form3", "no_hurdle", "no_fx", "split_withheld", "size_price_only"]
    write_csv(folder / COUNTS_FILE, head, cols_c, count_rows)
    for form in FORMS:
        rows = []
        by_hold = {h: {s.date: s for s in form_rounds[(form, h)]} for h in HOLDS}
        for cr in count_rows:
            d = cr["date"]
            sel = readings_by_date[d][1][form]
            row = {"date": d, "members": cr["members"], "pool": cr["pool"], "n_selected": len(sel),
                   "names": ";".join(sel)}
            for h in HOLDS:
                s = by_hold[h].get(d)
                if s is None:
                    continue
                for c, v in (("exit", s.exit_date), ("n_held", s.n_held), ("form_ann", s.form_ann),
                             ("basket_ann", s.pool_ann), ("index_ann", s.index_ann), ("cash_ann", s.cash_ann),
                             ("ex_basket", s.ex_pool), ("ex_index", s.ex_index), ("ex_cash", s.ex_cash),
                             ("drop_best", s.drop_best)):
                    row[f"{c}_h{h}"] = v
            rows.append(row)
        columns = ROUND_COLUMNS + [f"{c}_h{h}" for h in HOLDS for c in HOLD_COLUMNS]
        write_csv(folder / FORM_FILES[form], head + [f"Form {form}: {FORM_NAMES[form]}. Yearly rates; the 36-month hold "
                                                     f"is annualised."], columns, rows)
    if luck_arrays:
        np.savez_compressed(folder / LUCK_FILE, **luck_arrays)
    (folder / RESULT_FILE).write_text(json.dumps(result, indent=1), encoding="utf-8")
    (folder / DONE_FILE).write_text(json.dumps({
        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"), "units": result["units"],
        "rounds": result["rounds"], "code": code_commit(), "end": end.isoformat()}), encoding="utf-8")


# --------------------------------------------------------------------------- #
# all industries, with checkpoints and a budget
# --------------------------------------------------------------------------- #
def is_done(out_root, slug: str) -> bool:
    return (Path(out_root) / slug / DONE_FILE).exists()


def run_all(*, out_root=BACKTEST_DIR, end: Optional[date] = None, budget: Optional[int] = None, only=None,
            force: bool = False, max_rounds: Optional[int] = None, cohorts_root=None, adapter=None, bonds=None,
            benchmarks=None, random_baskets: int = RANDOM_BASKETS, progress=print, industries=None) -> dict:
    """Run every unfinished industry. Returns ``{"ran": [...], "skipped": [...], "stopped": reason or "", "units": n}``."""
    out_root = Path(out_root)
    end = end or default_end()
    todo = industries if industries is not None else list_industries(cohorts_root)
    if only:
        wanted = set(only)
        todo = [(s, n) for s, n in todo if s in wanted]
    bonds = bonds or HistoricalBonds(cache_dir=_cache_dir())
    benchmarks = benchmarks or Benchmarks.fetch(date(end.year - YEARS - 3, 1, 1), end)
    ran, skipped, failed, spent, stopped = [], [], [], 0, ""
    for slug, nm in todo:
        if is_done(out_root, slug) and not force:
            skipped.append(slug)
            continue
        try:
            members, _, _ = load_cohort_members(slug, cohorts_root)
        except FileNotFoundError:
            continue
        estimate = len(members) * UNITS_PER_FUNDAMENTALS
        if budget is not None and spent + estimate > budget:
            stopped = (f"budget: {spent} of {budget} units spent, the next industry ({nm}, {len(members)} names) needs "
                       f"about {estimate}; stopped cleanly, run again to resume")
            break
        t0 = time.time()
        try:
            res = run_industry(slug, end=end, out_root=out_root, adapter=adapter, bonds=bonds, benchmarks=benchmarks,
                               cohorts_root=cohorts_root, max_rounds=max_rounds, random_baskets=random_baskets,
                               progress=progress, name=nm)
        except Exception as exc:                                       # noqa: BLE001 - one industry never stops the run
            failed.append(slug)
            progress(f"FAILED {nm}: {type(exc).__name__}: {exc}; not marked finished, the next run retries it")
            gc.collect()
            continue
        spent += res["units"]
        ran.append(slug)
        progress(f"done {nm}: {res['units']} units, {time.time() - t0:.0f} s")
    try:
        (out_root / PROGRESS_FILE).unlink()
    except OSError:
        pass
    return {"ran": ran, "skipped": skipped, "failed": failed, "stopped": stopped, "units": spent}


def _cache_dir():
    from ..data.cache import DEFAULT_CACHE_DIR
    return DEFAULT_CACHE_DIR


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
def status_lines(out_root=BACKTEST_DIR, cohorts_root=None, industries=None) -> list[str]:
    out_root = Path(out_root)
    todo = industries if industries is not None else list_industries(cohorts_root)
    done, units = [], 0
    for slug, nm in todo:
        p = out_root / slug / DONE_FILE
        if p.exists():
            try:
                units += int(json.loads(p.read_text(encoding="utf-8")).get("units") or 0)
            except Exception:                                          # noqa: BLE001
                pass
            done.append(slug)
    lines = [SURVIVORSHIP, f"{len(done)} of {len(todo)} industries finished; {units} EODHD units spent so far."]
    pending = [nm for slug, nm in todo if slug not in done]
    if pending:
        lines.append(f"Next: {pending[0]}. Still to do: {len(pending)}.")
    else:
        lines.append("All industries are finished; run `summarise` to write SUMMARY.csv.")
    prog = out_root / PROGRESS_FILE
    if prog.exists():
        try:
            p = json.loads(prog.read_text(encoding="utf-8"))
            lines.append(f"Working on {p.get('industry')}: {p.get('stage')} {p.get('done')} of {p.get('of')} "
                         f"(heartbeat {p.get('updated')}).")
        except Exception:                                              # noqa: BLE001
            pass
    return lines
