"""The run manifest — one row per narration: condition, rep, params, timestamps, and the
raw-output file path. JSONL, append-only, mirroring this codebase's existing IO-at-the-edge
convention (``persistence/verdicts.py``'s append-only log).

Reproducibility note on "seeds": the Anthropic API does not expose a request-level seed
parameter the way some other providers do, so there is no seed to record. What IS
reproducible/recordable — and is recorded here — is the MODEL ID and TEMPERATURE per tier
(``agents.runners.runner_metadata``), which for a real run defaults to temperature 0.0 on
every tier (the project's own production default — this harness does not override it, so
the experiment measures the SYSTEM AS ACTUALLY DEPLOYED, not an artificially randomized
variant). Residual stochasticity at temp 0.0 is itself part of what Experiment A's noise
floor measures.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ManifestRow:
    run_id: str                 # e.g. "A_VUSA.AS_sell_rep2"
    experiment: str              # "A" | "A_baseline" | "B1" | "B2"
    condition_id: str
    fund_ticker: str
    rep: int
    note: str
    models: dict = field(default_factory=dict)     # runner_metadata() output
    started_at: str = field(default_factory=_utcnow_iso)
    finished_at: str = ""
    raw_output_path: str = ""
    ok: bool = False
    error: str = ""

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), default=str)


def append_row(row: ManifestRow, manifest_path: Path) -> None:
    """Append ``row`` to the JSONL manifest at ``manifest_path`` (created if absent).
    Append-only: an existing manifest from a prior partial run is never overwritten, so a
    resumed/retried run accumulates rather than destroys history."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("a", encoding="utf-8") as fh:
        fh.write(row.to_json_line() + "\n")


def read_manifest(manifest_path: Path) -> list[dict]:
    """All rows from ``manifest_path``, oldest first. Empty list when the file doesn't
    exist yet (a run that hasn't started)."""
    if not manifest_path.exists():
        return []
    rows = []
    with manifest_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
