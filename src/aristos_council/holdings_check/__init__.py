"""HOLDINGS-CHECK-1 - a private, on-demand check of the owner's own holdings, on this PC.

    python -m aristos_council.holdings_check [--names X,Y] [--dated] [--council]

It reads ``data/local/holdings/holdings.csv`` (git-ignored; names, tickers, broker, type - never
amounts), runs each STOCK through the same engine as the Analyse tab's company page
(``run_company_report``: its own peer group, every stock lens's vote with its track-record badge,
the valuation band, the small-company path under $5bn, cash runway, abstentions with reasons) and
writes ``summary.md``, ``summary.csv`` and one full company report per stock - all under
``data/local/holdings/``. Nothing about a holding is ever written anywhere else: the repo is public.

No model is called unless ``--council`` is passed. ETFs, funds and other types are listed as skipped
with a reason and never graded; a ticker that does not resolve is listed with the reason, never
guessed. The maths is the existing deterministic core; this package only chooses what to run and
lays the results out.
"""

from __future__ import annotations

import csv
import io
import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[3]
LOCAL_ROOT = ROOT / "data" / "local"
HOLDINGS_DIR = LOCAL_ROOT / "holdings"
HOLDINGS_CSV = HOLDINGS_DIR / "holdings.csv"

COLUMNS = ("name", "ticker", "broker", "type", "note")
TYPES = ("stock", "etf", "fund", "other")
TEMPLATE_ROWS = (
    ("Example Company A", "EXA", "Example broker", "stock", "replace this row"),
    ("Example Fund B", "EXB", "Example broker", "etf", "replace this row"),
)

SUMMARY_FIELDS = ("name", "ticker", "status", "cohort_or_peer_group", "verdict", "rank_in_group",
                  "lenses_voting", "valuation_band", "warnings", "report")
SKIP_REASONS = {
    "etf": "an ETF is a basket, not a company - the stock lenses do not grade it",
    "fund": "a fund is not a listed company - the stock lenses do not grade it",
    "other": "this type is not a listed company - the stock lenses do not grade it",
}


class OutsideLocalError(RuntimeError):
    """An output path that would leave ``data/local/`` - refused before anything is written."""


@dataclass(frozen=True)
class Holding:
    name: str
    ticker: str
    broker: str = ""
    type: str = "stock"
    note: str = ""


@dataclass
class Row:
    """One line of the summary: a graded stock, a skipped holding or an unresolved ticker."""
    name: str
    ticker: str
    status: str                     # graded | skipped | unresolved | failed
    cohort_or_peer_group: str = ""
    verdict: str = ""
    rank_in_group: str = ""
    lenses_voting: str = ""
    valuation_band: str = ""
    warnings: str = ""
    report: str = ""                # relative path of the full report, "" when there is none
    reason: str = ""                # why skipped / unresolved / failed (kept in ``verdict`` column)

    def as_csv(self) -> dict:
        d = {k: getattr(self, k) for k in SUMMARY_FIELDS}
        if self.reason and not d["verdict"]:
            d["verdict"] = self.reason
        return d


