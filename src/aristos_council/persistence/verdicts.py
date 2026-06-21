"""Append-only verdict history — ``verdicts/<TICKER>.json``.

Why this exists (Sprint 2): the JNJ live runs drifted BUY 0.62 -> BUY 0.65 ->
HOLD 0.62 on near-identical data with nothing to catch it, because each run was
stateless. Persisting one record per run lets the next run load its predecessor
and pass the prior verdict into the state field the recommendation_flip veto
already watches (``prior_recommendation``) — the trigger that could never fire
without history.

Contract
--------
- APPEND-ONLY. Existing records are never modified or removed; a new run only
  ever appends. The file is a JSON array (one element per run), so the whole
  history is loadable for trend inspection, not just the latest.
- IO AT THE EDGE. Nothing here is called from inside the graph. The example
  entrypoint loads the latest record before ``invoke`` and appends the new one
  after — the graph itself never touches disk.

Each record carries: ``run_at`` (ISO timestamp), ``strategy_id``, the
``verdict`` + ``confidence`` the Decision agent issued, each specialist's
``stance`` by name, the veto ``trigger`` names that fired, and the provenance
audit COUNTS (the integer tallies only — the violation prose stays in the run
log, not the persisted history).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from ..state import (
    Recommendation,
    ResearchState,
    Stance,
    VetoTrigger,
)

# The integer tallies we keep out of ProvenanceAudit.summary(); the list-valued
# keys (violations, unit_scaled_notes) are run-log detail, not history.
_AUDIT_COUNT_KEYS = (
    "figures_audited",
    "verified",
    "mismatch",
    "unresolvable",
    "unverifiable",
    "unit_scaled",
)


class VerdictRecord(BaseModel):
    """One run's verdict, persisted for the next run to read back.

    ``ticker`` is redundant with the filename but kept on the record so the log
    is self-describing and ``append_record`` can derive its own path.
    """

    ticker: str
    run_at: datetime
    strategy_id: str
    verdict: Optional[Recommendation] = None
    confidence: Optional[float] = None
    # specialist name -> stance, e.g. {"fundamental": "bullish", ...}
    stances: dict[str, Stance] = Field(default_factory=dict)
    # veto trigger names that fired this run (deduped, in fire order)
    veto_triggers: list[VetoTrigger] = Field(default_factory=list)
    # provenance audit COUNTS only (no violation prose)
    provenance_audit: Optional[dict] = None
    # Ephemeral per-run overrides applied on top of the base strategy (empty for a
    # default run). A non-empty value marks this as an EXPERIMENT run, which
    # load_latest never returns as the flip baseline.
    applied_overrides: dict = Field(default_factory=dict)


def _audit_counts(provenance_audit: Optional[dict]) -> Optional[dict]:
    if not provenance_audit:
        return None
    return {
        k: provenance_audit[k]
        for k in _AUDIT_COUNT_KEYS
        if k in provenance_audit
    }


def record_from_state(
    state: ResearchState, run_at: Optional[datetime] = None
) -> VerdictRecord:
    """Build a persistable record from a completed run's state.

    ``run_at`` defaults to the run's ``as_of`` stamp, so the record's timestamp
    matches the run it describes rather than the moment it happened to be saved.
    """
    decision = state.decision

    triggers: list[VetoTrigger] = []
    for flag in state.veto_flags:
        if flag.trigger not in triggers:
            triggers.append(flag.trigger)

    return VerdictRecord(
        ticker=state.ticker,
        run_at=run_at or state.as_of,
        strategy_id=state.strategy_id,
        verdict=decision.recommendation if decision else None,
        confidence=decision.confidence if decision else None,
        stances={
            op.specialist.value: op.stance for op in state.specialist_opinions
        },
        veto_triggers=triggers,
        provenance_audit=_audit_counts(state.provenance_audit),
        applied_overrides=dict(state.applied_overrides),
    )


def verdict_path(ticker: str, verdicts_dir: Path) -> Path:
    """The on-disk path for a ticker's history (ticker is upper-cased)."""
    return Path(verdicts_dir) / f"{ticker.upper()}.json"


def load_records(ticker: str, verdicts_dir: Path) -> list[VerdictRecord]:
    """The full append-only history for a ticker, oldest first ([] if none)."""
    path = verdict_path(ticker, verdicts_dir)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [VerdictRecord.model_validate(r) for r in raw]


def load_latest(
    ticker: str, verdicts_dir: Path, strategy_id: Optional[str] = None
) -> Optional[VerdictRecord]:
    """The most recent record for a ticker, or None if there is no history.

    When ``strategy_id`` is given, only records under that strategy are
    considered — so the recommendation_flip veto compares a run against the
    prior verdict for the same ticker AND strategy, never across strategies
    (a growth BUY must not register as a flip against a dividend HOLD).

    OVERRIDE (experiment) runs — records with a non-empty ``applied_overrides`` —
    are SKIPPED: they are never the flip baseline a future default run compares
    against. They remain in the append-only history (auditable), just not as the
    strategy's "official last verdict" for this purpose.

    INSUFFICIENT_EVIDENCE runs are likewise SKIPPED as a baseline: that verdict is
    off the buy/hold/sell ladder (a "can't tell", not a directional call), so it
    is never a flip TARGET to compare a later run against. The fallback baseline is
    the most recent DIRECTIONAL verdict. (The record stays in the history.)
    """
    records = load_records(ticker, verdicts_dir)
    if strategy_id is not None:
        records = [r for r in records if r.strategy_id == strategy_id]
    records = [r for r in records if not r.applied_overrides]
    records = [r for r in records
               if r.verdict != Recommendation.INSUFFICIENT_EVIDENCE]
    return records[-1] if records else None


def append_record(record: VerdictRecord, verdicts_dir: Path) -> Path:
    """Append one record to its ticker's history, creating the file if needed.

    Append-only: the existing records are loaded, the new one is added to the
    end, and the array is rewritten. Returns the path written.
    """
    records = load_records(record.ticker, verdicts_dir)
    records.append(record)
    path = verdict_path(record.ticker, verdicts_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([r.model_dump(mode="json") for r in records], indent=2)
        + "\n",
        encoding="utf-8",
    )
    return path
