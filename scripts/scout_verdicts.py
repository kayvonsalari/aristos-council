"""Scout → Aristos bridge: grade newly scouted tickers under every stock lens.

FOUR DISCOVERY SOURCES, ONE COHORT (decided 2026-08-17, extended 2026-08-18):
  - "ft" and "economist": news scouts, read from the scout sheets (public CSV
    export).
  - "growth-scanner": the weekly multi-market growth scanner's latest frozen
    CSV, read from the public Drive folder (never re-run here — read only).
  - "holdings": the owner's watch/holdings names. TWO inputs, one source:
    PRIMARY the dedicated "Holdings" TAB of the scout spreadsheet
    (Name | Ticker | Type — SCOUT-3), SUPPLEMENT any row still flagged HOLDING
    in the Flags column of a news tab (SCOUT-2, unchanged). SCOUT-2 shipped only
    the flag path and harvested ZERO rows on its first live run: the owner's list
    was never on the news tabs, it lives on its own tab. Ticker NAMES only — no
    amounts, no cost basis (that is PORTFOLIO-AWARE-1, still parked) — so they
    are not sensitive and are graded like any other candidate. The news lookback
    window does NOT apply to either input: a holding is watched CONTINUOUSLY, not
    scouted this week, so EVERY holdings row joins the cohort on every run.

All four are graded in a SINGLE combined cohort:
    growth_40_v1 + FT names + Economist names + scanner names + holdings
A rank verdict is universe-relative, so one big pool makes the quintile cuts
more meaningful AND lets a name's verdict move as new comparables arrive —
which is the point of running four discovery channels into one ranker. The
sources stay separately IDENTIFIABLE (own output folders, own spreadsheet
tabs, a "source" field and an "also_found_by" field per entry); they are NOT
separately ranked.

Every graded entry also carries the Piotroski F-Score as EVIDENCE, not
judgment (SCOUT-2 part B): ``f_score`` on the entry, an "F-Score" column in the
markdown. It is computed from the SAME adapter-fetched Fundamentals the ranker
read (one adapter, built here and threaded through — never a second fetch), and
it is STRICTLY DISPLAY-ONLY: ``min_f_score`` is adopted by no strategy, so the
number moves no verdict, rank or exclusion until a lens adopts it on evidence.

Outputs (per source, presentation kept apart):
    reports/scout/ft/<date>_verdicts.json + .md,          .../ft/latest.json
    reports/scout/economist/<date>_verdicts.json + .md,   .../economist/latest.json
    reports/scout/growth-scanner/<date>_verdicts.json + .md, .../growth-scanner/latest.json
    reports/scout/holdings/<date>_verdicts.json + .md,    .../holdings/latest.json
    reports/scout/latest.json    (combined index the publisher reads)

Nothing is silently dropped: unparseable rows are listed under "skipped" per
source with the verbatim cell text — a holdings row whose ticker cell is blank
lands in the HOLDINGS source's "skipped", never in a news source's, and a
holdings Type the stock lenses cannot grade ("Trust", "Cash", …) is skipped with
that verbatim type rather than guessed at. A holdings row that is not an equity
(the watch table includes ETFs) is graded and EXCLUDED by the stock lenses'
asset-kind gate with a named reason — correct behavior, not a failure: the ETF
lenses are deliberately NOT wired into this job (examples/grade_holdings.py
covers ETFs on demand). The scanner itself is NEVER modified or re-run here —
this only reads its frozen output.

Usage (from repo root):
    python scripts/scout_verdicts.py                     # live: fetch all, run
    python scripts/scout_verdicts.py --dry-run           # parse + print only
    python scripts/scout_verdicts.py --tickers-csv f.csv --source ft  # local test
    python scripts/scout_verdicts.py --scanner-csv scan.csv --dry-run # local test
    python scripts/scout_verdicts.py --holdings-csv h.csv --dry-run   # local test

Env:
    SCOUT_SHEET_FT           CSV export URL for the FT sheet (default built in)
    SCOUT_SHEET_ECONOMIST    CSV export URL for the Economist sheet/tab
    SCOUT_SHEET_HOLDINGS     CSV export URL for the Holdings TAB (default built
                             in; empty = no tab, the flag path still runs)
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
from typing import NamedTuple

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

# --- holdings-watch source (SCOUT-2 flags + SCOUT-3 tab) -------------------
# TWO inputs, ONE source. PRIMARY: the spreadsheet's dedicated "Holdings" tab,
# where the owner's actual list lives (Name | Ticker | Type). SUPPLEMENT: rows on
# the NEWS sheets whose Flags cell starts with HOLDING (SCOUT-2) — kept because a
# name can be flagged on a news tab before it reaches the Holdings tab. Both are
# watched CONTINUOUSLY, so the news lookback window applies to neither.
HOLDINGS_SOURCE = "holdings"
HOLDING_FLAG = "HOLDING"
SHEET_HOLDINGS_URL = os.environ.get(
    "SCOUT_SHEET_HOLDINGS",
    "https://docs.google.com/spreadsheets/d/"
    "1wDYdPDI_XDBTvx_ttmFskNbVpVDPonSJy_-auPY_Hb4/export?format=csv&gid=7542599")
HOLDINGS_TAB_SHEET = "holdings-tab"      # the "sheet" label on a tab-sourced row
HOLDINGS_TAB_COLUMNS = ("name", "ticker", "type")
# Types the STOCK lenses can be pointed at. "ETF" is included DELIBERATELY: the
# lenses then exclude it by their asset-kind gate with a named reason (SCOUT-2),
# which is the honest answer rather than a silent drop. Any other Type ("Trust",
# "Cash", a blank) is skipped naming the verbatim type — never guessed.
GRADEABLE_HOLDING_TYPES = ("STOCK", "ETF")
# A bare Asian listing code (1211, 0700) is a NUMBER to Sheets, so its CSV export
# can arrive as "1211.0" or "1,211" — read the digits back as text, nothing else.
_NUMERIC_CELL_RE = re.compile(r"\d+(?:,\d{3})*(?:\.0+)?")

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

class SheetRead(NamedTuple):
    """One sheet's rows split by SOURCE, not by sheet: the news candidates (window
    filtered) and the HOLDING-flagged watch rows (window ignored), each with its
    own skipped list so a bad holdings cell is never reported as a news skip."""

    scouted: list[dict]
    skipped: list[dict]
    holdings: list[dict]
    holdings_skipped: list[dict]


def read_scouted(text: str, source: str, window_days: int, today: date) -> SheetRead:
    """Split ONE news sheet's CSV into its news candidates and its holdings rows.

    News rows are window-filtered exactly as before (behavior byte-unchanged).
    HOLDING-flagged rows are NOT candidates of this news source: they are routed
    to the ``holdings`` source with the window deliberately NOT applied — a
    holding is watched continuously rather than scouted this week — and an
    unparseable one lands in ``holdings_skipped`` with the verbatim cell text.
    """
    cutoff = today - timedelta(days=window_days)
    scouted, skipped = [], []
    holdings, holdings_skipped = [], []
    rows = list(csv.reader(io.StringIO(text)))
    header_i = next((i for i, r in enumerate(rows)
                     if r and r[0].strip().lower() == "date added"), None)
    if header_i is None:
        return SheetRead([], [{"source": source, "cell": "(whole sheet)",
                               "reason": "no 'Date added' header found"}], [], [])
    for r in rows[header_i + 1:]:
        if not r or not r[0].strip():
            continue
        raw_date, raw_ticker = r[0].strip(), (r[1].strip() if len(r) > 1 else "")
        company = r[2].strip() if len(r) > 2 else ""
        story = r[3].strip() if len(r) > 3 else ""
        flags = r[6].strip() if len(r) > 6 else ""
        if flags.upper().startswith(HOLDING_FLAG):
            entry = {"source": HOLDINGS_SOURCE, "date_added": raw_date[:10],
                     "ticker_cell": raw_ticker, "company": company, "story": story,
                     "flags": flags, "sheet": source}
            symbol = parse_ticker(raw_ticker)
            if symbol is None:
                holdings_skipped.append({**entry, "cell": " | ".join(r[:3]),
                                         "reason": "no parseable listed ticker"})
            else:
                holdings.append({**entry, "symbol": symbol})
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
    return SheetRead(scouted, skipped, holdings, holdings_skipped)


# --- holdings-tab reading (SCOUT-3) ----------------------------------------

class HoldingsTabRead(NamedTuple):
    """The dedicated Holdings tab's rows, and what it refused to guess at."""

    holdings: list[dict]
    skipped: list[dict]


