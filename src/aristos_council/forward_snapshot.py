"""SNAPSHOT-2 - the quarterly FORWARD FREEZE of watched-cohort verdicts, and its 12-month check.

This replaces the July ``examples/snapshot_consensus.py`` (its universes and lenses are gone; it is
not run or revived). One command freezes, one command grades:

    python -m aristos_council.forward_snapshot take    # freeze today's verdicts, commit the CSV
    python -m aristos_council.forward_snapshot check   # grade every snapshot older than 12 months

NO MODEL IS CALLED, by either command. ``take`` runs ``run_multi_strategy_pipeline`` with
``ranker_only=True`` (no runners are built, no key is read); ``check`` fetches prices and nothing
else. ``tests/test_forward_snapshot.py`` asserts both.

WHAT IS FROZEN. The cohorts the local overlay watches (``data/local/cohorts/watch.yaml``), each at
its CURRENT frozen version, graded under EVERY voting lens: the visible rank strategies that are not
``kind: check`` and admit equities. One row per (cohort, lens, name):

    date, cohort, cohort_version, lens, ticker, status, rank, ranked_of, verdict, badge,
    agreement_buy, agreement_voted, price_local, currency, fx_to_usd, price_usd, price_date, reason

* ``rank`` is the name's cohort position under that lens (``ranked_of`` is the lens's ranked count).
* ``verdict`` is BUY/HOLD/SELL for a ranked name and EMPTY otherwise; ``status`` says why (ranked,
  excluded, unrateable, fetch_error, too_few, absent). An exclusion is a call too, so it is recorded -
  null is not false, and an empty verdict is never a HOLD.
* ``badge`` is the lens's track-record badge IN THAT COHORT (``backtest.track_record``), read, never
  fed back. ``agreement_buy`` / ``agreement_voted`` are how many voting lenses rated the name BUY, and
  how many actually voted on it (a lens that excluded it did not vote).
* ``price_usd`` is the last close in the listing currency, converted at the FX rate of the same day.
  A minor-unit quote (pence, South African cents, ILA) is first scaled to its major unit. A price that
  cannot be converted is EMPTY with the reason in ``reason`` - never a guess.

A snapshot file is IMMUTABLE: ``take`` refuses to overwrite one. If any name hit a transient fetch
failure it refuses to write at all (a freeze with holes in it would be graded as if complete) unless
``--allow-fetch-errors`` says otherwise.

WHAT IS CHECKED. For a snapshot at least ``MIN_AGE_DAYS`` old: within each (cohort, lens) the BUY, HOLD
and SELL names are each equal-weighted and their mean USD PRICE return since the freeze is compared.
Price return only - dividends are not included, and neither side of the comparison gets them. The
return uses the SAME price series at both ends (so a split cannot fake a move) and the FX move since the
freeze. A name with no current price is counted and left out, never scored as a loss. This is a
descriptive comparison; ~100 cohort x lens cells are compared, so a few will beat by chance - read the
count of cells where BUY beat both, not any one cell.
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = ROOT / "snapshots"
FILE_PREFIX = "forward_"
MIN_AGE_DAYS = 365                     # "older than 12 months"
MIN_GROUP = 3                          # a mean over fewer names than this is not stated
PRICE_WINDOW_DAYS = 10                 # how far back to look for the last close
STALE_PRICE_DAYS = 10                  # a "current" close older than this is no current price
BAR_TOLERANCE_DAYS = 7                 # the snapshot-day bar may sit this far before the date

COLUMNS = ("date", "cohort", "cohort_version", "lens", "ticker", "status", "rank", "ranked_of",
           "verdict", "badge", "agreement_buy", "agreement_voted", "price_local", "currency",
           "fx_to_usd", "price_usd", "price_date", "reason")


class SnapshotError(RuntimeError):
    """A refusal the operator should read: nothing was written."""


# --------------------------------------------------------------------------- #
# which cohorts, which lenses
# --------------------------------------------------------------------------- #
def voting_lens_ids(strategies_dir: Path | str | None = None) -> list[str]:
    """Every visible rank strategy that votes (not ``kind: check``) on equities, id-sorted.

    Derived from the strategy files, never listed here, so a lens added or retired changes the next
    snapshot without a code edit. The ETF lenses are out by their asset-kind gate; the superseded
    ``ui: hidden`` lenses are out by visibility."""
    from .strategy.discovery import visible_rank_strategies
    from .strategy.rank_loader import load_rank_strategy

    out = []
    for info in visible_rank_strategies(strategies_dir or ROOT / "strategies"):
        s = load_rank_strategy(info.path)
        kinds = [k.lower() for k in (getattr(s, "asset_kinds", None) or [])]
        if getattr(s, "kind", "selector") == "check":
            continue
        if kinds and "equity" not in kinds:
            continue
        out.append(info.id)
    return sorted(out)


def watched_definitions(cohorts_root: Path | str | None = None,
                        definitions_path: Path | str | None = None) -> list:
    """The cohort definitions the LOCAL overlay watches. An empty or missing overlay is an error:
    a snapshot of nothing must not look like a snapshot."""
    from .cohorts.definitions import (DEFAULT_WATCH_OVERLAY, apply_watch_overlay, load_definitions,
                                      load_watch_overlay, watched)

    root = Path(cohorts_root or ROOT / "data" / "local" / "cohorts")
    defs = load_definitions(definitions_path or ROOT / "data" / "cohort_definitions.yaml")
    chosen = watched(apply_watch_overlay(defs, load_watch_overlay(root / DEFAULT_WATCH_OVERLAY)))
    if not chosen:
        raise SnapshotError(f"no watched cohorts: {root / DEFAULT_WATCH_OVERLAY} is missing or empty")
    return chosen


def cohort_universe(cohorts_root: Path | str, slug: str) -> tuple[int, list[str], list[str]]:
    """``(version, yahoo tickers, untranslatable)`` for a cohort's CURRENT frozen version."""
    from .cohorts.freeze import MEMBERS_FILE, _safe_yahoo, current_version, read_members, version_dir

    version = current_version(cohorts_root, slug)
    if version is None:
        raise SnapshotError(f"cohort {slug!r} has no frozen version under {cohorts_root}")
    members = read_members(version_dir(cohorts_root, slug, version) / MEMBERS_FILE)
    tickers, bad = [], []
    for cand in sorted(members, key=lambda c: c.ticker):
        symbol = _safe_yahoo(cand.ticker)
        (tickers if symbol else bad).append(symbol or cand.ticker)
    return version, list(dict.fromkeys(tickers)), bad


