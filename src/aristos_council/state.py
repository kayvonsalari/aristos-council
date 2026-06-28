"""ResearchState: the shared state object threaded through the Aristos Council graph.

Design notes
------------
- This is the single source of truth that flows through every LangGraph node.
- Specialists APPEND to `specialist_opinions`; they never overwrite each other.
- Every numeric claim a specialist makes must carry provenance (see `Provenance`).
  The number-provenance guardrail later asserts that each Figure.value can be
  traced to a recorded tool output. Untraceable numbers are a hard failure.
- `veto_triggers` accumulates reasons the human gate must fire. An empty list
  after the Decision node means the run may auto-proceed; a non-empty list means
  it must pause for human review.
- We keep the schema permissive about WHICH specialists ran (dict keyed by name)
  so the same state works if you later add or remove a specialist without a
  schema migration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Provenance — the backbone of the "every figure traces to a tool output" rule
# --------------------------------------------------------------------------- #
class Provenance(BaseModel):
    """Where a single number came from.

    `tool_name` + `call_id` must match a ToolCall recorded in
    ResearchState.tool_calls. `field_path` documents which part of that tool's
    output the value was read from (e.g. "metrics.dividend_yield"), so the
    provenance check is auditable by a human, not just a boolean pass/fail.
    """

    tool_name: str
    call_id: str
    field_path: str = Field(
        description="Dotted path into the tool output the value was read from."
    )


class Figure(BaseModel):
    """A number a specialist used, bound to its provenance.

    Specialists must wrap EVERY externally-derived number in a Figure. Prose
    like 'yield looks healthy' is fine unquantified; the moment a specialist
    writes '3.8%', that 3.8 must arrive as a Figure with provenance.

    `value` may be None: citing a NULL field (e.g. years_dividend_growth
    returned null from the provider) is legitimate, provenance-traceable
    evidence of ABSENCE — the Risk specialist's bread and butter. What is
    never allowed is a value (or a null) without a resolvable call_id.
    """

    label: str
    value: Optional[float] = None
    unit: str = ""
    provenance: Provenance

    # Tolerate a null/missing unit (== unitless): mirrors FigureRef so a figure
    # carrying unit=None survives validation here too, with unit "". See the MO
    # live-run regression in agents/schemas.py:_coerce_unit.
    @field_validator("unit", mode="before")
    @classmethod
    def _unit_unitless_if_null(cls, v: Any) -> Any:
        return "" if v is None else v


# --------------------------------------------------------------------------- #
# Tool call ledger — deterministic tools log here so math/provenance is auditable
# --------------------------------------------------------------------------- #
class ToolCall(BaseModel):
    """One recorded invocation of a deterministic tool.

    The council's hard rule is that ALL math happens in deterministic tools, not
    in an LLM. Each call is logged with its inputs and full output so that (a)
    provenance checks can resolve Figure -> ToolCall, and (b) a human can replay
    the exact computation.
    """

    call_id: str
    tool_name: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    ok: bool = True
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------- #
# Specialist output
# --------------------------------------------------------------------------- #
class Stance(str, Enum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    ABSTAIN = "abstain"  # specialist lacked data to form a view


class SpecialistName(str, Enum):
    FUNDAMENTAL = "fundamental"
    TECHNICAL = "technical"
    SENTIMENT = "sentiment"
    RISK = "risk"


class SpecialistOpinion(BaseModel):
    """One specialist's contribution to the deliberation."""

    specialist: SpecialistName
    stance: Stance
    # 0.0–1.0 — the specialist's own confidence in its stance, BEFORE the
    # Critic and Decision agents weigh in.
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: str = Field(description="Short prose argument for the stance.")
    figures: list[Figure] = Field(default_factory=list)
    # Anything that should make the human gate nervous — stale data, a metric
    # that couldn't be computed, an assumption the specialist had to make.
    caveats: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------- #
# Critic output — argues the OPPOSITE of the emerging consensus
# --------------------------------------------------------------------------- #
class CriticReport(BaseModel):
    """The Critic's adversarial pass.

    `targets_stance` is the consensus the Critic argued against, so the Decision
    agent (and a human) can see what was being stress-tested. The Critic does not
    vote; it surfaces the strongest counter-case and any holes in the figures.

    The Critic is bound by the SAME provenance contract as specialists: any
    number it cites lands in `figures` with a resolvable ToolCall reference.
    Quantitative concerns it cannot support from the evidence (missing share
    count, suspected stale data, arithmetic it is not allowed to perform) go in
    `open_questions`, phrased as questions for human resolution — they are
    explicitly NOT evidence and the Decision agent must not treat them as such.
    """

    targets_stance: Stance
    counter_thesis: str
    weaknesses_found: list[str] = Field(default_factory=list)
    # Figures the Critic believes are mis-weighted, stale, or unsupported.
    challenged_figures: list[str] = Field(default_factory=list)
    # Provenance-bound numbers the Critic cites in its counter-case.
    figures: list[Figure] = Field(default_factory=list)
    # Unverifiable quantitative concerns, phrased as questions for a human.
    open_questions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------- #