def holdings_ticker(cell: str) -> str | None:
    """The yfinance symbol from a Holdings-tab Ticker cell: VERBATIM after trim.

    No name→ticker mapping, no venue inference, no normalization — the cell IS the
    contract ("a downstream automated pipeline parses this cell"), so data quality
    lives in the sheet and a wrong symbol is fixed THERE, never guessed here. The
    one transformation is de-numbering: Sheets stores a bare listing code like 1211
    as a NUMBER and can export it as "1211.0" or "1,211", which is not a symbol —
    those digits are read back as plain text. A blank cell returns None.
    """
    text = (cell or "").strip()
    if not text:
        return None
    if _NUMERIC_CELL_RE.fullmatch(text):
        return text.replace(",", "").split(".")[0]
    return text


def _tab_cell(row: list[str], cols: dict[str, int], key: str) -> str:
    i = cols.get(key)
    return row[i].strip() if i is not None and len(row) > i else ""


def read_holdings_tab(text: str, *, label: str = "Holdings tab") -> HoldingsTabRead:
    """Parse the Holdings tab (Name | Ticker | Type) into holdings rows.

    EVERY row joins: the tab has no date column and no news requirement, because a
    holding is watched CONTINUOUSLY — the same rationale that made the flag path
    window-free in SCOUT-2. Even a date column, if one is ever added, is ignored
    rather than filtered on.

    Two honest refusals, both recorded in ``skipped`` with the verbatim cell:
      - a Type the stock lenses cannot grade ("Trust", "Cash", a blank) — checked
        FIRST, since it settles the row whatever the ticker says;
      - a blank/unparseable Ticker cell.
    """
    rows = list(csv.reader(io.StringIO(text)))
    header_i: int | None = None
    cols: dict[str, int] = {}
    for i, row in enumerate(rows):
        lowered = [c.strip().lower() for c in row]
        if "ticker" in lowered:
            header_i = i
            cols = {name: lowered.index(name) for name in HOLDINGS_TAB_COLUMNS
                    if name in lowered}
            break
    if header_i is None:
        return HoldingsTabRead([], [{"source": HOLDINGS_SOURCE, "cell": label,
                                     "reason": "no 'Ticker' header found"}])
    holdings, skipped = [], []
    for row in rows[header_i + 1:]:
        name = _tab_cell(row, cols, "name")
        raw_ticker = _tab_cell(row, cols, "ticker")
        raw_type = _tab_cell(row, cols, "type")
        if not (name or raw_ticker or raw_type):
            continue                                  # blank spacer row
        entry = {"source": HOLDINGS_SOURCE, "date_added": "",
                 "ticker_cell": raw_ticker, "company": name, "story": "",
                 "holding_type": raw_type, "sheet": HOLDINGS_TAB_SHEET}
        cell = " | ".join([name, raw_ticker, raw_type])
        if raw_type.upper() not in GRADEABLE_HOLDING_TYPES:
            skipped.append({**entry, "cell": cell,
                            "reason": f"type '{raw_type}' not gradeable by the "
                                      "stock lenses"})
            continue
        symbol = holdings_ticker(raw_ticker)
        if symbol is None:
            skipped.append({**entry, "cell": cell,
                            "reason": "blank or unparseable ticker cell"})
            continue
        holdings.append({**entry, "symbol": symbol})
    return HoldingsTabRead(holdings, skipped)