# --------------------------------------------------------------------------- #
# prices in USD
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Quote:
    price_local: Optional[float] = None      # last close, in the MAJOR unit of ``currency``
    currency: str = ""
    fx_to_usd: Optional[float] = None
    price_date: Optional[date] = None
    note: str = ""

    @property
    def price_usd(self) -> Optional[float]:
        if self.price_local is None or self.fx_to_usd is None:
            return None
        return self.price_local * self.fx_to_usd


def _minor_units() -> dict[str, str]:
    from .tools.valuation_band import MINOR_UNITS
    return MINOR_UNITS


def _fx_to_usd(adapter, currency: str, today: date, cache: dict) -> Optional[float]:
    if currency == "USD":
        return 1.0
    if currency not in cache:
        from .factors import _fetch_fx_rate
        try:
            cache[currency] = _fetch_fx_rate(adapter, currency, "USD", today=today)
        except Exception:                                        # noqa: BLE001 - abstain, not crash
            cache[currency] = None
    return cache[currency]


def _last_bar(bars: Iterable, on_or_before: Optional[date] = None):
    ok = [b for b in bars if on_or_before is None or b.day <= on_or_before]
    return max(ok, key=lambda b: b.day) if ok else None


def latest_quote(adapter, ticker: str, today: date, fx_cache: dict) -> Quote:
    """One name's last close in USD, or a Quote that says why it has none."""
    from .data.adapter import TransientFetchError

    try:
        history = adapter.get_price_history(ticker, start=today - timedelta(days=PRICE_WINDOW_DAYS),
                                            end=today)
    except TransientFetchError:
        return Quote(note="price fetch failed (rerun)")
    except Exception as e:                                       # noqa: BLE001
        return Quote(note=f"no price: {e}")
    bar = _last_bar(getattr(history, "bars", None) or [])
    if bar is None:
        return Quote(note="no price bars")
    try:
        raw_ccy = (adapter.get_fundamentals(ticker).currency or "").strip()
    except Exception as e:                                       # noqa: BLE001
        return Quote(price_date=bar.day, note=f"currency unknown ({e})")
    if not raw_ccy:
        return Quote(price_date=bar.day, note="currency unknown")
    price, ccy = float(bar.close), raw_ccy
    if raw_ccy in _minor_units():
        ccy, price = _minor_units()[raw_ccy], price / 100.0
    fx = _fx_to_usd(adapter, ccy, today, fx_cache)
    if fx is None:
        return Quote(price_local=price, currency=ccy, price_date=bar.day,
                     note=f"no {ccy}->USD rate")
    return Quote(price_local=price, currency=ccy, fx_to_usd=fx, price_date=bar.day)


