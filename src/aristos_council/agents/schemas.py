"""Structured-output schemas the LLMs must fill in.

Specialists may ONLY cite numbers through `figures`, and every figure must name
the tool call it came from. The node validates those references against the
state's tool-call ledger; an untraceable number is recorded as a provenance
violation and trips the data-quality veto.
"""

from __future__ import annotations

import json
from typing import Any

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


class FigureRef(BaseModel):
    label: str
    value: float
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


class SpecialistOutput(BaseModel):
    stance: Stance
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: str
    figures: list[FigureRef] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)

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
