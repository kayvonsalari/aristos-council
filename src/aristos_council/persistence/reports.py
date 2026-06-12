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

from ..state import (
    CriticReport,
    Decision,
    ResearchState,
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
    strategy_id: str
    # Company name if the run captured it (get_fundamentals.name). Optional and
    # default None so reports saved before this field round-trip unchanged.
    company_name: Optional[str] = None
    specialist_opinions: list[SpecialistOpinion] = Field(default_factory=list)
    critic_report: Optional[CriticReport] = None
    decision: Optional[Decision] = None
    veto_flags: list[VetoFlag] = Field(default_factory=list)
    # FULL audit summary (counts AND violations/unit_scaled_notes prose) — the
    # reviewer-facing detail, kept verbatim unlike the verdict log's counts.
    provenance_audit: Optional[dict] = None


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


def report_from_state(
    state: ResearchState, run_at: Optional[datetime] = None
) -> RunReport:
    """Build a persistable report from a completed run's state.

    ``run_at`` defaults to the run's ``as_of`` stamp so the report's timestamp
    (and therefore its filename) matches the run it describes.
    """
    return RunReport(
        ticker=state.ticker,
        run_at=run_at or state.as_of,
        strategy_id=state.strategy_id,
        company_name=_company_name_from_state(state),
        specialist_opinions=list(state.specialist_opinions),
        critic_report=state.critic_report,
        decision=state.decision,
        veto_flags=list(state.veto_flags),
        provenance_audit=state.provenance_audit,
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