# --------------------------------------------------------------------------- #
# rows
# --------------------------------------------------------------------------- #
def _g(x: Optional[float]) -> str:
    return "" if x is None else f"{x:.10g}"


def build_rows(snap_date: date, cohort_name: str, slug: str, version: int, multi,
               quotes: dict[str, Quote], badge_of: Callable[[str, str], str],
               voting: list[str]) -> list[dict]:
    """The CSV rows for ONE cohort's multi-lens result. Pure: no network, no clock. ``voting`` is the
    lens ids that vote (``lens_agreement(multi).voting_ids``)."""
    from .pipeline import _RANKED

    rows: list[dict] = []
    for r in multi.rows:
        cells = {sid: r.cells[sid] for sid in voting if sid in r.cells}
        verdicts = [c.verdict.lower() for c in cells.values() if c.status == _RANKED and c.verdict]
        buy, voted = sum(v == "buy" for v in verdicts), len(verdicts)
        q = quotes.get(r.ticker, Quote(note="no price requested"))
        for sid in voting:
            c = cells.get(sid)
            if c is None:
                continue
            ranked = c.status == _RANKED
            reason = c.reason_plain or c.reason
            if q.note:
                reason = "; ".join(x for x in (reason, f"price: {q.note}") if x)
            rows.append({
                "date": snap_date.isoformat(), "cohort": cohort_name, "cohort_version": version,
                "lens": sid, "ticker": r.ticker, "status": c.status,
                "rank": c.position if ranked and c.position is not None else "",
                "ranked_of": c.cohort_size if ranked else "",
                "verdict": c.verdict.upper() if ranked else "",
                "badge": badge_of(slug, sid), "agreement_buy": buy, "agreement_voted": voted,
                "price_local": _g(q.price_local), "currency": q.currency, "fx_to_usd": _g(q.fx_to_usd),
                "price_usd": _g(q.price_usd), "price_date": q.price_date.isoformat() if q.price_date else "",
                "reason": reason})
    return rows


def snapshot_path(out_dir: Path | str, snap_date: date) -> Path:
    return Path(out_dir) / f"{FILE_PREFIX}{snap_date.isoformat()}.csv"


