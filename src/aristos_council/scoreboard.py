"""Prospective scoreboard — freeze verdicts + street consensus, score on forward returns.

The ONLY honest "how do we fare vs analysts" is FORWARD returns from frozen,
pre-registered snapshots — no hindsight, no re-fit. This module is the pure,
network-free core behind two CLIs:

- ``examples/snapshot_consensus.py`` freezes today's ranker verdicts and the street
  consensus into an append-only CSV (one row per name, exclusions included — an
  exclusion is a call too).
- ``examples/score_snapshot.py`` grades a past snapshot once a horizon has elapsed:
  forward total return per name, bucketed, with the PRE-COMMITTED test being bucket
  ORDERING (BUY > HOLD > SELL; loved > middle > unloved), not any single name.

NO LLM anywhere. Everything here is a pure function of adapter data + arithmetic, so
it is unit-tested with fake adapters and synthetic prices (no network).

Two design facts are baked in, not optional:
- **Street buckets are RELATIVE (terciles), never absolute rating bands.** Calibration
  (2026-07-04, 23-name growth universe): recommendationMean ranged 1.29-2.59 — every
  name is BUY/BUY-lean on absolute bands, so an absolute cut makes the street side
  untestable. Terciles WITHIN the snapshot universe restore a comparison.
- **A SELL is a RELATIVE rank, not a short thesis.** The verdict is a quintile of the
  ranked universe; the snapshot records that basis so the first scoring can't invite
  claims the system never made.
"""

from __future__ import annotations

import calendar
import csv
import statistics
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from .data.adapter import StreetConsensus, display_name

# --------------------------------------------------------------------------- #
# The append-only snapshot record
# --------------------------------------------------------------------------- #
COLUMNS = ["snapshot_date", "strategy", "universe_id", "ticker", "company_name",
           "aristos_verdict", "combined_rank", "price", "street_mean", "n_analysts",
           "target_mean", "notes"]

SELL_NOTE = ("bottom quintile of {n}-name universe "
             "(relative rank, not a short thesis)")

STANDING_CAVEAT = ("One snapshot is an anecdote with arithmetic; ordering across "
                   "repeated snapshots is the evidence.")

ADJUSTMENT_NOTE = ("Forward TOTAL return uses auto-adjusted closes — dividends are "
                   "approximated via the price adjustment, not summed cash flows.")


@dataclass
class SnapshotRow:
    snapshot_date: str                  # ISO 'YYYY-MM-DD'
    strategy: str
    universe_id: str                    # manifest id, or 'adhoc:<hex8>' (Item 1)
    ticker: str
    aristos_verdict: str                # BUY|HOLD|SELL|EXCLUDED:<criterion>|UNRATEABLE
    combined_rank: Optional[float]
    price: Optional[float]
    street_mean: Optional[float]        # recommendationMean (1=StrongBuy..5=Sell)
    n_analysts: Optional[int]
    target_mean: Optional[float]
    notes: str
    company_name: Optional[str] = None  # yfinance longName (display label; rows predating
                                        # this column read back as None -> bare ticker)

    def to_csv(self) -> dict:
        return {
            "snapshot_date": self.snapshot_date, "strategy": self.strategy,
            "universe_id": self.universe_id, "ticker": self.ticker,
            "company_name": (self.company_name or ""),
            "aristos_verdict": self.aristos_verdict,
            "combined_rank": _num(self.combined_rank), "price": _num(self.price),
            "street_mean": _num(self.street_mean), "n_analysts": _num(self.n_analysts),
            "target_mean": _num(self.target_mean), "notes": self.notes,
        }

    @classmethod
    def from_csv(cls, d: dict) -> "SnapshotRow":
        return cls(
            snapshot_date=d["snapshot_date"], strategy=d["strategy"],
            universe_id=(d.get("universe_id") or ""),      # pre-Item-1 rows -> ''
            ticker=d["ticker"], aristos_verdict=d["aristos_verdict"],
            combined_rank=_as_float(d.get("combined_rank")),
            price=_as_float(d.get("price")),
            street_mean=_as_float(d.get("street_mean")),
            n_analysts=_as_int(d.get("n_analysts")),
            target_mean=_as_float(d.get("target_mean")),
            notes=d.get("notes", "") or "",
            company_name=(d.get("company_name") or None))


def _num(v) -> str:
    """CSV cell for a nullable number: '' for None (abstain-not-guess is recorded)."""
    return "" if v is None else str(v)