# Decision output
# --------------------------------------------------------------------------- #
class Recommendation(str, Enum):
    BUY = "buy"
    HOLD = "hold"
    SELL = "sell"
    # Off-ladder verdict (NOT a rank on buy/hold/sell): a NOT-EVAL (passed is
    # None) on a GATING criterion means the screen could not even decide, so
    # direction is undefined and the run is short-circuited to human review.
    # Deliberately ABSENT from disposition._RANK — it must never be compared as
    # more/less bullish. Fixes the SK Hynix wrong-HOLD (non-USD market cap
    # NOT-EVAL was silently treated as a pass).
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Decision(BaseModel):
    recommendation: Recommendation
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    # Specialists who dissented from the final call, noted explicitly so dissent
    # is never silently dropped.
    dissent: list[SpecialistName] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)

    # --- Deterministic disposition-gate audit (is_gating build) -------------- #
    # `recommendation` is the FINAL verdict (post-gate). These record whether the
    # gate overrode the LLM and why, so the override is auditable. All optional /
    # default so previously-stored verdicts still parse.
    original_recommendation: Optional[Recommendation] = None  # LLM pre-gate verdict
    gate_override_applied: bool = False
    gating_criterion_fired: Optional[str] = None              # the criterion that capped
    # True when the verdict was short-circuited to INSUFFICIENT_EVIDENCE because a
    # GATING criterion was NOT-EVAL (passed is None). Distinct from a confirmed-fail
    # SELL cap (gate_override_applied alone). Default False so older records parse.
    insufficient_evidence: bool = False


# --------------------------------------------------------------------------- #
# Veto gate
# --------------------------------------------------------------------------- #
class VetoTrigger(str, Enum):
    LOW_CONFIDENCE = "low_confidence"
    SPECIALIST_CONFLICT = "specialist_conflict"
    DATA_QUALITY = "data_quality"
    RECOMMENDATION_FLIP = "recommendation_flip"
    # Decision verdict contradicts the strict stance-majority of non-abstaining
    # specialists (e.g. a HOLD over a 3-bullish council). See agents/veto.py.
    MAJORITY_OVERRIDE = "majority_override"
    # Verdict is INSUFFICIENT_EVIDENCE: a gating criterion was NOT-EVAL (passed is
    # None), so the run is off the buy/hold/sell ladder and ALWAYS pauses for a
    # human — unconditionally, never an auto-proceed.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    # The deterministic gate capped the verdict AND the LLM/gate disagreement was
    # LARGE (a confidently bullish call hard-stopped to the SELL cap). Routine caps
    # (HOLD->SELL, low-confidence) do NOT fire this — only the surprising ones.
    GATE_OVERRIDE_MATERIAL = "gate_override_material"


class VetoFlag(BaseModel):
    trigger: VetoTrigger
    detail: str
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------- #
# Run health / observability — WHY a datum was absent, so silent degradation
# becomes loud. The whole point is the HONEST vs TOOL-FAILURE distinction below.
# --------------------------------------------------------------------------- #
class FailureKind(str, Enum):
    """Why a criterion abstained or a source produced nothing.

    The cut that matters: DATA_ABSENT and CURRENCY_MISMATCH are the system working
    CORRECTLY (the datum genuinely doesn't exist / isn't comparable) — honest
    abstention that flows through NOT-EVAL / INSUFFICIENT_EVIDENCE and must NOT
    raise the alarm. FETCH_ERROR, EMPTY_RESPONSE and MISSING_KEY are FIXABLE TOOL
    failures — a flaky API, an empty response, an unset key — and those mark the
    run DEGRADED so the verdict carries a loud warning. Don't cry wolf on honest
    abstention; do scream on a broken tool.
    """

    DATA_ABSENT = "data_absent"            # datum genuinely doesn't exist (honest)
    FETCH_ERROR = "fetch_error"            # adapter raised / API errored / timeout
    EMPTY_RESPONSE = "empty_response"      # call returned but no usable data
    MISSING_KEY = "missing_key"            # optional source had no API key
    CURRENCY_MISMATCH = "currency_mismatch"  # not comparable (e.g. EUR cap vs USD)