def write_snapshot(rows: list[dict], out_dir: Path | str, snap_date: date) -> Path:
    path = snapshot_path(out_dir, snap_date)
    if path.exists():
        raise SnapshotError(f"{path} already exists - a snapshot is never overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


# --------------------------------------------------------------------------- #
# take
# --------------------------------------------------------------------------- #
@dataclass
class TakeResult:
    path: Optional[Path]
    rows: int
    cohorts: int
    lenses: list[str]
    names: int
    fetch_errors: list[tuple[str, str, str]] = field(default_factory=list)   # (cohort, ticker, reason)
    untranslatable: list[tuple[str, str]] = field(default_factory=list)
    no_price: list[str] = field(default_factory=list)
    committed: bool = False


def take_snapshot(*, today: Optional[date] = None, adapter=None, out_dir: Path | str = SNAPSHOT_DIR,
                  cohorts_root: Path | str | None = None, definitions_path=None,
                  strategies_dir: Path | str | None = None, grade=None, track_record=None,
                  allow_fetch_errors: bool = False, commit: bool = False, git=None,
                  freeze_dir=None, progress: Callable[[str], None] = lambda m: None) -> TakeResult:
    """Run every watched cohort under every voting lens (ranker only), write the CSV, optionally commit."""
    today = today or date.today()
    strategies_dir = Path(strategies_dir or ROOT / "strategies")
    cohorts_root = Path(cohorts_root or ROOT / "data" / "local" / "cohorts")
    if snapshot_path(out_dir, today).exists():
        raise SnapshotError(f"{snapshot_path(out_dir, today)} already exists - a snapshot is never overwritten")
    lenses = voting_lens_ids(strategies_dir)
    if not lenses:
        raise SnapshotError("no voting lens found")
    defs = watched_definitions(cohorts_root, definitions_path)
    if adapter is None:
        from .pipeline import _build_adapter
        adapter = _build_adapter(today=today, use_cache=True)
    if grade is None:
        from .pipeline import run_multi_strategy_pipeline as grade
    if track_record is None:
        from .backtest import track_record as _tr
        track_record = lambda slug, sid: _tr(slug, sid).label            # noqa: E731

    from .pipeline import lens_agreement

    graded, errors, bad = [], [], []
    for i, d in enumerate(defs, 1):
        version, tickers, untranslatable = cohort_universe(cohorts_root, d.slug)
        bad += [(d.name, t) for t in untranslatable]
        progress(f"[{i}/{len(defs)}] {d.name} v{version}: {len(tickers)} names x {len(lenses)} lenses")
        multi = grade(tickers, lenses, strategies_dir=strategies_dir, adapter=adapter, today=today,
                      ranker_only=True, freeze_dir=freeze_dir, progress=progress)
        for res in multi.results.values():
            errors += [(d.name, t, str(why)) for t, why in (res.fetch_errors or [])]
        graded.append((d, version, multi))

    if errors and not allow_fetch_errors:
        listing = "; ".join(f"{c}: {t}" for c, t, _ in dict.fromkeys(errors))
        raise SnapshotError(f"{len(set(errors))} name(s) hit a transient fetch failure - nothing written, "
                            f"rerun (or --allow-fetch-errors): {listing}")

    every = sorted({r.ticker for _, _, m in graded for r in m.rows})
    progress(f"pricing {len(every)} names in USD")
    fx_cache: dict = {}
    quotes = {t: latest_quote(adapter, t, today, fx_cache) for t in every}
    rows: list[dict] = []
    for d, version, multi in graded:
        rows += build_rows(today, d.name, d.slug, version, multi, quotes, track_record,
                           list(lens_agreement(multi).voting_ids))
    path = write_snapshot(rows, out_dir, today)
    result = TakeResult(path=path, rows=len(rows), cohorts=len(defs), lenses=lenses, names=len(every),
                        fetch_errors=list(dict.fromkeys(errors)), untranslatable=bad,
                        no_price=[t for t, q in quotes.items() if q.price_usd is None])
    if commit:
        commit_snapshot(path, today, git=git)
        result.committed = True
    return result


def commit_snapshot(path: Path, snap_date: date, *, git=None) -> None:
    """``git add`` + ``git commit`` of THIS file only, with a dated message. Anything else staged or
    modified in the tree is left alone."""
    git = git or (lambda *a: subprocess.run(["git", *a], cwd=ROOT, check=True))
    git("add", "--", str(path))
    git("commit", "-m", f"forward snapshot {snap_date.isoformat()}", "--", str(path))


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #
def read_snapshot(path: Path | str) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def snapshot_date_of(path: Path | str) -> date:
    stem = Path(path).stem
    if not stem.startswith(FILE_PREFIX):
        raise ValueError(f"not a forward snapshot file: {path}")
    return date.fromisoformat(stem[len(FILE_PREFIX):])


def snapshot_files(directory: Path | str = SNAPSHOT_DIR) -> list[Path]:
    out = []
    for p in sorted(Path(directory).glob(f"{FILE_PREFIX}*.csv")):
        try:
            snapshot_date_of(p)
        except ValueError:
            continue
        out.append(p)
    return out


@dataclass
class Group:
    returns: list[float] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.returns)

    @property
    def mean(self) -> Optional[float]:
        return sum(self.returns) / self.n if self.n >= MIN_GROUP else None


