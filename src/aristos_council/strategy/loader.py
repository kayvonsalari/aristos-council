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

    ``is_gating`` (is_gating build): if True, a CONFIRMED fail (passed is False)
    of this criterion caps the final disposition at SELL deterministically, in
    code — the LLM Decision agent cannot raise it. Default False, so every
    existing strategy is unaffected. Unlike ``unverifiable_blocks`` this IS wired
    to behaviour (see agents/disposition.py + the decision node). It is a
    committed YAML strategy property, deliberately NOT exposed as a UI-editable
    per-run knob.
    """

    name: str
    # No global lower bound: each criterion's ParamSpec declares its own range
    # (validated up front in validate_selections). Most are >= 0, but a RETURN-based
    # floor like min_price_momentum is legitimately negative (catch breakdowns, not
    # flatness), which a blanket ge=0.0 wrongly rejected.
    threshold: float
    unverifiable_blocks: bool = False
    is_gating: bool = False


class StrategyPolicy(BaseModel):
    partial_pass_allows_hold: bool = True


class ScoringConfig(BaseModel):
    """Weights + thresholds for the deterministic ``decision_matrix`` (the hybrid
    verdict that runs alongside the LLM Decision agent).

    SCREEN-DOMINANT by design: each criterion is worth far more than a specialist
    stance, so the deterministic screen ANCHORS the score and the (wobble-prone) LLM
    stances only TILT it. Defaults are chosen so a handful of criteria outweigh the
    whole specialist panel; strategies override per-criterion weights in YAML.
    Tunable knobs, not code.
    """

    # Points a criterion contributes at full margin; per-criterion overrides below.
    default_criterion_weight: float = Field(default=20.0, ge=0.0)
    criterion_weights: dict[str, float] = Field(default_factory=dict)
    # SMALL on purpose — one specialist flip must not cross a threshold on a clear
    # name (the screen-dominance guarantee, asserted in tests).
    stance_weight: float = Field(default=3.0, ge=0.0)
    buy_threshold: float = 20.0
    sell_threshold: float = -20.0
    # Score within this distance of the nearest band boundary -> BORDERLINE (a
    # deterministic, single-run "your call" signal — no n=5 needed).
    borderline_margin: float = Field(default=6.0, ge=0.0)
    # Price-momentum (value+momentum): a SIGNED contribution =
    # clamp(return_12m, -cap, +cap) x weight. A POSITIVE return boosts the score, a
    # NEGATIVE return SUBTRACTS (a cheap-but-falling name is dragged out of BUY).
    # The weight is meaningful (comparable to a screen criterion), NOT the tiny
    # stance weight — momentum is supposed to matter.
    momentum_weight: float = Field(default=20.0, ge=0.0)
    momentum_cap: float = Field(default=0.5, ge=0.0)   # clamp |return| before scaling

    def weight_for(self, name: str) -> float:
        return self.criterion_weights.get(name, self.default_criterion_weight)


class VetoPolicy(BaseModel):
    """Thresholds for the deterministic human-veto gate."""

    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class Strategy(BaseModel):
    id: str
    name: str
    version: int = Field(ge=1)
    # Friendly, user-facing name for dropdowns/captions. The `id` stays the STABLE
    # record key (never renamed); display-only, falling back to `name` then `id`.
    display_name: str = ""
    role: str = ""              # optional one-line role caption (display-only)
    # UI visibility (Sprint 4C): "hidden" -> not listed in dropdowns by default (still
    # loadable via CLI/loader). Presentation only.
    ui: str = ""
    description: str = ""
    criteria: list[CriterionSpec] = Field(min_length=1)
    policy: StrategyPolicy = Field(default_factory=StrategyPolicy)
    veto: VetoPolicy = Field(default_factory=VetoPolicy)
    # Deterministic decision-matrix weights/thresholds (hybrid verdict). Optional
    # with screen-dominant defaults, so strategies without a `scoring:` block still
    # produce a matrix verdict.
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
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
