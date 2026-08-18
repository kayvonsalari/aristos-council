"""HOLDINGS-RUN-1 — grade the owner's PRIVATE holdings under every applicable lens
plus the Piotroski F-Score, LOCALLY, with every output kept OUT of this repository.

WHY THIS IS NOT A GITHUB ACTION (do not wire it into one)
---------------------------------------------------------
The scout job's venue — GitHub Actions on this PUBLIC repository, committed result
files, world-readable run logs — is the CORRECT pattern for publicly scouted tickers
and the WRONG one for a personal portfolio. In Actions, the workflow log alone would
publish the holdings list, and a committed artefact would publish it permanently.
So this entrypoint runs on the owner's machine only, and it enforces that in CODE:

  * ``--holdings`` and ``--out`` paths that resolve INSIDE the repository working
    tree are REFUSED (``git add -A`` has swept stray files into this repo before).
  * NOTHING is written under the repo: the grading runs ranker-only with freezing
    and the agreement CSV disabled (the only two writers in the rank path), and the
    fetch cache — whose default ``.aristos_cache`` IS repo-relative — is relocated
    beside ``--out`` (or switched off with ``--no-cache``).
  * ``.gitignore`` carries backstop entries for ``holdings*.csv`` / ``holdings*.txt``.

WHAT IT IS
----------
A THIN orchestration CLI over machinery that already exists — no new decision logic,
no lens changes, no new math:

    Stock rows -> run_multi_strategy_pipeline(STOCK_LENSES, ranker_only)
    ETF rows   -> run_multi_strategy_pipeline(ETF_LENSES,   ranker_only)
    every stock additionally gets tools.screening.piotroski_f_score — DISPLAY ONLY
    (no screen is enabled; ``min_f_score`` remains adopted by no strategy)

Free by construction: deterministic ranker only, no LLM, no narrator, no API keys.

Each name is graded AS IF FRESH: entry price, cost basis and position size are
deliberately absent (that is PORTFOLIO-AWARE-1, still parked).

INPUT (phase 1 is file-in / file-out — deliberately NOT a live Google Sheets read;
Drive credentials stay out of this repo). A CSV with at least the columns ``ticker``
(yfinance symbol) and ``asset_type`` (Stock / ETF / anything else); extra columns are
ignored. Rows that are neither Stock nor ETF (Cash, private companies, investment
trusts), or whose ticker is blank, are SKIPPED and LISTED with a reason — never
guessed, never silently dropped.

Usage (from the repo root, with the CSV and the output OUTSIDE the repo):

    python examples/grade_holdings.py --holdings ~/private/holdings.csv \\
        --out ~/private/holdings_graded.csv
    python examples/grade_holdings.py --holdings ~/private/holdings.csv \\
        --out ~/private/holdings_graded.csv --no-cache
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aristos_council.cli_guards import force_utf8_stdout  # noqa: E402
from aristos_council.data.adapter import normalize_ticker  # noqa: E402
from aristos_council.pipeline import (  # noqa: E402
    MultiStrategyResult,
    format_multi_strategy_grid,
    run_multi_strategy_pipeline,
)
from aristos_council.tools.screening import (  # noqa: E402
    _F_SCORE_CHECK_NAMES,
    FScoreResult,
    piotroski_f_score,
)

STRATEGIES_DIR = ROOT / "strategies"

# The five STOCK lenses and the three ETF lenses — the same sets the scout job and the
# strategy picker use (scripts/scout_verdicts.STOCK_LENSES, tests/test_strategy_
# applicability._ETF_LENSES). Order given = column order in the grid.
STOCK_LENSES = [
    "conservative_plus_v1",       # Defensive Income
    "magic_formula_momentum_v1",  # Value + Momentum
    "magic_formula_raw_v1",       # Greenblatt RAW
    "growth_garp_v2",             # GARP
    "financials_v1",              # Financials
]
ETF_LENSES = ["etf_core_v1", "etf_dividend_v1", "etf_growth_v1"]

STOCK, ETF = "Stock", "ETF"

# The honesty banner. Same discipline as the scoreboard's SELL_NOTE: a rank verdict is
# universe-relative, and a universe made of YOUR OWN holdings has a bottom quintile by
# construction. It leads BOTH the printed report and the CSV.
BANNER = (
    "Verdicts are RELATIVE to this cohort: the bottom quintile of YOUR OWN holdings "
    "reads SELL by construction. Read ranks, screens and factor values; a SELL here "
    "means 'weakest holding', not 'sell it'."
)

# F-Score per-check glyphs: earned / no point / unavailable (never a failed check —
# project rule 3, the null≠false discipline).
_EARNED, _NO_POINT, _UNAVAILABLE = "+", ".", "?"
_CHECK_LEGEND = (f"{_EARNED} point earned   {_NO_POINT} no point   "
                 f"{_UNAVAILABLE} unavailable (NOT a failed check)")

# Cache directory NAME (never a path): the cache lives beside --out, which the privacy
# guard has already proven is outside the repo. The system default (".aristos_cache")
# is repo-RELATIVE, so using it here would write ticker-named files into the working
# tree of a public repo — exactly what this entrypoint exists to prevent.
CACHE_DIR_NAME = ".aristos_holdings_cache"


class PrivacyError(Exception):
    """A path or venue that would publish the portfolio. Refusal, never a warning."""


# --------------------------------------------------------------------------- #
# Privacy guard — paths must live OUTSIDE the repository working tree
# --------------------------------------------------------------------------- #
def ensure_outside_repo(path: str | Path, label: str, *,
                        repo_root: str | Path = ROOT) -> Path:
    """The resolved ``path``, or ``PrivacyError`` if it lands inside ``repo_root``.

    Resolution (not string prefixes) is what is compared, so ``./x``, a relative path
    and a symlink into the tree are all caught.
    """
    resolved = Path(path).expanduser().resolve()
    root = Path(repo_root).expanduser().resolve()
    if resolved == root or root in resolved.parents:
        raise PrivacyError(
            f"refusing {label} {resolved} — it resolves INSIDE the repository "
            f"working tree ({root}). This repository is PUBLIC and 'git add -A' has "
            f"swept stray files into it before, so a holdings file here publishes the "
            f"portfolio. Put it somewhere outside the repo (e.g. your home "
            f"directory).")
    return resolved


# --------------------------------------------------------------------------- #
# Input parsing (pure — no network, no adapter)
# --------------------------------------------------------------------------- #
@dataclass
class Holdings:
    """The parsed holdings file: the two graded buckets and everything skipped.

    ``skipped`` is a list of (row number, ticker-as-written, asset_type-as-written,
    reason) — nothing is dropped silently, and nothing is guessed.
    """

    stocks: list[str] = field(default_factory=list)
    etfs: list[str] = field(default_factory=list)
    skipped: list[tuple[int, str, str, str]] = field(default_factory=list)


def classify_asset_type(raw: str) -> Optional[str]:
    """``STOCK`` / ``ETF`` for the two gradeable kinds, else None (never guessed).

    Anything else — Cash, a private company, an investment trust, a mutual fund, a
    blank cell — is None, which routes the row to the SKIPPED list with its verbatim
    cell text. Only case and surrounding whitespace are normalized: the matching is
    EXACT, so a "Fund" or "Investment trust" is never guessed into the ETF lenses.
    """
    value = (raw or "").strip().lower()
    if value in {"stock", "stocks"}:
        return STOCK
    if value in {"etf", "etfs"}:
        return ETF
    return None


def parse_holdings(text: str) -> Holdings:
    """Parse holdings CSV text into the two buckets + the skipped list.

    Requires ``ticker`` and ``asset_type`` columns (header matching is
    case/space-insensitive); extra columns are ignored. Duplicates within a bucket are
    collapsed to the first occurrence and the later row is LISTED as skipped, so the
    cohort has each name exactly once without a silent drop.
    """
    reader = csv.DictReader(io.StringIO(text))
    fields = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}
    missing = [c for c in ("ticker", "asset_type") if c not in fields]
    if missing:
        raise ValueError(
            f"holdings CSV is missing required column(s): {', '.join(missing)} "
            f"(found: {', '.join(reader.fieldnames or []) or 'nothing'})")

    holdings = Holdings()
    seen: dict[str, str] = {}                    # ticker -> bucket it was graded in
    for i, row in enumerate(reader, start=1):
        raw_ticker = (row.get(fields["ticker"]) or "").strip()
        raw_kind = (row.get(fields["asset_type"]) or "").strip()
        if not raw_ticker and not raw_kind and not any(
                str(v or "").strip() for v in row.values()):
            continue                              # a wholly blank line is not a row
        kind = classify_asset_type(raw_kind)
        if not raw_ticker:
            holdings.skipped.append((i, raw_ticker, raw_kind,
                                     "no ticker in the row — never guessed"))
            continue
        if kind is None:
            shown = raw_kind or "(blank)"
            holdings.skipped.append((
                i, raw_ticker, raw_kind,
                f"asset_type {shown} is neither Stock nor ETF — no lens grades it"))
            continue
        ticker = normalize_ticker(raw_ticker)
        if ticker in seen:
            holdings.skipped.append((
                i, raw_ticker, raw_kind,
                f"duplicate ticker — already graded as {seen[ticker]}"))
            continue
        seen[ticker] = kind
        (holdings.stocks if kind == STOCK else holdings.etfs).append(ticker)
    return holdings


def read_holdings(path: str | Path) -> Holdings:
    return parse_holdings(Path(path).read_text(encoding="utf-8-sig"))


# --------------------------------------------------------------------------- #
# F-Score (display only — no screen is enabled)
# --------------------------------------------------------------------------- #
def f_scores_for(tickers: list[str], adapter) -> dict[str, Optional[FScoreResult]]:
    """``piotroski_f_score`` per ticker. None means the fundamentals fetch itself
    failed (a different failure from a thin-data ABSTENTION, which is an
    ``FScoreResult`` with ``score is None``)."""
    out: dict[str, Optional[FScoreResult]] = {}
    for ticker in tickers:
        try:
            out[ticker] = piotroski_f_score(adapter.get_fundamentals(ticker))
        except Exception as exc:                  # transient/absent data — never fatal
            out[ticker] = None
            print(f"  F-Score: fundamentals unavailable for {ticker} ({exc})")
    return out


def f_score_glyphs(result: FScoreResult) -> str:
    """The nine checks as a fixed-order glyph string (scoring order)."""
    by_name = dict(result.checks)
    return "".join(_EARNED if by_name.get(n) is True
                   else _NO_POINT if by_name.get(n) is False
                   else _UNAVAILABLE
                   for n in _F_SCORE_CHECK_NAMES)


def f_score_display(result: Optional[FScoreResult]) -> str:
    """``score/9`` or an honest abstention marker — never a zero standing in for a gap."""
    if result is None:
        return "no data"
    if result.score is None:
        return f"abstained ({result.computed}/9 computable)"
    return f"{result.score}/9"


def format_f_score_table(tickers: list[str],
                         scores: dict[str, Optional[FScoreResult]],
                         displays: dict[str, str]) -> str:
    """The F-Score block that rides ALONGSIDE the stock grid — score, how many of the
    nine checks were computable, and the per-check outcomes. DISPLAY ONLY: no screen
    consumes it (``min_f_score`` is adopted by no strategy), so it never moves a
    verdict."""
    lines = ["=== PIOTROSKI F-SCORE (display only — no screen consumes it) ===",
             f"  checks in scoring order: {'  '.join(_F_SCORE_CHECK_NAMES)}",
             f"  {_CHECK_LEGEND}",
             "",
             f"  {'name':<24} {'F-Score':<24} {'computed':<10} checks"]
    for ticker in tickers:
        r = scores.get(ticker)
        glyphs = f_score_glyphs(r) if r is not None else "—"
        computed = f"{r.computed}/9" if r is not None else "—"
        lines.append(f"  {displays.get(ticker, ticker)[:24]:<24} "
                     f"{f_score_display(r):<24} {computed:<10} {glyphs}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
@dataclass
class HoldingsRunResult:
    """One holdings grading: the parsed input, the two (optional) grids, the F-Scores,
    the printable report and the CSV rows. Nothing here is persisted by the library —
    only ``write_csv`` writes, and only to the guarded ``--out`` path."""

    holdings: Holdings
    stock_result: Optional[MultiStrategyResult]
    etf_result: Optional[MultiStrategyResult]
    f_scores: dict[str, Optional[FScoreResult]]
    report: str
    rows: list[dict]


def _grade(tickers: list[str], lenses: list[str], *, adapter, today: date,
           strategies_dir: Path, progress: Optional[Callable[[str], None]]
           ) -> Optional[MultiStrategyResult]:
    """One bucket through the shared multi-lens entry — ranker-only by construction,
    and with ``freeze_dir`` left unset so no run record is written."""
    if not tickers:
        return None
    return run_multi_strategy_pipeline(
        tickers, lenses, strategies_dir=strategies_dir, adapter=adapter, today=today,
        progress=progress, freeze_dir=None)


def _displays(*results: Optional[MultiStrategyResult]) -> dict[str, str]:
    out: dict[str, str] = {}
    for res in results:
        for row in (res.rows if res else []):
            out[row.ticker] = row.display
    return out


def _csv_rows(holdings: Holdings, stock: Optional[MultiStrategyResult],
              etf: Optional[MultiStrategyResult],
              f_scores: dict[str, Optional[FScoreResult]]) -> list[dict]:
    """The combined CSV: one row per graded name (its cell under every lens of its
    asset class, plus the F-Score for stocks), then one row per SKIPPED name carrying
    its reason and NO grades."""
    rows: list[dict] = []
    for kind, res, lenses in ((STOCK, stock, STOCK_LENSES), (ETF, etf, ETF_LENSES)):
        by_ticker = {row.ticker: row for row in (res.rows if res else [])}
        for ticker in (holdings.stocks if kind == STOCK else holdings.etfs):
            row = by_ticker.get(ticker)
            f = f_scores.get(ticker)
            out = {
                "status": "graded",
                "asset_type": kind,
                "ticker": ticker,
                "name": row.display if row else ticker,
                "rank_sum": "" if not row or row.rank_sum is None else row.rank_sum,
                "graded_by": row.graded if row else 0,
                "comparable": "yes" if row and row.comparable else "no",
                "f_score": f_score_display(f) if kind == STOCK else "",
                "f_score_computed": (f"{f.computed}/9"
                                     if kind == STOCK and f is not None else ""),
                "f_score_checks": (f_score_glyphs(f)
                                   if kind == STOCK and f is not None else ""),
                "f_score_note": f.note if kind == STOCK and f is not None else "",
                "skip_reason": "",
            }
            for sid in lenses:
                cell = row.cells.get(sid) if row else None
                out[sid] = cell.render() if cell else "—"
                out[f"{sid}_status"] = cell.status if cell else ""
                out[f"{sid}_position"] = (cell.position
                                          if cell and cell.position is not None else "")
            rows.append(out)
    for _i, ticker, raw_kind, reason in holdings.skipped:
        rows.append({"status": "skipped", "asset_type": raw_kind, "ticker": ticker,
                     "name": "", "skip_reason": reason})
    return rows


def _csv_fieldnames() -> list[str]:
    head = ["status", "asset_type", "ticker", "name", "rank_sum", "graded_by",
            "comparable", "f_score", "f_score_computed", "f_score_checks",
            "f_score_note", "skip_reason"]
    for sid in STOCK_LENSES + ETF_LENSES:
        head += [sid, f"{sid}_status", f"{sid}_position"]
    return head


def _format_report(holdings: Holdings, stock: Optional[MultiStrategyResult],
                   etf: Optional[MultiStrategyResult],
                   f_scores: dict[str, Optional[FScoreResult]], today: date) -> str:
    """The printed report. The banner LEADS it — the relative-verdict caveat is read
    before any verdict is."""
    displays = _displays(stock, etf)
    parts = [f"=== HOLDINGS GRADE — {today.isoformat()} ===", BANNER, ""]
    parts.append(
        f"Cohorts graded SEPARATELY: {len(holdings.stocks)} stock(s) under "
        f"{len(STOCK_LENSES)} lens(es), {len(holdings.etfs)} ETF(s) under "
        f"{len(ETF_LENSES)}. Deterministic ranker only — no LLM ran, nothing was spent.")
    parts.append("")
    if stock is not None:
        parts += ["--- STOCKS ---", format_multi_strategy_grid(stock), "",
                  format_f_score_table(holdings.stocks, f_scores, displays), ""]
    else:
        parts += ["--- STOCKS --- (none in the holdings file)", ""]
    if etf is not None:
        parts += ["--- ETFs ---", format_multi_strategy_grid(etf), ""]
    else:
        parts += ["--- ETFs --- (none in the holdings file)", ""]
    parts.append("=== SKIPPED (nothing dropped silently, nothing guessed) ===")
    if holdings.skipped:
        for i, ticker, raw_kind, reason in holdings.skipped:
            shown = ticker or "(blank ticker)"
            parts.append(f"  row {i}: {shown} [{raw_kind or 'blank asset_type'}] "
                         f"— {reason}")
    else:
        parts.append("  none — every row was gradeable.")
    return "\n".join(parts)


def write_csv(path: Path, rows: list[dict]) -> None:
    """Write the combined CSV, BANNER first. The banner rides as leading ``#`` comment
    lines so the caveat cannot be separated from the numbers by a copy-paste."""
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        for line in BANNER.split(". "):
            text = line if line.endswith(".") else f"{line}."
            fh.write(f"# {text}\n")
        writer = csv.DictWriter(fh, fieldnames=_csv_fieldnames(),
                                extrasaction="ignore", quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_adapter(*, today: date, cache_dir: Optional[Path]):
    """The market adapter for a holdings run: the SAME provider + retry armour the rest
    of the system uses, with the cache either OFF or in the caller-chosen (guarded,
    outside-the-repo) directory — never the repo-relative default."""
    from aristos_council.data.provider import select_market_adapter
    from aristos_council.data.retry import RetryAdapter

    adapter = RetryAdapter(select_market_adapter())
    if cache_dir is not None:
        from aristos_council.data.cache import CachingAdapter
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        adapter = CachingAdapter(adapter, cache_dir=cache_dir, today=today)
    return adapter


def grade_holdings(holdings_path: str | Path, out_path: str | Path, *, adapter=None,
                   today: Optional[date] = None,
                   strategies_dir: str | Path = STRATEGIES_DIR,
                   cache_dir: str | Path | None = None, use_cache: bool = True,
                   repo_root: str | Path = ROOT,
                   progress: Optional[Callable[[str], None]] = None,
                   write: bool = True) -> HoldingsRunResult:
    """Grade a holdings CSV and (unless ``write`` is False) write the combined CSV.

    Both paths are guarded FIRST: a path inside the repository working tree is refused
    before anything is read, fetched or written. Grading is ranker-only, so the run is
    free and writes nothing but ``out_path``.
    """
    holdings_file = ensure_outside_repo(holdings_path, "--holdings", repo_root=repo_root)
    out_file = ensure_outside_repo(out_path, "--out", repo_root=repo_root)
    today = today or date.today()
    holdings = read_holdings(holdings_file)

    if adapter is None:
        resolved_cache = None
        if use_cache:
            resolved_cache = ensure_outside_repo(
                cache_dir if cache_dir else out_file.parent / CACHE_DIR_NAME,
                "--cache-dir", repo_root=repo_root)
        adapter = build_adapter(today=today, cache_dir=resolved_cache)

    common = dict(adapter=adapter, today=today,
                  strategies_dir=Path(strategies_dir), progress=progress)
    stock = _grade(holdings.stocks, STOCK_LENSES, **common)
    etf = _grade(holdings.etfs, ETF_LENSES, **common)
    # F-Score AFTER the grading pass, so a warm cache serves the fundamentals.
    f_scores = f_scores_for(holdings.stocks, adapter)

    report = _format_report(holdings, stock, etf, f_scores, today)
    rows = _csv_rows(holdings, stock, etf, f_scores)
    if write:
        write_csv(out_file, rows)
    return HoldingsRunResult(holdings=holdings, stock_result=stock, etf_result=etf,
                             f_scores=f_scores, report=report, rows=rows)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Grade PRIVATE holdings under every applicable lens + the "
                    "Piotroski F-Score. Local only — never wired into GitHub Actions.")
    p.add_argument("--holdings", required=True,
                   help="CSV with at least 'ticker' and 'asset_type' columns. MUST be "
                        "outside this repository (public repo).")
    p.add_argument("--out", required=True,
                   help="combined CSV to write. MUST be outside this repository.")
    p.add_argument("--cache-dir", default=None,
                   help=f"fetch cache directory (default: {CACHE_DIR_NAME} beside "
                        f"--out). Also refused inside the repo.")
    p.add_argument("--no-cache", action="store_true",
                   help="do not cache fetches to disk at all")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    force_utf8_stdout()      # the grid's '—' / '‡' must not crash a cp1252 console
    args = parse_args(argv)
    try:
        result = grade_holdings(
            args.holdings, args.out, cache_dir=args.cache_dir,
            use_cache=not args.no_cache, progress=lambda msg: print(f"  {msg}"))
    except PrivacyError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(result.report)
    print(f"\nwrote {Path(args.out).expanduser().resolve()} "
          f"({len(result.rows)} row(s)) — outside the repository, as required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