@dataclass
class CellResult:
    cohort: str
    lens: str
    badge: str
    buy: Group
    hold: Group
    sell: Group
    everyone: Group
    no_return: int                       # verdict-bearing names with no usable return

    @staticmethod
    def _beat(a: Group, b: Group) -> Optional[bool]:
        return None if a.mean is None or b.mean is None else a.mean > b.mean

    @property
    def beat_hold(self) -> Optional[bool]:
        return self._beat(self.buy, self.hold)

    @property
    def beat_sell(self) -> Optional[bool]:
        return self._beat(self.buy, self.sell)

    @property
    def beat_both(self) -> Optional[bool]:
        a, b = self.beat_hold, self.beat_sell
        if a is None or b is None:
            return None
        return a and b


@dataclass
class NameReturn:
    usd_return: Optional[float]
    note: str = ""


def usd_return(adapter, ticker: str, snap_date: date, then_fx: Optional[float], today: date,
               fx_cache: dict, currency: str) -> NameReturn:
    """The USD price return since the freeze, from ONE price series (split-safe) and the FX move."""
    from .data.adapter import TransientFetchError

    try:
        history = adapter.get_price_history(ticker, start=snap_date - timedelta(days=BAR_TOLERANCE_DAYS),
                                            end=today)
    except TransientFetchError:
        return NameReturn(None, "price fetch failed")
    except Exception as e:                                       # noqa: BLE001
        return NameReturn(None, f"no price: {e}")
    bars = getattr(history, "bars", None) or []
    then, now = _last_bar(bars, snap_date), _last_bar(bars)
    if then is None or (snap_date - then.day).days > BAR_TOLERANCE_DAYS:
        return NameReturn(None, "no price at the snapshot date")
    if now is None or (today - now.day).days > STALE_PRICE_DAYS:
        return NameReturn(None, "no current price")
    if not then.close or then_fx is None or not currency:
        return NameReturn(None, "no frozen USD price")
    fx_now = _fx_to_usd(adapter, currency, today, fx_cache)
    if fx_now is None:
        return NameReturn(None, f"no {currency}->USD rate now")
    return NameReturn((now.close / then.close) * (fx_now / then_fx) - 1.0)


def grade_snapshot(rows: list[dict], snap_date: date, adapter, today: date) -> tuple[list[CellResult], dict]:
    """Cell results for one snapshot, plus per-name notes ``{ticker: note}`` for names left out."""
    fx_cache: dict = {}
    first: dict[str, dict] = {}
    for r in rows:
        if r["verdict"] in ("BUY", "HOLD", "SELL"):
            first.setdefault(r["ticker"], r)
    returns: dict[str, NameReturn] = {}
    for t, r in first.items():
        fx = float(r["fx_to_usd"]) if r["fx_to_usd"] else None
        returns[t] = usd_return(adapter, t, snap_date, fx, today, fx_cache, r["currency"])
    cells: dict[tuple[str, str], CellResult] = {}
    for r in rows:
        if r["verdict"] not in ("BUY", "HOLD", "SELL"):
            continue
        key = (r["cohort"], r["lens"])
        cell = cells.setdefault(key, CellResult(r["cohort"], r["lens"], r["badge"], Group(), Group(),
                                                Group(), Group(), 0))
        nr = returns.get(r["ticker"])
        if nr is None or nr.usd_return is None:
            cell.no_return += 1
            continue
        getattr(cell, r["verdict"].lower()).returns.append(nr.usd_return)
        cell.everyone.returns.append(nr.usd_return)
    left_out = {t: nr.note for t, nr in returns.items() if nr.usd_return is None}
    return sorted(cells.values(), key=lambda c: (c.cohort, c.lens)), left_out


