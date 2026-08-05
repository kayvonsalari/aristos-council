"""Universe-run .md/.html auto-persistence (UI-FIX-1).

WHY: two paid narrated runs (2026-08-04) existed only in one browser tab's Streamlit
session state; a Streamlit restart destroyed both before download — the outputs of
~$1.50 of narration were unrecoverable. Auto-persisting to disk the moment a run
completes (ad-hoc runs included), before rendering, means a restart can never again
destroy a completed run.

NOT project data like ``reports/<TICKER>/*.json`` (Sprint 3, persistence/reports.py):
those are the committed council-run record. A universe run's .md/.html is a
byte-identical, disposable copy of what the UI's download buttons already serve — a
local recovery cache, not a new record format — so it lives under
``reports/universe_runs/`` and is gitignored.
"""

from __future__ import annotations

from pathlib import Path


def save_universe_run(md_bytes: bytes, html_bytes: bytes, *, md_name: str,
                      html_name: str, out_dir: Path) -> tuple[Path, Path]:
    """Write the run's markdown + HTML to ``out_dir`` (created if needed).

    Returns the two paths written. ``md_bytes``/``html_bytes`` are expected to be the
    SAME bytes the UI's download buttons serve — this function does no rendering of
    its own, so the persisted copy can never drift from the download."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / md_name
    html_path = out_dir / html_name
    md_path.write_bytes(md_bytes)
    html_path.write_bytes(html_bytes)
    return md_path, html_path
