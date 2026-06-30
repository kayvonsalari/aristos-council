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


class DecisionOutput(BaseModel):
    recommendation: Recommendation
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    dissent: list[SpecialistName] = Field(default_factory=list)

    _coerce = field_validator("dissent", mode="before")(_coerce_json_list)