def _as_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_int(v) -> Optional[int]:
    f = _as_float(v)
    return None if f is None else int(f)


# --------------------------------------------------------------------------- #
# Building snapshot rows from a ranker-only pipeline result + street consensus
# --------------------------------------------------------------------------- #
def _criterion_of(reason: str) -> str:
    """A short criterion tag for an EXCLUDED verdict, from the pipeline's reason.

    'screen: min_roic (observed ...)' -> 'min_roic'; the coarser cap/sector/payout
    reasons map to their gate name."""
    if reason.startswith("screen: "):
        return reason[len("screen: "):].split()[0]
    low = reason.lower()
    if "market cap" in low:
        return "min_market_cap"
    if "payout" in low:
        return "payout"
    if "sector" in low:
        return "sector"
    if "missing factor" in low:
        return "missing_factor"
    return reason.split()[0] if reason else "unknown"


def build_snapshot_rows(result, consensus: dict[str, StreetConsensus], *,
                        snapshot_date: date, strategy: str) -> list[SnapshotRow]:
    """Turn a ranker-only ``RankPipelineResult`` + a {ticker: StreetConsensus} map into
    the append-only rows. Ranked names carry their verdict + combined rank; a SELL row
    carries the relative-rank basis note; EXCLUDED and UNRATEABLE names are recorded
    too (an exclusion is a call — it gets scored like any other)."""
    iso = snapshot_date.isoformat()
    n_universe = len(result.ranked)
    universe_id = getattr(result, "meta", {}).get("universe_id", "") or ""
    names = getattr(result, "names", {}) or {}
    rows: list[SnapshotRow] = []

    def _row(ticker, verdict, combined_rank, note):
        c = consensus.get(ticker)
        return SnapshotRow(
            snapshot_date=iso, strategy=strategy, universe_id=universe_id,
            ticker=ticker, aristos_verdict=verdict, combined_rank=combined_rank,
            price=(c.current_price if c else None),
            street_mean=(c.recommendation_mean if c else None),
            n_analysts=(c.n_analysts if c else None),
            target_mean=(c.target_mean_price if c else None), notes=note,
            company_name=names.get(ticker))

    for r in result.ranked:
        verdict = r.verdict.upper()
        note = SELL_NOTE.format(n=n_universe) if verdict == "SELL" else ""
        rows.append(_row(r.ticker, verdict, round(r.combined_rank, 4), note))
    for t, reason in result.excluded:
        rows.append(_row(t, "EXCLUDED:" + _criterion_of(reason), None, reason))
    for t, reason in result.unrateable:
        rows.append(_row(t, "UNRATEABLE", None, reason))
    return rows


# --------------------------------------------------------------------------- #
# CSV sink — append-only, never rewritten
# --------------------------------------------------------------------------- #
# Column order BEFORE company_name was inserted — the shape of the legacy rows a
# whole-row-quoted line re-parses into (ITEM 5).
_LEGACY_COLUMNS = [c for c in COLUMNS if c != "company_name"]


def _repair_whole_row_quoted(d: dict, fieldnames: list[str]) -> dict:
    """Repair a legacy WHOLE-ROW-QUOTED row (ITEM 5).

    A row that was serialized as ONE quoted string reads back, under a strict CSV parser,
    as a single column: the entire row text lands in the FIRST field and every other
    field is empty/None. Detect that shape (ticker empty AND the first column carries
    embedded commas) and re-parse the first column back into fields. A normal row is
    returned unchanged — so this is a safe read-side normalization, and the next write
    then emits the row with proper per-field quoting (never whole-row-quoted)."""
    first = fieldnames[0] if fieldnames else "snapshot_date"
    if not d.get("ticker") and "," in (d.get(first) or ""):
        fields = next(csv.reader([d[first]]), [])
        keys = _LEGACY_COLUMNS if len(fields) == len(_LEGACY_COLUMNS) else COLUMNS
        return dict(zip(keys, fields))
    return d


