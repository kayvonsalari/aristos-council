#!/usr/bin/env python3
"""
Scout staging sweeper — merges stranded staging sheets into the master tracker.

The FT/Economist scout tasks run as scheduled CLOUD sessions, which never have
access to Kayvon's machine — so the browser path in those prompts can never fire
and every run falls back to writing a "… Staging <date>" sheet. Those sheets then
accumulate un-pasted (five of them by 2026-08-20, master last updated 08-13).

This script is the deterministic half of the fix: the LLM keeps doing the research
and writing staging sheets; this appends their rows into the right master tabs and
retires the staged file (trash it if we own it, else rename it "SWEPT …" since a
writer-only service account cannot trash another user's file). No model, no browser,
no manual paste.

Staging layout it understands (as emitted by the scout tasks):

    FT SCOUT — new candidate rows (paste into 'FT Scout' tab)
    Date added | Ticker & listing | Company/Industry | FT source | ...
    <data rows>
    HOLDINGS MENTIONS — new rows (paste into 'Holdings Mentions' tab)
    Date | Holding | FT headline | News or noise | What was said
    <data rows>
    RUN NOTES
    <prose — never appended>

Section markers route to master tabs; the row after a marker is a column header and
is skipped; RUN NOTES ends the parse. Rows are deduped against what the master
already holds, so a re-run is safe and a partially-pasted sheet won't double up.

Usage (needs GOOGLE_SERVICE_ACCOUNT_JSON):
    python scripts/sweep_staging.py --folder <DRIVE_FOLDER_ID> \
        --spreadsheet-id <MASTER_ID> [--dry-run] [--keep]

Exit codes: 0 ok (including nothing to do) · 1 error
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time


def call(request, tries: int = 5):
    """Execute a Google API request, retrying transient 5xx/429 errors.

    Google's Sheets/Drive APIs return 500/503/429 sporadically under no fault of
    ours (a 503 on a Growth-Scanner read killed the 2026-08-20 run after every row
    had already merged). Back off and retry those; let real errors (403, 404, 400)
    propagate immediately.
    """
    from googleapiclient.errors import HttpError
    for i in range(tries):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(2 ** i)          # 1, 2, 4, 8 seconds
                continue
            raise

# Section marker -> (master tab, dedup key column indexes)
# Keys are chosen to identify a logical row: date + the thing it is about.
SECTIONS: list[tuple[re.Pattern, str, tuple[int, ...]]] = [
    (re.compile(r"^FT SCOUT\b", re.I),          "FT Scout",          (0, 1)),
    (re.compile(r"^ECONOMIST SCOUT\b", re.I),   "Economist Scout",   (0, 1)),
    (re.compile(r"^HOLDINGS MENTIONS\b", re.I), "Holdings Mentions", (0, 1)),
]
STOP = re.compile(r"^RUN NOTES\b", re.I)

# A service account that is only a *writer* on the staging folder cannot trash a
# file Kayvon owns — Drive reserves trashing for the owner (403
# insufficientFilePermissions, hit on the first real run 2026-08-20). A writer CAN
# rename, so renaming with this prefix is the fallback way to mark a sheet done.
# Files already carrying the prefix are skipped on the next run.
SWEPT_PREFIX = "SWEPT "


def creds():
    from google.oauth2.service_account import Credentials
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        sys.exit("GOOGLE_SERVICE_ACCOUNT_JSON is not set")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as e:
        # The failure mode that cost a run on 2026-08-20: the secret held the key
        # pasted twice. Say so plainly instead of surfacing a bare parser error.
        sys.exit(f"GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON ({e}). "
                 f"Length {len(raw)} chars. A 'Extra data' error here almost always "
                 f"means the key was pasted more than once into the secret.")
    return Credentials.from_service_account_info(info, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])


def trim(row: list[str]) -> list[str]:
    """Drop trailing empties — staging sheets pad every row to the widest section."""
    out = list(row)
    while out and not str(out[-1]).strip():
        out.pop()
    return out


def parse_staging(values: list[list[str]]) -> dict[str, list[list[str]]]:
    """Split a staging sheet's rows into {master tab: [data rows]}."""
    out: dict[str, list[list[str]]] = {}
    tab: str | None = None
    expect_header = False

    for raw in values:
        row = trim(raw)
        if not row:
            continue
        first = str(row[0]).strip()

        if STOP.match(first):
            break
        matched = next((t for pat, t, _ in SECTIONS if pat.match(first)), None)
        if matched:
            tab, expect_header = matched, True
            out.setdefault(tab, [])
            continue
        if tab is None:
            continue          # preamble before the first marker
        if expect_header:
            expect_header = False
            continue          # the column-header row
        out[tab].append(row)
    return out


