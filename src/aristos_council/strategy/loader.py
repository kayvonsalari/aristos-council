"""Strategy config loader.

Loads a versioned strategy YAML into a validated object. A malformed or
incomplete strategy is a hard failure here, at load time — never a silent
default that would corrupt every downstream screen.

A strategy SELECTS criteria from the registry by name and parameterizes their
thresholds (Sprint 4A). The loader validates those selections UP FRONT against
the registry (tools/criteria/registry.py): unknown criterion names, out-of-range
thresholds, and required-but-unavailable evidence are rejected at load time.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from ..tools.criteria.registry import validate_selections


class CriterionSpec(BaseModel):
    """One criterion a strategy selects from the registry, with its threshold.

    ``unverifiable_blocks`` records whether an UNVERIFIABLE (passed=null) result
    for this criterion should count as blocking for the human gate — the
    per-criterion successor to the old strategy-wide
    ``unverifiable_streak_is_blocking`` flag. (Metadata in 4A; no logic consumes
    it yet, exactly as before.)
    """

    name: str
    threshold: float = Field(ge=0.0)
    unverifiable_blocks: bool = False


class StrategyPolicy(BaseModel):
    partial_pass_allows_hold: bool = True


class VetoPolicy(BaseModel):
    """Thresholds for the deterministic human-veto gate."""

    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class Strategy(BaseModel):
    id: str
    name: str
    version: int = Field(ge=1)
    description: str = ""
    criteria: list[CriterionSpec] = Field(min_length=1)
    policy: StrategyPolicy = Field(default_factory=StrategyPolicy)
    veto: VetoPolicy = Field(default_factory=VetoPolicy)
    rationale: str = ""
    notes: str = ""

    @field_validator("id")
    @classmethod
    def _id_carries_version(cls, v: str) -> str:
        # Enforce the naming convention that makes runs reproducible: the id
        # must end in _v<N> so a logged decision points at an exact file.
        if "_v" not in v:
            raise ValueError(
                f"strategy id '{v}' must encode a version, e.g. '..._v1'"
            )
        return v

    @model_validator(mode="after")
    def _criteria_resolve_against_registry(self) -> "Strategy":
        # Fail fast: every selected criterion must be a known registry name with
        # an in-range threshold whose required evidence the run can supply.
        problems = validate_selections(self.criteria)
        if problems:
            raise ValueError("invalid criteria: " + "; ".join(problems))
        return self


def load_strategy(path: str | Path) -> Strategy:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"strategy file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"strategy file {p} did not parse to a mapping")
    return Strategy.model_validate(raw)
