"""Orchestrates ONE narration per (condition, rep): builds the ResearchState from a
``conditions.ConditionInputs``, invokes the council graph, saves the raw output verbatim,
and appends a manifest row.

Deliberately does NOT call ``pipeline._council_stage``. That function's internal
``_annotate_narration`` always checks the narrator against the SAME ``RankedTicker`` it was
handed — fine for Experiments A/B1 (narrator_ticker == true_ticker there), but WRONG for
B2's corrupted conditions: checking the narrator against the corrupted table it was already
shown would find nothing (the narrator was truthful to what it saw), which measures
nothing. This module replicates ``_council_stage``'s ~15 lines directly (still only calling
PUBLIC ``aristos_council`` functions — ``build_council``, ``report_from_state`` — no
production code is modified) so every condition's fact-check can be run separately, always
against ``ConditionInputs.true_ticker``.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from aristos_council.agents.runners import runner_metadata
from aristos_council.graph import build_council
from aristos_council.persistence.reports import report_from_state
from aristos_council.pipeline import _static_factor_evidence
from aristos_council.rank_engine import RankedTicker
from aristos_council.state import Recommendation, ResearchState

from . import checker
from .conditions import ConditionInputs
from .fixtures import frame
from .manifest import ManifestRow, append_row

FRAME = frame()


def _ranked_ticker_snapshot(r: RankedTicker) -> dict:
    return asdict(r)


def _invoke_narrator(inputs: ConditionInputs, runners: dict) -> ResearchState:
    app = build_council(inputs.adapter, FRAME, runners, council_mode="narrator",
                        run_matrix=False)
    nt = inputs.narrator_ticker
    imputed_fraction = (len(nt.imputed_factors) / len(nt.factor_ranks)
                        if nt.factor_ranks else 0.0)
    state = ResearchState(
        ticker=nt.ticker, strategy_id=FRAME.id,
        ranker_verdict=Recommendation(nt.verdict),
        ranker_explanation=nt.explain(),
        ranker_cohort_size=nt.universe_size,
        ranker_imputed_fraction=imputed_fraction,
        static_factor_evidence=_static_factor_evidence(nt))
    return ResearchState.model_validate(app.invoke(state))


def run_one(inputs: ConditionInputs, rep: int, runners: dict, *,
           out_dir: Path, manifest_path: Path) -> dict:
    """Runs ONE narration, saves it to
    ``out_dir/{inputs.condition_id}_rep{rep}.json``, appends a manifest row, and returns
    the saved dict. Never raises on an LLM-side failure — the manifest row records
    ``ok=False``/``error`` and the run continues (one bad narration should not lose the
    other 26)."""
    run_id = f"{inputs.condition_id}_rep{rep}"
    started = datetime.now(timezone.utc).isoformat()
    row = ManifestRow(run_id=run_id, experiment=inputs.experiment,
                      condition_id=inputs.condition_id, fund_ticker=inputs.fund_ticker,
                      rep=rep, note=inputs.note, started_at=started)
    out_path = out_dir / f"{run_id}.json"

    try:
        state = _invoke_narrator(inputs, runners)
        report = report_from_state(state)
        report.models = runner_metadata(runners)
        narrative = report.decision.rationale if report.decision else ""
        annotations = checker.check_against_truth(narrative, inputs.true_ticker)

        payload = {
            "run_id": run_id,
            "experiment": inputs.experiment,
            "condition_id": inputs.condition_id,
            "fund_ticker": inputs.fund_ticker,
            "rep": rep,
            "note": inputs.note,
            "forced_verdict": inputs.narrator_ticker.verdict,
            "narrator_ticker": _ranked_ticker_snapshot(inputs.narrator_ticker),
            "true_ticker": _ranked_ticker_snapshot(inputs.true_ticker),
            "corrupted_claims": list(inputs.corrupted_claims),
            "narration": narrative,
            "true_table_annotations": annotations,
            "report": report.model_dump(mode="json"),
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ok": True,
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

        row.models = report.models or {}
        row.finished_at = payload["finished_at"]
        row.raw_output_path = str(out_path)
        row.ok = True
    except Exception as exc:                                         # noqa: BLE001
        row.finished_at = datetime.now(timezone.utc).isoformat()
        row.ok = False
        row.error = f"{type(exc).__name__}: {exc}"
        payload = {"run_id": run_id, "ok": False, "error": row.error}
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        row.raw_output_path = str(out_path)
    finally:
        append_row(row, manifest_path)

    return payload


def run_all(conditions: list[ConditionInputs], reps: int, runners: dict, *,
           out_dir: Path, manifest_path: Path,
           progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Runs every condition x rep (in condition, then rep, order) and returns every saved
    payload. ``progress`` (optional) is called with a human status string before each
    narration — real runs take minutes; silence would look like a hang."""
    out: list[dict] = []
    total = len(conditions) * reps
    i = 0
    for inputs in conditions:
        for rep in range(1, reps + 1):
            i += 1
            if progress:
                progress(f"[{i}/{total}] {inputs.condition_id} rep {rep}")
            out.append(run_one(inputs, rep, runners, out_dir=out_dir,
                               manifest_path=manifest_path))
    return out