# The kinds that mean a TOOL failed (fixable) — these and only these set
# `ResearchState.degraded`. DATA_ABSENT / CURRENCY_MISMATCH are deliberately ABSENT.
_DEGRADING_FAILURES: frozenset[FailureKind] = frozenset(
    {FailureKind.FETCH_ERROR, FailureKind.EMPTY_RESPONSE, FailureKind.MISSING_KEY}
)


class RunIssue(BaseModel):
    """One typed thing that went wrong (or was honestly absent) during a run.

    The structured successor to a bare ``errors`` string: it carries WHERE
    (`source`, e.g. 'sentiment', 'fundamentals'), WHY (`reason`, a FailureKind),
    and the raw `detail`. ``is_degrading`` is the single source of truth for
    whether this issue trips the degraded-run alarm.
    """

    source: str
    reason: FailureKind
    detail: str = ""
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def is_degrading(self) -> bool:
        return self.reason in _DEGRADING_FAILURES


# --------------------------------------------------------------------------- #
# Top-level state
# --------------------------------------------------------------------------- #
class ResearchState(BaseModel):
    """The object threaded through the entire council graph.

    Lifecycle (Phase 1 cares only about the data substrate, but the shape is
    laid out for the full graph):
        request -> specialists append opinions -> critic -> decision ->
        veto evaluation -> (human gate | auto-proceed)
    """

    # --- request ---
    ticker: str
    strategy_id: str = Field(
        description="Which versioned strategy YAML governs this run, "
        "e.g. 'dividend_aristocrats_v1'. Stays the BASE id even when this run "
        "applied ephemeral overrides (the delta is in applied_overrides)."
    )
    as_of: datetime = Field(default_factory=_utcnow)
    # Recommendation from the previous run for this ticker (if any) — used by
    # the veto gate to detect a RECOMMENDATION_FLIP.
    prior_recommendation: Optional[Recommendation] = None
    # Ephemeral per-run disposition overrides applied on top of the base strategy
    # (e.g. {"partial_pass_allows_hold": false,
    #        "criteria.min_dividend_growth_streak.is_gating": true}). Empty for a
    # default run. A non-empty value means the run was an EXPERIMENT: it does not
    # fire recommendation_flip and is never the flip baseline (see veto.py /
    # verdicts.load_latest).
    applied_overrides: dict = Field(default_factory=dict)

    # --- evidence substrate ---
    tool_calls: list[ToolCall] = Field(default_factory=list)

    # --- deliberation ---
    specialist_opinions: list[SpecialistOpinion] = Field(default_factory=list)
    critic_report: Optional[CriticReport] = None
    decision: Optional[Decision] = None

    # --- audit ---
    # Summary of the deep provenance audit (see audit/provenance.py): figure
    # counts by status plus violation texts. Populated by the audit node,
    # after decision and before veto. None if the audit hasn't run.
    provenance_audit: Optional[dict] = None

    # --- gate ---
    veto_flags: list[VetoFlag] = Field(default_factory=list)
    human_reviewed: bool = False
    human_override: Optional[Recommendation] = None

    # --- bookkeeping ---
    errors: list[str] = Field(default_factory=list)
    # Typed run-health channel (observability): WHY a source/criterion was absent.
    # The bare `errors` strings stay (veto/provenance still append to them); this is
    # the structured layer the degraded-run banner and health summary read.
    run_issues: list[RunIssue] = Field(default_factory=list)

    # ------------------------------------------------------------------ #
    # Convenience accessors (no business logic — those live in graph nodes)
    # ------------------------------------------------------------------ #
    def tool_call_by_id(self, call_id: str) -> Optional[ToolCall]:
        for tc in self.tool_calls:
            if tc.call_id == call_id:
                return tc
        return None

    def opinion_for(
        self, specialist: SpecialistName
    ) -> Optional[SpecialistOpinion]:
        for op in self.specialist_opinions:
            if op.specialist == specialist:
                return op
        return None

    @property
    def requires_human_review(self) -> bool:
        return len(self.veto_flags) > 0 and not self.human_reviewed

    # ------------------------------------------------------------------ #
    # Run health (observability) — derived, never stored, so it can't drift
    # ------------------------------------------------------------------ #
    @property
    def degrading_issues(self) -> list[RunIssue]:
        """The TOOL-failure issues (fixable) — the ones that mark the run degraded.
        Honest DATA_ABSENT / CURRENCY_MISMATCH abstentions are excluded."""
        return [i for i in self.run_issues if i.is_degrading]

    @property
    def degraded(self) -> bool:
        """True iff a FIXABLE tool failure occurred (fetch/empty/missing-key). A run
        that only abstained honestly (DATA_ABSENT / CURRENCY_MISMATCH) is NOT
        degraded — the system was working correctly."""
        return len(self.degrading_issues) > 0
