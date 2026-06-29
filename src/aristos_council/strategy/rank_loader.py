"""Rank-strategy loader — a strategy that RANKS a universe on proven factors.

Distinct from the screen ``Strategy`` (criteria + thresholds): a rank strategy
declares a FACTOR LIST (from the factor registry), the rank direction per factor,
universe exclusions, and a BUY cut method — and NO point weights (the whole point
of the rank rebuild). Validated against the factor registry at load.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from ..factors import FACTOR_REGISTRY


class RankFactorSpec(BaseModel):
    name: str
    direction: str | None = None        # "high" | "low"; None -> registry default

    @field_validator("direction")
    @classmethod
    def _dir_valid(cls, v):
        if v is not None and v not in ("high", "low"):
            raise ValueError(f"direction must be 'high' or 'low', got {v!r}")
        return v


class RankStrategy(BaseModel):
    id: str
    name: str
    version: int = Field(ge=1)
    description: str = ""
    rationale: str = ""
    # The proven factors to rank on (>=1), validated against the factor registry.
    factors: list[RankFactorSpec] = Field(min_length=1)
    # Verdict cut over the ranked universe.
    cut: str = "quintile"               # quintile | top_k | top_percentile
    k: int = Field(default=6, ge=1)
    percentile: float = Field(default=0.2, gt=0.0, le=1.0)
    # A NOT-EVAL factor: keep at worst rank, or exclude the name from BUY entirely.
    missing: str = "worst"              # worst | exclude
    # Universe exclusions (applied by the caller before ranking).
    min_market_cap: float | None = None
    exclude_sectors: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_carries_version(cls, v: str) -> str:
        if "_v" not in v:
            raise ValueError(f"rank-strategy id '{v}' must encode a version, e.g. '..._v1'")
        return v

    @field_validator("cut")
    @classmethod
    def _cut_valid(cls, v):
        if v not in ("quintile", "top_k", "top_percentile"):
            raise ValueError(f"cut must be quintile|top_k|top_percentile, got {v!r}")
        return v

    @model_validator(mode="after")
    def _factors_resolve(self) -> "RankStrategy":
        unknown = [f.name for f in self.factors if f.name not in FACTOR_REGISTRY]
        if unknown:
            raise ValueError("unknown factors: " + ", ".join(unknown))
        return self


def load_rank_strategy(path: str | Path) -> RankStrategy:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"rank-strategy file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"rank-strategy file {p} did not parse to a mapping")
    return RankStrategy.model_validate(raw)