# --------------------------------------------------------------------------- #
# The file
# --------------------------------------------------------------------------- #
def parse_holdings(text: str) -> tuple[list[Holding], list[str]]:
    """``(holdings, problems)`` from the CSV text. Columns are matched by header name (any order,
    any case); a blank line is ignored; a row with neither name nor ticker is a problem, not a
    holding."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}
    problems: list[str] = []
    missing = [c for c in ("name", "ticker", "type") if c not in fields]
    if missing:
        return [], [f"the holdings file has no {', '.join(missing)} column "
                    f"(expected: {', '.join(COLUMNS)})"]
    out: list[Holding] = []
    for n, raw in enumerate(reader, start=2):
        def cell(key: str) -> str:
            col = fields.get(key)
            return (raw.get(col) or "").strip() if col else ""
        if not any((v or "").strip() for v in raw.values() if isinstance(v, str)):
            continue
        name, ticker = cell("name"), cell("ticker")
        if not name and not ticker:
            problems.append(f"line {n}: no name and no ticker")
            continue
        out.append(Holding(name=name or ticker, ticker=ticker, broker=cell("broker"),
                           type=cell("type").lower() or "stock", note=cell("note")))
    return out, problems


def write_template(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        w.writerows(TEMPLATE_ROWS)


def _rows_from_local_json(path: Path) -> list[Holding]:
    """Seed rows from a PORTFOLIO-AWARE-1 style ``holdings.json``: a list of objects (or an object
    holding such a list under ``holdings``) with a ticker and optionally a name/type. Amounts are
    ignored on purpose."""
    import json
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("holdings") or data.get("positions") or []
    rows = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, str):
            item = {"ticker": item}
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or item.get("symbol") or "").strip()
        if ticker:
            rows.append(Holding(name=str(item.get("name") or ticker), ticker=ticker,
                                broker=str(item.get("broker") or ""),
                                type=str(item.get("type") or "stock").lower(),
                                note=str(item.get("note") or "")))
    return rows


def seed_or_template(csv_path: Path, *, local_root: Path = LOCAL_ROOT) -> str:
    """Create the holdings file when it is missing. Seeds it from ``data/local/holdings.json`` if
    that exists; otherwise writes the two-row template. Returns one line saying what was done."""
    csv_path = Path(csv_path)
    legacy = Path(local_root) / "holdings.json"
    if legacy.is_file():
        rows = _rows_from_local_json(legacy)
        if rows:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            with csv_path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(COLUMNS)
                for h in rows:
                    w.writerow((h.name, h.ticker, h.broker, h.type, h.note))
            return (f"Seeded {csv_path} with {len(rows)} row(s) from {legacy} (amounts were not "
                    f"copied). Check the types and fill in brokers.")
    write_template(csv_path)
    return (f"No holdings file existed, so a template with two example rows was written to "
            f"{csv_path}. Replace the rows with your own (name, ticker, broker, type, note - no "
            f"amounts) and run this again.")


# --------------------------------------------------------------------------- #
# Containment: nothing leaves data/local/
# --------------------------------------------------------------------------- #
def ensure_local(path: Path, local_root: Path = LOCAL_ROOT) -> Path:
    resolved, root = Path(path).resolve(), Path(local_root).resolve()
    if root != resolved and root not in resolved.parents:
        raise OutsideLocalError(
            f"{resolved} is outside {root}. Holdings output stays under data/local/ because the "
            f"repository is public.")
    return resolved


# --------------------------------------------------------------------------- #
# Reading a report into a summary row
# --------------------------------------------------------------------------- #
def _ordinal_rank(position, size) -> str:
    return f"{position} of {size}" if position and size else ""


def _warnings(report) -> list[str]:
    out: list[str] = []
    if getattr(report, "outside_tested_range", False):
        out.append("outside tested range (under $5bn)")
        if getattr(report, "size_matched_peers", False):
            out.append("ranked against size-matched peers, no small-company band")
    group = report.peer_group
    if group is not None and getattr(group, "members", None) and getattr(group, "thin", False):
        out.append(f"small peer group: {len(group.members)} companies")
    for v in report.votes:
        if v.ranked and v.small_group_note:
            out.append(f"{v.label}: {v.small_group_note}")
    di = report.check.data_integrity
    if getattr(di, "abstained_criteria", None):
        out.append("data gaps (screen): " + ", ".join(di.abstained_criteria))
    if getattr(di, "not_evaluated_factors", None):
        out.append("data gaps (factors): " + ", ".join(di.not_evaluated_factors))
    if getattr(di, "implausible", None):
        out.append("implausible vendor value flagged")
    for v in report.votes:
        if v.status in ("fetch_error", "unrateable"):
            out.append(f"{v.label}: {v.reason or v.status}")
    seen, uniq = set(), []
    for w in out:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq


def _group_text(report) -> str:
    group = report.peer_group
    if report.cohort_slug:
        return f"cohort {report.cohort_slug}"
    if group is not None and getattr(group, "members", None):
        band = getattr(group, "band", "") or ""
        return f"peer group: {len(group.members)} peers ({band})".replace(" ()", "")
    return "none (no peer group formed)"


def row_from_report(h: Holding, report, report_path: str) -> Row:
    """Summarise a ``CompanyReport`` as one row. ``verdict`` is the Analyse tab's own headline."""
    from ..company_story import story_page
    if report.unrateable:
        return Row(h.name, h.ticker, "unresolved",
                   reason=f"no data for this ticker ({report.check.data_integrity.note})",
                   report=report_path)
    agreement = report.agreement
    verdict = agreement.headline if agreement is not None else (
        report.no_vote_reason or "no lens voted")
    try:
        answer = story_page(report).answer[0]
        if answer:
            verdict = answer
    except Exception:                                        # noqa: BLE001 - headline is enough
        pass
    ranked = [v for v in report.votes if v.ranked and v.votes]
    lead = next((v for v in ranked if v.verdict == "buy"), ranked[0] if ranked else None)
    rank = _ordinal_rank(lead.position, lead.cohort_size) if lead else ""
    if lead and rank:
        rank += f" ({lead.label})"
    voting = [v for v in report.votes if v.votes]
    voted = ", ".join(f"{v.label}: {v.word}" for v in ranked) or "none"
    return Row(h.name, h.ticker, "graded", cohort_or_peer_group=_group_text(report),
               verdict=verdict, rank_in_group=rank,
               lenses_voting=f"{len(ranked)} of {len(voting)} - {voted}",
               valuation_band=report.check.valuation_band or "—",
               warnings="; ".join(_warnings(report)), report=report_path)


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #
Runner = Callable[..., object]


