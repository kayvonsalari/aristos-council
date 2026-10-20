"""JUDGE-PANEL-1 - the panel's log: the whole point of the feature.

Every run appends one row per judge per call to ``judge_log.csv`` and one summary row per company
to ``judge_summary.csv``, both under ``data/local/judges/`` (git-ignored: nothing about what is
being looked at belongs in the repo). In twelve months these two files are what the panel is
judged on: what each model said, how stable it was, and what the lenses said at that moment.

The files are append-only and the headers are fixed. A header that no longer matches the file is
never "migrated" in place - the writer refuses, so a changed column order cannot quietly shift
twelve months of data under the wrong titles.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Optional

from .panel import (ERROR, INCOMPLETE, INVALID, NOT_RUN, STABLE, UNSTABLE, PanelResult,
                    judges_answered)

_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DIR = _ROOT / "data" / "local" / "judges"
LOG_NAME = "judge_log.csv"
SUMMARY_NAME = "judge_summary.csv"

_LOG_V1 = ("run_date", "ticker", "judge_family", "model_name", "call_number", "verdict",
           "confidence", "stable", "reason", "would_change_my_mind", "verdict_of_record",
           "lens_split", "pack_hash", "call_status")
_LOG_V2 = _LOG_V1 + ("error_detail",)
LOG_COLUMNS = _LOG_V2 + ("driving_fact", "key_risk", "unexplained", "own_knowledge_claims")

FAMILY_COLUMNS = (("anthropic", "claude"), ("openai", "openai"), ("google", "gemini"))
_SUMMARY_V1 = (("run_date", "ticker", "pack_hash", "verdict_of_record", "lens_split",
                "record_headline")
               + tuple(c for _, name in FAMILY_COLUMNS for c in (f"{name}_verdict", f"{name}_confidence"))
               + ("panel_split", "panel_agreement"))
_SUMMARY_V2 = _SUMMARY_V1 + ("judges_answered",)
SUMMARY_COLUMNS = _SUMMARY_V2 + ("peers",)
# Earlier headers of the same files. A file carrying one of THESE is upgraded in place (the new
# columns are appended, empty for old rows - nothing is reordered or lost); any other header is
# still refused.
_PREVIOUS = {LOG_NAME: (_LOG_V1, _LOG_V2), SUMMARY_NAME: (_SUMMARY_V1, _SUMMARY_V2)}


class LogHeaderMismatch(RuntimeError):
    """The file on disk has different columns than this code writes."""


def judges_dir(path=None) -> Path:
    return Path(path or os.environ.get("ARISTOS_JUDGES_DIR") or DEFAULT_DIR)


def _upgrade(path: Path, columns: tuple) -> None:
    """Rewrite a file that carries an earlier known header with the current one, padding old rows."""
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    width = len(columns)
    body = [r + [""] * (width - len(r)) for r in rows[1:]]
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(body)
    tmp.replace(path)


def _append(path: Path, columns: tuple, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists() or path.stat().st_size == 0
    if not fresh:
        with path.open(encoding="utf-8", newline="") as f:
            header = next(csv.reader(f), [])
        if tuple(header) in _PREVIOUS.get(path.name, ()):
            _upgrade(path, columns)
            header = list(columns)
        if tuple(header) != columns:
            raise LogHeaderMismatch(f"{path.name} has columns {header}, this code writes {list(columns)}; "
                                    "move the old file aside rather than mixing the two")
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if fresh:
            writer.writerow(columns)
        writer.writerows(rows)


def _cell(text) -> str:
    return " ".join(str(text or "").split())


def log_rows(result: PanelResult) -> list[list]:
    """One row per judge per call (a judge with no key gets a single call-0 row, so its absence is
    in the data and not just missing from it)."""
    rows: list[list] = []
    common = (result.run_at, result.ticker)
    tail = (result.record.cell, result.record.split, result.pack_hash)
    for j in result.judges:
        stable = "yes" if j.status == STABLE else "no"
        if not j.calls:
            rows.append([*common, j.family, j.model, 0, "NOT_RUN", "", stable, j.note, "", *tail, NOT_RUN, "", "", "", "", 0])
            continue
        for c in j.calls:
            verdict = c.verdict if c.status == "ok" else {"invalid": "INVALID", "error": "ERROR"}.get(c.status, "")
            rows.append([*common, j.family, j.model, c.call_no, verdict, c.confidence, stable,
                         _cell(c.reason or c.error), _cell(c.would_change_my_mind), *tail, c.status,
                         _cell(c.error_detail), _cell(c.driving_fact), _cell(c.key_risk),
                         _cell(c.unexplained), c.own_knowledge_claims])
    return rows


def family_cell(j) -> tuple[str, str]:
    """``(verdict cell, confidence cell)`` for one judge in the summary row."""
    if j is None:
        return "", ""
    if j.status == STABLE:
        c = j.first_ok
        return j.verdict, (c.confidence if c else "")
    if j.status == UNSTABLE:
        return "UNSTABLE:" + "/".join(j.verdicts), ""
    if j.status == INCOMPLETE:
        return "INCOMPLETE:" + "/".join(j.verdicts), ""
    return {NOT_RUN: "NOT_RUN", INVALID: "INVALID", ERROR: "ERROR"}.get(j.status, j.status.upper()), ""


def summary_row(result: PanelResult) -> list:
    by_family = {j.family: j for j in result.judges}
    row = [result.run_at, result.ticker, result.pack_hash, result.record.cell,
           result.record.split, _cell(result.record.headline)]
    for family, _ in FAMILY_COLUMNS:
        row.extend(family_cell(by_family.get(family)))
    row += [result.split_line, result.agreement, judges_answered(result.judges),
            ";".join(result.peers)]
    return row


def write_log(result: PanelResult, directory=None) -> Optional[Path]:
    """Append this run to both files and return the directory written to. Nothing is written for a
    panel that asked no one (no judge had a key): an all-"not run" row is
    not data about the judges."""
    if not result.available or not any(j.calls for j in result.judges):
        return None
    folder = judges_dir(directory)
    _append(folder / LOG_NAME, LOG_COLUMNS, log_rows(result))
    _append(folder / SUMMARY_NAME, SUMMARY_COLUMNS, [summary_row(result)])
    return folder
