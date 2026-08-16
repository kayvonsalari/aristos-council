"""Scout → Aristos bridge: grade newly scouted tickers under every stock lens.

Reads the scout sheets (public CSV export) — FT and The Economist, kept as
SEPARATE, identifiable sources — extracts tickers scouted in the last
SCOUT_WINDOW_DAYS days, merges them into the standing growth_40_v1 universe
(a rank verdict is universe-relative; the standing pool keeps quintiles
meaningful), runs ONE deterministic multi-lens re-grade over the combined
cohort (FUND-RUN-1 grid — no LLM, no spend), and writes PER-SOURCE outputs:

    reports/scout/ft/<date>_verdicts.json + .md,   reports/scout/ft/latest.json
    reports/scout/economist/<date>_verdicts.json + .md,  .../latest.json
    reports/scout/latest.json          (combined index the publisher reads)

Nothing is silently dropped: rows whose ticker cannot be parsed are listed
under "skipped" (per source) with the verbatim cell text. Holdings-watch rows
(Flags starting with "HOLDING") are not analysis candidates and are ignored.

Usage (from repo root):
    python scripts/scout_verdicts.py                     # live: fetch sheets, run
    python scripts/scout_verdicts.py --dry-run           # parse + print cohort only
    python scripts/scout_verdicts.py --tickers-csv f.csv --source ft   # local test

Env:
    SCOUT_SHEET_FT           CSV export URL for the FT sheet (default built in)
    SCOUT_SHEET_ECONOMIST    CSV export URL for the Economist sheet (default: unset —
                             skipped with a warning until the sheet exists)
    SCOUT_WINDOW_DAYS        lookback window in days (default 8)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# --- sources ---------------------------------------------------------------
# Each scout source is named and produces its own output folder. To wire up the
# Economist sheet once it exists, set SCOUT_SHEET_ECONOMIST (or edit the default
# below) to its CSV export URL:
#   https://docs.google.com/spreadsheets/d/<FILE_ID>/export?format=csv&gid=0
SHEET_SOURCES: dict[str, str] = {
    "ft": os.environ.get(
        "SCOUT_SHEET_FT",
        "https://docs.google.com/spreadsheets/d/"
        "1wDYdPDI_XDBTvx_ttmFskNbVpVDPonSJy_-auPY_Hb4/export?format=csv&gid=0"),
    "economist": os.environ.get("SCOUT_SHEET_ECONOMIST", ""),
}

STOCK_LENSES = [
    "conservative_plus_v1",       # Defensive Income
    "magic_formula_momentum_v1",  # Value + Momentum
    "growth_garp_v2",             # GARP
    "magic_formula_raw_v1",       # Greenblatt RAW
    "financials_v1",              # Financials
]

BASE_UNIVERSE = "growth_40_v1"

# Venue text (as it appears in a "Ticker & listing" cell) → yfinance suffix.
VENUE_SUFFIX = {
    "NYSE": "", "NASDAQ": "", "ADR": "",
    "LSE": ".L", "TSE": ".T", "KRX": ".KS", "HKEX": ".HK",
    "XETRA": ".DE", "FRANKFURT": ".DE", "DEUTSCHE BORSE": ".DE", "DEUTSCHE BÖRSE": ".DE",
    "EURONEXT PARIS": ".PA", "PARIS": ".PA",
    "EURONEXT AMSTERDAM": ".AS", "AMSTERDAM": ".AS",
    "ASX": ".AX", "TSX": ".TO", "SIX": ".SW", "DUBLIN": ".IR",
    "MILAN": ".MI", "MADRID": ".MC", "STOCKHOLM": ".ST", "OSLO": ".OL",
    "COPENHAGEN": ".CO", "HELSINKI": ".HE",
}
US_VENUES = ("NYSE", "NASDAQ", "ADR")
# allows class/exchange suffixes: BRK.B, FRAS.L, MAERSK-B.CO, 012450
SYMBOL_RE = re.compile(r"\b([A-Z0-9]{1,7}(?:[.-][A-Z0-9]{1,2}){0,2})\b")
VENUE_WORD_RE = re.compile(r"|".join(re.escape(v) for v in
                                     sorted(VENUE_SUFFIX, key=len, reverse=True)),
                           re.IGNORECASE)


def parse_ticker(cell: str) -> str | None:
    """Best yfinance symbol from a scout-sheet 'Ticker & listing' cell, or None.

    Prefers a US listing (NYSE/Nasdaq/ADR); otherwise maps the venue to a
    yfinance suffix. Returns None when no confident (symbol, venue) pair is
    found — the caller records the verbatim cell under "skipped" so nothing
    vanishes silently.
    """
    if not cell or not cell.strip():
        return None
    candidates: list[tuple[str, str]] = []          # (symbol, venue-upper)
    parts = re.split(r"\s*/\s*", cell.strip())
    pending_symbol: str | None = None
    for part in parts:
        venue_m = VENUE_WORD_RE.search(part)
        venue = venue_m.group(0).upper() if venue_m else ""
        sym_text = part[: venue_m.start()] if venue_m else part
        # strip brackets and em/en dashes (separators) but KEEP ascii hyphens —
        # they are part of symbols like MAERSK-B.CO
        sym_text = re.sub(r"[()\[\]—–]", " ", sym_text)
        sym_m = SYMBOL_RE.search(sym_text)
        # a part like "Nasdaq" carries only a venue for the previous symbol
        if sym_m is None and venue and pending_symbol:
            candidates.append((pending_symbol, venue))
            pending_symbol = None
            continue
        if sym_m is None:
            continue
        symbol = sym_m.group(1)
        if venue:
            if pending_symbol:
                # "BRK.B / BRK.A (NYSE)" — a bare leading symbol shares its
                # sibling's venue; listed first, it stays the preferred line
                candidates.append((pending_symbol, venue))
                pending_symbol = None
            candidates.append((symbol, venue))
        else:
            pending_symbol = symbol
    if pending_symbol and not candidates:
        # bare cell with no venue text at all (newer sheet convention, e.g.
        # "FRAS.L", "MAERSK-B.CO", "GS") — already yfinance-style, trust it
        return pending_symbol
    if not candidates:
        return None
    for symbol, venue in candidates:                  # US listing wins
        if any(us in venue for us in US_VENUES):
            # yfinance writes US share classes with a dash: BRK.B -> BRK-B
            return symbol.replace(".", "-")
    symbol, venue = candidates[0]
    for key, suffix in VENUE_SUFFIX.items():
        if key in venue:
            return symbol + suffix
    return None


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "aristos-scout/1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


def read_scouted(text: str, source: str, window_days: int, today: date
                 ) -> tuple[list[dict], list[dict]]:
    """(scouted rows, skipped rows) for ONE source's CSV, filtered to the window."""
    cutoff = today - timedelta(days=window_days)
    scouted, skipped = [], []
    rows = list(csv.reader(io.StringIO(text)))
    header_i = next((i for i, r in enumerate(rows)
                     if r and r[0].strip().lower() == "date added"), None)
    if header_i is None:
        return [], [{"source": source, "cell": "(whole sheet)",
                     "reason": "no 'Date added' header found"}]
    for r in rows[header_i + 1:]:
        if not r or not r[0].strip():
            continue
        raw_date, raw_ticker = r[0].strip(), (r[1].strip() if len(r) > 1 else "")
        company = r[2].strip() if len(r) > 2 else ""
        story = r[3].strip() if len(r) > 3 else ""
        flags = r[6].strip() if len(r) > 6 else ""
        if flags.upper().startswith("HOLDING"):       # holdings watch, not a candidate
            continue
        try:
            added = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except ValueError:
            skipped.append({"source": source, "cell": " | ".join(r[:3]),
                            "reason": "unparseable date"})
            continue
        if added < cutoff:
            continue
        symbol = parse_ticker(raw_ticker)
        entry = {"source": source, "date_added": raw_date[:10],
                 "ticker_cell": raw_ticker, "company": company, "story": story}
        if symbol is None:
            skipped.append({**entry, "reason": "no parseable listed ticker"})
        else:
            scouted.append({**entry, "symbol": symbol})
    return scouted, skipped