def slug(h: Holding) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "_", h.ticker or h.name).strip("_") or "holding"
    return base.lower()


def default_runner(ticker: str, *, runs_dir: Path, with_council: bool, progress=None):
    """The Analyse tab's company run, with every lens it offers in Stocks mode ticked."""
    from ..data.adapter import normalize_ticker
    from ..data.cache import DEFAULT_CACHE_DIR, CachingAdapter
    from ..data.provider import select_market_adapter
    from ..company_report import run_company_report
    from ..smallcap_band import SMALLCAP_CEILING_USD
    from ..sweep.producers import stock_lenses

    strategies_dir, universes_dir = ROOT / "strategies", ROOT / "universes"
    adapter = CachingAdapter(select_market_adapter(), cache_dir=DEFAULT_CACHE_DIR,
                             today=date.today())
    t = normalize_ticker(ticker)
    cap = None
    try:                                         # the same three-tier size lookup as the tab
        from ..market_index import peers as _peers
        subject = getattr(_peers(t), "subject", None)
        cap = getattr(subject, "market_cap_usd", None) if subject is not None else None
    except Exception:                                        # noqa: BLE001
        cap = None
    if cap is None:
        try:
            cap = adapter.get_fundamentals(t).market_cap
        except Exception:                                    # noqa: BLE001
            cap = None
    lenses = [sid for sid, _ in stock_lenses(strategies_dir)]
    return run_company_report(
        t, lenses, adapter=adapter, strategies_dir=strategies_dir, universes_dir=universes_dir,
        runs_dir=runs_dir, with_summary=False, with_council=with_council, with_judges=False,
        include_small=bool(cap is not None and cap < SMALLCAP_CEILING_USD),
        save=False, progress=progress)


@dataclass
class CheckResult:
    rows: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    out_dir: Optional[Path] = None
    changes: list = field(default_factory=list)


def run_check(holdings: list[Holding], out_dir: Path, *, runner: Optional[Runner] = None,
              names: Optional[list[str]] = None, with_council: bool = False, dated: bool = False,
              today: Optional[date] = None, local_root: Path = LOCAL_ROOT,
              progress: Optional[Callable[[str], None]] = None,
              markdown: Optional[Callable[[object], str]] = None) -> CheckResult:
    """Grade the stocks, write the summary and the per-stock reports under ``out_dir``."""
    say = progress or (lambda _m: None)
    out_dir = ensure_local(out_dir, local_root)
    today = today or date.today()
    runner = runner or default_runner
    if markdown is None:
        from ..company_markdown import company_report_markdown as markdown
    wanted = {n.strip().lower() for n in names or [] if n.strip()}
    if wanted:
        holdings = [h for h in holdings
                    if h.name.lower() in wanted or h.ticker.lower() in wanted]
    reports_dir = out_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = ensure_local(out_dir / "_runs", local_root)

    from ..cli_guards import implausible_ticker_reason
    result = CheckResult(out_dir=out_dir)
    used: set[str] = set()
    for h in holdings:
        if h.type not in TYPES:
            result.rows.append(Row(h.name, h.ticker, "skipped",
                                   reason=f"skipped: type {h.type!r} is not one of "
                                          f"{', '.join(TYPES)}, so it was not graded"))
            continue
        if h.type != "stock":
            result.rows.append(Row(h.name, h.ticker, "skipped",
                                   reason=f"skipped: {SKIP_REASONS[h.type]}"))
            continue
        if not h.ticker:
            result.rows.append(Row(h.name, "", "unresolved",
                                   reason="no ticker in the holdings file, so nothing to look up "
                                          "(a ticker is never guessed from a name)"))
            continue
        why = implausible_ticker_reason(h.ticker)
        if why:
            result.rows.append(Row(h.name, h.ticker, "unresolved",
                                   reason=f"not a usable ticker: {why}"))
            continue
        say(f"{h.name} ({h.ticker})")
        try:
            report = runner(h.ticker, runs_dir=runs_dir, with_council=with_council,
                            progress=say)
        except Exception as exc:                             # noqa: BLE001 - one name never stops the rest
            result.rows.append(Row(h.name, h.ticker, "failed",
                                   reason=f"the run failed: {type(exc).__name__}: {exc}"))
            continue
        name = slug(h)
        while name in used:
            name += "_2"
        used.add(name)
        rel = f"reports/{name}.md"
        (out_dir / rel).write_text(markdown(report), encoding="utf-8")
        result.rows.append(row_from_report(h, report, rel))

    previous = _previous_dated(out_dir, today) if dated else None
    if previous is not None:
        result.changes = changes_since(previous[1], result.rows)
    write_summary(out_dir, result, today=today, previous_date=previous[0] if previous else None,
                  council=with_council)
    if dated:
        keep_dated_copy(out_dir, today)
    shutil.rmtree(runs_dir, ignore_errors=True)
    return result


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def _cell(text: str) -> str:
    return (text or "").replace("|", "/").replace("\n", " ")


