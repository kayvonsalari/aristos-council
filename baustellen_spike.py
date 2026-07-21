#!/usr/bin/env python3
"""
Munich Baustellen — feed spike.

Answers ONE question: is there a machine-readable feed of Munich construction
sites with (a) a stable ID per site and (b) a planned end date?

If yes -> the project is buildable. If no -> stop now, you've lost 10 minutes.

Stdlib only. Python 3.9+.

    python3 baustellen_spike.py probe                  # find candidate datasets
    python3 baustellen_spike.py snapshot <url>         # take one snapshot
    python3 baustellen_spike.py diff                   # compare last two
    python3 baustellen_spike.py verdict                # traffic lights so far
"""

import json
import sqlite3
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import hashlib
import os
from datetime import datetime, timezone, date

DB = os.environ.get("BAUSTELLEN_DB", "baustellen.db")
RAW_DIR = os.environ.get("BAUSTELLEN_RAW", "raw")
UA = "baustellen-spike/0.1 (civic data experiment; contact: you@example.com)"

CKAN = "https://opendata.muenchen.de/api/3/action"

# Field names to hunt for. Munich feeds are inconsistent; cast a wide net.
ID_KEYS = ["id", "ID", "baustelle_id", "objectid", "OBJECTID", "uuid",
           "identifier", "massnahme_id", "nummer", "lfd_nr"]
END_KEYS = ["ende", "Ende", "end", "bis", "enddatum", "datum_bis", "end_date",
            "geplantes_ende", "bauende", "ende_geplant", "endtermin",
            "voraussichtliches_ende", "dateEnd", "endDate"]
START_KEYS = ["beginn", "Beginn", "start", "von", "startdatum", "datum_von",
              "start_date", "baubeginn", "startzeit", "dateStart", "startDate"]


# ---------------------------------------------------------------- utilities

def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(), dict(r.headers)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pick(d, keys):
    """First matching key in a dict, case-insensitively-ish."""
    lowered = {k.lower(): k for k in d.keys()}
    for k in keys:
        if k in d:
            return k, d[k]
        if k.lower() in lowered:
            real = lowered[k.lower()]
            return real, d[real]
    return None, None


def parse_date(v):
    """Best-effort. Returns date or None. Feeds use every format known to man."""
    if v is None:
        return None
    if isinstance(v, (int, float)):          # epoch millis, seen in ArcGIS feeds
        try:
            ms = float(v)
            if ms > 1e11:
                ms /= 1000.0
            return datetime.fromtimestamp(ms, timezone.utc).date()
        except Exception:
            return None
    s = str(v).strip()
    if not s or s.lower() in ("null", "none", "-"):
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
                "%d.%m.%Y %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


def rows_from(payload):
    """Pull a list of flat dicts out of GeoJSON / plain JSON / CKAN-ish blobs."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if "features" in payload:                # GeoJSON or ArcGIS
        out = []
        for f in payload["features"]:
            props = dict(f.get("properties") or f.get("attributes") or {})
            if f.get("id") is not None and "id" not in props:
                props["id"] = f["id"]
            out.append(props)
        return out
    for key in ("records", "result", "data", "items", "elements"):
        if key in payload:
            return rows_from(payload[key])
    return []


# ---------------------------------------------------------------- schema

def db():
    os.makedirs(RAW_DIR, exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS snapshots (
        run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        source      TEXT NOT NULL,
        fetched_at  TEXT NOT NULL,
        http_status INTEGER,
        row_count   INTEGER,
        raw_path    TEXT
    );
    CREATE TABLE IF NOT EXISTS observations (
        run_id       INTEGER NOT NULL,
        source       TEXT NOT NULL,
        site_id      TEXT NOT NULL,
        observed_at  TEXT NOT NULL,
        start_date   TEXT,
        end_date     TEXT,
        payload_hash TEXT NOT NULL,
        raw_json     TEXT NOT NULL,
        PRIMARY KEY (run_id, source, site_id)
    );
    CREATE INDEX IF NOT EXISTS obs_site ON observations(source, site_id, observed_at);
    """)
    return c


# ---------------------------------------------------------------- probe

