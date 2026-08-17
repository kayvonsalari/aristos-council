"""Scout → Aristos bridge: grade newly scouted tickers under every stock lens.

THREE SOURCES, kept separate and identifiable end to end:
  - "ft" and "economist": news scouts, read from the scout sheets (public CSV
    export). Graded together in the NEWS cohort (growth_40_v1 + news names).
  - "growth-scanner": the weekly multi-market growth scanner's latest frozen
    CSV, read from the public Drive folder. Graded in its OWN cohort
    (growth_40_v1 + scanner names) so scanner names NEVER affect news
    verdicts and vice versa (a rank verdict is universe-relative).

Outputs (per source, never mixed):
    reports/scout/ft/<date>_verdicts.json + .md,          .../ft/latest.json
    reports/scout/economist/<date>_verdicts.json + .md,   .../economist/latest.json
    reports/scout/growth-scanner/<date>_verdicts.json + .md, .../growth-scanner/latest.json
    reports/scout/latest.json    (combined index the publisher reads)

Nothing is silently dropped: unparseable rows are listed under "skipped" per
source with the verbatim cell text. Holdings-watch rows (Flags starting with
"HOLDING") are not candidates and are ignored. The scanner itself is NEVER
modified or re-run here — this only reads its frozen output.

Usage (from repo root):
    python scripts/scout_verdicts.py                     # live: fetch all, run
    python scripts/scout_verdicts.py --dry-run           # parse + print only
    python scripts/scout_verdicts.py --tickers-csv f.csv --source ft  # local test
    python scripts/scout_verdicts.py --scanner-csv scan.csv --dry-run # local test

Env:
    SCOUT_SHEET_FT           CSV export URL for the FT sheet (default built in)
    SCOUT_SHEET_ECONOMIST    CSV export URL for the Economist sheet/tab
    SCOUT_SCANNER_FOLDER     Drive folder ID holding growth_scan_*.csv
                             (default built in; folder must be link-viewable)
    SCOUT_SCANNER_CSV        optional direct CSV URL — overrides folder lookup
    SCOUT_WINDOW_DAYS        news lookback window in days (default 8)
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

# --- news sources ----------------------------------------------------------
SHEET_SOURCES: dict[str, str] = {
    "ft": os.environ.get(
        "SCOUT_SHEET_FT",
        "https://docs.google.com/spreadsheets/d/"
        "1wDYdPDI_XDBTvx_ttmFskNbVpVDPonSJy_-auPY_Hb4/export?format=csv&gid=0"),
    "economist": os.environ.get("SCOUT_SHEET_ECONOMIST", ""),
}

# --- growth scanner source -------------------------------------------------
# Public Drive folder holding the scanner's frozen growth_scan_YYYY-MM-DD[_N].csv
# files. The folder must be shared "anyone with the link: viewer". The scanner
# is owned elsewhere; we only READ its latest freeze.
SCANNER_FOLDER_ID = os.environ.get("SCOUT_SCANNER_FOLDER",
                                   "1LH4lcUoRhMxJzMulwsBCtkYM4bFv0PzF")
SCANNER_CSV_URL = os.environ.get("SCOUT_SCANNER_CSV", "")
SCANNER_SOURCE = "growth-scanner"
SCAN_NAME_RE = re.compile(r"growth_scan_(\d{4}-\d{2}-\d{2})(?:_(\d+))?\.csv")

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
    """Best yfinance symbol from a scout-sheet 'Ticker & listing' cell, or None."""
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
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8", errors="replace")


# --- news-sheet reading ----------------------------------------------------

def read_scouted(text: str, source: str, window_days: int, today: date
                 ) -> tuple[list[dict], list[dict]]:
    """(scouted rows, skipped rows) for ONE news source's CSV, window-filtered."""
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
        if flags.upper().startswith("HOLDING"):
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


# --- growth-scanner reading ------------------------------------------------

