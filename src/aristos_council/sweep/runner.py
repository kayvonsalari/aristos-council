"""SWEEP-1 - run a set of entries end to end and write what came out.

The core is deliberately ignorant of how a report is made: it is handed a *producer* per entry kind
(``producers.py`` has the real ones, which call exactly what the Analyse tab calls) and does the rest
- concurrency, one folder per entry, failure isolation, the checks, the manifest.

* Company entries run first, up to ``workers`` at a time; list entries then run one at a time.
* Every entry writes only into its own folder, and only after its producer returned, so concurrent
  entries cannot interleave a report.
* A failing entry is recorded (``error.txt`` holds the traceback) and the sweep carries on.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from . import checks
from .sets import Entry, SweepSet

OK, FAILED = "ok", "failed"


@dataclass
class Products:
    """What a producer returns for one entry."""
    files: dict = field(default_factory=dict)         # file name -> text
    objs: dict = field(default_factory=dict)          # structured results, for the structured rules
    ticked: list = field(default_factory=list)        # lens labels that must be in the lens table
    names: list = field(default_factory=list)         # full company names (the truncation rule)
    judge_panel: Optional[dict] = None                # the panel as a dict, for the judge rules
    facts: dict = field(default_factory=dict)         # what the summary table shows
    calls: int = 0                                    # model calls made
    cost: float = 0.0                                 # approximate USD
    cost_note: str = ""


@dataclass
class EntryResult:
    entry: Entry
    status: str = FAILED
    error: str = ""
    seconds: float = 0.0
    files: list = field(default_factory=list)
    calls: int = 0
    cost: float = 0.0
    cost_note: str = ""
    facts: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)
    flags: dict = field(default_factory=dict)
    started: str = ""
    ended: str = ""


Producer = Callable[[Entry, dict, Callable[[str], None]], Products]


def effective_flags(entry: Entry, *, judges: bool, council: bool) -> dict:
    """The flags ONE entry runs with: its own setting wins over the command line."""
    return {"judges": judges if entry.judges is None else bool(entry.judges),
            "council": bool(entry.council) or (council and entry.is_company)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_state(root: Optional[Path] = None) -> dict:
    root = root or Path(__file__).resolve().parents[3]
    try:
        run = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True,
                                        timeout=20, check=True).stdout.strip()
        return {"commit": run("rev-parse", "HEAD"), "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
                "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}
    except Exception as exc:                                      # noqa: BLE001 - never stops a sweep
        return {"commit": "unknown", "branch": "", "dirty": None, "error": str(exc)[:120]}


def run_entry(entry: Entry, out_dir: Path, flags: dict, producer: Producer,
              say: Callable[[str], None] = lambda _m: None) -> EntryResult:
    """Produce, write and check ONE entry. Never raises."""
    folder = Path(out_dir) / entry.id
    res = EntryResult(entry=entry, flags=dict(flags), started=_now())
    t0 = time.perf_counter()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        products = producer(entry, {**flags, "folder": str(folder)},
                            lambda m: say(f"[{entry.id}] {m}"))
        for name, text in products.files.items():
            (folder / name).write_text(text, encoding="utf-8")
            res.files.append(name)
        findings = []
        for name, text in products.files.items():
            if name.endswith(".md"):
                findings += checks.check_markdown(text, entry=entry.id, file=name,
                                                  ticked=products.ticked, names=products.names)
        findings += checks.check_structured(products.objs, entry=entry.id)
        findings += checks.check_judges(products.judge_panel, entry=entry.id)
        res.findings = findings
        res.facts, res.calls, res.cost = products.facts, products.calls, products.cost
        res.cost_note = products.cost_note
        res.status = OK
    except Exception:                                             # noqa: BLE001 - recorded, not raised
        res.error = traceback.format_exc()
        res.status = FAILED
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "error.txt").write_text(res.error, encoding="utf-8")
        except OSError:
            pass
    res.seconds = round(time.perf_counter() - t0, 1)
    res.ended = _now()
    say(f"[{entry.id}] {res.status} in {res.seconds}s"
        + (f" ({len(res.findings)} finding(s))" if res.status == OK else ""))
    return res


def market_index_status() -> str:
    """INDEX-AGE-1: ``python -m aristos_council.market_index status`` as text, so a sweep records how old the
    peer sizes it used were. Never raises (a missing index is itself the finding)."""
    try:
        from ..market_index import status
        return chr(10).join(status().lines())
    except Exception as exc:                             # noqa: BLE001
        return f"market index status unavailable: {type(exc).__name__}: {exc}"


def manifest_dict(sweep_set: SweepSet, results: list, *, flags: dict, started: str,
                  ended: str, git: dict, workers: int, finished: bool) -> dict:
    return {
        "set": sweep_set.name, "git": git, "started": started, "ended": ended,
        "finished": finished, "workers": workers,
        "flags": {"judges": flags["judges"], "council": flags["council"]},
        "market_index": market_index_status(),
        "totals": {"entries": len(sweep_set.entries), "ok": sum(r.status == OK for r in results),
                   "failed": sum(r.status == FAILED for r in results),
                   "model_calls": sum(r.calls for r in results),
                   "approx_cost_usd": round(sum(r.cost for r in results), 4),
                   "findings": sum(len(r.findings) for r in results)},
        "entries": [{"id": r.entry.id, "kind": r.entry.kind, "status": r.status,
                     "flags": r.flags, "started": r.started, "ended": r.ended,
                     "seconds": r.seconds, "files": r.files, "model_calls": r.calls,
                     "approx_cost_usd": round(r.cost, 4), "cost_note": r.cost_note,
                     "findings": len(r.findings),
                     "error": (r.error.strip().splitlines() or [""])[-1] if r.error else ""}
                    for r in results],
    }


def status_line(manifest: dict) -> str:
    """``KO.US ok | F.US ok | 1211.HK failed | ...`` - the per-entry status in one line."""
    return " | ".join(f"{e['id']} {e['status']}" for e in manifest["entries"])


def run_sweep(sweep_set: SweepSet, out_dir: Path, producers: dict, *, judges: bool = True,
              council: bool = False, workers: int = 3,
              say: Callable[[str], None] = print, write_summary: bool = True) -> tuple[dict, list]:
    """Run the set; returns ``(manifest, results)``. ``producers`` maps "company"/"list" to a
    producer. The exit code policy lives with the caller (``exit_code``)."""
    from . import summary

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    flags = {"judges": judges, "council": council}
    started, git = _now(), git_state()
    ordered = sweep_set.ordered()
    results: dict = {}
    lock = threading.Lock()

    def snapshot(finished: bool) -> None:
        done = [results[e.id] for e in ordered if e.id in results]
        m = manifest_dict(sweep_set, done, flags=flags, started=started,
                          ended=_now() if finished else "", git=git, workers=workers,
                          finished=finished)
        (out_dir / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")

    safe_say = lambda m: _locked(lock, say, m)

    def one(entry: Entry) -> EntryResult:
        r = run_entry(entry, out_dir, effective_flags(entry, judges=judges, council=council),
                      producers[entry.kind], safe_say)
        with lock:
            results[entry.id] = r
            snapshot(False)
        return r

    snapshot(False)
    companies = [e for e in ordered if e.is_company]
    lists = [e for e in ordered if not e.is_company]
    if companies:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(one, companies))
    for e in lists:                                   # one at a time, after the companies
        one(e)
    done = [results[e.id] for e in ordered]
    snapshot(True)
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    if write_summary:
        (out_dir / "summary.md").write_text(summary.summary_markdown(manifest, done),
                                            encoding="utf-8")
    return manifest, done


def _locked(lock, say, msg) -> None:
    with lock:
        say(msg)


def exit_code(results: list) -> int:
    """Non-zero only when EVERY entry failed (findings never fail a sweep)."""
    return 1 if results and all(r.status == FAILED for r in results) else 0
