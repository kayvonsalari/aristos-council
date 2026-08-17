"""Piotroski F-Score COVERAGE PROBE (PIOTROSKI-1, Phase 3 item 16).

Answers the gate question before any question about whether the F-Score is USEFUL:
**is it computable on your universes at all?**

For every name it reports the score, how many of the nine checks were computable, and
WHICH checks were unavailable — then aggregates per-check availability across the
universe. A check that is unavailable on most names systematically depresses every
score in that universe, and nothing downstream (threshold choice, exclusion deltas,
adoption) means anything until you know that.

NO LLM, no council, no spend. Fundamentals only — it does NOT fetch price history, so
it is much faster than a rank run. Uses the SAME adapter and cache the rest of the
system uses (respects ARISTOS_MARKET_PROVIDER).

Usage:
    python examples/piotroski_probe.py --universe defensive_16_v1
    python examples/piotroski_probe.py --universe defensive_16_v1 growth_40_v1 financials_16_v1
    python examples/piotroski_probe.py AAPL MSFT NVO --csv probe.csv
    python examples/piotroski_probe.py --universe growth_40_v1 --refresh   # ignore cache

Read the output in this order:
    1. PER-CHECK AVAILABILITY  — the gate. Any check under ~80% is a data problem.
    2. SCORE DISTRIBUTION      — does the score discriminate, or does everything cluster?
    3. PER-NAME TABLE          — the names behind the aggregates.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

from aristos_council.data.adapter import normalize_ticker
from aristos_council.data.cache import DEFAULT_CACHE_DIR, CachingAdapter
from aristos_council.data.provider import select_market_adapter
from aristos_council.tools.screening import (
    _F_SCORE_CHECK_NAMES,
    _F_SCORE_MIN_COMPUTABLE,
    FScoreResult,
    piotroski_f_score,
)
from aristos_council.universe import load_universe_by_id

ROOT = Path(__file__).resolve().parents[1]
UNIVERSES_DIR = ROOT / "universes"

# Availability below this fraction means the check is effectively dead on that universe
# and every score in it is depressed by a point it never had a chance to earn.
_AVAILABILITY_WARN = 0.80

# Plain-English gloss per check, so the output reads without the source open.
_CHECK_GLOSS: dict[str, str] = {
    "roa_positive": "1 ROA > 0",
    "ocf_positive": "2 operating cash flow > 0",
    "roa_improved": "3 ROA improved YoY",
    "ocf_exceeds_net_income": "4 OCF > net income (cash quality)",
    "ltd_ratio_decreased": "5 long-term debt ratio fell",
    "current_ratio_improved": "6 current ratio improved",
    "no_new_share_issuance": "7 no new shares issued",
    "gross_margin_improved": "8 gross margin improved",
    "asset_turnover_improved": "9 asset turnover improved",
}


# --------------------------------------------------------------------------- #
# Pure aggregation (no network, no adapter — unit-testable on synthetic rows)
# --------------------------------------------------------------------------- #
def summarize(rows: list[tuple[str, FScoreResult | None]]) -> dict:
    """Aggregate probe rows. ``None`` as the result means fundamentals were absent
    entirely (a delisted / all-404 name), which is counted SEPARATELY from an
    abstention — no data is a different failure from thin data."""
    no_data = [t for t, r in rows if r is None]
    live = [(t, r) for t, r in rows if r is not None]

    availability: dict[str, int] = {name: 0 for name in _F_SCORE_CHECK_NAMES}
    earned: dict[str, int] = {name: 0 for name in _F_SCORE_CHECK_NAMES}
    for _, r in live:
        for name, outcome in r.checks:
            if outcome is not None:
                availability[name] += 1
                if outcome is True:
                    earned[name] += 1

    abstained = [t for t, r in live if r.score is None]
    scored = [(t, r) for t, r in live if r.score is not None]
    distribution: dict[int, int] = {n: 0 for n in range(10)}
    for _, r in scored:
        distribution[int(r.score)] += 1

    return {
        "n_total": len(rows),
        "n_live": len(live),
        "n_no_data": len(no_data),
        "no_data": no_data,
        "n_abstained": len(abstained),
        "abstained": abstained,
        "n_scored": len(scored),
        "availability": availability,
        "earned": earned,
        "distribution": distribution,
        "mean_unavailable": (
            sum(r.unavailable for _, r in live) / len(live) if live else 0.0),
    }


def format_report(label: str, rows, summary: dict) -> str:
    """The human report. Pure string building so it can be diffed / saved."""
    n_live = summary["n_live"]
    out: list[str] = []
    out.append("=" * 78)
    out.append(f"PIOTROSKI F-SCORE COVERAGE PROBE — {label}")
    out.append(f"{summary['n_total']} names | {n_live} with fundamentals | "
               f"{summary['n_no_data']} no data | {summary['n_abstained']} abstained "
               f"(<{_F_SCORE_MIN_COMPUTABLE} checks) | {summary['n_scored']} scored")
    out.append("=" * 78)

    # 1. THE GATE.
    out.append("")
    out.append("1. PER-CHECK AVAILABILITY (the gate — anything low is a DATA problem,")
    out.append("   not a company problem; it depresses every score in this universe)")
    out.append("")
    out.append(f"   {'check':<36} {'available':>10} {'of live':>8}  {'earned':>7}")
    for name in _F_SCORE_CHECK_NAMES:
        avail = summary["availability"][name]
        pct = (avail / n_live) if n_live else 0.0
        flag = "  <-- LOW" if pct < _AVAILABILITY_WARN else ""
        earned_pct = (summary["earned"][name] / avail) if avail else 0.0
        out.append(f"   {_CHECK_GLOSS[name]:<36} {pct:>9.0%} {avail:>8} "
                   f"{earned_pct:>7.0%}{flag}")
    out.append("")
    out.append(f"   Mean unavailable checks per name: {summary['mean_unavailable']:.2f} of 9")

    low = [n for n in _F_SCORE_CHECK_NAMES
           if n_live and (summary["availability"][n] / n_live) < _AVAILABILITY_WARN]
    if low:
        out.append("")
        out.append("   VERDICT: the following checks are effectively unavailable here —")
        for n in low:
            out.append(f"     - {_CHECK_GLOSS[n]}")
        out.append("   Fix the adapter mapping before trusting any threshold work.")
    else:
        out.append("")
        out.append("   VERDICT: all nine checks are broadly available. Coverage gate PASSED.")

    # The specific finding from the PIOTROSKI-1 audit.
    ltd_avail = summary["availability"]["ltd_ratio_decreased"]
    if n_live and ltd_avail < n_live:
        out.append("")
        out.append(f"   NOTE (audit item 17): check 5 was unavailable on "
                   f"{n_live - ltd_avail} of {n_live} names. A debt-free company whose")
        out.append("   provider OMITS the long-term-debt line is counted unavailable, not")
        out.append("   penalised — so the strict zero-LTD convention may rarely bind.")

    # 2. Distribution.
    out.append("")
    out.append("2. SCORE DISTRIBUTION (does it discriminate, or does everything cluster?)")
    out.append("")
    peak = max(summary["distribution"].values()) if summary["n_scored"] else 0
    for score in range(9, -1, -1):
        count = summary["distribution"][score]
        bar = "#" * int(round((count / peak) * 40)) if peak else ""
        out.append(f"   {f'{score}/9':<4} {count:>4}  {bar}")
    if summary["n_abstained"]:
        out.append(f"   {'abst':<4} {summary['n_abstained']:>4}  "
                   f"({', '.join(summary['abstained'][:8])}"
                   f"{'…' if summary['n_abstained'] > 8 else ''})")
    if summary["n_no_data"]:
        out.append(f"   {'ndat':<4} {summary['n_no_data']:>4}  "
                   f"({', '.join(summary['no_data'][:8])}"
                   f"{'…' if summary['n_no_data'] > 8 else ''})")

    # 3. Per name.
    out.append("")
    out.append("3. PER-NAME")
    out.append("")
    out.append(f"   {'ticker':<10} {'score':>7} {'computed':>9} {'unavailable checks'}")
    for ticker, r in rows:
        if r is None:
            out.append(f"   {ticker:<10} {'NO DATA':>7} {'-':>9}")
            continue
        score = "ABSTAIN" if r.score is None else f"{r.score}/9"
        missing = ", ".join(_CHECK_GLOSS[n].split(" ", 1)[0]
                            for n, o in r.checks if o is None) or "—"
        out.append(f"   {ticker:<10} {score:>7} {r.computed:>9} {missing}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _write_csv(path: str, all_rows: list[tuple[str, str, FScoreResult | None]]) -> None:
    header = (["universe", "ticker", "score", "points", "computed", "unavailable"]
              + list(_F_SCORE_CHECK_NAMES) + ["note"])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for label, ticker, r in all_rows:
            if r is None:
                w.writerow([label, ticker, "NO_DATA", "", "", ""]
                           + [""] * len(_F_SCORE_CHECK_NAMES) + ["no fundamentals"])
                continue
            cells = {n: ("P" if o is True else ("-" if o is False else "NA"))
                     for n, o in r.checks}
            w.writerow(
                [label, ticker,
                 "ABSTAIN" if r.score is None else r.score,
                 r.points, r.computed, r.unavailable]
                + [cells[n] for n in _F_SCORE_CHECK_NAMES] + [r.note])


def main() -> None:
    p = argparse.ArgumentParser(
        description="Piotroski F-Score coverage probe — no LLM, no spend.")
    p.add_argument("tickers", nargs="*", help="ad-hoc tickers (alternative to --universe)")
    p.add_argument("--universe", nargs="*", default=[],
                   help="one or more universe ids from universes/ (or universes/local/)")
    p.add_argument("--csv", help="write the per-name detail to this CSV")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--refresh", action="store_true", help="ignore cached fundamentals")
    args = p.parse_args()

    groups: list[tuple[str, list[str]]] = []
    for uid in args.universe:
        u = load_universe_by_id(uid, UNIVERSES_DIR)
        groups.append((u.id, list(u.tickers)))
    if args.tickers:
        groups.append(("ad-hoc", [normalize_ticker(t) for t in args.tickers]))
    if not groups:
        p.error("give --universe <id> and/or positional tickers")

    today = date.today()
    adapter = select_market_adapter()
    if not args.no_cache:
        adapter = CachingAdapter(adapter, cache_dir=DEFAULT_CACHE_DIR, today=today,
                                 refresh=args.refresh)
    print(f"(provider: {adapter.name}; "
          f"cache: {'off' if args.no_cache else DEFAULT_CACHE_DIR}; "
          f"min computable checks: {_F_SCORE_MIN_COMPUTABLE})")

    csv_rows: list[tuple[str, str, FScoreResult | None]] = []
    for label, tickers in groups:
        rows: list[tuple[str, FScoreResult | None]] = []
        for t in tickers:
            try:
                f = adapter.get_fundamentals(t)
            except Exception as exc:                  # absent name -> NO DATA, never a 0
                print(f"   ! {t}: fundamentals unavailable ({type(exc).__name__})")
                rows.append((t, None))
                csv_rows.append((label, t, None))
                continue
            r = piotroski_f_score(f)
            rows.append((t, r))
            csv_rows.append((label, t, r))
        print()
        print(format_report(label, rows, summarize(rows)))

    if args.csv:
        _write_csv(args.csv, csv_rows)
        print(f"\nwrote {args.csv} ({len(csv_rows)} rows)")


if __name__ == "__main__":
    main()