def agreement_table(rows: list[dict], snap_date: date, adapter, today: date) -> dict:
    """``{cohort: {agreement_buy: [returns]}}`` - descriptive: does more agreement mean a better return?"""
    fx_cache: dict = {}
    seen: set = set()
    out: dict[str, dict[int, list[float]]] = {}
    for r in rows:
        key = (r["cohort"], r["ticker"])
        if key in seen or not r["agreement_voted"] or int(r["agreement_voted"]) == 0:
            continue
        seen.add(key)
        fx = float(r["fx_to_usd"]) if r["fx_to_usd"] else None
        nr = usd_return(adapter, r["ticker"], snap_date, fx, today, fx_cache, r["currency"])
        if nr.usd_return is not None:
            out.setdefault(r["cohort"], {}).setdefault(int(r["agreement_buy"]), []).append(nr.usd_return)
    return out


def _pct(x: Optional[float]) -> str:
    return "  n/a" if x is None else f"{x * 100:+6.1f}%"


def _verdict_words(c: CellResult) -> str:
    if c.beat_both is None:
        return "too few names to compare"
    if c.beat_both:
        return "BUY beat HOLD and SELL"
    got = [w for w, ok in (("HOLD", c.beat_hold), ("SELL", c.beat_sell)) if ok]
    return f"BUY beat {' and '.join(got)} only" if got else "BUY beat neither"


def format_check(snap_date: date, today: date, cells: list[CellResult], left_out: dict,
                 agreement: dict) -> str:
    lines = [f"Snapshot {snap_date.isoformat()} checked on {today.isoformat()} "
             f"({(today - snap_date).days} days). USD price return, equal weight within each cohort; "
             f"dividends not included. A group under {MIN_GROUP} names states no mean.", ""]
    lines.append(f"{'cohort':<46}{'lens':<27}{'BUY':>8}{'HOLD':>8}{'SELL':>8}{'all':>8}  n(B/H/S)  result")
    for c in cells:
        lines.append(f"{c.cohort[:45]:<46}{c.lens:<27}{_pct(c.buy.mean)}{_pct(c.hold.mean)}"
                     f"{_pct(c.sell.mean)}{_pct(c.everyone.mean)}  "
                     f"{c.buy.n}/{c.hold.n}/{c.sell.n}"
                     f"{f' (+{c.no_return} no price)' if c.no_return else ''}  {_verdict_words(c)}")
    judged = [c for c in cells if c.beat_both is not None]
    won = sum(1 for c in judged if c.beat_both)
    lines += ["", f"BUY beat both HOLD and SELL in {won} of {len(judged)} comparable cohort x lens cells "
                  f"({len(cells) - len(judged)} cells had too few names).",
              "Roughly 1 in 3 cells would do this by chance alone with no skill at all; the count above "
              "is the result, no single cell is."]
    if left_out:
        reasons: dict[str, int] = {}
        for note in left_out.values():
            reasons[note] = reasons.get(note, 0) + 1
        lines += ["", f"{len(left_out)} name(s) had no usable return and are LEFT OUT, not scored as losses: "
                  + "; ".join(f"{n} x {why}" for why, n in sorted(reasons.items()))
                  + ". Names that vanished (delisted, merged) land here, which flatters every bucket alike."]
    if agreement:
        lines += ["", "Agreement (voting lenses rating the name BUY) vs mean USD return, per cohort:"]
        for cohort in sorted(agreement):
            parts = []
            for k in sorted(agreement[cohort], reverse=True):
                rs = agreement[cohort][k]
                parts.append(f"{k} BUY: {_pct(sum(rs) / len(rs) if len(rs) >= MIN_GROUP else None).strip()} (n={len(rs)})")
            lines.append(f"  {cohort[:45]:<46}" + "   ".join(parts))
    return "\n".join(lines)