def load_base_universe() -> list[str]:
    import yaml
    data = yaml.safe_load((ROOT / "universes" / f"{BASE_UNIVERSE}.yaml").read_text())
    return list(data["tickers"])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true",
                   help="parse sheets and print the cohort; no ranking run")
    p.add_argument("--tickers-csv", help="read a local CSV instead of fetching")
    p.add_argument("--source", default="ft",
                   help="source label for --tickers-csv (ft | economist)")
    p.add_argument("--window-days", type=int,
                   default=int(os.environ.get("SCOUT_WINDOW_DAYS", "8")))
    args = p.parse_args()
    today = date.today()

    per_source: dict[str, tuple[list[dict], list[dict]]] = {}
    if args.tickers_csv:
        text = Path(args.tickers_csv).read_text(encoding="utf-8")
        per_source[args.source] = read_scouted(text, args.source,
                                               args.window_days, today)
    else:
        fetched_any = False
        for source, url in SHEET_SOURCES.items():
            if not url:
                print(f"NOTE: no sheet configured for source '{source}' — skipped.",
                      file=sys.stderr)
                continue
            try:
                text = _fetch(url)
                fetched_any = True
            except Exception as e:                    # noqa: BLE001
                print(f"WARN: could not fetch {source} sheet: {e}", file=sys.stderr)
                per_source[source] = ([], [{"source": source, "cell": url,
                                            "reason": f"fetch failed: {e}"}])
                continue
            per_source[source] = read_scouted(text, source, args.window_days, today)
        if not fetched_any:
            sys.exit("no sheet could be fetched — is the sheet link-viewable?")

    # combined de-duped cohort (first source's metadata wins per symbol)
    seen: dict[str, dict] = {}
    for source, (scouted, _s) in per_source.items():
        for s in scouted:
            seen.setdefault(s["symbol"], s)
    scout_symbols = list(seen)
    base = load_base_universe()
    cohort = base + [s for s in scout_symbols if s not in base]

    for source, (scouted, skipped) in per_source.items():
        print(f"{source}: scouted {[s['symbol'] for s in scouted] or 'none'}, "
              f"skipped {len(skipped)}")
    print(f"cohort: {len(cohort)} names ({BASE_UNIVERSE} {len(base)} + scout "
          f"{len(cohort) - len(base)})")
    if args.dry_run:
        for source, (_sc, skipped) in per_source.items():
            for row in skipped:
                print(f"  skipped[{source}]: {row}")
        return

    result = None
    if scout_symbols:
        from aristos_council.pipeline import run_multi_strategy_pipeline
        result = run_multi_strategy_pipeline(
            cohort, STOCK_LENSES, strategies_dir=ROOT / "strategies", today=today,
            freeze_dir=ROOT / "runs", progress=lambda m: print(f"  {m}"))
    else:
        print("nothing new scouted in the window — no ranking run.")
    _write_outputs(today, per_source, result, cohort)