def probe():
    print("Probing opendata.muenchen.de for construction-site datasets...\n")
    found = []
    for q in ("Baustelle", "Baustellen", "Verkehr Baustelle", "Straßenbau"):
        url = f"{CKAN}/package_search?q={urllib.parse.quote(q)}&rows=25"
        try:
            status, body, _ = fetch(url)
        except urllib.error.HTTPError as e:
            print(f"  ! {q}: HTTP {e.code} — portal may not be CKAN. "
                  f"Open the portal in a browser and look for an API link.")
            continue
        except Exception as e:
            print(f"  ! {q}: {e}")
            continue
        data = json.loads(body)
        for pkg in data.get("result", {}).get("results", []):
            for res in pkg.get("resources", []):
                fmt = (res.get("format") or "").upper()
                if fmt in ("JSON", "GEOJSON", "CSV", "WFS", "API", "REST"):
                    found.append((pkg.get("title"), fmt, res.get("url")))
    if not found:
        print("Nothing found. Two possibilities:\n"
              "  1. The portal isn't CKAN / changed its API -> check by hand.\n"
              "  2. Munich genuinely doesn't publish this -> project is dead.\n"
              "Do NOT assume (2) without checking (1).")
        return
    seen = set()
    for title, fmt, url in found:
        if url in seen:
            continue
        seen.add(url)
        print(f"  [{fmt:7}] {title}\n            {url}\n")
    print(f"{len(seen)} candidate resources.\n"
          f"Next: python3 {sys.argv[0]} snapshot <url>")


# ---------------------------------------------------------------- snapshot

def snapshot(url):
    c = db()
    ts = now_iso()
    try:
        status, body, _ = fetch(url)
    except Exception as e:
        print(f"FETCH FAILED: {e}")
        c.execute("INSERT INTO snapshots(source,fetched_at,http_status,row_count,raw_path)"
                  " VALUES (?,?,?,?,?)", (url, ts, 0, 0, None))
        c.commit()
        return

    raw_path = os.path.join(RAW_DIR, f"{int(time.time())}_"
                            f"{hashlib.sha1(url.encode()).hexdigest()[:8]}.bin")
    with open(raw_path, "wb") as f:
        f.write(body)

    try:
        payload = json.loads(body)
    except Exception:
        print("Not JSON. Raw saved to", raw_path,
              "\nIf it's CSV, adapt rows_from(). If it's HTML, there's no feed here.")
        return

    rows = rows_from(payload)
    if not rows:
        print("Parsed, but found no records. Structure:",
              list(payload)[:10] if isinstance(payload, dict) else type(payload))
        return

    sample = rows[0]
    id_key, _ = pick(sample, ID_KEYS)
    end_key, _ = pick(sample, END_KEYS)
    start_key, _ = pick(sample, START_KEYS)

    print(f"{len(rows)} records. Fields: {', '.join(list(sample)[:14])}"
          f"{' ...' if len(sample) > 14 else ''}")
    print(f"  id field:    {id_key or '*** NONE FOUND ***'}")
    print(f"  end field:   {end_key or '*** NONE FOUND ***'}")
    print(f"  start field: {start_key or '(none)'}")

    if not end_key:
        print("\nVERDICT: no planned end date in the feed. The whole idea rests on\n"
              "tracking that date moving. Without it there is nothing to build.\n"
              "Check the field list above by eye before believing me — my key list\n"
              "may just be missing the right German word.")
        return
    if not id_key:
        print("\nWARNING: no obvious stable ID. Falling back to a fingerprint of\n"
              "the record's non-date fields. If the feed reshuffles those, you\n"
              "will get phantom 'new' sites and the diff is worthless.")

    cur = c.execute("INSERT INTO snapshots(source,fetched_at,http_status,row_count,raw_path)"
                    " VALUES (?,?,?,?,?)", (url, ts, status, len(rows), raw_path))
    run_id = cur.lastrowid

    n = 0
    for r in rows:
        if id_key:
            sid = str(r.get(id_key))
        else:
            stable = {k: v for k, v in sorted(r.items())
                      if k not in (end_key, start_key)}
            sid = hashlib.sha1(json.dumps(stable, sort_keys=True,
                                          default=str).encode()).hexdigest()[:16]
        ed = parse_date(r.get(end_key)) if end_key else None
        sd = parse_date(r.get(start_key)) if start_key else None
        blob = json.dumps(r, sort_keys=True, default=str)
        try:
            c.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)",
                      (run_id, url, sid, ts,
                       sd.isoformat() if sd else None,
                       ed.isoformat() if ed else None,
                       hashlib.sha1(blob.encode()).hexdigest(), blob))
            n += 1
        except sqlite3.IntegrityError:
            pass  # duplicate id within one snapshot -> ids not unique
    c.commit()

    parsed = c.execute("SELECT COUNT(*) FROM observations WHERE run_id=? AND end_date IS NOT NULL",
                       (run_id,)).fetchone()[0]
    print(f"\nStored run {run_id}: {n} sites, {parsed} with a parseable end date"
          f" ({100*parsed//max(n,1)}%).")
    if n < len(rows):
        print(f"!! {len(rows)-n} records had duplicate IDs. The ID field is not unique.")
    if parsed < n * 0.5:
        print("!! Over half the end dates failed to parse. Check parse_date() "
              "against the raw values before drawing conclusions.")
    print(f"\nRun this again in 12-24h, then: python3 {sys.argv[0]} diff")