def find_latest_scan(folder_id: str) -> tuple[str, str] | None:
    """(file_id, filename) of the newest growth_scan CSV in a public Drive
    folder, or None. Same-day re-runs (_2, _3...) sort after the base file,
    matching the scanner's append-only convention."""
    html = _fetch(f"https://drive.google.com/embeddedfolderview?id={folder_id}#list")
    best: tuple[tuple[str, int], str, str] | None = None
    # entries appear as data-id="<id>" ... with the filename nearby
    for m in re.finditer(r'data-id="([\w-]+)"(.{0,600}?)</div>', html, re.S):
        file_id, blob = m.group(1), m.group(2)
        name_m = SCAN_NAME_RE.search(blob)
        if not name_m:
            continue
        key = (name_m.group(1), int(name_m.group(2) or 0))
        if best is None or key > best[0]:
            best = (key, file_id, name_m.group(0))
    if best is None:
        # fallback: ids and names may not share one div — pair them positionally
        ids = re.findall(r'data-id="([\w-]+)"', html)
        names = SCAN_NAME_RE.findall(html)
        if ids and names and len(ids) >= len(names):
            keyed = sorted(zip(names, ids[:len(names)]),
                           key=lambda p: (p[0][0], int(p[0][1] or 0)))
            (d, n), fid = keyed[-1]
            return fid, f"growth_scan_{d}{'_' + n if n else ''}.csv"
        return None
    return best[1], best[2]