def _upgrade_schema(path: Path) -> None:
    """One-time ADDITIVE column upgrade for an existing CSV whose header predates a new
    column (e.g. Item 1's ``universe_id``). Reads existing rows, rewrites with the full
    ``COLUMNS`` header, missing cells -> ''. This preserves every recorded VALUE (no
    verdict is changed), so it honours 'append-only' — it adds a column, never rewrites
    history."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        if set(COLUMNS) <= set(header):
            return                                    # already current (or a superset)
        existing = [_repair_whole_row_quoted(d, header) for d in reader]
    with path.open("w", newline="", encoding="utf-8") as fh:
        # QUOTE_MINIMAL (explicit): only fields that NEED quoting (a comma in notes) are
        # quoted — a row is never whole-row-quoted (ITEM 5).
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore",
                           quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for d in existing:
            w.writerow({c: d.get(c, "") for c in COLUMNS})


def append_rows(rows: list[SnapshotRow], path: str | Path) -> Path:
    """APPEND rows to the snapshot CSV (write the header only when creating it). The
    store is append-only by contract: a re-run adds rows, never rewrites history. If an
    existing file predates a schema column, it is upgraded additively first (values
    preserved) so appended rows stay aligned."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    if not new:
        _upgrade_schema(p)
    with p.open("a", newline="", encoding="utf-8") as fh:
        # QUOTE_MINIMAL (explicit) — never whole-row-quote a row (ITEM 5).
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore",
                           quoting=csv.QUOTE_MINIMAL)
        if new:
            w.writeheader()
        for row in rows:
            w.writerow(row.to_csv())
    return p


def read_rows(path: str | Path, *, snapshot_date: Optional[date] = None,
              strategy: Optional[str] = None) -> list[SnapshotRow]:
    """Read snapshot rows, optionally filtered to one date and/or strategy."""
    p = Path(path)
    if not p.exists():
        return []
    want_date = snapshot_date.isoformat() if snapshot_date else None
    out: list[SnapshotRow] = []
    with p.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        for d in reader:
            d = _repair_whole_row_quoted(d, header)   # tolerate legacy whole-row-quoting
            if want_date and d.get("snapshot_date") != want_date:
                continue
            if strategy and d.get("strategy") != strategy:
                continue
            out.append(SnapshotRow.from_csv(d))
    return out


# --------------------------------------------------------------------------- #
# Street terciles — RELATIVE buckets within the snapshot universe
# --------------------------------------------------------------------------- #
LOVED, MIDDLE, UNLOVED = "most-loved", "middle", "least-loved"
_STREET_BUCKETS = (LOVED, MIDDLE, UNLOVED)