def load_holdings_tab(url: str, *, fetch=None) -> HoldingsTabRead:
    """Fetch + parse the Holdings tab, degrading EXACTLY like a news sheet: a fetch
    failure becomes ONE ``skipped`` entry naming the reason and the run continues on
    the other sources — an unreachable tab must never take the whole scout down."""
    if not url:
        return HoldingsTabRead([], [{"source": HOLDINGS_SOURCE, "cell": "-",
                                     "reason": "no Holdings tab configured "
                                               "(SCOUT_SHEET_HOLDINGS empty)"}])
    try:
        return read_holdings_tab((fetch or _fetch)(url), label=url)
    except Exception as e:                        # noqa: BLE001 — never fatal
        return HoldingsTabRead([], [{"source": HOLDINGS_SOURCE, "cell": url,
                                     "reason": f"fetch failed: {e}"}])


# --- growth-scanner reading ------------------------------------------------

def _scan_key(name: str) -> tuple[str, int] | None:
    """Sort key for a scan filename: (date, rerun-number). Same-day re-runs
    (_2, _3...) sort after the base file, matching the append-only convention."""
    m = SCAN_NAME_RE.fullmatch(name.strip())
    return (m.group(1), int(m.group(2) or 0)) if m else None


def find_repo_scan() -> tuple[Path, str] | None:
    """(path, filename) of the newest growth_scan CSV committed under
    data/growth_scans/ in this repo, or None. Preferred over Drive when
    present: no network, no sharing, and the freeze lives in git history."""
    d = ROOT / "data" / "growth_scans"
    if not d.is_dir():
        return None
    best: tuple[tuple[str, int], Path] | None = None
    for p in sorted(d.glob("growth_scan_*.csv")):
        key = _scan_key(p.name)
        if key and (best is None or key > best[0]):
            best = (key, p)
    return (best[1], best[1].name) if best else None


