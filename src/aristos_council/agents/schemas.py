"""Structured-output schemas the LLMs must fill in.

Specialists may ONLY cite numbers through `figures`, and every figure must name
the tool call it came from. The node validates those references against the
state's tool-call ledger; an untraceable number is recorded as a provenance
violation and trips the data-quality veto.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..state import Recommendation, SpecialistName, Stance


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


class CriticOutput(BaseModel):
    counter_thesis: str
    weaknesses_found: list[str] = Field(default_factory=list)
    challenged_figures: list[str] = Field(default_factory=list)


class DecisionOutput(BaseModel):
    recommendation: Recommendation
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    dissent: list[SpecialistName] = Field(default_factory=list)