def street_terciles(rows: list[SnapshotRow]) -> dict[str, str]:
    """Assign each name with a non-null street_mean to a tercile of the universe:
    most-loved / middle / least-loved. Lower recommendationMean = more loved
    (1=StrongBuy). Ties at a tercile edge go to the MORE-LOVED bucket, deterministically
    (the boundary is pushed past equal values). Names with null street data are omitted
    (abstain-not-guess) — the caller still scores them in their Aristos bucket."""
    scored = [(r.ticker, r.street_mean) for r in rows if r.street_mean is not None]
    if not scored:
        return {}
    scored.sort(key=lambda tv: (tv[1], tv[0]))       # most-loved first, ticker tie-break
    n = len(scored)

    def _push(i: int) -> int:
        # extend past a tie so equal values land in the more-loved bucket
        while 0 < i < n and scored[i][1] == scored[i - 1][1]:
            i += 1
        return i

    i1 = _push(n // 3)
    i2 = _push((2 * n) // 3)
    if i2 < i1:
        i2 = i1
    out: dict[str, str] = {}
    for idx, (ticker, _) in enumerate(scored):
        out[ticker] = LOVED if idx < i1 else (MIDDLE if idx < i2 else UNLOVED)
    return out


# --------------------------------------------------------------------------- #
# Divergence map (snapshot-time) — sticky labels + aristos-vs-street disagreement
# --------------------------------------------------------------------------- #
_ARISTOS_TO_STREET = {"BUY": LOVED, "HOLD": MIDDLE, "SELL": UNLOVED}


@dataclass
class DivergenceRow:
    ticker: str
    aristos_verdict: str
    street_mean: Optional[float]
    street_bucket: Optional[str]
    target_mean: Optional[float]
    price: Optional[float]
    tercile_disagreement: bool          # aristos bucket != street bucket
    sticky_label: bool                  # top-tercile rating but target <= price
    company_name: Optional[str] = None  # display label (yfinance longName)

    @property
    def display(self) -> str:
        return display_name(self.ticker, self.company_name)


def divergence_map(rows: list[SnapshotRow]) -> list[DivergenceRow]:
    """Snapshot-time divergences, sorted by street_mean (nulls last): a tercile
    disagreement between Aristos and the street, and 'sticky-label' rows — a name the
    street rates in its most-loved tercile while its own mean target sits AT/BELOW the
    current price (a bullish rating its price target no longer supports)."""
    terciles = street_terciles(rows)
    out: list[DivergenceRow] = []
    for r in rows:
        sb = terciles.get(r.ticker)
        ab = _ARISTOS_TO_STREET.get(r.aristos_verdict)   # None for EXCLUDED/UNRATEABLE
        disagree = bool(ab and sb and ab != sb)
        sticky = bool(sb == LOVED and r.target_mean is not None
                      and r.price is not None and r.target_mean <= r.price)
        out.append(DivergenceRow(
            ticker=r.ticker, aristos_verdict=r.aristos_verdict,
            street_mean=r.street_mean, street_bucket=sb, target_mean=r.target_mean,
            price=r.price, tercile_disagreement=disagree, sticky_label=sticky,
            company_name=r.company_name))
    out.sort(key=lambda d: (d.street_mean is None, d.street_mean or 0.0, d.ticker))
    return out


def format_divergence_map(rows: list[SnapshotRow]) -> str:
    dm = divergence_map(rows)
    lines = ["Divergence map (sorted by street rating; 1=StrongBuy .. 5=Sell)",
             f"  {'name':<34} {'aristos':<16} {'street':<6} {'tercile':<11} "
             f"{'target':<9} {'price':<9} flags"]
    for d in dm:
        flags = []
        if d.tercile_disagreement:
            flags.append("DISAGREE")
        if d.sticky_label:
            flags.append("STICKY-LABEL")
        disp = d.display if len(d.display) <= 34 else d.display[:33] + "…"
        lines.append(
            f"  {disp:<34} {d.aristos_verdict:<16} "
            f"{('—' if d.street_mean is None else f'{d.street_mean:.2f}'):<6} "
            f"{(d.street_bucket or '—'):<11} "
            f"{('—' if d.target_mean is None else f'{d.target_mean:.2f}'):<9} "
            f"{('—' if d.price is None else f'{d.price:.2f}'):<9} "
            f"{', '.join(flags)}")
    n_dis = sum(1 for d in dm if d.tercile_disagreement)
    n_stk = sum(1 for d in dm if d.sticky_label)
    lines.append(f"  -> {n_dis} tercile disagreement(s), {n_stk} sticky-label row(s)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Forward-return scoring
# --------------------------------------------------------------------------- #
def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


@dataclass
class ReturnResult:
    ticker: str
    status: str                         # "OK" | "UNRESOLVED"
    ret: Optional[float]                # total return fraction (end/start - 1)
    start_price: Optional[float] = None
    end_price: Optional[float] = None
    start_day: Optional[date] = None
    end_day: Optional[date] = None
    note: str = ""


def compute_return(bars, snapshot_date: date, want_end: date, *,
                   max_stale_days: int = 7) -> ReturnResult:
    """Forward TOTAL return over [snapshot_date, want_end] from a name's adj-close bars.

    ``bars`` is a sequence with ``.day`` and ``.adj_close`` (a ``PriceHistory.bars``
    list, or any equivalent). UNRESOLVED — reported, never silently dropped and never
    assumed -100% (a delisting can be an acquisition) — when there is no bar on/after
    the snapshot, no bar on/before the window end, or the series STOPS TRADING more than
    ``max_stale_days`` before the window end."""
    seq = sorted(bars, key=lambda b: b.day)
    if not seq:
        return ReturnResult(ticker="", status="UNRESOLVED", ret=None,
                            note="no price history in range")
    start = next((b for b in seq if b.day >= snapshot_date), None)
    end = None
    for b in seq:
        if b.day <= want_end:
            end = b
        else:
            break
    if start is None or end is None or end.day < start.day:
        return ReturnResult(ticker="", status="UNRESOLVED", ret=None,
                            note="no bar bracketing the window")
    last_day = seq[-1].day
    if (want_end - last_day).days > max_stale_days:
        return ReturnResult(ticker="", status="UNRESOLVED", ret=None,
                            start_day=start.day, end_day=last_day,
                            note=f"stopped trading {last_day} before window end "
                                 f"{want_end} — delisted/acquired (not assumed -100%)")
    ret = end.adj_close / start.adj_close - 1.0
    return ReturnResult(ticker="", status="OK", ret=ret,
                        start_price=start.adj_close, end_price=end.adj_close,
                        start_day=start.day, end_day=end.day)


@dataclass
class BucketStat:
    bucket: str
    n: int
    mean: Optional[float]
    median: Optional[float]
    mean_vs_universe: Optional[float]


@dataclass
class StrategyScore:
    strategy: str
    aristos: list[BucketStat]           # BUY/HOLD/SELL/EXCLUDED
    street: list[BucketStat]            # most-loved/middle/least-loved
    universe_mean: Optional[float]
    n_resolved: int
    unresolved: list[tuple[str, str]]   # (ticker, note)
    aristos_ordered: Optional[bool]     # BUY>HOLD>SELL (None if a bucket is empty)
    street_ordered: Optional[bool]      # loved>middle>unloved (None if a bucket empty)


def _stat(bucket: str, rets: list[float], uni_mean: Optional[float]) -> BucketStat:
    if not rets:
        return BucketStat(bucket, 0, None, None, None)
    m = statistics.mean(rets)
    return BucketStat(bucket, len(rets), m, statistics.median(rets),
                      (m - uni_mean) if uni_mean is not None else None)


def _aristos_bucket(verdict: str) -> str:
    if verdict.startswith("EXCLUDED"):
        return "EXCLUDED"
    return verdict            # BUY / HOLD / SELL / UNRATEABLE


_ARISTOS_ORDER = ("BUY", "HOLD", "SELL", "EXCLUDED")


def _ordered(stats: dict[str, BucketStat], keys: tuple[str, ...]) -> Optional[bool]:
    means = [stats[k].mean for k in keys if k in stats and stats[k].mean is not None]
    if len(means) < len(keys):
        return None                     # a bucket is empty -> ordering undefined
    return all(a > b for a, b in zip(means, means[1:]))


def score_strategy(rows: list[SnapshotRow], returns: dict[str, ReturnResult],
                   *, strategy: str) -> StrategyScore:
    """Bucket the resolved forward returns (Aristos verdict + street tercile) and
    compute mean/median per bucket vs the equal-weight universe, plus the pre-committed
    ORDERING flags. Only OK returns count; UNRESOLVED are reported separately."""
    terciles = street_terciles(rows)
    resolved = {t: r for t, r in returns.items() if r.status == "OK" and r.ret is not None}
    uni_mean = statistics.mean([r.ret for r in resolved.values()]) if resolved else None

    aristos_rets: dict[str, list[float]] = {}
    street_rets: dict[str, list[float]] = {}
    for row in rows:
        rr = resolved.get(row.ticker)
        if rr is None:
            continue
        aristos_rets.setdefault(_aristos_bucket(row.aristos_verdict), []).append(rr.ret)
        sb = terciles.get(row.ticker)
        if sb is not None:                              # null street data -> not bucketed
            street_rets.setdefault(sb, []).append(rr.ret)

    aristos_stats = {b: _stat(b, aristos_rets.get(b, []), uni_mean)
                     for b in _ARISTOS_ORDER}
    street_stats = {b: _stat(b, street_rets.get(b, []), uni_mean)
                    for b in _STREET_BUCKETS}

    unresolved = [(t, r.note) for t, r in returns.items() if r.status != "OK"]
    return StrategyScore(
        strategy=strategy,
        aristos=[aristos_stats[b] for b in _ARISTOS_ORDER],
        street=[street_stats[b] for b in _STREET_BUCKETS],
        universe_mean=uni_mean, n_resolved=len(resolved),
        unresolved=sorted(unresolved),
        aristos_ordered=_ordered(aristos_stats, ("BUY", "HOLD", "SELL")),
        street_ordered=_ordered(street_stats, _STREET_BUCKETS))


def _fmt_pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:+.1f}%"


def _fmt_bucket_line(s: BucketStat) -> str:
    return (f"    {s.bucket:<12} n={s.n:<3} mean {_fmt_pct(s.mean):<8} "
            f"median {_fmt_pct(s.median):<8} vs universe {_fmt_pct(s.mean_vs_universe)}")


def format_strategy_score(score: StrategyScore, *, snapshot_date: date,
                          horizon_months: int, partial: bool) -> str:
    period = (f"partial period (horizon of {horizon_months}mo NOT fully elapsed — "
              "scored to today)" if partial
              else f"full {horizon_months}-month horizon")
    lines = [f"=== {score.strategy} · snapshot {snapshot_date.isoformat()} · {period} ===",
             f"  {ADJUSTMENT_NOTE}",
             f"  Equal-weight universe mean: {_fmt_pct(score.universe_mean)} "
             f"({score.n_resolved} resolved name(s))",
             "  Aristos buckets:"]
    lines += [_fmt_bucket_line(s) for s in score.aristos]
    lines.append("  Street terciles (relative — most-loved = lowest recommendationMean):")
    lines += [_fmt_bucket_line(s) for s in score.street]

    def _verdict(flag: Optional[bool]) -> str:
        return "n/a (a bucket is empty)" if flag is None else ("HOLDS" if flag else "does NOT hold")
    lines.append("  Pre-committed ORDERING test (the evidence, not any single name):")
    lines.append(f"    Aristos  BUY > HOLD > SELL: {_verdict(score.aristos_ordered)}")
    lines.append(f"    Street   loved > middle > unloved: {_verdict(score.street_ordered)}")
    if score.unresolved:
        lines.append(f"  UNRESOLVED ({len(score.unresolved)} — reported, never dropped "
                     "or assumed -100%):")
        for t, note in score.unresolved:
            lines.append(f"    {t:<10} {note}")
    lines.append(f"  {STANDING_CAVEAT}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Orchestration cores (adapter injected — the CLIs build the real one; tests fake it)
# --------------------------------------------------------------------------- #
def collect_consensus(adapter, tickers: list[str]) -> dict[str, StreetConsensus]:
    """Fetch street consensus per ticker through the adapter seam. A fetch failure is
    an abstention (all-null consensus), never a guess — analyst data gets the same
    null-not-fabricate discipline as everything else."""
    out: dict[str, StreetConsensus] = {}
    for t in tickers:
        try:
            out[t] = adapter.get_street_consensus(t)
        except Exception:
            out[t] = StreetConsensus(ticker=t)
    return out


def run_snapshot(universe: Optional[list[str]], strategy_id: str, *, adapter,
                 today: date, strategies_dir, out_dir: str | Path,
                 universe_id: Optional[str] = None, universes_dir=None,
                 freeze_dir=None) -> tuple[list[SnapshotRow], Path]:
    """Freeze one snapshot: ranker-only pipeline (no LLM, $0) + street consensus for
    every name (ranked, excluded, unrateable), appended to the CSV. Pass a ``universe``
    list or a ``universe_id`` manifest; the resolved id is stamped on every row. When
    ``freeze_dir`` is set the run's raw inputs are frozen too (ITEM 4 — same freezing as
    a pipeline run), so a snapshot is replayable. Returns the rows and the CSV path.
    Adapter is injected — the CLI builds the real one, tests fake it."""
    from .pipeline import run_rank_pipeline

    result = run_rank_pipeline(universe, strategy_id, ranker_only=True,
                               universe_id=universe_id, universes_dir=universes_dir,
                               strategies_dir=strategies_dir, adapter=adapter,
                               today=today, freeze_dir=freeze_dir)
    tickers = ([r.ticker for r in result.ranked]
               + [t for t, _ in result.excluded]
               + [t for t, _ in result.unrateable])
    consensus = collect_consensus(adapter, tickers)
    rows = build_snapshot_rows(result, consensus, snapshot_date=today,
                               strategy=result.meta["rank_strategy_id"])
    path = append_rows(rows, Path(out_dir) / "verdict_consensus.csv")
    return rows, path


def score_snapshot(rows: list[SnapshotRow], *, adapter, snapshot_date: date,
                   today: date, horizon_months: int) -> tuple[dict[str, StrategyScore], bool]:
    """Score every strategy present in ``rows`` on forward total return. Returns
    ({strategy: StrategyScore}, partial) where ``partial`` is True when the horizon has
    NOT fully elapsed (scored to today, labelled — never a silent full period)."""
    target_end = add_months(snapshot_date, horizon_months)
    partial = target_end > today
    want_end = min(target_end, today)

    returns: dict[str, ReturnResult] = {}
    for row in {r.ticker for r in rows}:
        try:
            bars = adapter.get_price_history(row, start=snapshot_date, end=today).bars
        except Exception:
            bars = []
        rr = compute_return(bars, snapshot_date, want_end)
        returns[row] = ReturnResult(ticker=row, status=rr.status, ret=rr.ret,
                                    start_price=rr.start_price, end_price=rr.end_price,
                                    start_day=rr.start_day, end_day=rr.end_day,
                                    note=rr.note)

    by_strategy: dict[str, list[SnapshotRow]] = {}
    for row in rows:
        by_strategy.setdefault(row.strategy, []).append(row)
    scores = {sid: score_strategy(srows, returns, strategy=sid)
              for sid, srows in by_strategy.items()}
    return scores, partial