# an id token, then (later in the document) the filename it belongs to
_DRIVE_TOKEN_RE = re.compile(
    r'id="entry-([\w-]{20,})"'          # embeddedfolderview markup
    r'|data-id="([\w-]{20,})"'          # alternative/older markup
    r'|(growth_scan_\d{4}-\d{2}-\d{2}(?:_\d+)?\.csv)')


def find_latest_scan(folder_id: str) -> tuple[str, str] | None:
    """(file_id, filename) of the newest growth_scan CSV in a PUBLIC Drive
    folder, or None.

    Walks the folder-view HTML in document order pairing each filename with the
    most recent preceding file-id token, so it survives markup changes as long
    as ids still precede their titles."""
    html = _fetch(f"https://drive.google.com/embeddedfolderview?id={folder_id}#list")
    found: list[tuple[tuple[str, int], str, str]] = []
    current_id: str | None = None
    for m in _DRIVE_TOKEN_RE.finditer(html):
        file_id = m.group(1) or m.group(2)
        if file_id:
            current_id = file_id
            continue
        name = m.group(3)
        key = _scan_key(name)
        if key and current_id:
            found.append((key, current_id, name))
    if not found:
        return None
    found.sort(key=lambda t: t[0])
    return found[-1][1], found[-1][2]


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
    # growth_40_v1 is no longer shipped product data (FUND-UI-2 removed the demo cohorts
    # from universes/), but the scout's base cohort must stay the SAME 40 names for its
    # dated verdict files to be comparable — so it reads the kept fixture copy.
    import yaml
    base = ROOT / "tests" / "fixtures" / "universes" / f"{BASE_UNIVERSE}.yaml"
    data = yaml.safe_load(base.read_text())
    return list(data["tickers"])


# --- cohort composition ----------------------------------------------------

def dedup(rows: list[dict]) -> dict[str, dict]:
    """symbol -> its FIRST row. Order of the sources decides who wins a collision;
    the loser is never dropped, it is recorded in ``also_found_by``."""
    seen: dict[str, dict] = {}
    for s in rows:
        seen.setdefault(s["symbol"], s)
    return seen


