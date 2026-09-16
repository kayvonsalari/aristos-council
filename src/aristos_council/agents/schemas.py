"""Structured-output schemas the LLMs must fill in.

Specialists may ONLY cite numbers through `figures`, and every figure must name
the tool call it came from. The node validates those references against the
state's tool-call ledger; an untraceable number is recorded as a provenance
violation and trips the data-quality veto.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from ..state import Recommendation, SpecialistName, Stance


def _coerce_json_list(v: Any) -> Any:
    """Models sometimes return a list field as a JSON *string*. Parse it back.

    Live-run regression: the Risk specialist returned figures as
    '[\\n  {"label": ...}]' (a string), which pydantic rightly rejected as a
    list — and crashed the run. Tolerance at parse time, strictness at
    validation time: an unparseable string degrades to [] rather than killing
    the council; the figure-provenance machinery then shows the opinion simply
    carries no traceable figures.
    """
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
        return []
    return v


def _coerce_unit(v: Any) -> Any:
    """A null/missing unit means 'unitless'. Live-run regression (MO): an agent
    emitted figures with ``unit: null`` and the string-only field failed the
    WHOLE SpecialistOutput, killing the run. Same posture as _coerce_json_list:
    tolerate at parse time, coerce null -> '' rather than crash."""
    return "" if v is None else v


class FigureRef(BaseModel):
    label: str
    # Optional: a model may cite a NULL field as evidence of absence (live-run
    # regression: Risk cited years_dividend_growth=None and the float-only
    # schema crashed the run). Null + valid call_id = legitimate citation.
    value: float | None = None
    # Tolerant: null/missing unit -> "" (unitless). See _coerce_unit.
    unit: str = ""
    # call_id/field_path are REQUIRED by policy but optional at parse time:
    # if a model omits them, validation must not crash the run. The specialist
    # node treats a missing/unknown call_id as a provenance violation — the
    # figure is dropped, the violation is logged, and the data-quality veto
    # fires. Crash-on-parse would punish the user; violation-and-flag is the
    # designed behaviour.
    call_id: str = Field(
        default="", description="ToolCall.call_id this number came from"
    )
    field_path: str = Field(
        default="", description="Where in that tool output it was read"
    )

    _unitless = field_validator("unit", mode="before")(_coerce_unit)
    # Tolerate an EXPLICIT null (not just an omitted key): the LLM intermittently
    # emits `call_id: null` / `field_path: null`, and a str-only field rejects None
    # and kills the WHOLE council run. Coerce null -> "" here; the specialist node
    # then treats an empty call_id as a provenance VIOLATION (figure dropped, logged,
    # data-quality veto fires) — violation-and-flag, never crash-on-parse.
    _ids_nullable = field_validator("call_id", "field_path",
                                    mode="before")(_coerce_unit)


class SpecialistOutput(BaseModel):
    stance: Stance
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: str
    figures: list[FigureRef] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    # Aristos v2 integrated pipeline: the specialist is an ANALYST, not a voter. When
    # a RANKER verdict is in the evidence, it states whether its domain view SUPPORTS
    # (True) or CHALLENGES (False) that verdict — None when it has no domain opinion
    # on the ranker's call. The dissent_note is the one-line "why" (the forward-
    # looking check trailing factors lack, e.g. an un-priced patent-cliff headline).
    agrees_with_ranker: Optional[bool] = None
    dissent_note: str = ""

    _coerce = field_validator("figures", "caveats", mode="before")(
        _coerce_json_list
    )


class CriticOutput(BaseModel):
    counter_thesis: str
    weaknesses_found: list[str] = Field(default_factory=list)
    challenged_figures: list[str] = Field(default_factory=list)
    # Same provenance contract as specialists: numbers the Critic cites must
    # arrive here with a resolvable call_id, or they are violations.
    figures: list[FigureRef] = Field(default_factory=list)
    # Quantitative concerns the Critic could NOT support from the evidence,
    # phrased as questions for human resolution — never asserted as facts.
    open_questions: list[str] = Field(default_factory=list)

    _coerce = field_validator("weaknesses_found", "challenged_figures",
                              "figures", "open_questions",
                              mode="before")(_coerce_json_list)


# --------------------------------------------------------------------------- #
# REPORT-4 — the narration arrives as NAMED FIELDS, not as prose carrying layout
# --------------------------------------------------------------------------- #
# The narrator used to return one free-text `rationale` that the report pasted verbatim.
# Left to invent its own structure it invented a different one every run — "---"
# separators, ALL-CAPS pseudo-headings ("ALL LENS VERDICTS — STATED IN FULL BEFORE ANY
# PROSE:"), even a markdown h2 that broke the document's own heading tree — and it quoted
# numbers as strings mid-sentence ("46.92", "KRW 24,793,783,000,000"), so a price could
# render beside another name's price in a different currency with nothing to tell them
# apart. Layout is the REPORT's job and formatting is the unit rules' job; the narrator's
# job is what it knows. So it fills these fields and renders nothing.
class FactorRank(BaseModel):
    """One factor's ordinal for this name under one lens. ``rank`` of ``cohort_size``."""

    factor: str
    rank: int
    cohort_size: int
    note: str = ""                      # optional plain gloss, e.g. "below the median"