def read_scanner(text: str, scan_label: str) -> tuple[list[dict], list[dict]]:
    """(scouted rows, skipped rows) from one frozen growth-scanner CSV.

    Comment lines (rules/coverage header) are preserved into the payload's
    "scan_header" by the caller; rows keep the scanner's own notes column —
    it is load-bearing (pre-revenue names, stale fundamentals, shells)."""
    scouted, skipped = [], []
    data_lines = [ln for ln in text.splitlines()
                  if ln.strip() and not ln.lstrip().startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    if not reader.fieldnames or "ticker" not in reader.fieldnames:
        return [], [{"source": SCANNER_SOURCE, "cell": scan_label,
                     "reason": "no 'ticker' column found in scan CSV"}]
    for row in reader:
        symbol = (row.get("ticker") or "").strip()
        if not symbol:
            skipped.append({"source": SCANNER_SOURCE,
                            "cell": json.dumps(row)[:200],
                            "reason": "empty ticker"})
            continue
        story_bits = []
        if row.get("ret_6m"):
            story_bits.append(f"6m return {row['ret_6m']}")
        if row.get("market"):
            story_bits.append(f"market {row['market']}")
        if row.get("mktcap_usd_m"):
            story_bits.append(f"mktcap ${row['mktcap_usd_m']}M")
        if row.get("latest_rev_growth_yoy"):
            story_bits.append(f"rev YoY {row['latest_rev_growth_yoy']}")
        scouted.append({
            "source": SCANNER_SOURCE, "symbol": symbol,
            "date_added": scan_label,
            "ticker_cell": symbol,
            "company": (row.get("name") or "").strip(),
            "story": "; ".join(story_bits),
            "scanner_notes": (row.get("notes") or "").strip(),
            "ret_6m": row.get("ret_6m", ""),
            "market": row.get("market", ""),
        })
    return scouted, skipped


def load_base_universe() -> list[str]:
    import yaml
    data = yaml.safe_load((ROOT / "universes" / f"{BASE_UNIVERSE}.yaml").read_text())
    return list(data["tickers"])


# --- main ------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--tickers-csv", help="local news CSV instead of fetching")
    p.add_argument("--source", default="ft",
                   help="source label for --tickers-csv (ft | economist)")
    p.add_argument("--scanner-csv", help="local scanner CSV instead of Drive")
    p.add_argument("--no-scanner", action="store_true",
                   help="skip the growth-scanner source entirely")
    p.add_argument("--window-days", type=int,
                   default=int(os.environ.get("SCOUT_WINDOW_DAYS", "8")))
    args = p.parse_args()
    today = date.today()

    # ---- news sources ----
    news: dict[str, tuple[list[dict], list[dict]]] = {}
    if args.tickers_csv:
        text = Path(args.tickers_csv).read_text(encoding="utf-8")
        news[args.source] = read_scouted(text, args.source, args.window_days, today)
    else:
        for source, url in SHEET_SOURCES.items():
            if not url:
                print(f"NOTE: no sheet configured for '{source}' — skipped.",
                      file=sys.stderr)
                continue
            try:
                text = _fetch(url)
            except Exception as e:                    # noqa: BLE001
                print(f"WARN: could not fetch {source} sheet: {e}", file=sys.stderr)
                news[source] = ([], [{"source": source, "cell": url,
                                      "reason": f"fetch failed: {e}"}])
                continue
            news[source] = read_scouted(text, source, args.window_days, today)

    # ---- growth-scanner source (own cohort, never mixed with news) ----
    scanner: tuple[list[dict], list[dict]] = ([], [])
    scan_header = ""
    scan_label = ""
    if not args.no_scanner:
        try:
            if args.scanner_csv:
                text = Path(args.scanner_csv).read_text(encoding="utf-8")
                scan_label = Path(args.scanner_csv).name
            elif SCANNER_CSV_URL:
                text = _fetch(SCANNER_CSV_URL)
                scan_label = SCANNER_CSV_URL.rsplit("/", 1)[-1]
            else:
                found = find_latest_scan(SCANNER_FOLDER_ID)
                if found is None:
                    raise RuntimeError(
                        "no growth_scan_*.csv found in Drive folder — is the "
                        "folder shared 'anyone with link: viewer'?")
                file_id, scan_label = found
                text = _fetch("https://drive.google.com/uc?export=download"
                              f"&id={file_id}")
            scan_header = "\n".join(ln for ln in text.splitlines()
                                    if ln.lstrip().startswith("#"))
            scanner = read_scanner(text, scan_label)
        except Exception as e:                        # noqa: BLE001
            print(f"WARN: growth-scanner source unavailable: {e}", file=sys.stderr)
            scanner = ([], [{"source": SCANNER_SOURCE, "cell": scan_label or "-",
                             "reason": f"fetch/parse failed: {e}"}])

    # ---- cohorts (separate by design) ----
    base = load_base_universe()

    def dedup(rows: list[dict]) -> dict[str, dict]:
        seen: dict[str, dict] = {}
        for s in rows:
            seen.setdefault(s["symbol"], s)
        return seen

    news_meta = dedup([s for sc, _sk in news.values() for s in sc])
    scan_meta = dedup(scanner[0])
    news_cohort = base + [s for s in news_meta if s not in base]
    scan_cohort = base + [s for s in scan_meta if s not in base]

    for source, (sc, sk) in news.items():
        print(f"{source}: scouted {[s['symbol'] for s in sc] or 'none'}, "
              f"skipped {len(sk)}")
    print(f"{SCANNER_SOURCE}: {scan_label or 'n/a'} — "
          f"{len(scan_meta)} names, skipped {len(scanner[1])}")
    print(f"news cohort {len(news_cohort)} | scanner cohort {len(scan_cohort)} "
          f"(base {BASE_UNIVERSE} = {len(base)})")
    if args.dry_run:
        for _src, (_sc, sk) in {**news, SCANNER_SOURCE: scanner}.items():
            for row in sk:
                print(f"  skipped: {row}")
        return

    from aristos_council.pipeline import run_multi_strategy_pipeline

    def grade(cohort: list[str], have_new: bool):
        if not have_new:
            return None
        return run_multi_strategy_pipeline(
            cohort, STOCK_LENSES, strategies_dir=ROOT / "strategies",
            today=today, freeze_dir=ROOT / "runs",
            progress=lambda m: print(f"  {m}"))

    news_result = grade(news_cohort, bool(news_meta))
    scan_result = grade(scan_cohort, bool(scan_meta))

    per_source = {src: {"scouted": sc, "skipped": sk, "result": news_result,
                        "cohort_size": len(news_cohort), "cohort": "news"}
                  for src, (sc, sk) in news.items()}
    per_source[SCANNER_SOURCE] = {
        "scouted": scanner[0], "skipped": scanner[1], "result": scan_result,
        "cohort_size": len(scan_cohort), "cohort": "scanner",
        "scan_file": scan_label, "scan_header": scan_header}
    _write_outputs(today, per_source)


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
    for extra in ("scanner_notes", "ret_6m", "market"):
        if meta.get(extra):
            entry[extra] = meta[extra]
    if row:
        for sid, cell in row.cells.items():
            entry["lenses"][sid] = {
                "status": cell.status, "verdict": cell.verdict,
                "position": cell.position, "cohort_size": cell.cohort_size,
                "reason": cell.reason, "rendered": cell.render()}
    return entry


def _write_outputs(today: date, per_source: dict) -> None:
    stamp = today.isoformat()
    index = {"run_date": stamp, "base_universe": BASE_UNIVERSE,
             "lenses": STOCK_LENSES, "sources": {}}

    for source, blob in per_source.items():
        result = blob["result"]
        by_ticker = {r.ticker: r for r in result.rows} if result else {}
        out_dir = ROOT / "reports" / "scout" / source
        out_dir.mkdir(parents=True, exist_ok=True)
        entries = [_entry_for(by_ticker.get(m["symbol"]), m)
                   for m in blob["scouted"]]
        payload = {"run_date": stamp, "source": source,
                   "cohort": blob["cohort"], "cohort_size": blob["cohort_size"],
                   "base_universe": BASE_UNIVERSE, "lenses": STOCK_LENSES,
                   "scouted": entries, "skipped": blob["skipped"]}
        if source == SCANNER_SOURCE:
            payload["scan_file"] = blob.get("scan_file", "")
            payload["scan_header"] = blob.get("scan_header", "")
        (out_dir / f"{stamp}_verdicts.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8")
        (out_dir / "latest.json").write_text(json.dumps(payload, indent=2),
                                             encoding="utf-8")
        lines = [f"# {source.upper()} scout verdicts — {stamp}",
                 f"Cohort: {blob['cohort_size']} names ({BASE_UNIVERSE} + "
                 f"{blob['cohort']} names, graded separately). Deterministic "
                 f"ranker only — the math judges.", ""]
        if source == SCANNER_SOURCE and blob.get("scan_file"):
            lines.append(f"Scan file: {blob['scan_file']}")
            lines.append("")
        for e in entries:
            lines.append(f"## {e['display']} ({e['symbol']}) — {e['date_added']}")
            if e.get("story"):
                lines.append(f"Context: {e['story']}")
            if e.get("scanner_notes"):
                lines.append(f"Scanner notes: {e['scanner_notes']}")
            for sid in STOCK_LENSES:
                cell = e["lenses"].get(sid, {})
                lines.append(f"- **{sid}**: {cell.get('rendered', '—')}")
            lines.append("")
        if blob["skipped"]:
            lines.append("## Skipped (nothing dropped silently)")
            for srow in blob["skipped"]:
                lines.append(f"- {srow}")
        (out_dir / f"{stamp}_verdicts.md").write_text("\n".join(lines),
                                                      encoding="utf-8")
        index["sources"][source] = {
            "scouted": len(entries), "skipped": len(blob["skipped"]),
            "cohort": blob["cohort"], "cohort_size": blob["cohort_size"],
            "json": f"reports/scout/{source}/{stamp}_verdicts.json",
            "md": f"reports/scout/{source}/{stamp}_verdicts.md"}
        print(f"wrote reports/scout/{source}/{stamp}_verdicts.json + .md")

    (ROOT / "reports" / "scout").mkdir(parents=True, exist_ok=True)
    (ROOT / "reports" / "scout" / "latest.json").write_text(
        json.dumps(index, indent=2), encoding="utf-8")
    print("wrote reports/scout/latest.json (combined index)")


if __name__ == "__main__":
    main()