def check_snapshots(directory: Path | str = SNAPSHOT_DIR, *, today: Optional[date] = None, adapter=None,
                    min_age_days: int = MIN_AGE_DAYS, say: Callable[[str], None] = print) -> int:
    """Grade every snapshot at least ``min_age_days`` old; say plainly when none is. Returns how many."""
    today = today or date.today()
    files = snapshot_files(directory)
    if not files:
        say(f"No forward snapshots in {directory}.")
        return 0
    due = [p for p in files if (today - snapshot_date_of(p)).days >= min_age_days]
    for p in files:
        if p not in due:
            d = snapshot_date_of(p)
            say(f"{p.name}: {(today - d).days} days old - not graded until it is {min_age_days} "
                f"({(d + timedelta(days=min_age_days)).isoformat()}).")
    if not due:
        return 0
    if adapter is None:
        from .pipeline import _build_adapter
        adapter = _build_adapter(today=today, use_cache=True)
    for p in due:
        d = snapshot_date_of(p)
        rows = read_snapshot(p)
        cells, left_out = grade_snapshot(rows, d, adapter, today)
        say("")
        say(format_check(d, today, cells, left_out, agreement_table(rows, d, adapter, today)))
    return len(due)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _say(message: str) -> None:
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", "") or "ascii"
        print(message.encode(enc, "replace").decode(enc, "replace"), flush=True)


def _load_env() -> None:
    """Read ``.env`` for the data-provider keys, then drop the model key from this process: neither
    command may be able to reach a model even by accident. A no-op under pytest (TEST-ISOLATION-1)."""
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    os.environ.pop("ANTHROPIC_API_KEY", None)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m aristos_council.forward_snapshot",
                                description="SNAPSHOT-2: quarterly forward freeze of watched-cohort verdicts.")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("take", help="freeze today's verdicts into snapshots/forward_<date>.csv and commit it")
    t.add_argument("--out", default=str(SNAPSHOT_DIR))
    t.add_argument("--no-commit", action="store_true", help="write the CSV but do not git commit it")
    t.add_argument("--allow-fetch-errors", action="store_true")
    c = sub.add_parser("check", help="grade every snapshot older than 12 months against today's prices")
    c.add_argument("--dir", default=str(SNAPSHOT_DIR))
    c.add_argument("--min-age-days", type=int, default=MIN_AGE_DAYS,
                   help=f"default {MIN_AGE_DAYS}; lower it only to rehearse the check, never to report a result")
    args = p.parse_args(argv)
    _load_env()
    try:
        if args.cmd == "take":
            r = take_snapshot(out_dir=args.out, allow_fetch_errors=args.allow_fetch_errors,
                              commit=not args.no_commit, freeze_dir=ROOT / "runs", progress=_say)
            _say(f"wrote {r.path} - {r.rows} rows, {r.cohorts} cohorts x {len(r.lenses)} lenses "
                 f"({', '.join(r.lenses)}), {r.names} names" + (", committed" if r.committed else ", NOT committed"))
            if r.no_price:
                _say(f"{len(r.no_price)} name(s) have no USD price (reason in the reason column): "
                     + ", ".join(r.no_price))
            if r.untranslatable:
                _say("no Yahoo symbol, not graded: " + ", ".join(f"{t} ({c})" for c, t in r.untranslatable))
            if r.fetch_errors:
                _say(f"WARNING: {len(r.fetch_errors)} fetch error(s) written as fetch_error rows")
        else:
            check_snapshots(args.dir, min_age_days=args.min_age_days, say=_say)
    except SnapshotError as e:
        _say(f"REFUSED: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