def also_found_by(by_source: dict[str, dict[str, dict]]) -> dict[str, dict[str, list[str]]]:
    """Per source: {symbol: [the OTHER sources that also found it]}.

    One derivation for every source (news, scanner, holdings), so a name found by
    two channels shows both wherever it is presented — e.g. a holding that FT also
    scouted appears on the FT tab with also_found_by ["holdings"] and on the
    holdings tab with ["ft"]."""
    out: dict[str, dict[str, list[str]]] = {}
    for source, meta in by_source.items():
        others: dict[str, list[str]] = {}
        for sym in meta:
            found = sorted(o for o, m in by_source.items()
                           if o != source and sym in m)
            if found:
                others[sym] = found
        out[source] = others
    return out


# --- grading ---------------------------------------------------------------

def build_adapter(today: date):
    """The ONE market adapter for a scout run.

    Built here (rather than left to the pipeline) so the F-Score can read the SAME
    fetched ``Fundamentals`` the ranker already used — the cache is consulted, so
    no name is fetched twice. It is the pipeline's own builder, so the adapter
    stack (retry + cache) is byte-identical to what the pipeline would have made."""
    from aristos_council.pipeline import _build_adapter
    return _build_adapter(today=today, use_cache=True)


def grade_cohort(cohort: list[str], *, today: date, adapter=None,
                 freeze_dir: Path | None = None, progress=None):
    """ONE grading pass over the combined cohort under every stock lens — every
    source reads its rows out of the same grid, so ranks ARE comparable across
    tabs. Deterministic ranker only (no LLM, nothing spent)."""
    from aristos_council.pipeline import run_multi_strategy_pipeline
    return run_multi_strategy_pipeline(
        cohort, STOCK_LENSES, strategies_dir=ROOT / "strategies", adapter=adapter,
        today=today, freeze_dir=freeze_dir, progress=progress)


# --- F-Score (SCOUT-2 part B: evidence, never judgment) ---------------------

def f_scores_for(symbols: list[str], adapter, progress=None) -> dict[str, dict]:
    """The ``f_score`` block per symbol, from the adapter the ranker already used.

    A fetch failure is an ABSTENTION too, never a zero: score null, display
    ABSTAIN, with the failure named in the note. DISPLAY ONLY — no caller of this
    is allowed to gate on it (``min_f_score`` is adopted by no strategy)."""
    from aristos_council.tools.screening import piotroski_f_score

    out: dict[str, dict] = {}
    for i, symbol in enumerate(symbols, 1):
        if progress is not None and i % 25 == 0:
            progress(f"F-Score: {i} of {len(symbols)}…")
        try:
            out[symbol] = f_score_block(piotroski_f_score(adapter.get_fundamentals(symbol)))
        except Exception as e:                     # noqa: BLE001 — absent data, never fatal
            out[symbol] = f_score_block(None, note=f"fundamentals unavailable: {e}")
    return out