def key_for(tab: str, row: list[str]) -> tuple:
    idx = next((k for _p, t, k in SECTIONS if t == tab), (0, 1))
    return tuple(str(row[i]).strip().lower() if i < len(row) else "" for i in idx)


def retire(drive, f) -> str:
    """Mark a swept sheet done. Trash it if we own it; otherwise rename it.

    Returns a short word for the log: 'trashed', 'renamed' or 'left in place'.
    """
    from googleapiclient.errors import HttpError
    try:
        call(drive.files().update(fileId=f["id"], body={"trashed": True}))
        return "trashed"
    except HttpError as e:
        if e.resp.status != 403:
            raise   # a real error (auth, network) — do not paper over it
    try:
        call(drive.files().update(fileId=f["id"],
                                  body={"name": SWEPT_PREFIX + f["name"]}))
        return "renamed " + SWEPT_PREFIX + f["name"]
    except HttpError as e:
        return f"could not retire ({e.resp.status}) — will be re-read next run, dedup will skip its rows"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True, help="Drive folder holding the staging sheets")
    ap.add_argument("--spreadsheet-id", required=True, help="master tracker spreadsheet")
    ap.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    ap.add_argument("--keep", action="store_true", help="do not trash swept sheets")
    args = ap.parse_args()

    from googleapiclient.discovery import build
    c = creds()
    sheets = build("sheets", "v4", credentials=c).spreadsheets()
    drive = build("drive", "v3", credentials=c)

    staged = call(drive.files().list(
        q=(f"'{args.folder}' in parents and trashed = false and "
           f"mimeType = 'application/vnd.google-apps.spreadsheet'"),
        fields="files(id,name)", orderBy="name")).get("files", [])
    staged = [f for f in staged if not f["name"].startswith(SWEPT_PREFIX)]
    if not staged:
        print("no staging sheets — nothing to do")
        return 0
    print(f"found {len(staged)} staging sheet(s)")

    # What the master already holds, per tab, as dedup keys.
    existing: dict[str, set[tuple]] = {}
    live_tabs = {s["properties"]["title"] for s in
                 call(sheets.get(spreadsheetId=args.spreadsheet_id))["sheets"]}
    for _pat, tab, _k in SECTIONS:
        if tab not in live_tabs:
            continue
        vals = call(sheets.values().get(spreadsheetId=args.spreadsheet_id,
                                        range=f"'{tab}'")).get("values", [])
        existing[tab] = {key_for(tab, trim(r)) for r in vals[1:] if trim(r)}

    total_new = 0
    failed = 0
    for f in staged:
        try:
            vals = call(sheets.values().get(spreadsheetId=f["id"],
                                            range="A:Z")).get("values", [])
        except Exception as e:
            # One unreadable sheet must not abort the others or lose the merges and
            # renames already done above. Log it, count it, move on.
            print(f"  {f['name']}: could not read ({e}) — SKIPPED, will retry next run")
            failed += 1
            continue
        sections = parse_staging(vals)
        if not sections:
            print(f"  {f['name']}: no recognisable sections — LEFT IN PLACE")
            continue

        swept_all = True
        for tab, rows in sections.items():
            if tab not in live_tabs:
                print(f"  {f['name']}: master has no '{tab}' tab — skipped")
                swept_all = False
                continue
            seen = existing.setdefault(tab, set())
            fresh = [r for r in rows if key_for(tab, r) not in seen]
            dupes = len(rows) - len(fresh)
            if fresh and not args.dry_run:
                call(sheets.values().append(
                    spreadsheetId=args.spreadsheet_id, range=f"'{tab}'!A1",
                    valueInputOption="RAW", insertDataOption="INSERT_ROWS",
                    body={"values": fresh}))
                seen.update(key_for(tab, r) for r in fresh)
            total_new += len(fresh)
            print(f"  {f['name']} -> {tab}: {len(fresh)} new"
                  + (f", {dupes} already present" if dupes else "")
                  + (" (dry run)" if args.dry_run else ""))

        if swept_all and not args.dry_run and not args.keep:
            print(f"  {f['name']}: {retire(drive, f)}")

    print(f"done — {total_new} row(s) appended to the master"
          + (f"; {failed} sheet(s) unreadable this run, will retry next run" if failed else ""))
    # A sheet we could not read is deferred, not lost — it is re-read next run and
    # dedup makes that safe. So a transient Google outage does not fail the job.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
