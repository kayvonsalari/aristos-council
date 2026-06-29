"""Full run reports — ``reports/<TICKER>/<run_at>.json``.

Why this exists (Sprint 3): ``verdicts/<TICKER>.json`` (Sprint 2) is a thin,
append-only log — verdict, confidence, stances, veto names, audit counts —
exactly what the *next* run needs and no more. Council Station (the Streamlit
UI) needs the OPPOSITE: the entire deliberation, so a human can re-read any past
run — every specialist thesis, the critic's counter-case, the decision
rationale, the full provenance audit including violation prose — without
re-spending API credits to regenerate it.

So this is a second, fatter sink for the same run. Design mirrors verdicts.py:

- ONE FILE PER RUN, not an append-only array. Reports are large and immutable;
  a per-run file (named by ``run_at``) is cheap to list, cheap to load one at a
  time, and never rewrites history. Files live under a per-ticker subdirectory.
- IO AT THE EDGE. Nothing here is called from inside the graph. The entrypoint
  saves the report after ``invoke`` returns, alongside the verdict append.
- FULL FIDELITY. Unlike VerdictRecord (which strips the audit to integer
  counts), the report keeps the audit summary verbatim — violation texts and
  all — because that detail is precisely what a reviewer opens the UI to see.

The report embeds the live state models (SpecialistOpinion, CriticReport,
Decision, VetoFlag) directly, so the round-trip is exact and the schema can
never silently drift from the deliberation it describes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from ..agents.nodes import _is_screen_tool  # screen tool-name back-compat shim
from ..state import (
    CriticReport,
    Decision,
    MatrixVerdict,
    ResearchState,
    RunIssue,
    SpecialistOpinion,
    VetoFlag,
)


class RunReport(BaseModel):
    """One run's complete deliberation, persisted for later re-rendering.

    Embeds the same models that flowed through the graph, so a loaded report is
    indistinguishable from the live state's deliberation slice.
    """

    ticker: str
    run_at: datetime
    strategy_id: str  # the BASE strategy id (unchanged even under overrides)
    # Ephemeral per-run overrides applied on top of the base strategy (the delta
    # vs the YAML; empty for a default run). Recorded so an overridden run is
    # reproducible and never mistaken for a default one. Optional/default for
    # backward compatibility with reports saved before this field.
    applied_overrides: dict = Field(default_factory=dict)
    # Company name if the run captured it (get_fundamentals.name). Optional and
    # default None so reports saved before this field round-trip unchanged.
    company_name: Optional[str] = None
    # Structured dividend-aristocrat screen result ({criteria:[...], flags:[...]}),
    # so the UI can render a deterministic criteria table regardless of how the
    # LLM formatted its prose. Optional/default None for backward compatibility.
    screen: Optional[dict] = None
    specialist_opinions: list[SpecialistOpinion] = Field(default_factory=list)
    critic_report: Optional[CriticReport] = None
    decision: Optional[Decision] = None
    # Deterministic decision-matrix verdict computed in PARALLEL with the LLM
    # `decision` (hybrid). `agreement` is "AGREE"/"DISAGREE" between the two so batch
    # runs can tally match rate. Both optional/default so older reports round-trip.
    matrix_decision: Optional[MatrixVerdict] = None
    agreement: Optional[str] = None
    veto_flags: list[VetoFlag] = Field(default_factory=list)
    # FULL audit summary (counts AND violations/unit_scaled_notes prose) — the
    # reviewer-facing detail, kept verbatim unlike the verdict log's counts.
    provenance_audit: Optional[dict] = None
    # Model + temperature each agent tier ran at, e.g.
    # {"decision": {"model": "anthropic:claude-sonnet-4-6", "temperature": 0.0}}.
    # Stamped at the edge from the runners (agents.runners.runner_metadata) so a
    # verdict is auditable/reproducible down to the model and temperature it used.
    # Optional/default None so reports saved before this field round-trip unchanged.
    models: Optional[dict] = None
    # Run-health (observability): the typed issues that occurred and whether the run
    # was DEGRADED by a fixable tool failure (fetch/empty/missing-key). `degraded`
    # is stored flat so a batch log / CSV can carry it as a column without parsing
    # run_issues. Both optional/default so older reports round-trip unchanged.
    run_issues: list[RunIssue] = Field(default_factory=list)
    degraded: bool = False
    # Contested-verdict flag: a one-run signal (derived from panel-split / dissent /
    # majority-override already on this report) that the verdict is a CLOSE call and
    # the user should read the report and apply their own judgement. Stored flat so a
    # screener/log can filter "clean BUY" vs "contested BUY". Optional/default so
    # older reports round-trip unchanged.
    contested: bool = False
    contested_reasons: list[str] = Field(default_factory=list)
    # Which versioned agent-prompt wording produced this run (agents.prompts.
    # PROMPT_VERSION). Stamped so a behavioural prompt change is attributable: a
    # verdict records the exact prompt it came from. Optional/default None so older
    # reports round-trip unchanged.
    prompt_version: Optional[str] = None
    # Decision-node micro-harness result (reproducibility.decision_stability_summary):
    # {verdict_distribution, modal_verdict, stability "STABLE"/"BORDERLINE", gated, n,
    # confidence_mean/stdev} from replaying the Decision node N times on this run's
    # cached post-Critic state. None when the run wasn't measured. Optional/default so
    # older reports round-trip unchanged.
    decision_stability: Optional[dict] = None


def _company_name_from_state(state: ResearchState) -> Optional[str]:
    """The company name from the get_fundamentals tool call, if the run got one.

    The fundamentals output is a Fundamentals dataclass at runtime but may be a
    plain dict after (de)serialisation, so read it both ways. Best-effort: any
    miss simply yields None and the UI falls back to the ticker alone.
    """
    for tc in state.tool_calls:
        if tc.tool_name == "get_fundamentals" and tc.ok and tc.output is not None:
            out = tc.output
            name = out.get("name") if isinstance(out, dict) else getattr(
                out, "name", None)
            return name or None
    return None


def _screen_from_state(state: ResearchState) -> Optional[dict]:
    """The structured dividend-aristocrat screen result, if the run produced one.

    The screen tool logs ``asdict(ScreenResult)`` (a plain nested dict); read it
    both as dict and dataclass to be robust to (de)serialisation.
    """
    from dataclasses import asdict, is_dataclass

    for tc in state.tool_calls:
        if _is_screen_tool(tc.tool_name) and tc.ok and tc.output:
            out = tc.output
            if isinstance(out, dict):
                return out
            if is_dataclass(out):
                return asdict(out)
    return None


def report_from_state(
    state: ResearchState, run_at: Optional[datetime] = None
) -> RunReport:
    """Build a persistable report from a completed run's state.

    ``run_at`` defaults to the run's ``as_of`` stamp so the report's timestamp
    (and therefore its filename) matches the run it describes.
    """
    # Contested flag — derived (not new analysis) from panel-split / dissent /
    # majority-override already on the state. Imported lazily to avoid any import
    # cycle with the presentation layer.
    from ..presentation import contested as _contested
    from ..agents.prompts import PROMPT_VERSION
    is_contested, contested_reasons = _contested(state)
    # Agreement between the LLM verdict and the deterministic matrix verdict.
    agreement = None
    if state.matrix_decision is not None and state.decision is not None:
        agreement = ("AGREE"
                     if state.matrix_decision.verdict == state.decision.recommendation
                     else "DISAGREE")
    return RunReport(
        ticker=state.ticker,
        run_at=run_at or state.as_of,
        strategy_id=state.strategy_id,
        applied_overrides=dict(state.applied_overrides),
        company_name=_company_name_from_state(state),
        screen=_screen_from_state(state),
        specialist_opinions=list(state.specialist_opinions),
        critic_report=state.critic_report,
        decision=state.decision,
        matrix_decision=state.matrix_decision,
        agreement=agreement,
        veto_flags=list(state.veto_flags),
        provenance_audit=state.provenance_audit,
        run_issues=list(state.run_issues),
        degraded=state.degraded,
        contested=is_contested,
        contested_reasons=contested_reasons,
        prompt_version=PROMPT_VERSION,
    )


def _run_at_slug(run_at: datetime) -> str:
    """A filesystem-safe slug for a timestamp (no colons — invalid on Windows).

    Normalised to UTC so the lexical order of slugs is also chronological order,
    which lets ``list_reports`` sort by filename alone.
    """
    dt = (run_at.astimezone(timezone.utc)
          if run_at.tzinfo else run_at.replace(tzinfo=timezone.utc))
    return dt.strftime("%Y-%m-%dT%H-%M-%SZ")


def ticker_dir(ticker: str, reports_dir: Path) -> Path:
    """The per-ticker directory holding that ticker's run reports."""
    return Path(reports_dir) / ticker.upper()


def report_path(ticker: str, run_at: datetime, reports_dir: Path) -> Path:
    """The on-disk path for one run's report."""
    return ticker_dir(ticker, reports_dir) / f"{_run_at_slug(run_at)}.json"


def save_report(report: RunReport, reports_dir: Path) -> Path:
    """Write one run report to ``reports/<TICKER>/<run_at>.json``.

    Creates the per-ticker directory if needed. Returns the path written.
    """
    path = report_path(report.ticker, report.run_at, reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_report(path: str | Path) -> RunReport:
    """Load a single run report from its path."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return RunReport.model_validate(raw)


def list_reports(ticker: str, reports_dir: Path) -> list[Path]:
    """All report file paths for a ticker, oldest first ([] if none).

    Slugs are UTC and zero-padded, so a lexical sort is chronological.
    """
    d = ticker_dir(ticker, reports_dir)
    if not d.exists():
        return []
    return sorted(d.glob("*.json"))


def load_reports(ticker: str, reports_dir: Path) -> list[RunReport]:
    """All run reports for a ticker, oldest first ([] if none)."""
    return [load_report(p) for p in list_reports(ticker, reports_dir)]
