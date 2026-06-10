"""Strategy config loader.

Loads a versioned strategy YAML into a validated object. A malformed or
incomplete strategy is a hard failure here, at load time — never a silent
default that would corrupt every downstream screen. The validated `criteria`
are what get injected into the deterministic screening tools.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class ScreenCriteria(BaseModel):
    min_dividend_yield: float = Field(ge=0.0, le=1.0)
    max_payout_ratio: float = Field(ge=0.0)
    min_market_cap: float = Field(ge=0.0)
    min_dividend_growth_years: int = Field(ge=0)


class StrategyPolicy(BaseModel):
    unverifiable_streak_is_blocking: bool = True
    partial_pass_allows_hold: bool = True


class VetoPolicy(BaseModel):
    """Thresholds for the deterministic human-veto gate."""

    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class Strategy(BaseModel):
    id: str
    name: str
    version: int = Field(ge=1)
    description: str = ""
    criteria: ScreenCriteria
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


def load_strategy(path: str | Path) -> Strategy:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"strategy file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"strategy file {p} did not parse to a mapping")
    return Strategy.model_validate(raw)