def f_score_block(result, *, note: str = "") -> dict:
    """One entry's F-Score evidence. ``score`` is None for the under-5-computable
    abstention AND for a missing fetch; ``display`` renders "ABSTAIN" in both cases
    — a 0/9 is a REAL score (nine checks computed, none earned) and must never
    stand in for a gap (project rule 3, the null≠false discipline)."""
    from aristos_council.tools.screening import _F_SCORE_CHECKS

    if result is None:
        return {"score": None, "computed": 0, "unavailable": _F_SCORE_CHECKS,
                "display": "ABSTAIN",
                "note": note or "F-Score not computed: no fundamentals"}
    return {"score": result.score, "computed": result.computed,
            "unavailable": result.unavailable,
            "display": ("ABSTAIN" if result.score is None
                        else f"{result.score}/{_F_SCORE_CHECKS}"),
            "note": result.note}


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
    p.add_argument("--holdings-csv",
                   help="local Holdings-tab CSV instead of fetching the tab")
    p.add_argument("--no-holdings-tab", action="store_true",
                   help="skip the Holdings tab (the HOLDING-flag rows still run)")
    p.add_argument("--window-days", type=int,
                   default=int(os.environ.get("SCOUT_WINDOW_DAYS", "8")))
    args = p.parse_args()
    today = date.today()

    # ---- news sources (+ the HOLDING rows riding on the same sheets) ----
    news: dict[str, SheetRead] = {}
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
                news[source] = SheetRead([], [{"source": source, "cell": url,
                                               "reason": f"fetch failed: {e}"}], [], [])
                continue
            news[source] = read_scouted(text, source, args.window_days, today)

    # ---- holdings: the dedicated Holdings TAB (primary) + the HOLDING flags ----
    if args.holdings_csv:
        tab = read_holdings_tab(Path(args.holdings_csv).read_text(encoding="utf-8"),
                                label=Path(args.holdings_csv).name)
    elif args.no_holdings_tab:
        print("NOTE: Holdings tab skipped (--no-holdings-tab).", file=sys.stderr)
        tab = HoldingsTabRead([], [])
    else:
        tab = load_holdings_tab(SHEET_HOLDINGS_URL)
        for row in tab.skipped:                   # surface only the tab-level ones
            if row["reason"].startswith(("fetch failed", "no Holdings tab",
                                         "no 'Ticker' header")):
                print(f"WARN: Holdings tab: {row['reason']}", file=sys.stderr)

    # ONE holdings source out of both inputs, assembled ACROSS the tab and every
    # news sheet (any date — the news window does not apply to a continuously
    # watched name), de-duped by symbol: a name on the tab AND flagged on a sheet
    # is ONE holding. The TAB row is listed first, so it WINS the metadata
    # collision — it is the owner's authoritative list.
    flagged = [h for read in news.values() for h in read.holdings]
    holdings_rows = list(dedup(tab.holdings + flagged).values())
    holdings_skipped = tab.skipped + [h for read in news.values()
                                      for h in read.holdings_skipped]

    # ---- growth-scanner source (own cohort, never mixed with news) ----
    scanner: tuple[list[dict], list[dict]] = ([], [])
    scan_header = ""
    scan_label = ""
    if not args.no_scanner:
        try:
            in_repo = find_repo_scan()
            if args.scanner_csv:
                text = Path(args.scanner_csv).read_text(encoding="utf-8")
                scan_label = Path(args.scanner_csv).name
            elif in_repo is not None:
                # committed scans win: no network, no sharing, in git history
                path, scan_label = in_repo
                text = path.read_text(encoding="utf-8", errors="replace")
                print(f"  scanner source: repo data/growth_scans/{scan_label}")
            elif SCANNER_CSV_URL:
                text = _fetch(SCANNER_CSV_URL)
                scan_label = SCANNER_CSV_URL.rsplit("/", 1)[-1]
            else:
                found = find_latest_scan(SCANNER_FOLDER_ID)
                if found is None:
                    raise RuntimeError(
                        "no growth_scan_*.csv found via Drive folder listing "
                        f"(id {SCANNER_FOLDER_ID}). Either commit the scan to "
                        "data/growth_scans/ in this repo, or set the repo "
                        "variable SCOUT_SCANNER_CSV to a direct file URL.")
                file_id, scan_label = found
                text = _fetch("https://drive.google.com/uc?export=download"
                              f"&id={file_id}")
                print(f"  scanner source: Drive {scan_label}")
            scan_header = "\n".join(ln for ln in text.splitlines()
                                    if ln.lstrip().startswith("#"))
            scanner = read_scanner(text, scan_label)
        except Exception as e:                        # noqa: BLE001
            print(f"WARN: growth-scanner source unavailable: {e}", file=sys.stderr)
            scanner = ([], [{"source": SCANNER_SOURCE, "cell": scan_label or "-",
                             "reason": f"fetch/parse failed: {e}"}])

    # ---- cohort ----
    base = load_base_universe()

    # ONE COMBINED COHORT (decided 2026-08-17; holdings joined it 2026-08-18).
    # Every source's names are graded in the SAME universe: base + FT +
    # Economist + growth-scanner + holdings. A rank verdict is universe-relative,
    # so a bigger pool makes the quintile cuts more meaningful and lets a name's
    # verdict move when new comparables arrive — which is the point of feeding
    # four discovery channels into one ranker. Sources stay separately
    # IDENTIFIABLE (own folders/tabs/columns); they are not separately RANKED.
    per_source_meta = {src: dedup(read.scouted) for src, read in news.items()}
    per_source_meta[SCANNER_SOURCE] = dedup(scanner[0])
    per_source_meta[HOLDINGS_SOURCE] = dedup(holdings_rows)
    news_meta = dedup([s for read in news.values() for s in read.scouted])
    scan_meta = per_source_meta[SCANNER_SOURCE]
    hold_meta = per_source_meta[HOLDINGS_SOURCE]
    all_meta = {**news_meta}
    for extra in (scan_meta, hold_meta):      # news metadata wins on collision
        for sym, m in extra.items():
            all_meta.setdefault(sym, m)
    cohort = base + [s for s in all_meta if s not in base]

    for source, read in news.items():
        print(f"{source}: scouted {[s['symbol'] for s in read.scouted] or 'none'}, "
              f"skipped {len(read.skipped)}")
    print(f"{SCANNER_SOURCE}: {scan_label or 'n/a'} — "
          f"{len(scan_meta)} names, skipped {len(scanner[1])}")
    print(f"{HOLDINGS_SOURCE}: {sorted(hold_meta) or 'none'} "
          f"({len(tab.holdings)} from the Holdings tab, {len(flagged)} "
          f"HOLDING-flagged on the news tabs; watched continuously — no date "
          f"window), skipped {len(holdings_skipped)}")
    overlap = sorted(set(news_meta) & set(scan_meta))
    new_news = [s for s in news_meta if s not in base]
    new_scan = [s for s in scan_meta if s not in base and s not in news_meta]
    new_hold = [s for s in hold_meta
                if s not in base and s not in news_meta and s not in scan_meta]
    print(f"combined cohort {len(cohort)} = {BASE_UNIVERSE} {len(base)} + "
          f"news {len(new_news)} + scanner {len(new_scan)} + "
          f"holdings {len(new_hold)}"
          + (f" (found by both news and scanner: {', '.join(overlap)})"
             if overlap else ""))
    if args.dry_run:
        for read in news.values():
            for row in read.skipped:
                print(f"  skipped: {row}")
        for row in list(scanner[1]) + holdings_skipped:
            print(f"  skipped: {row}")
        return

    # The adapter is built ONCE here and threaded through BOTH the ranker and the
    # F-Score, so the F-Score reads the fundamentals the ranker already fetched.
    adapter = build_adapter(today) if all_meta else None
    result = grade_cohort(cohort, today=today, adapter=adapter,
                          freeze_dir=ROOT / "runs",
                          progress=lambda m: print(f"  {m}")) if all_meta else None

    # F-Score for every symbol we EMIT (display only — it judges nothing).
    f_scores = (f_scores_for(sorted(all_meta), adapter,
                             progress=lambda m: print(f"  {m}"))
                if result is not None else {})

    also = also_found_by(per_source_meta)
    per_source = {src: {"scouted": read.scouted, "skipped": read.skipped,
                        "result": result, "cohort_size": len(cohort),
                        "cohort": "combined", "also_found_by": also.get(src, {})}
                  for src, read in news.items()}
    per_source[SCANNER_SOURCE] = {
        "scouted": scanner[0], "skipped": scanner[1], "result": result,
        "cohort_size": len(cohort), "cohort": "combined",
        "also_found_by": also.get(SCANNER_SOURCE, {}),
        "scan_file": scan_label, "scan_header": scan_header}
    per_source[HOLDINGS_SOURCE] = {
        "scouted": holdings_rows, "skipped": holdings_skipped, "result": result,
        "cohort_size": len(cohort), "cohort": "combined",
        "also_found_by": also.get(HOLDINGS_SOURCE, {})}
    _write_outputs(today, per_source, f_scores)


