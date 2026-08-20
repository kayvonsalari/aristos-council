#!/usr/bin/env python3
"""
Scout staging sweeper — merges stranded staging sheets into the master tracker.

The FT/Economist scout tasks run as scheduled CLOUD sessions, which never have
access to Kayvon's machine — so the browser path in those prompts can never fire
and every run falls back to writing a "… Staging <date>" sheet. Those sheets then
accumulate un-pasted (five of them by 2026-08-20, master last updated 08-13).

This script is the deterministic half of the fix: the LLM keeps doing the research
and writing staging sheets; this appends their rows into the right master tabs and
trashes the staged file. No model, no browser, no manual paste.

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

# Section marker -> (master tab, dedup key column indexes)
# Keys are chosen to identify a logical row: date + the thing it is about.
SECTIONS: list[tuple[re.Pattern, str, tuple[int, ...]]] = [
    (re.compile(r"^FT SCOUT\b", re.I),          "FT Scout",          (0, 1)),
    (re.compile(r"^ECONOMIST SCOUT\b", re.I),   "Economist Scout",   (0, 1)),
    (re.compile(r"^HOLDINGS MENTIONS\b", re.I), "Holdings Mentions", (0, 1)),
]
STOP = re.compile(r"^RUN NOTES\b", re.I)


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

    staged = drive.files().list(
        q=(f"'{args.folder}' in parents and trashed = false and "
           f"mimeType = 'application/vnd.google-apps.spreadsheet'"),
        fields="files(id,name)", orderBy="name").execute().get("files", [])
    if not staged:
        print("no staging sheets — nothing to do")
        return 0
    print(f"found {len(staged)} staging sheet(s)")

    # What the master already holds, per tab, as dedup keys.
    existing: dict[str, set[tuple]] = {}
    live_tabs = {s["properties"]["title"] for s in
                 sheets.get(spreadsheetId=args.spreadsheet_id).execute()["sheets"]}
    for _pat, tab, _k in SECTIONS:
        if tab not in live_tabs:
            continue
        vals = sheets.values().get(spreadsheetId=args.spreadsheet_id,
                                   range=f"'{tab}'").execute().get("values", [])
        existing[tab] = {key_for(tab, trim(r)) for r in vals[1:] if trim(r)}

    total_new = 0
    for f in staged:
        vals = sheets.values().get(spreadsheetId=f["id"],
                                   range="A:Z").execute().get("values", [])
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
                sheets.values().append(
                    spreadsheetId=args.spreadsheet_id, range=f"'{tab}'!A1",
                    valueInputOption="RAW", insertDataOption="INSERT_ROWS",
                    body={"values": fresh}).execute()
                seen.update(key_for(tab, r) for r in fresh)
            total_new += len(fresh)
            print(f"  {f['name']} -> {tab}: {len(fresh)} new"
                  + (f", {dupes} already present" if dupes else "")
                  + (" (dry run)" if args.dry_run else ""))

        if swept_all and not args.dry_run and not args.keep:
            drive.files().update(fileId=f["id"], body={"trashed": True}).execute()
            print(f"  {f['name']}: trashed")

    print(f"done — {total_new} row(s) appended to the master")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