class LensVerdictItem(BaseModel):
    """One lens's verdict for this name. EVERY selected lens appears — including the ones
    that rated it HOLD/SELL or excluded it — because a reader is never shown a one-sided
    case (NARR-UNION-1). ``position``/``cohort_size`` are omitted for an excluded name."""

    lens: str
    verdict: str
    position: Optional[int] = None
    cohort_size: Optional[int] = None
    excluded_reason: str = ""


class LensAttributionItem(BaseModel):
    """Why ONE lens placed the name where it did — its factor ranks and the screens it
    passed, in that lens's own terms. One entry per lens that ranked the name."""

    lens: str
    factor_ranks: list[FactorRank] = Field(default_factory=list)
    # SCORE-NAME-1 — this lens's factor ranks ADDED UP, for this name. Named
    # `factor_score` and never "rank-sum": that word meant two unrelated numbers, the
    # cross-lens sum of POSITIONS and this within-lens sum of FACTOR RANKS, and the
    # narration quoted one a section below a table showing the other.
    factor_score: Optional[float] = None
    screens_passed: list[str] = Field(default_factory=list)
    reasoning: str = ""

    _coerce = field_validator("factor_ranks", "screens_passed",
                              mode="before")(_coerce_json_list)


class SpecialistView(BaseModel):
    """One specialist's contribution.

    NOT ASSESSED is a first-class state, not a zero: ``assessed=False`` carries the CAUSE
    and MUST carry no stance and no confidence. The sentiment specialist abstaining for
    want of data used to render as "abstain, confidence 0.00", which reads as a measured
    neutral rather than as a channel the system could not see at all (house rule 3 — null
    is NOT EVALUATED, never false)."""

    specialist: str
    assessed: bool = True
    stance: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: str = ""
    not_assessed_reason: str = ""


class PeriodValue(BaseModel):
    """One period of a multi-year series — ``FY2024``, ``69659000000``.

    ``period`` may be empty when the statements genuinely carry no fiscal label; the
    renderer then says "oldest first" rather than leaving the order to be guessed."""

    period: str = ""
    value: Optional[float] = None


class MoneySeries(BaseModel):
    """A multi-period money series, OLDEST FIRST, with its currency.

    SERIES-LABEL-1. The narrator used to write these as prose — "the most recent figure
    is USD 69,659,000,000, preceded by USD 28,989,000,000, USD 70,012,000,000, and
    USD 64,134,000,000" — which leaves a reader unable to say which year is which. On the
    2026-08-26 run that mattered: one year's free cash flow halved and recovered, and
    "preceded by" cannot express that. Structured, the renderer can label and order it."""

    label: str = ""
    currency: str = ""
    periods: list[PeriodValue] = Field(default_factory=list)

    _coerce = field_validator("periods", mode="before")(_coerce_json_list)


class Narration(BaseModel):
    """The narrator's whole output, as fields. The report renders them."""

    echoed_verdict: str = ""
    # SERIES-LABEL-1 — multi-period money series as DATA, so the renderer can label the
    # periods and order them oldest -> newest instead of the narrator writing
    # "preceded by" and hoping.
    money_series: list[MoneySeries] = Field(default_factory=list)
    lens_verdicts: list[LensVerdictItem] = Field(default_factory=list)
    lens_attribution: list[LensAttributionItem] = Field(default_factory=list)
    # Present ONLY when the lenses disagree — the disagreement is reported and LEFT
    # STANDING, never resolved (CROSS_LENS_CONSTRAINT).
    disagreement_note: str = ""
    neutral_context: list[str] = Field(default_factory=list)
    specialist_views: list[SpecialistView] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    _coerce = field_validator("lens_verdicts", "lens_attribution", "neutral_context",
                              "specialist_views", "open_questions", "money_series",
                              mode="before")(_coerce_json_list)


class DecisionOutput(BaseModel):
    recommendation: Recommendation
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    dissent: list[SpecialistName] = Field(default_factory=list)
    # REPORT-4. Optional so that SECOND-OPINION mode (which issues a verdict rather than
    # narrating) and every report recorded before this change still load unchanged.
    narration: Optional[Narration] = None

    _coerce = field_validator("dissent", mode="before")(_coerce_json_list)


class ReaderSummary(BaseModel):
    """READER-1 — one short plain-English note about the WHOLE RUN.

    Five named fields rather than one blob: the shape is the point. A reader wants what
    was asked, what happened, what survived and what to doubt, in that order, and a model
    given one free-text field writes an essay. The renderer joins them as five short
    paragraphs under their own bold leads.

    ``cannot_say`` is not a disclaimer and not optional. Every other section of the report
    states what it could not see; a summary that only reported findings would be the one
    place in the document that implies completeness."""

    asked: str          # 1-2 sentences: the list, its size, the tests that ran
    happened: str       # 2-4 sentences: what each test did, and any dominant rule
    survived: str       # 1-3 sentences: the shortlist, or why there is none
    doubt: str          # 1-3 sentences: abstentions, gaps, withheld figures
    cannot_say: str     # 1 sentence: the question this run structurally cannot answer