def _entry_for(row, meta: dict, also: dict | None = None,
               f_scores: dict | None = None) -> dict:
    entry = {"symbol": meta["symbol"], "source": meta["source"],
             "also_found_by": (also or {}).get(meta["symbol"], []),
             "company": meta.get("company", ""),
             "date_added": meta.get("date_added", ""),
             "story": meta.get("story", ""),
             "display": row.display if row else meta["symbol"],
             "rank_sum": row.rank_sum if row else None,
             "graded": row.graded if row else 0,
             "comparable": row.comparable if row else False,
             # EVIDENCE, not judgment: no threshold reads this (SCOUT-2 part B).
             "f_score": (f_scores or {}).get(meta["symbol"]) or f_score_block(None),
             "lenses": {}}
    for extra in ("scanner_notes", "ret_6m", "market", "flags", "sheet",
                  "holding_type"):
        if meta.get(extra):
            entry[extra] = meta[extra]
    if row:
        for sid, cell in row.cells.items():
            entry["lenses"][sid] = {
                "status": cell.status, "verdict": cell.verdict,
                "position": cell.position, "cohort_size": cell.cohort_size,
                "reason": cell.reason, "rendered": cell.render()}
    return entry


def _write_outputs(today: date, per_source: dict, f_scores: dict | None = None,
                   root: Path | None = None) -> None:
    root = root or ROOT
    stamp = today.isoformat()
    index = {"run_date": stamp, "base_universe": BASE_UNIVERSE,
             "lenses": STOCK_LENSES, "sources": {}}

    for source, blob in per_source.items():
        result = blob["result"]
        by_ticker = {r.ticker: r for r in result.rows} if result else {}
        out_dir = root / "reports" / "scout" / source
        out_dir.mkdir(parents=True, exist_ok=True)
        entries = [_entry_for(by_ticker.get(m["symbol"]), m,
                               blob.get("also_found_by"), f_scores)
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
                 f"ranker only — the math judges.",
                 "F-Score: Piotroski 0-9, EVIDENCE ONLY — no lens consumes it, so "
                 "it moves no verdict, rank or exclusion. ABSTAIN = fewer than 5 of "
                 "the 9 checks computable (never a 0).", ""]
        if source == SCANNER_SOURCE and blob.get("scan_file"):
            lines.append(f"Scan file: {blob['scan_file']}")
            lines.append("")
        if source == HOLDINGS_SOURCE:
            lines.append("Holdings rows — the owner's Holdings tab "
                         "(Name | Ticker | Type) plus any row still flagged on a "
                         "news tab (Flags: HOLDING) — watched continuously, so the "
                         "news date window does not apply. Ticker names only; no "
                         "amounts or cost basis. The Ticker cell is used verbatim "
                         "as the symbol: data quality lives in the sheet. A "
                         "non-equity row (e.g. an ETF) is EXCLUDED by the stock "
                         "lenses' asset-kind gate with a named reason — correct, "
                         "not a failure: the ETF lenses are not wired into this "
                         "job. A Type the stock lenses cannot grade (Trust, Cash, "
                         "…) is listed under Skipped, never guessed.")
            lines.append("")
        for e in entries:
            head = f"## {e['display']} ({e['symbol']})"
            # a Holdings-tab row carries no date at all — no dangling dash for it
            lines.append(f"{head} — {e['date_added']}" if e.get("date_added")
                         else head)
            if e.get("holding_type"):
                lines.append(f"Type: {e['holding_type']}")
            if e.get("story"):
                lines.append(f"Context: {e['story']}")
            if e.get("scanner_notes"):
                lines.append(f"Scanner notes: {e['scanner_notes']}")
            for sid in STOCK_LENSES:
                cell = e["lenses"].get(sid, {})
                lines.append(f"- **{sid}**: {cell.get('rendered', '—')}")
            fs = e.get("f_score") or {}
            lines.append(f"- **F-Score**: {fs.get('display', 'ABSTAIN')} "
                         f"({fs.get('note', '')})")
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

    (root / "reports" / "scout").mkdir(parents=True, exist_ok=True)
    (root / "reports" / "scout" / "latest.json").write_text(
        json.dumps(index, indent=2), encoding="utf-8")
    print("wrote reports/scout/latest.json (combined index)")


if __name__ == "__main__":
    main()