def _entry_for(row, meta: dict) -> dict:
    entry = {"symbol": meta["symbol"], "source": meta["source"],
             "company": meta.get("company", ""),
             "date_added": meta.get("date_added", ""),
             "story": meta.get("story", ""),
             "display": row.display if row else meta["symbol"],
             "rank_sum": row.rank_sum if row else None,
             "graded": row.graded if row else 0,
             "comparable": row.comparable if row else False,
             "lenses": {}}
    if row:
        for sid, cell in row.cells.items():
            entry["lenses"][sid] = {
                "status": cell.status, "verdict": cell.verdict,
                "position": cell.position, "cohort_size": cell.cohort_size,
                "reason": cell.reason, "rendered": cell.render()}
    return entry


def _write_outputs(today: date, per_source: dict, result, cohort: list[str]) -> None:
    stamp = today.isoformat()
    by_ticker = {row.ticker: row for row in result.rows} if result else {}
    index = {"run_date": stamp, "base_universe": BASE_UNIVERSE,
             "cohort_size": len(cohort), "lenses": STOCK_LENSES, "sources": {}}

    for source, (scouted, skipped) in per_source.items():
        out_dir = ROOT / "reports" / "scout" / source
        out_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        for meta in scouted:
            entries.append(_entry_for(by_ticker.get(meta["symbol"]), meta))
        payload = {"run_date": stamp, "source": source,
                   "base_universe": BASE_UNIVERSE, "cohort_size": len(cohort),
                   "lenses": STOCK_LENSES, "scouted": entries, "skipped": skipped}
        (out_dir / f"{stamp}_verdicts.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8")
        (out_dir / "latest.json").write_text(json.dumps(payload, indent=2),
                                             encoding="utf-8")
        lines = [f"# {source.upper()} scout verdicts — {stamp}",
                 f"Cohort: {len(cohort)} names ({BASE_UNIVERSE} + week's scout). "
                 f"Deterministic ranker only — the math judges.", ""]
        for e in entries:
            lines.append(f"## {e['display']} ({e['symbol']}) — scouted "
                         f"{e['date_added']}")
            if e.get("story"):
                lines.append(f"Story: {e['story']}")
            for sid in STOCK_LENSES:
                cell = e["lenses"].get(sid, {})
                lines.append(f"- **{sid}**: {cell.get('rendered', '—')}")
            lines.append("")
        if skipped:
            lines.append("## Skipped (review manually — nothing dropped silently)")
            for srow in skipped:
                lines.append(f"- {srow}")
        (out_dir / f"{stamp}_verdicts.md").write_text("\n".join(lines),
                                                      encoding="utf-8")
        index["sources"][source] = {
            "scouted": len(entries), "skipped": len(skipped),
            "json": f"reports/scout/{source}/{stamp}_verdicts.json",
            "md": f"reports/scout/{source}/{stamp}_verdicts.md"}
        print(f"wrote reports/scout/{source}/{stamp}_verdicts.json + .md")

    (ROOT / "reports" / "scout").mkdir(parents=True, exist_ok=True)
    (ROOT / "reports" / "scout" / "latest.json").write_text(
        json.dumps(index, indent=2), encoding="utf-8")
    print("wrote reports/scout/latest.json (combined index)")


if __name__ == "__main__":
    main()
Displaying scout_verdicts_v3_py.txt.