# ---------------------------------------------------------------- diff

def diff():
    c = db()
    runs = c.execute("SELECT run_id, source, fetched_at FROM snapshots "
                     "WHERE row_count > 0 ORDER BY run_id DESC LIMIT 2").fetchall()
    if len(runs) < 2:
        print("Need two successful snapshots. Take another one tomorrow.")
        return
    (new_id, src, new_t), (old_id, _, old_t) = runs[0], runs[1]
    print(f"Diffing run {old_id} ({old_t}) -> run {new_id} ({new_t})\n")

    old = {r[0]: r[1] for r in c.execute(
        "SELECT site_id, end_date FROM observations WHERE run_id=?", (old_id,))}
    new = {r[0]: r[1] for r in c.execute(
        "SELECT site_id, end_date FROM observations WHERE run_id=?", (new_id,))}

    appeared = set(new) - set(old)
    vanished = set(old) - set(new)
    moved = [(k, old[k], new[k]) for k in set(old) & set(new)
             if old[k] != new[k]]

    print(f"  unchanged:  {len(set(old) & set(new)) - len(moved)}")
    print(f"  date moved: {len(moved)}")
    print(f"  appeared:   {len(appeared)}")
    print(f"  vanished:   {len(vanished)}")

    churn = (len(appeared) + len(vanished)) / max(len(old), 1)
    if churn > 0.3:
        print(f"\n!! {churn:.0%} of IDs changed between two runs. Either the feed\n"
              "   really churns that fast (implausible for roadworks) or the IDs\n"
              "   are regenerated on publish. If the latter: this whole approach\n"
              "   fails and you need geometry-based matching instead.")
    else:
        print(f"\n  ID churn {churn:.1%} — IDs look stable. Good.")

    for k, o, n in moved[:15]:
        print(f"    {k}: {o} -> {n}")


# ---------------------------------------------------------------- verdict

def verdict():
    c = db()
    sites = c.execute("""
        SELECT site_id,
               MIN(observed_at) AS first_seen,
               MAX(observed_at) AS last_seen,
               COUNT(DISTINCT end_date) AS distinct_ends
        FROM observations WHERE end_date IS NOT NULL
        GROUP BY site_id""").fetchall()
    if not sites:
        print("No data yet.")
        return
    today = date.today()
    tally = {"GREY": 0, "GREEN": 0, "AMBER": 0, "RED": 0}
    for sid, first_seen, last_seen, distinct in sites:
        first_end = c.execute("SELECT end_date FROM observations WHERE site_id=? "
                              "AND end_date IS NOT NULL ORDER BY observed_at LIMIT 1",
                              (sid,)).fetchone()[0]
        curr_end = c.execute("SELECT end_date FROM observations WHERE site_id=? "
                             "AND end_date IS NOT NULL ORDER BY observed_at DESC LIMIT 1",
                             (sid,)).fetchone()[0]
        moves = distinct - 1
        baseline_days = (datetime.fromisoformat(last_seen).date()
                         - datetime.fromisoformat(first_seen).date()).days
        slip = (parse_date(curr_end) - parse_date(first_end)).days if first_end and curr_end else 0
        overdue = parse_date(curr_end) and parse_date(curr_end) < today

        if baseline_days < 30:
            tally["GREY"] += 1
        elif overdue or moves >= 3 or slip > 90:
            tally["RED"] += 1
        elif moves >= 1:
            tally["AMBER"] += 1
        else:
            tally["GREEN"] += 1

    print(f"{len(sites)} sites tracked")
    for k in ("GREY", "GREEN", "AMBER", "RED"):
        print(f"  {k:6} {tally[k]}")
    if tally["GREY"] == len(sites):
        print("\nEverything grey — you haven't watched long enough. As designed.")


if __name__ == "__main__":
    # In Jupyter/IPython, sys.argv belongs to the kernel, not to you.
    if any(a.endswith(".json") or a == "-f" for a in sys.argv[1:]):
        sys.exit("Looks like you're inside a notebook — sys.argv is the kernel's.\n"
                 "Use a shell cell instead:   !python3 baustellen_spike.py probe\n"
                 "Or import it:               import baustellen_spike as b; b.probe()")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if cmd == "probe":
        probe()
    elif cmd == "snapshot":
        if len(sys.argv) < 3:
            sys.exit("usage: snapshot <url>")
        snapshot(sys.argv[2])
    elif cmd == "diff":
        diff()
    elif cmd == "verdict":
        verdict()
    else:
        sys.exit(__doc__)