def summary_markdown(result: CheckResult, *, today: date, previous_date: Optional[str],
                     council: bool) -> str:
    rows = result.rows
    graded = [r for r in rows if r.status == "graded"]
    out = [f"# Holdings check - {today.isoformat()}", "",
           "*Private: this file lives under data/local/ and is never committed. The verdicts come "
           "from the deterministic ranker; nothing here changes them.*", "",
           f"{len(graded)} graded, {sum(r.status == 'skipped' for r in rows)} skipped, "
           f"{sum(r.status in ('unresolved', 'failed') for r in rows)} not resolved or failed. "
           + ("A council narration was requested (it writes, it never votes)."
              if council else "No model was called."), "",
           "| Holding | Ticker | Group used | Verdict | Rank | Lenses voting | Valuation band "
           "| Warnings | Report |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        d = r.as_csv()
        link = f"[report]({r.report})" if r.report and r.status == "graded" else ""
        out.append("| " + " | ".join(_cell(x) for x in (
            d["name"], d["ticker"], d["cohort_or_peer_group"], d["verdict"], d["rank_in_group"],
            d["lenses_voting"], d["valuation_band"], d["warnings"])) + f" | {link} |")
    if previous_date is not None:
        out += ["", f"## Changed since {previous_date}", ""]
        out += [f"- {c}" for c in result.changes] or ["- Nothing changed."]
    return "\n".join(out) + "\n"


def write_summary(out_dir: Path, result: CheckResult, *, today: date,
                  previous_date: Optional[str], council: bool) -> None:
    (out_dir / "summary.md").write_text(
        summary_markdown(result, today=today, previous_date=previous_date, council=council),
        encoding="utf-8")
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        for r in result.rows:
            w.writerow(r.as_csv())


def keep_dated_copy(out_dir: Path, today: date) -> Path:
    target = out_dir / "dated" / today.isoformat()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name in ("summary.md", "summary.csv"):
        shutil.copy2(out_dir / name, target / name)
    shutil.copytree(out_dir / "reports", target / "reports")
    return target


def _previous_dated(out_dir: Path, today: date) -> Optional[tuple[str, list[dict]]]:
    base = out_dir / "dated"
    if not base.is_dir():
        return None
    older = sorted(p.name for p in base.iterdir()
                   if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)
                   and p.name < today.isoformat() and (p / "summary.csv").is_file())
    if not older:
        return None
    with (base / older[-1] / "summary.csv").open(newline="", encoding="utf-8") as fh:
        return older[-1], list(csv.DictReader(fh))


def changes_since(previous: list[dict], rows: list[Row]) -> list[str]:
    """Plain sentences for what moved between two runs: verdict, rank, band, new and gone."""
    key = lambda name, ticker: (ticker or name).upper()           # noqa: E731
    before = {key(p["name"], p["ticker"]): p for p in previous}
    now = {key(r.name, r.ticker): r.as_csv() for r in rows}
    out: list[str] = []
    for k, cur in now.items():
        old = before.get(k)
        label = f"{cur['name']} ({cur['ticker']})" if cur["ticker"] else cur["name"]
        if old is None:
            out.append(f"{label}: new in this run.")
            continue
        for column, word in (("verdict", "verdict"), ("rank_in_group", "rank"),
                             ("valuation_band", "valuation band"), ("status", "status")):
            if (old.get(column) or "") != (cur.get(column) or ""):
                out.append(f"{label}: {word} was '{old.get(column) or 'none'}', now "
                           f"'{cur.get(column) or 'none'}'.")
    for k, old in before.items():
        if k not in now:
            out.append(f"{old['name']} ({old['ticker']}): not in this run.")
    return out
